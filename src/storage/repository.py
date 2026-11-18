"""All database reads and writes used by the pipeline (SQLAlchemy Core).

Functions take an open ``Connection`` so the caller controls the transaction
boundary: one transaction per report (Section 3.5.2).
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
from sqlalchemy import Connection, Engine, text

from src.common.text import normalise_alias

log = logging.getLogger(__name__)

MEASURE_COLUMNS: tuple[str, ...] = (
    "suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths",
)


# =============================================================================
#  Run log
# =============================================================================

RUN_COUNTERS: tuple[str, ...] = (
    "files_discovered", "files_new", "files_skipped_duplicate", "files_manual_queue",
    "rows_extracted", "rows_passed", "rows_warned", "rows_quarantined", "revisions_detected",
)


@dataclass
class RunStats:
    """In-memory counters, written to ``ops.pipeline_run_log`` at the end of a run."""

    files_discovered: int = 0
    files_new: int = 0
    files_skipped_duplicate: int = 0
    files_manual_queue: int = 0
    rows_extracted: int = 0
    rows_passed: int = 0
    rows_warned: int = 0
    rows_quarantined: int = 0
    revisions_detected: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in RUN_COUNTERS}


def start_run(
    engine: Engine,
    run_type: str,
    *,
    trigger_source: str = "cli",
    dag_id: str | None = None,
    code_version: str | None = None,
    attempt_number: int = 1,
) -> uuid.UUID:
    """Insert a ``running`` row and return its id (committed immediately)."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                """INSERT INTO ops.pipeline_run_log
                       (run_type, trigger_source, dag_id, code_version, attempt_number)
                   VALUES (:run_type, :trigger_source, :dag_id, :code_version, :attempt)
                   RETURNING run_id"""
            ),
            {"run_type": run_type, "trigger_source": trigger_source, "dag_id": dag_id,
             "code_version": code_version, "attempt": attempt_number},
        ).scalar_one()


def finish_run(
    engine: Engine, run_id: uuid.UUID, status: str, stats: RunStats | None = None, error_message: str | None = None
) -> None:
    """Close a run with its final status. Counters are only overwritten when ``stats`` is given."""
    assignments = ""
    params: dict[str, Any] = {"status": status, "err": error_message, "run_id": run_id}
    if stats is not None:
        assignments = ", " + ", ".join(f"{k} = :{k}" for k in RUN_COUNTERS)
        params.update(stats.as_dict())
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""UPDATE ops.pipeline_run_log
                       SET status = :status, ended_at = now(), error_message = :err{assignments}
                     WHERE run_id = :run_id"""
            ),
            params,
        )


# =============================================================================
#  Raw layer
# =============================================================================

def document_exists(conn: Connection, sha256: str) -> bool:
    """True if a file with this hash was already stored (idempotency check)."""
    return conn.execute(
        text("SELECT 1 FROM raw.documents WHERE sha256 = :h"), {"h": sha256}
    ).first() is not None


def register_document(conn: Connection, **fields: Any) -> int:
    """Insert a row into ``raw.documents`` and return ``document_id``."""
    columns = ", ".join(fields)
    params = ", ".join(f":{k}" for k in fields)
    return conn.execute(
        text(f"INSERT INTO raw.documents ({columns}) VALUES ({params}) RETURNING document_id"), fields
    ).scalar_one()


def set_document_status(conn: Connection, document_id: int, status: str, reason: str | None = None) -> None:
    conn.execute(
        text(
            """UPDATE raw.documents SET processing_status = :s, status_reason = :r
                WHERE document_id = :d"""
        ),
        {"s": status, "r": reason, "d": document_id},
    )


def pending_documents(conn: Connection, *, include_failed: bool = False) -> list[dict[str, Any]]:
    """Documents waiting for extraction, oldest first."""
    statuses = ["new"] + (["parse_failed"] if include_failed else [])
    rows = conn.execute(
        text(
            """SELECT document_id, sha256, source_name, source_url, file_path, file_name,
                      content_type, listing_title, listing_date, disease_hint, is_synthetic
                 FROM raw.documents
                WHERE processing_status = ANY(:st)
                ORDER BY document_id"""
        ),
        {"st": statuses},
    ).mappings()
    return [dict(r) for r in rows]


