"""End-to-end: the SYNTHETIC demo through ingestion, parsing, validation, storage and analytics."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

import shutil  # noqa: E402
import uuid  # noqa: E402

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.demo.synthetic import write_demo_files  # noqa: E402
from src.orchestration.pipeline import run_pipeline  # noqa: E402
from src.storage import repository as repo  # noqa: E402


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def test_run_succeeds_and_is_logged(engine, demo_run):
    assert demo_run["status"] == "success"
    with engine.connect() as conn:
        run = conn.execute(text("SELECT * FROM ops.pipeline_run_log WHERE run_id = :r"), {"r": demo_run["run_id"]}).mappings().one()
    assert run["files_new"] == len(demo_run["files"])
    assert run["rows_passed"] > 1000 and run["rows_quarantined"] == 5
    assert run["revisions_detected"] > 0 and run["ended_at"] is not None


def test_every_document_parsed(engine, demo_run):
    assert scalar(engine, "SELECT count(*) FROM raw.documents WHERE processing_status <> 'parsed'") == 0
    assert scalar(engine, "SELECT bool_and(is_synthetic) FROM raw.documents")


def test_pdf_reports_loaded_with_their_template(engine, demo_run):
    n = scalar(engine, """SELECT count(*) FROM curated.dim_source_report
                          WHERE template_id = 'lassa_sitrep_v1' AND extraction_engine = 'pdfplumber'""")
    assert n == 2
    facts = scalar(engine, """SELECT count(*) FROM curated.fact_weekly_cases f
                              JOIN curated.dim_source_report r USING (report_id)
                              WHERE r.template_id = 'lassa_sitrep_v1'""")
    assert facts > 2  # national + states


def test_quarantine_holds_one_row_per_broken_rule(engine, demo_run):
    with engine.connect() as conn:
        rules = sorted(conn.scalars(text("SELECT rule_id FROM ops.quarantine WHERE scope = 'row'")))
    assert rules == [2, 3, 4, 8, 9]


def test_fact_grain_and_state_sums(engine, demo_run):
    assert scalar(engine, """SELECT count(*) FROM (SELECT disease_id, location_id, week_id, count(*)
                             FROM curated.fact_weekly_cases GROUP BY 1, 2, 3 HAVING count(*) > 1) d""") == 0
    mismatches = scalar(engine, """
        SELECT count(*) FROM (
          SELECT f.disease_id, f.week_id, max(f.confirmed) FILTER (WHERE l.admin_level = 0) AS nat,
                 sum(f.confirmed) FILTER (WHERE l.admin_level = 1) AS states
            FROM curated.fact_weekly_cases f JOIN curated.dim_location l USING (location_id)
            JOIN curated.dim_disease d USING (disease_id) WHERE d.disease_code = 'LASSA'
           GROUP BY 1, 2) x WHERE states IS NOT NULL AND nat <> states""")
    assert mismatches == 0


def test_revisions_recorded_with_old_and_new_reports(engine, demo_run):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT field_name, old_value, new_value, old_report_id, new_report_id FROM curated.revision_history")).all()
    assert rows
    assert {r.field_name for r in rows} <= {"suspected", "confirmed", "deaths", "cfr_pct"}
    assert all(r.old_report_id != r.new_report_id and r.new_value > r.old_value for r in rows if r.field_name == "confirmed")


def test_analytics_outputs(engine, demo_run):
    assert scalar(engine, "SELECT count(DISTINCT (disease_id, location_id)) FROM analytics.stl_component") >= 7
    assert scalar(engine, "SELECT count(*) FROM analytics.alert") > 100
    live = scalar(engine, "SELECT count(*) FROM analytics.forecast WHERE NOT is_backtest")
    models = scalar(engine, "SELECT count(DISTINCT model_name) FROM analytics.forecast WHERE NOT is_backtest")
    assert models >= 5 and live == models * 4
    assert scalar(engine, "SELECT count(*) FROM analytics.model_evaluation WHERE mase IS NOT NULL") > 0
    assert scalar(engine, "SELECT count(*) FROM analytics.alert WHERE method = 'SARIMA_RESID_Z'") > 0


def test_second_run_is_idempotent(engine, demo_run, settings):
    before = scalar(engine, "SELECT count(*) FROM curated.fact_weekly_cases")
    revisions = scalar(engine, "SELECT count(*) FROM curated.revision_history")
    # drop the same files in again: every one must be recognised by its hash
    tmp = settings.data_dir / "again"
    write_demo_files(tmp, end=(2023, 30), pdf_weeks=2)
    for f in tmp.iterdir():
        if f.suffix == ".csv":  # CSV content is deterministic; PDFs embed a timestamp
            shutil.copy(f, settings.inbox_dir / f.name)
    run_id, status = run_pipeline(settings, mode="manual", crawl=False, run_analytics_step=False)
    assert status == "success"
    with engine.connect() as conn:
        run = conn.execute(text("SELECT * FROM ops.pipeline_run_log WHERE run_id = :r"), {"r": run_id}).mappings().one()
    assert run["files_new"] == 0 and run["files_skipped_duplicate"] >= 9
    assert scalar(engine, "SELECT count(*) FROM curated.fact_weekly_cases") == before
    assert scalar(engine, "SELECT count(*) FROM curated.revision_history") == revisions


def test_older_report_never_overwrites_newer(engine, demo_run):
    with engine.begin() as conn:
        run_id = conn.execute(text("SELECT run_id FROM ops.pipeline_run_log ORDER BY started_at LIMIT 1")).scalar_one()
        doc = conn.execute(text("SELECT document_id FROM raw.documents LIMIT 1")).scalar_one()
        new_rep = repo.upsert_source_report(conn, report_code=f"TEST-NEW-{uuid.uuid4()}", document_id=doc,
                                            report_type="manual_entry", disease_code="MPOX", week_id=202320)
        old_rep = repo.upsert_source_report(conn, report_code=f"TEST-OLD-{uuid.uuid4()}", document_id=doc,
                                            report_type="manual_entry", disease_code="MPOX", week_id=202310)
        row = lambda s: pd.DataFrame([dict(disease_code="MPOX", pcode="NG", epi_year=2023, epi_week=10, suspected=s,  # noqa: E731
                                           confirmed=None, deaths=None, cum_suspected=None, cum_confirmed=None,
                                           cum_deaths=None, cfr_pct=None, cfr_reported=None,
                                           is_derived_from_cumulative=False, quality_flags=[], source_page=None)])
        repo.insert_staging(conn, run_id, new_rep, row(500))
        repo.merge_staging(conn, run_id, new_rep)
        repo.insert_staging(conn, run_id, old_rep, row(1))
        result = repo.merge_staging(conn, run_id, old_rep)
        value = conn.execute(text("""SELECT suspected FROM curated.fact_weekly_cases f
                                     JOIN curated.dim_disease d USING (disease_id)
                                     JOIN curated.dim_location l USING (location_id)
                                     WHERE d.disease_code = 'MPOX' AND l.pcode = 'NG' AND f.week_id = 202310""")).scalar_one()
    assert value == 500 and result["updated"] == 0
