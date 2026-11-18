# Blueprint

Read this before changing structure, dependencies or the design.

## Purpose
NCDC publishes weekly situation reports for Nigeria's priority diseases as PDFs. The numbers are public but locked in documents with changing layouts, get revised after publication, and are hard to analyse over time. This project turns those PDFs into a clean, validated, versioned weekly database, then uses it for trend analysis, Lassa fever forecasting and outbreak alerts, shown on a dashboard. It is the artefact of the thesis *Design and Implementation of an Automated Data Pipeline That Monitors and Analyzes Infectious Disease Trends in Nigeria* (Miva Open University).

## Users
- **The researcher** (thesis author): runs the backfill, reviews quarantine, reads evaluation results.
- **A public-health analyst**: reads trends, alerts and forecasts on the dashboard; needs to know which numbers are provisional or flagged.
- **The examiner**: needs to reproduce the results from the repository with one or two commands.

## Shape: seven layers (Chapter 3, Fig. 3.2)

```
 L1 Sources        NCDC sitreps (PDF) · HDX p-codes/population (CSV) · WHO AFRO · ReliefWeb · ERA5
        │
 L2 Ingestion      src/ingestion     crawler + inbox ─ SHA-256 ─► skip if already stored
        │
 L3 Raw zone       data/raw/…  +  raw.documents                 files never modified
        │
 L4 Transform      src/extraction    classify ─► template ─► tables ─► standardise ─► weekly from cumulative ─► CFR
    + Validate     src/validation    12 rules ─► errors → ops.quarantine │ warnings → ops.data_quality_flag
        │
 L5 Curated        src/storage       staging ─► UPSERT ─► star schema; trigger ─► revision_history
        │                            one transaction per report (all or nothing)
 L6 Analytics      src/analytics     STL · EARS C1-C3 · SARIMA residual z · rolling-origin forecasts · MASE · DM test
        │
 L7 Presentation   dashboard/        Streamlit: overview · trends · states · forecasts · alerts · pipeline health

 Orchestration     src/orchestration  CLI · scheduler container (cron) · Airflow DAG ─► ops.pipeline_run_log
```

## Key properties
- **Idempotent**: re-running any step creates no duplicates (hash keys, URL check, UPSERT, `ON CONFLICT`).
- **Traceable**: every curated figure points to the report (`report_id`) and file (`document_id`, SHA-256) it came from.
- **Honest about quality**: missing is stored as missing, never zero; recent weeks are flagged provisional; warnings stay attached to rows; synthetic data is marked at every level.
- **Fail small**: a bad row goes to quarantine, a bad report to the manual queue; the run carries on and records its status.
- **Reproducible**: Docker Compose brings up the database, the pipeline and the dashboard; the demo loads the same way on any machine.

## Decisions
See [adr/](adr/).