# =============================================================================
#  Reference data
# =============================================================================

def seed_reference(conn: Connection, reference_dir: Path) -> dict[str, int]:
    """Load states and aliases from ``data/reference`` (idempotent).

    Every state gets aliases for its name, its p-code and "<name> state".
    """
    national_id = conn.execute(
        text("SELECT location_id FROM curated.dim_location WHERE pcode = 'NG'")
    ).scalar_one()

    states_path = reference_dir / "nga_states.csv"
    aliases_path = reference_dir / "nga_location_aliases.csv"
    n_states = n_aliases = 0

    with states_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            population = int(row["population"]) if row.get("population") else None
            pop_year = int(row["population_year"]) if row.get("population_year") else None
            location_id = conn.execute(
                text(
                    """INSERT INTO curated.dim_location
                           (admin_level, pcode, parent_location_id, state_name, geo_zone,
                            population, population_year, population_source)
                       VALUES (1, :pcode, :parent, :name, :zone, :pop, :pop_year, :pop_src)
                       ON CONFLICT (pcode) DO UPDATE
                          SET state_name = EXCLUDED.state_name,
                              geo_zone = EXCLUDED.geo_zone,
                              population = COALESCE(EXCLUDED.population, curated.dim_location.population),
                              population_year = COALESCE(EXCLUDED.population_year,
                                                         curated.dim_location.population_year),
                              population_source = COALESCE(EXCLUDED.population_source,
                                                           curated.dim_location.population_source)
                       RETURNING location_id"""
                ),
                {"pcode": row["pcode"], "parent": national_id, "name": row["state_name"],
                 "zone": row["geo_zone"] or None, "pop": population, "pop_year": pop_year,
                 "pop_src": row.get("population_source") or None},
            ).scalar_one()
            n_states += 1
            for alias in {row["state_name"], row["pcode"], f"{row['state_name']} state"}:
                n_aliases += _add_alias(conn, alias, location_id, "seed")

    if aliases_path.exists():
        with aliases_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                location_id = conn.execute(
                    text(
                        """SELECT location_id FROM curated.dim_location
                            WHERE admin_level = 1 AND state_name = :n"""
                    ),
                    {"n": row["state_name"]},
                ).scalar_one_or_none()
                if location_id is None:
                    log.warning("alias %r points at unknown state %r", row["alias"], row["state_name"])
                    continue
                n_aliases += _add_alias(conn, row["alias"], location_id, "reference_csv")

    _add_alias(conn, "ng", national_id, "seed")
    return {"states": n_states, "aliases": n_aliases}


def _add_alias(conn: Connection, alias: str, location_id: int, source: str) -> int:
    result = conn.execute(
        text(
            """INSERT INTO curated.dim_location_alias (alias_normalised, location_id, alias_source)
               VALUES (:a, :l, :s) ON CONFLICT (alias_normalised) DO NOTHING"""
        ),
        {"a": normalise_alias(alias), "l": location_id, "s": source},
    )
    return result.rowcount or 0


def location_alias_map(conn: Connection) -> dict[str, str]:
    """``{normalised alias: pcode}`` for Python-side name standardisation."""
    rows = conn.execute(
        text(
            """SELECT a.alias_normalised, l.pcode
                 FROM curated.dim_location_alias a
                 JOIN curated.dim_location l USING (location_id)"""
        )
    )
    return {alias: pcode for alias, pcode in rows}


def disease_lookup(conn: Connection) -> dict[str, dict[str, Any]]:
    """``{disease_code: {disease_id, cfr_denominator, disease_name}}``."""
    rows = conn.execute(
        text("SELECT disease_code, disease_id, cfr_denominator, disease_name FROM curated.dim_disease")
    ).mappings()
    return {r["disease_code"]: dict(r) for r in rows}


