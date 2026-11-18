"""End-to-end pipeline run (Section 3.3.2).

One run moves data through the layers in order::

    ingest (crawler + inbox) -> process (extract, validate, stage, merge)
    -> post-load checks -> analytics -> Parquet export

Each step is a separate function so the Airflow DAG can call them as tasks;
``run_pipeline`` chains them for cron and the command line. Every step adds
its counters to ``ops.pipeline_run_log`` and one failing report never stops
the others (Section 3.5.2).
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import Engine, text

from src.common.config import REPO_ROOT, Settings
from src.common.diseases import DISEASES
from src.common.epiweek import is_valid_week, week_start
from src.common.logging_setup import clear_report, set_context
from src.extraction.classify import classify
from src.extraction.extract import ExtractionError, ReportExtraction, extract_document
from src.extraction.pdf_reader import read_pdf
from src.ingestion.http import DownloadError, PoliteSession
from src.ingestion.inbox import move_to_raw, scan_inbox
from src.ingestion.ncdc_crawler import NcdcCrawler
from src.storage import repository as repo
from src.storage.db import apply_schema, get_engine
from src.validation.engine import ValidationContext, validate_report

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    engine: Engine
    settings: Settings
    run_id: uuid.UUID
    stats: repo.RunStats
    failures: int = 0

    def flush(self) -> None:
        """Add this step's counters to the run log and reset them."""
        values = self.stats.as_dict()
        if any(values.values()):
            assignments = ", ".join(f"{k} = {k} + :{k}" for k in values)
            with self.engine.begin() as conn:
                conn.execute(text(f"UPDATE ops.pipeline_run_log SET {assignments} WHERE run_id = :run_id"),
                             {**values, "run_id": self.run_id})
        self.stats = repo.RunStats()


def code_version() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


# =============================================================================
#  setup
# =============================================================================

def init_database(settings: Settings, engine: Engine | None = None) -> dict:
    """Create/upgrade the schema and load the reference data (idempotent)."""
    engine = engine or get_engine(settings.database_url)
    apply_schema(engine)
    with engine.begin() as conn:
        counts = repo.seed_reference(conn, settings.reference_dir)
    log.info("database ready", extra={"data": counts})
    return counts


def begin_run(settings: Settings, run_type: str, trigger_source: str = "cli", dag_id: str | None = None) -> RunContext:
    engine = get_engine(settings.database_url)
    run_id = repo.start_run(engine, run_type, trigger_source=trigger_source, dag_id=dag_id, code_version=code_version())
    set_context(run_id=str(run_id))
    log.info("run started (%s)", run_type)
    return RunContext(engine=engine, settings=settings, run_id=run_id, stats=repo.RunStats())


def end_run(ctx: RunContext, error: str | None = None) -> str:
    ctx.flush()
    status = "failed" if error else ("partial" if ctx.failures else "success")
    repo.finish_run(ctx.engine, ctx.run_id, status, error_message=error)
    log.info("run finished: %s", status)
    return status


# =============================================================================
#  Layer 2: ingestion
# =============================================================================

