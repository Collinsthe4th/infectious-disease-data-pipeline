"""Command-line entry point: ``python -m src.orchestration.cli <command>``.

Commands
--------
init-db            create the schema and load the state reference data
run                one full pipeline run (crawl -> process -> analytics -> export)
backfill           historical load 2021-2026 (same code as weekly runs)
process            process files already in the raw layer / inbox (no crawling)
analytics          re-run STL, alerts and forecasts on the current data
demo               load the SYNTHETIC demo dataset through the real pipeline
parse-check        parse local or freshly downloaded NCDC PDFs and print what was read
refresh-population fill state populations from HDX
status             show the latest runs and table sizes
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.common.config import get_settings
from src.common.diseases import DISEASES
from src.common.logging_setup import configure_logging

log = logging.getLogger("pipeline")


def _diseases(value: str | None) -> list[str] | None:
    if not value:
        return None
    codes = [v.strip().upper() for v in value.split(",") if v.strip()]
    unknown = [c for c in codes if c not in DISEASES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown disease codes {unknown}; choose from {list(DISEASES)}")
    return codes


def cmd_init_db(args, settings) -> int:
    from src.orchestration.pipeline import init_database

    counts = init_database(settings)
    print(f"Database ready: {counts['states']} states, {counts['aliases']} new aliases.")
    return 0


def cmd_run(args, settings) -> int:
    from src.orchestration.pipeline import run_pipeline

    run_id, status = run_pipeline(
        settings, mode=args.mode, diseases=args.diseases, max_files=args.max_files, crawl=not args.no_crawl,
        run_analytics_step=not args.no_analytics, forecast=not args.no_forecast, trigger_source=args.trigger,
    )
    print(f"Run {run_id} finished: {status}")
    return 0 if status in ("success", "partial") else 1


def cmd_backfill(args, settings) -> int:
    args.mode = "backfill"
    return cmd_run(args, settings)


def cmd_process(args, settings) -> int:
    args.mode, args.no_crawl, args.diseases, args.max_files = "manual", True, None, None
    return cmd_run(args, settings)


def cmd_analytics(args, settings) -> int:
    from src.orchestration.pipeline import analytics, begin_run, end_run, export_parquet

    ctx = begin_run(settings, "manual", args.trigger)
    try:
        analytics(ctx, forecast=not args.no_forecast)
        export_parquet(ctx)
    except Exception as exc:
        log.exception("analytics failed")
        print(f"Analytics failed: {exc}")
        end_run(ctx, error=str(exc))
        return 1
    print(f"Analytics run {ctx.run_id}: {end_run(ctx)}")
    return 0


def cmd_demo(args, settings) -> int:
    from src.demo.synthetic import write_demo_files
    from src.orchestration.pipeline import init_database, run_pipeline

    init_database(settings)
    from sqlalchemy import text

    from src.storage.db import get_engine

    with get_engine(settings.database_url).connect() as conn:
        real = conn.execute(text("SELECT count(*) FROM curated.dim_source_report WHERE NOT is_synthetic")).scalar_one()
    if real and not args.allow_with_real_data:
        print(f"Refusing to load demo data: this database already holds {real} real NCDC reports, and synthetic "
              "figures would mix with them. Use an empty database for the demo.")
        return 1
    files = write_demo_files(settings.inbox_dir, seed=settings.random_seed)
    print(f"Wrote {len(files)} SYNTHETIC files to {settings.inbox_dir}; loading them through the pipeline...")
    run_id, status = run_pipeline(settings, mode="manual", crawl=False, forecast=not args.no_forecast,
                                  trigger_source=args.trigger)
    print(f"Demo run {run_id} finished: {status}. Start the dashboard to explore it.")
    return 0 if status in ("success", "partial") else 1


def cmd_parse_check(args, settings) -> int:
    """Parse PDFs without touching the database; print a JSON report.

    With --download N, fetch the newest N reports per disease from NCDC first
    (used by the 'NCDC parse check' GitHub workflow to test against real files).
    """
    import csv

    from src.common.text import normalise_alias
    from src.extraction.extract import ExtractionError, extract_pdf

    alias = {"ng": "NG"}
    states = list(csv.DictReader((settings.reference_dir / "nga_states.csv").open(encoding="utf-8")))
    by_name = {r["state_name"]: r["pcode"] for r in states}
    for r in states:
        for a in (r["state_name"], r["pcode"], f"{r['state_name']} state"):
            alias[normalise_alias(a)] = r["pcode"]
    for r in csv.DictReader((settings.reference_dir / "nga_location_aliases.csv").open(encoding="utf-8")):
        alias[normalise_alias(r["alias"])] = by_name[r["state_name"]]

    targets: list[tuple[Path, dict]] = [(Path(p), {}) for p in args.paths]
    if args.download:
        from src.ingestion.http import PoliteSession
        from src.ingestion.ncdc_crawler import NcdcCrawler

        out_dir = Path(args.out_dir)
        session = PoliteSession(user_agent=settings.user_agent, delay_s=settings.request_delay_s,
                                timeout_s=settings.request_timeout_s, max_retries=1, backoff_base_s=5)
        crawler = NcdcCrawler(session, settings.ncdc_base_url, out_dir)
        for code in args.diseases or list(DISEASES):
            listing = list(crawler.discover([code]))
            if args.spread and len(listing) > args.download:
                step = (len(listing) - 1) / max(args.download - 1, 1)
                entries = [listing[round(i * step)] for i in range(args.download)]
            else:
                entries = listing[: args.download]
            for entry in entries:
                try:
                    f = crawler.download(entry, lambda h: False)
                    targets.append((f.path, {"listing_title": entry.title, "disease_hint": code,
                                             "listing_date": entry.listing_date, "url": entry.url}))
                except Exception as exc:
                    print(json.dumps({"url": entry.url, "download_error": str(exc)}))

    report = []
    for path, meta in targets:
        from src.ingestion.hashing import sha256_file

        doc = {"sha256": sha256_file(path), **meta}
        if args.dump:
            _dump_pdf(path, meta)
        try:
            ex = extract_pdf(doc, path, alias)
            rows = ex.rows.drop(columns=["epi_year", "epi_week"], errors="ignore")
            item = {"file": str(path), "url": meta.get("url"), "ok": True, "report_code": ex.report_code,
                    "template": ex.template_id, "engine": ex.engine, "week": f"{ex.epi_year}-W{ex.epi_week:02d}",
                    "printed": [str(ex.printed_start), str(ex.printed_end)], "rows": len(rows),
                    "national": rows[rows["location_raw"] == "National"].to_dict(orient="records")[:1],
                    "states_read": int((rows["location_raw"] != "National").sum()),
                    "parse_errors": ex.parse_errors[:5]}
        except ExtractionError as exc:
            item = {"file": str(path), "url": meta.get("url"), "ok": False, "error": str(exc)}
        report.append(item)
        print(json.dumps(item, default=str))
    ok = sum(1 for r in report if r["ok"])
    print(f"\nParsed {ok}/{len(report)} PDFs")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0


def _dump_pdf(path: Path, meta: dict) -> None:
    """Print the text layer and table layout of a PDF (for writing templates)."""
    from src.extraction.pdf_reader import camelot_tables, read_pdf

    print(f"\n======== DUMP {path.name} {meta.get('url', '')}\n{meta.get('listing_title', '')}")
    try:
        content = read_pdf(path, max_pages=6)
    except Exception as exc:
        print(f"cannot read: {exc}")
        return
    for i, text in enumerate(content.page_texts[:4], start=1):
        print(f"--- page {i} text ---\n{text[:1800]}")
    tables = content.tables or camelot_tables(path, pages="1-4")
    engine = "pdfplumber" if content.tables else "camelot"
    for page, rows in tables[:6]:
        print(f"--- {engine} table on page {page}: {len(rows)} rows ---")
        for row in rows[:8]:
            print(" | ".join(c[:28] for c in row))
    print("======== END DUMP")


def cmd_start_run(args, settings) -> int:
    """Open a run and print its id as the LAST line (Airflow passes it to the next tasks)."""
    from src.orchestration.pipeline import begin_run

    ctx = begin_run(settings, args.type, trigger_source=args.trigger, dag_id=args.dag_id)
    print(ctx.run_id)
    return 0


STEPS = ("collect", "inbox", "process", "quality", "analyse", "export")


def cmd_step(args, settings) -> int:
    """Run ONE pipeline step inside an existing run (one Airflow box)."""
    import uuid

    from src.orchestration import pipeline as p

    ctx = p.context_for(settings, uuid.UUID(args.run_id))
    actions = {
        "collect": lambda: p.ingest_ncdc(ctx, args.diseases, max_files=args.max_files),
        "inbox": lambda: p.ingest_inbox(ctx),
        "process": lambda: p.process_documents(ctx, include_failed=args.retry_failed),
        "quality": lambda: p.post_load(ctx),
        "analyse": lambda: p.analytics(ctx, forecast=not args.no_forecast),
        "export": lambda: p.export_parquet(ctx),
    }
    try:
        actions[args.name]()
        ctx.flush()
    except Exception as exc:
        log.exception("step %s failed", args.name)
        p.note_run(ctx, f"step failed: {args.name}: {type(exc).__name__}: {exc}")
        return 1
    print(f"step {args.name}: done")
    return 0


def cmd_finish_run(args, settings) -> int:
    import uuid

    from src.orchestration.pipeline import context_for, finish_from_log

    status = finish_from_log(context_for(settings, uuid.UUID(args.run_id)))
    print(f"run {args.run_id}: {status}")
    return 0 if status != "failed" else 1


def cmd_purge_synthetic(args, settings) -> int:
    """Remove the synthetic demo data, keep the real NCDC data."""
    from pathlib import Path

    from sqlalchemy import text

    from src.storage.db import get_engine

    sql = (Path(__file__).resolve().parents[1] / "storage" / "purge_synthetic.sql").read_text(encoding="utf-8")
    engine = get_engine(settings.database_url)
    with engine.connect() as conn:
        reports, files, reread = conn.execute(text(
            """SELECT (SELECT count(*) FROM curated.dim_source_report WHERE is_synthetic),
                      (SELECT count(*) FROM raw.documents WHERE is_synthetic),
                      (SELECT count(DISTINCT r.document_id) FROM curated.revision_history h
                         JOIN curated.dim_source_report r ON r.report_id = h.old_report_id
                         JOIN curated.dim_source_report n ON n.report_id = h.new_report_id
                        WHERE n.is_synthetic AND NOT r.is_synthetic)""")).one()
    raw = engine.raw_connection()
    try:
        raw.autocommit = True  # the script has its own BEGIN ... COMMIT
        with raw.cursor() as cur:
            cur.execute(sql)
    finally:
        raw.close()
    with engine.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM curated.dim_source_report WHERE is_synthetic")).scalar_one()
    print(f"Removed {reports} synthetic reports and {files} synthetic files; "
          f"{reread} real files will be read again. Synthetic reports left: {left}.")
    return 0 if left == 0 else 1


def cmd_reread(args, settings) -> int:
    """Queue already-read reports of one disease to be read again (e.g. after a parser improvement)."""
    from sqlalchemy import text

    from src.storage.db import get_engine

    with get_engine(settings.database_url).begin() as conn:
        n = conn.execute(text(
            """UPDATE raw.documents SET processing_status = 'new',
                      status_reason = 'queued to be read again: ' || :why
                WHERE NOT is_synthetic AND disease_hint = ANY(:d)
                  AND processing_status IN ('parsed', 'parse_failed', 'manual_queue')"""),
            {"d": [c.strip() for c in args.disease.split(",")], "why": args.reason}).rowcount
    print(f"{n} {args.disease} files queued to be read again")
    return 0


def cmd_retract(args, settings) -> int:
    """Remove figures loaded from specific reports (found to be misread) and read those reports again.

    ``--reports`` takes report codes or code prefixes, e.g. NCDC-SITREP-LASSA-2022-W29 or
    NCDC-SITREP-MPOX-2021. The facts whose current source is one of those reports are deleted
    (with their revision rows and flags), and the documents go back to 'new' so the next
    ``process`` reads them with the current parser.
    """
    from sqlalchemy import text

    from src.storage.db import get_engine

    prefixes = [p.strip() + "%" for p in args.reports.split(",") if p.strip()]
    with get_engine(settings.database_url).begin() as conn:
        reports = conn.execute(text(
            """SELECT report_id, report_code, document_id FROM curated.dim_source_report
                WHERE NOT is_synthetic AND report_code LIKE ANY(:p)"""), {"p": prefixes}).mappings().all()
        ids = [r["report_id"] for r in reports]
        docs = sorted({r["document_id"] for r in reports if r["document_id"]})
        facts = conn.execute(text("DELETE FROM curated.fact_weekly_cases WHERE report_id = ANY(:i)"),
                             {"i": ids}).rowcount if ids else 0
        if docs and not args.no_reread:
            conn.execute(text(
                """UPDATE raw.documents SET processing_status = 'new',
                          status_reason = 'figures retracted: ' || :why WHERE document_id = ANY(:d)"""),
                {"d": docs, "why": args.reason})
    for r in reports:
        print(f"retracted {r['report_code']}")
    print(f"{len(reports)} reports, {facts} figures removed, {len(docs)} files queued to be read again")
    return 0


def cmd_reset_db(args, settings) -> int:
    """Delete EVERYTHING (all five schemas) and recreate empty tables. Used to switch from demo to real data."""
    if not args.yes:
        print("Refusing: add --yes to confirm that all data in the database will be deleted.")
        return 1
    from sqlalchemy import text

    from src.orchestration.pipeline import init_database
    from src.storage.db import get_engine

    engine = get_engine(settings.database_url)
    with engine.begin() as conn:
        for schema in ("analytics", "staging", "curated", "raw", "ops"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    counts = init_database(settings, engine)
    print(f"Database emptied and recreated ({counts['states']} states loaded).")
    return 0


def cmd_refresh_population(args, settings) -> int:
    from src.ingestion.hdx import refresh_population
    from src.ingestion.http import PoliteSession

    session = PoliteSession(user_agent=settings.user_agent, delay_s=1, timeout_s=settings.request_timeout_s)
    n = refresh_population(session, settings.reference_dir / "nga_states.csv")
    print(f"Population updated for {n} states. Run 'init-db' to load it into the database.")
    return 0


def cmd_status(args, settings) -> int:
    from sqlalchemy import text

    from src.storage.db import get_engine

    engine = get_engine(settings.database_url)
    with engine.connect() as conn:
        runs = conn.execute(text(
            """SELECT started_at, run_type, status, files_new, rows_passed, rows_quarantined, revisions_detected
                 FROM ops.pipeline_run_log ORDER BY started_at DESC LIMIT 5""")).all()
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in (
            "raw.documents", "curated.fact_weekly_cases", "curated.revision_history", "ops.quarantine",
            "ops.data_quality_flag", "analytics.alert", "analytics.forecast")}
    print("Latest runs:")
    for r in runs:
        print(f"  {r.started_at:%Y-%m-%d %H:%M}  {r.run_type:<11} {r.status:<8} new files={r.files_new:<4} "
              f"rows ok={r.rows_passed:<6} quarantined={r.rows_quarantined:<4} revisions={r.revisions_detected}")
    print("Table sizes:")
    for t, n in counts.items():
        print(f"  {t:<30} {n:>8}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--plain-logs", action="store_true", help="human-readable logs instead of JSON")
    p.add_argument("--trigger", default="cli", choices=["cli", "cron", "airflow"])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    for name, func in (("run", cmd_run), ("backfill", cmd_backfill)):
        r = sub.add_parser(name)
        if name == "run":
            r.add_argument("--mode", default="weekly", choices=["weekly", "daily_check", "manual"])
        r.add_argument("--diseases", type=_diseases, help="comma-separated codes, e.g. LASSA,CHOLERA")
        r.add_argument("--max-files", type=int, help="limit downloads per disease (testing)")
        r.add_argument("--no-crawl", action="store_true")
        r.add_argument("--no-analytics", action="store_true")
        r.add_argument("--no-forecast", action="store_true")
        r.set_defaults(func=func)

    pr = sub.add_parser("process")
    pr.add_argument("--no-analytics", action="store_true")
    pr.add_argument("--no-forecast", action="store_true")
    pr.set_defaults(func=cmd_process)

    a = sub.add_parser("analytics")
    a.add_argument("--no-forecast", action="store_true")
    a.set_defaults(func=cmd_analytics)

    d = sub.add_parser("demo")
    d.add_argument("--no-forecast", action="store_true", help="skip the slow rolling-origin forecast evaluation")
    d.add_argument("--allow-with-real-data", action="store_true", help=argparse.SUPPRESS)
    d.set_defaults(func=cmd_demo)

    c = sub.add_parser("parse-check")
    c.add_argument("paths", nargs="*", help="local PDF files")
    c.add_argument("--download", type=int, default=0, help="download the newest N reports per disease first")
    c.add_argument("--diseases", type=_diseases)
    c.add_argument("--spread", action="store_true", help="sample reports evenly across the whole listing (all years)")
    c.add_argument("--dump", action="store_true", help="print each PDF's text and tables (for writing templates)")
    c.add_argument("--out-dir", default="data/raw/parse_check")
    c.add_argument("--json-out")
    c.set_defaults(func=cmd_parse_check)

    sr = sub.add_parser("start-run", help="open a run and print its id (for Airflow)")
    sr.add_argument("--type", default="scheduled", choices=["scheduled", "daily_check", "backfill", "manual"])
    sr.add_argument("--dag-id")
    sr.set_defaults(func=cmd_start_run)

    st = sub.add_parser("step", help="run one step of an existing run (for Airflow)")
    st.add_argument("name", choices=STEPS)
    st.add_argument("--run-id", required=True)
    st.add_argument("--diseases", type=_diseases)
    st.add_argument("--max-files", type=int)
    st.add_argument("--retry-failed", action="store_true")
    st.add_argument("--no-forecast", action="store_true")
    st.set_defaults(func=cmd_step)

    fr = sub.add_parser("finish-run", help="close a run started with start-run")
    fr.add_argument("--run-id", required=True)
    fr.set_defaults(func=cmd_finish_run)

    sub.add_parser("purge-synthetic", help="remove synthetic demo data, keep real data").set_defaults(func=cmd_purge_synthetic)

    rr = sub.add_parser("reread", help="read one disease's reports again with the current parser")
    rr.add_argument("--disease", required=True, help="one code or several, comma-separated")
    rr.add_argument("--reason", default="parser updated")
    rr.set_defaults(func=cmd_reread)

    rt = sub.add_parser("retract", help="remove figures from misread reports and read them again")
    rt.add_argument("--reports", required=True, help="comma-separated report codes or prefixes")
    rt.add_argument("--reason", default="misread figures")
    rt.add_argument("--no-reread", action="store_true")
    rt.set_defaults(func=cmd_retract)

    rd = sub.add_parser("reset-db", help="DELETE all data and recreate empty tables")
    rd.add_argument("--yes", action="store_true")
    rd.set_defaults(func=cmd_reset_db)

    sub.add_parser("refresh-population").set_defaults(func=cmd_refresh_population)
    sub.add_parser("status").set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, json_output=not args.plain_logs)
    return args.func(args, get_settings())


if __name__ == "__main__":
    sys.exit(main())