# =============================================================================
#  Reports, staging and the curated merge
# =============================================================================

def upsert_source_report(conn: Connection, **fields: Any) -> int:
    """Insert or refresh ``curated.dim_source_report`` by ``report_code``."""
    disease_code = fields.pop("disease_code", None)
    fields["disease_id"] = None
    if disease_code:
        fields["disease_id"] = conn.execute(
            text("SELECT disease_id FROM curated.dim_disease WHERE disease_code = :c"), {"c": disease_code}
        ).scalar_one()
    columns = ", ".join(fields)
    params = ", ".join(f":{k}" for k in fields)
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields if k != "report_code")
    return conn.execute(
        text(
            f"""INSERT INTO curated.dim_source_report ({columns}) VALUES ({params})
                ON CONFLICT (report_code) DO UPDATE SET {updates}
                RETURNING report_id"""
        ),
        fields,
    ).scalar_one()


def previous_cumulative(
    conn: Connection, disease_code: str, pcode: str, wid: int
) -> Optional[dict[str, Optional[int]]]:
    """Cumulative totals stored for the latest week before ``wid`` in the same year."""
    row = conn.execute(
        text(
            """SELECT f.cum_suspected, f.cum_confirmed, f.cum_deaths, f.week_id
                 FROM curated.fact_weekly_cases f
                 JOIN curated.dim_disease d USING (disease_id)
                 JOIN curated.dim_location l USING (location_id)
                WHERE d.disease_code = :d AND l.pcode = :p
                  AND f.week_id < :w AND f.week_id / 100 = :w / 100
                ORDER BY f.week_id DESC
                LIMIT 1"""
        ),
        {"d": disease_code, "p": pcode, "w": wid},
    ).mappings().first()
    return dict(row) if row else None


STAGING_COLUMNS: tuple[str, ...] = (
    "disease_code", "pcode", "epi_year", "epi_week", *MEASURE_COLUMNS,
    "cfr_pct", "cfr_reported", "is_derived_from_cumulative", "quality_flags", "source_page",
)


def insert_staging(conn: Connection, run_id: uuid.UUID, report_id: int, rows: pd.DataFrame) -> int:
    """Write validated rows to ``staging.weekly_cases``."""
    if rows.empty:
        return 0
    records = []
    for rec in rows.to_dict(orient="records"):
        out: dict[str, Any] = {"run_id": run_id, "report_id": report_id}
        for col in STAGING_COLUMNS:
            value = rec.get(col)
            out[col] = _py(value)
        out["quality_flags"] = list(out["quality_flags"] or [])
        out["is_derived_from_cumulative"] = bool(out["is_derived_from_cumulative"] or False)
        records.append(out)
    columns = ", ".join(("run_id", "report_id", *STAGING_COLUMNS))
    params = ", ".join(f":{c}" for c in ("run_id", "report_id", *STAGING_COLUMNS))
    conn.execute(text(f"INSERT INTO staging.weekly_cases ({columns}) VALUES ({params})"), records)
    return len(records)