def ingest_ncdc(ctx: RunContext, diseases: Iterable[str] | None = None, max_files: int | None = None,
                recheck_known_urls: bool = False) -> None:
    """Crawl NCDC listings and store new PDFs in the raw layer."""
    set_context(step="ingest_ncdc")
    s = ctx.settings
    session = PoliteSession(user_agent=s.user_agent, delay_s=s.request_delay_s, timeout_s=s.request_timeout_s,
                            max_retries=s.max_retries, backoff_base_s=s.backoff_base_s)
    crawler = NcdcCrawler(session, s.ncdc_base_url, s.raw_dir)
    with ctx.engine.connect() as conn:
        known_urls = {u for (u,) in conn.execute(text("SELECT source_url FROM raw.documents WHERE source_url IS NOT NULL"))}
    per_disease: dict[str, int] = {}
    for entry in crawler.discover(diseases):
        ctx.stats.files_discovered += 1
        if entry.url in known_urls and not recheck_known_urls:
            ctx.stats.files_skipped_duplicate += 1
            continue
        key = entry.disease_code or "WER"
        if max_files is not None and per_disease.get(key, 0) >= max_files:
            continue
        per_disease[key] = per_disease.get(key, 0) + 1
        try:
            with ctx.engine.begin() as conn:
                downloaded = crawler.download(entry, lambda h: repo.document_exists(conn, h))
                if downloaded is None:
                    ctx.stats.files_skipped_duplicate += 1
                    continue
                repo.register_document(
                    conn, sha256=downloaded.sha256, source_name="ncdc", source_url=entry.url,
                    file_path=str(downloaded.path.relative_to(s.data_dir)), file_name=downloaded.path.name,
                    content_type="application/pdf", file_size_bytes=downloaded.size, run_id=ctx.run_id,
                    listing_title=entry.title, listing_date=entry.listing_date, disease_hint=entry.disease_code,
                )
                ctx.stats.files_new += 1
        except DownloadError as exc:
            ctx.stats.files_manual_queue += 1
            _manual_queue_note(s, {"url": entry.url, "title": entry.title, "error": str(exc)})
            log.warning("download failed, added to manual queue: %s", entry.url)
    ctx.flush()


def ingest_inbox(ctx: RunContext) -> None:
    """Move hand-placed PDFs/CSVs from data/inbox into the raw layer."""
    set_context(step="ingest_inbox")
    s = ctx.settings
    for item in scan_inbox(s.inbox_dir):
        ctx.stats.files_discovered += 1
        with ctx.engine.begin() as conn:
            if repo.document_exists(conn, item.sha256):
                ctx.stats.files_skipped_duplicate += 1
                dup_dir = s.quarantine_dir / "duplicates"
                dup_dir.mkdir(parents=True, exist_ok=True)
                item.source_path.rename(dup_dir / item.source_path.name)
                continue
            target = move_to_raw(item, s.raw_dir)
            repo.register_document(
                conn, sha256=item.sha256, source_name="manual_inbox", source_url=None,
                file_path=str(target.relative_to(s.data_dir)), file_name=item.source_path.name,
                content_type=item.content_type, file_size_bytes=target.stat().st_size, run_id=ctx.run_id,
                is_synthetic=item.is_synthetic,
            )
            ctx.stats.files_new += 1
    ctx.flush()


