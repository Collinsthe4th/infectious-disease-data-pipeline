# Software requirements

Derived from Chapter 3. IDs are referenced by the tests.

## Functional requirements

| ID | Requirement | Where |
|---|---|---|
| F1 | Discover NCDC situation reports for the seven priority diseases and download each PDF over HTTPS with a clear user agent, ≥ 5 s between requests and robots.txt respected | `src/ingestion/ncdc_crawler.py`, `http.py` |
| F2 | Compute a SHA-256 hash for every file and skip files already stored (idempotent ingestion) | `hashing.py`, `raw.documents.sha256 UNIQUE` |
| F3 | Accept hand-downloaded PDFs and manual-entry CSVs from `data/inbox` and process them like crawled files | `src/ingestion/inbox.py` |
| F4 | Store raw files unchanged with source URL, hash, download time and run id | `data/raw/`, `raw.documents` |
| F5 | Check that a file is a PDF with a text layer; send scanned files to the manual queue | `pdf_reader.py`, `extract.py` |
| F6 | Read report type, disease, epi week/year and printed dates from the title page | `classify.py` |
| F7 | Extract tables with layout templates (pdfplumber, Camelot fallback); try the next template on failure | `templates/`, `parsers.py` |
| F8 | Map headers to standard variable names; match place names to HDX p-codes through an alias table | `standardise.py`, `dim_location_alias` |
| F9 | Parse counts (thousand separators, footnotes); store dashes/blanks as missing, never zero | `clean_count` |
| F10 | Derive weekly values from year-to-date totals; quarantine negative differences | `transform.weekly_from_cumulative` |
| F11 | Compute CFR with the disease's denominator; keep the printed CFR for comparison | `transform.add_cfr`, rule 5 |
| F12 | Apply the 12 rules of Table 3.4; errors → quarantine with rule id and reason; warnings → load and flag | `src/validation/` |
| F13 | Load each report in one transaction; roll back the whole report on any database error | `pipeline._load_report` |
| F14 | Upsert into the star schema; record old and new values in `revision_history` when a figure changes | `merge_staging`, trigger |
| F15 | Flag the four most recent weeks per disease as provisional | `refresh_provisional_flags()` |
| F16 | Record each run (start, end, status, files, rows passed/failed, revisions) in `pipeline_run_log` | `repository.start_run/finish_run` |
| F17 | STL decomposition per series (period 52, robust) | `analytics/decomposition.py` |
| F18 | Forecast Lassa fever 1-4 weeks ahead with seasonal naive, SARIMA, NB, RF, GB, XGBoost, SVR; rolling-origin evaluation with MAE, RMSE, MASE and Diebold-Mariano | `analytics/forecasting.py`, `evaluation.py` |
| F19 | EARS C1-C3 (weekly) and SARIMA residual z-score alerts | `analytics/anomaly.py` |
| F20 | Dashboard: trends, state comparison, forecasts with intervals, alerts, pipeline health (runs, quarantine, flags, revisions) | `dashboard/` |
| F21 | Export curated tables to Parquet for DuckDB/Jupyter | `pipeline.export_parquet` |
| F22 | Schedule a weekly run and a daily check; retry failed runs 3 times with growing waits | `scripts/scheduler.py`, Airflow DAG |

## Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | Runs on a small server or laptop: PostgreSQL + one Python container, no paid services |
| N2 | Reproducible: `docker compose` brings up everything; dependencies pinned to compatible ranges |
| N3 | Traceable: every curated figure links to its report and raw file |
| N4 | Fault-tolerant: one bad row, report or download never stops a run |
| N5 | Observable: structured JSON logs (time, run id, report, step, message) and a run log table |
| N6 | Tested: unit tests for every rule and transformation; integration tests on PostgreSQL in CI |
| N7 | Ethical: public aggregated data only, polite crawling, synthetic data always labelled |