def merge_staging(conn: Connection, run_id: uuid.UUID, report_id: int) -> dict[str, int]:
    """Upsert staged rows into ``curated.fact_weekly_cases`` (Section 3.3.2, step 5).

    * A value missing from the new report never erases a stored value.
    * An older report never overwrites figures from a newer one.
    * Changed figures are copied to ``revision_history`` by the database trigger.
    """
    before = conn.execute(text("SELECT count(*) FROM curated.revision_history")).scalar_one()
    measure_updates = ",\n".join(
        f"{c} = COALESCE(EXCLUDED.{c}, f.{c})" for c in (*MEASURE_COLUMNS, "cfr_pct", "cfr_reported")
    )
    changed = " OR ".join(
        f"(EXCLUDED.{c} IS NOT NULL AND EXCLUDED.{c} IS DISTINCT FROM f.{c})"
        for c in (*MEASURE_COLUMNS, "cfr_pct", "cfr_reported")
    )
    result = conn.execute(
        text(
            f"""
            INSERT INTO curated.fact_weekly_cases AS f
                (disease_id, location_id, week_id, report_id, {", ".join(MEASURE_COLUMNS)},
                 cfr_pct, cfr_reported, is_derived_from_cumulative, quality_flags, last_run_id)
            SELECT d.disease_id, l.location_id, s.epi_year * 100 + s.epi_week, s.report_id,
                   {", ".join("s." + c for c in MEASURE_COLUMNS)},
                   s.cfr_pct, s.cfr_reported, s.is_derived_from_cumulative, s.quality_flags, s.run_id
              FROM staging.weekly_cases s
              JOIN curated.dim_disease  d ON d.disease_code = s.disease_code
              JOIN curated.dim_location l ON l.pcode = s.pcode
             WHERE s.run_id = :run_id AND s.report_id = :report_id
            ON CONFLICT (disease_id, location_id, week_id) DO UPDATE SET
                report_id = EXCLUDED.report_id,
                {measure_updates},
                is_derived_from_cumulative = EXCLUDED.is_derived_from_cumulative,
                quality_flags = EXCLUDED.quality_flags,
                last_run_id = EXCLUDED.last_run_id
            WHERE ({changed})
              AND (SELECT week_id FROM curated.dim_source_report WHERE report_id = EXCLUDED.report_id)
               >= (SELECT week_id FROM curated.dim_source_report WHERE report_id = f.report_id)
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {"run_id": run_id, "report_id": report_id},
    ).all()
    inserted = sum(1 for r in result if r.inserted)
    after = conn.execute(text("SELECT count(*) FROM curated.revision_history")).scalar_one()
    conn.execute(
        text("DELETE FROM staging.weekly_cases WHERE run_id = :r AND report_id = :p"),
        {"r": run_id, "p": report_id},
    )
    return {"inserted": inserted, "updated": len(result) - inserted, "revisions": after - before}


# =============================================================================
#  Quarantine and data-quality flags
# =============================================================================

def insert_quarantine(
    conn: Connection,
    *,
    run_id: uuid.UUID,
    document_id: int,
    report_id: int | None,
    rule_id: int | None,
    scope: str,
    reason: str,
    payload: Mapping[str, Any] | None = None,
    source_page: int | None = None,
) -> None:
    conn.execute(
        text(
            """INSERT INTO ops.quarantine
                   (run_id, document_id, report_id, rule_id, scope, failure_reason, payload, source_page)
               VALUES (:run, :doc, :rep, :rule, :scope, :reason, CAST(:payload AS jsonb), :page)"""
        ),
        {"run": run_id, "doc": document_id, "rep": report_id, "rule": rule_id, "scope": scope,
         "reason": reason, "payload": json.dumps(_jsonable(payload)) if payload is not None else None,
         "page": source_page},
    )


def insert_flags(conn: Connection, run_id: uuid.UUID, report_id: int | None, flags: Iterable[Mapping[str, Any]]) -> int:
    """Record warning-level results, resolving natural keys to dimension ids."""
    n = 0
    for flag in flags:
        wid = None
        if flag.get("epi_year") and flag.get("epi_week"):
            wid = int(flag["epi_year"]) * 100 + int(flag["epi_week"])
        conn.execute(
            text(
                """INSERT INTO ops.data_quality_flag
                       (run_id, rule_id, flag_type, report_id, disease_id, location_id, week_id, detail)
                   SELECT :run, :rule, :type, :rep,
                          (SELECT disease_id FROM curated.dim_disease WHERE disease_code = :d),
                          (SELECT location_id FROM curated.dim_location WHERE pcode = :p),
                          (SELECT week_id FROM curated.dim_epi_week WHERE week_id = :w),
                          CAST(:detail AS jsonb)"""
            ),
            {"run": run_id, "rule": flag.get("rule_id"), "type": flag["flag_type"], "rep": report_id,
             "d": flag.get("disease_code"), "p": flag.get("pcode"), "w": wid,
             "detail": json.dumps(_jsonable(flag.get("detail", {})))},
        )
        n += 1
    return n


def link_flags_to_facts(conn: Connection, run_id: uuid.UUID) -> None:
    """Fill ``fact_id`` on this run's flags once the facts exist."""
    conn.execute(
        text(
            """UPDATE ops.data_quality_flag q SET fact_id = f.fact_id
                 FROM curated.fact_weekly_cases f
                WHERE q.run_id = :r AND q.fact_id IS NULL
                  AND f.disease_id = q.disease_id AND f.location_id = q.location_id
                  AND f.week_id = q.week_id"""
        ),
        {"r": run_id},
    )


def record_completeness_gaps(conn: Connection, run_id: uuid.UUID) -> int:
    """Rule 12: flag every missing week inside each series' observed range, once."""
    conn.execute(
        text(
            """DELETE FROM ops.data_quality_flag q
                USING curated.fact_weekly_cases f
                WHERE q.flag_type = 'R12_COMPLETENESS'
                  AND f.disease_id = q.disease_id AND f.location_id = q.location_id
                  AND f.week_id = q.week_id"""
        )
    )
    result = conn.execute(
        text(
            """WITH bounds AS (
                   SELECT disease_id, location_id, min(week_id) AS first_w, max(week_id) AS last_w
                     FROM curated.fact_weekly_cases GROUP BY disease_id, location_id
               )
               INSERT INTO ops.data_quality_flag
                   (run_id, rule_id, flag_type, disease_id, location_id, week_id, detail)
               SELECT :run, 12, 'R12_COMPLETENESS', b.disease_id, b.location_id, w.week_id,
                      jsonb_build_object('message', 'week missing from series')
                 FROM bounds b
                 JOIN curated.dim_epi_week w ON w.week_id BETWEEN b.first_w AND b.last_w
                WHERE NOT EXISTS (SELECT 1 FROM curated.fact_weekly_cases f
                                   WHERE f.disease_id = b.disease_id AND f.location_id = b.location_id
                                     AND f.week_id = w.week_id)
               ON CONFLICT (flag_type, disease_id, location_id, week_id)
                   WHERE flag_type = 'R12_COMPLETENESS' DO NOTHING"""
        ),
        {"run": run_id},
    )
    return result.rowcount or 0


def refresh_provisional(conn: Connection, weeks: int) -> int:
    return conn.execute(text("SELECT curated.refresh_provisional_flags(:w)"), {"w": weeks}).scalar_one()


# =============================================================================
#  Reads for analytics and the dashboard
# =============================================================================

def weekly_cases_frame(
    conn: Connection, *, disease_code: str | None = None, pcode: str | None = None
) -> pd.DataFrame:
    """Rows of ``curated.v_weekly_cases`` (optionally filtered)."""
    clauses, params = [], {}
    if disease_code:
        clauses.append("disease_code = :d")
        params["d"] = disease_code
    if pcode:
        clauses.append("pcode = :p")
        params["p"] = pcode
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return pd.read_sql(text(f"SELECT * FROM curated.v_weekly_cases {where} ORDER BY week_id"), conn, params=params)


def series_keys(conn: Connection, min_points: int = 1) -> list[dict[str, Any]]:
    """Every (disease, location) series with at least ``min_points`` weeks."""
    rows = conn.execute(
        text(
            """SELECT d.disease_code, d.disease_id, l.pcode, l.location_id, l.admin_level, count(*) AS n
                 FROM curated.fact_weekly_cases f
                 JOIN curated.dim_disease d USING (disease_id)
                 JOIN curated.dim_location l USING (location_id)
                GROUP BY 1, 2, 3, 4, 5 HAVING count(*) >= :m
                ORDER BY 1, 3"""
        ),
        {"m": min_points},
    ).mappings()
    return [dict(r) for r in rows]


def week_calendar(conn: Connection) -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM curated.dim_epi_week ORDER BY week_id"), conn)


# =============================================================================
#  helpers
# =============================================================================

def _py(value: Any) -> Any:
    """Convert pandas/numpy scalars (and NaN) to plain Python for the driver."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return _py(obj)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