def _manual_queue_note(settings: Settings, record: dict) -> None:
    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    with (settings.quarantine_dir / "manual_download_queue.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**record, "time": datetime.now(timezone.utc).isoformat()}) + "\n")


# =============================================================================
#  Layers 4-5: extraction, validation, storage
# =============================================================================

def _processing_order(doc: dict, data_dir: Path) -> tuple:
    """CSV files first (by name), then PDFs in epi-week order, so 'newest wins' holds in a backfill."""
    path = data_dir / doc["file_path"]
    if path.suffix.lower() == ".csv":
        return (0, 0, 0, doc["file_name"])
    try:
        cls = classify(read_pdf(path, max_pages=1).head_text(1), listing_title=doc.get("listing_title"),
                       disease_hint=doc.get("disease_hint"))
        return (1, cls.epi_year or 0, cls.epi_week or 0, doc["file_name"])
    except Exception:
        return (2, 0, 0, doc["file_name"])


def process_documents(ctx: RunContext, *, include_failed: bool = False) -> None:
    """Extract, validate and load every pending document."""
    set_context(step="process")
    s = ctx.settings
    with ctx.engine.connect() as conn:
        docs = repo.pending_documents(conn, include_failed=include_failed)
        alias_map = repo.location_alias_map(conn)
        diseases = repo.disease_lookup(conn)
    denominators = {code: d["cfr_denominator"] for code, d in diseases.items()}
    docs.sort(key=lambda d: _processing_order(d, s.data_dir))
    log.info("%d documents to process", len(docs))

    for doc in docs:
        path = s.data_dir / doc["file_path"]
        set_context(report=doc["file_name"])
        try:
            reports = extract_document(doc, path, alias_map)
        except ExtractionError as exc:
            status = "manual_queue" if exc.manual else "ignored"
            with ctx.engine.begin() as conn:
                repo.set_document_status(conn, doc["document_id"], status, str(exc))
                if exc.manual:
                    repo.insert_quarantine(conn, run_id=ctx.run_id, document_id=doc["document_id"], report_id=None,
                                           rule_id=None, scope="document", reason=f"parse failure: {exc}")
            ctx.stats.files_manual_queue += int(exc.manual)
            log.warning("could not parse %s: %s", doc["file_name"], exc)
            continue
        except Exception as exc:  # unexpected parser crash: record and continue
            ctx.failures += 1
            with ctx.engine.begin() as conn:
                repo.set_document_status(conn, doc["document_id"], "parse_failed", f"{type(exc).__name__}: {exc}")
            log.exception("parser crashed on %s", doc["file_name"])
            continue

        in_window = [r for r in reports if s.study_start_year <= r.epi_year <= s.study_end_year]
        if not in_window:
            with ctx.engine.begin() as conn:
                repo.set_document_status(conn, doc["document_id"], "ignored",
                                         f"outside study period {s.study_start_year}-{s.study_end_year}")
            continue

        doc_ok = True
        for report in in_window:
            try:
                _load_report(ctx, doc, report, alias_map, denominators)
            except Exception as exc:
                doc_ok = False
                ctx.failures += 1
                log.exception("loading %s failed and was rolled back: %s", report.report_code, exc)
        with ctx.engine.begin() as conn:
            repo.set_document_status(conn, doc["document_id"], "parsed" if doc_ok else "parse_failed",
                                     None if doc_ok else "one or more reports failed to load; see logs")
        ctx.flush()
    clear_report()
    ctx.flush()


def _load_report(ctx: RunContext, doc: dict, report: ReportExtraction, alias_map, denominators) -> None:
    """Everything for one report in ONE transaction: all or nothing."""
    set_context(report=report.report_code)
    s = ctx.settings
    if not is_valid_week(report.epi_year, report.epi_week):
        # rule 8 at report level: the week does not exist, so no report row can reference it
        with ctx.engine.begin() as conn:
            for _, row in report.rows.iterrows():
                repo.insert_quarantine(conn, run_id=ctx.run_id, document_id=doc["document_id"], report_id=None,
                                       rule_id=8, scope="row",
                                       reason=f"{report.epi_year}-W{report.epi_week:02d} does not exist",
                                       payload=row.to_dict())
        ctx.stats.rows_extracted += len(report.rows)
        ctx.stats.rows_quarantined += len(report.rows)
        return
    with ctx.engine.begin() as conn:
        conn.execute(text("SELECT set_config('pipeline.run_id', :r, true)"), {"r": str(ctx.run_id)})
        _ensure_week(conn, report.epi_year, report.epi_week)
        report_id = repo.upsert_source_report(
            conn, report_code=report.report_code, document_id=doc["document_id"], report_type=report.report_type,
            publisher="NCDC" if doc["source_name"] == "ncdc" else ("SYNTHETIC" if report.is_synthetic else "manual"),
            disease_code=report.disease_code, week_id=report.epi_year * 100 + report.epi_week,
            published_date=report.published_date, printed_week_start=report.printed_start,
            printed_week_end=report.printed_end, template_id=report.template_id,
            extraction_engine=report.engine, title=report.title, is_synthetic=report.is_synthetic,
        )
        ctx.stats.rows_extracted += len(report.rows)

        def previous(disease_code: str, pcode: str, wid: int):
            return repo.previous_cumulative(conn, disease_code, pcode, wid)

        vctx = ValidationContext(
            alias_map=alias_map, denominators=denominators, previous=previous,
            cfr_tolerance_pp=s.cfr_tolerance_pp, timeliness_days=s.timeliness_days,
            printed_start=report.printed_start, printed_end=report.printed_end,
            published_date=report.published_date, report_year=report.epi_year, report_week=report.epi_week,
        )
        result = validate_report(report.rows, vctx)

        if result.table_error is not None:
            repo.insert_quarantine(conn, run_id=ctx.run_id, document_id=doc["document_id"], report_id=report_id,
                                   rule_id=result.table_error.rule_id, scope="table",
                                   reason=result.table_error.message,
                                   payload={"columns": list(report.rows.columns), "rows": len(report.rows)})
            ctx.stats.rows_quarantined += len(report.rows)
            return

        for q in result.quarantined:
            repo.insert_quarantine(conn, run_id=ctx.run_id, document_id=doc["document_id"], report_id=report_id,
                                   rule_id=q["rule_id"], scope="row", reason=q["reason"], payload=q["payload"],
                                   source_page=q["source_page"])
        ctx.stats.rows_quarantined += len(result.quarantined)

        repo.insert_staging(conn, ctx.run_id, report_id, result.passed)
        merged = repo.merge_staging(conn, ctx.run_id, report_id)
        repo.insert_flags(conn, ctx.run_id, report_id, result.flags)
        ctx.stats.rows_passed += len(result.passed)
        ctx.stats.rows_warned += result.n_warned
        ctx.stats.revisions_detected += merged["revisions"]


def _ensure_week(conn, year: int, week: int) -> None:
    if not is_valid_week(year, week):
        return  # rule 8 will quarantine the rows
    exists = conn.execute(text("SELECT 1 FROM curated.dim_epi_week WHERE week_id = :w"), {"w": year * 100 + week}).first()
    if not exists:
        ws = week_start(year, week)
        conn.execute(text("SELECT curated.populate_epi_weeks(:a, :b)"), {"a": ws, "b": ws})


# =============================================================================
#  Post-load, analytics and export
# =============================================================================

def post_load(ctx: RunContext) -> None:
    set_context(step="post_load")
    with ctx.engine.begin() as conn:
        repo.refresh_provisional(conn, ctx.settings.provisional_weeks)
        gaps = repo.record_completeness_gaps(conn, ctx.run_id)
        repo.link_flags_to_facts(conn, ctx.run_id)
    log.info("post-load checks done (%d new gaps flagged)", gaps)


def analytics(ctx: RunContext, forecast: bool = True) -> None:
    from src.analytics.run import run_analytics

    set_context(step="analytics")
    run_analytics(ctx.engine, ctx.run_id, ctx.settings, forecast=forecast)


def export_parquet(ctx: RunContext) -> Path | None:
    """Write curated tables to Parquet for DuckDB/Jupyter (Section 3.3.3)."""
    set_context(step="export")
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        log.warning("pyarrow not installed: Parquet export skipped")
        return None
    import pandas as pd

    out = ctx.settings.parquet_dir
    out.mkdir(parents=True, exist_ok=True)
    with ctx.engine.connect() as conn:
        for name, sql in {
            "weekly_cases": "SELECT * FROM curated.v_weekly_cases",
            "revision_history": "SELECT * FROM curated.revision_history",
            "alerts": "SELECT * FROM analytics.alert",
            "forecasts": "SELECT * FROM analytics.forecast WHERE is_backtest = false",
        }.items():
            pd.read_sql(text(sql), conn).to_parquet(out / f"{name}.parquet", index=False)
    log.info("exported Parquet files to %s", out)
    return out


# =============================================================================
#  The whole run
# =============================================================================

def run_pipeline(
    settings: Settings,
    *,
    mode: str = "weekly",
    diseases: Iterable[str] | None = None,
    max_files: int | None = None,
    crawl: bool = True,
    run_analytics_step: bool = True,
    forecast: bool = True,
    trigger_source: str = "cli",
) -> tuple[uuid.UUID, str]:
    """Run every layer once. ``mode``: weekly | backfill | daily_check | manual."""
    run_type = {"weekly": "scheduled", "backfill": "backfill", "daily_check": "daily_check"}.get(mode, "manual")
    ctx = begin_run(settings, run_type, trigger_source)
    try:
        if crawl:
            ingest_ncdc(ctx, diseases or list(DISEASES), max_files=max_files)
        ingest_inbox(ctx)
        process_documents(ctx, include_failed=(mode == "backfill"))
        post_load(ctx)
        if run_analytics_step and mode != "daily_check":
            analytics(ctx, forecast=forecast)
        export_parquet(ctx)
    except Exception as exc:
        log.exception("run failed")
        return ctx.run_id, end_run(ctx, error=f"{type(exc).__name__}: {exc}")
    return ctx.run_id, end_run(ctx)
