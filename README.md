<div align="center">

# Nigeria Infectious Disease Surveillance Pipeline

**An automated data pipeline that collects Nigeria's public disease surveillance reports, turns them into a validated, versioned database, and uses it to monitor trends, forecast cases and flag possible outbreaks.**

[![CI](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/ci.yml)
[![Weekly load](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/load-database.yml/badge.svg)](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/load-database.yml)
[![NCDC parse check](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/ncdc-parse-check.yml/badge.svg)](https://github.com/Collinsthe4th/infectious-disease-data-pipeline/actions/workflows/ncdc-parse-check.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Airflow](https://img.shields.io/badge/Apache%20Airflow-3.3-017CEE?logo=apacheairflow&logoColor=white)](https://airflow.apache.org/)
[![Streamlit](https://img.shields.io/badge/dashboard-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://infectious-disease-data-pipeline.streamlit.app)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[**Live dashboard**](https://infectious-disease-data-pipeline.streamlit.app) · [**Results**](results/README.md) · [**Documentation**](docs/README.md)

</div>

---

Final-year project, BSc Data Science, Miva Open University: *Design and Implementation of an Automated Data Pipeline for Monitoring and Analysing Infectious Disease Trends in Nigeria.*

## Contents

- [About the project](#about-the-project)
- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Running the pipeline](#running-the-pipeline)
- [The dashboard](#the-dashboard)
- [The database](#the-database)
- [Results](#results)
- [Testing and quality](#testing-and-quality)
- [Project structure](#project-structure)
- [Data sources and ethics](#data-sources-and-ethics)
- [Limitations](#limitations)
- [Citation](#citation)
- [License](#license)

## About the project

The Nigeria Centre for Disease Control and Prevention (NCDC) publishes situation reports for priority diseases (Lassa fever, cholera, measles, mpox, diphtheria, cerebrospinal meningitis and yellow fever) as PDF files. The reports mix text, tables and figures, their layout changes from year to year, and figures published earlier are sometimes revised in later reports. Because of this, most analyses depend on figures copied by hand, often for a single year.

I built this project to provide the data foundation behind such analyses. It collects the reports on a schedule, reads the figures out of the PDFs, checks them against 12 quality rules, stores them with a full history of revisions, and then uses the resulting multi-year dataset for trend analysis, short-term forecasting and outbreak alerts.

The work follows the objectives of the thesis:

1. **Analysis** of the public sources, their terms of use and the structure of NCDC reports across years.
2. **Design** of a layered pipeline, a star-schema database with revision history, and run monitoring.
3. **Development** of the collection, PDF extraction, validation, storage and analytics modules.
4. **Implementation** of scheduled runs, a historical backfill (2021 to 2026) and an interactive dashboard.
5. **Testing and evaluation** of extraction, pipeline reliability, data revisions and forecasting accuracy against baselines.

## Features

| | |
|---|---|
| **Automated collection** | Discovers new NCDC reports, downloads each file once (SHA-256 fingerprint), waits between requests and respects robots.txt |
| **Layout-aware extraction** | Reads tables and text from PDFs with layout templates for each report family and year, and reads image-only state tables with OCR |
| **12 validation rules** | Errors are held in quarantine for review, warnings are loaded with a flag, and the database repeats the key rules as constraints |
| **Revision history** | A database trigger records every figure that a later report changes, with the old value, the new value and both reports |
| **Trend analysis** | STL decomposition (trend and seasonal pattern) for every disease |
| **Forecasting** | Seven methods compared 1 to 4 weeks ahead with a rolling origin, scored with MAE, RMSE and MASE and tested with Diebold-Mariano |
| **Outbreak alerts** | EARS C1, C2 and C3 checks and a SARIMA residual check, combined into a plain "chance of an outbreak" indicator |
| **Dashboard** | Streamlit app with a national overview, a map of cases per 100,000 people, forecasts, alerts and pipeline health |
| **Reproducible results** | Fixed random seeds and a saved data snapshot, with a script that re-runs the model comparison: identical numbers on the same type of processor, and the same conclusions on any machine |
| **Scheduling** | GitHub Actions runs the pipeline every Monday, and the same steps run as an Apache Airflow DAG |

## Architecture

```mermaid
flowchart TD
    S["<b>1. Sources</b><br/>NCDC situation reports (PDF)<br/>HDX population · geoBoundaries"]
    I["<b>2. Ingestion</b> (src/ingestion)<br/>crawler · manual inbox · SHA-256 dedup · retries"]
    R["<b>3. Raw zone</b> (raw.documents)<br/>files stored unchanged"]
    T["<b>4. Transform and validate</b> (src/extraction, src/validation)<br/>classify · layout template · tables, text, OCR · standardise<br/>12 rules: errors to quarantine, warnings to flags"]
    C["<b>5. Curated storage</b> (src/storage)<br/>staging · upsert · PostgreSQL star schema<br/>trigger to revision_history · one transaction per report"]
    A["<b>6. Analytics</b> (src/analytics)<br/>STL · EARS C1-C3 · SARIMA residual<br/>rolling-origin forecasts · MAE, RMSE, MASE · Diebold-Mariano"]
    P["<b>7. Presentation</b> (dashboard)<br/>Streamlit: overview · trends · states · forecasts · alerts · pipeline health"]
    O(["Orchestration (src/orchestration, dags)<br/>GitHub Actions weekly · Airflow DAG · CLI<br/>run log in ops.pipeline_run_log"])
    S --> I --> R --> T --> C --> A --> P
    O -.-> I & T & C & A
    C -. Parquet .-> D[("DuckDB / Jupyter")]
```

In one weekly run, the crawler finds new reports and stores each PDF unchanged. The title page tells the pipeline the disease and the epidemiological week, and a layout template reads the figures. Names and numbers are standardised, and the 12 rules decide what is loaded, what is flagged and what is held back. Everything from one report is saved in a single transaction, so a failed report never leaves half its figures behind. Finally the analytics are refreshed and the dashboard shows the result. If a report cannot be read, it is recorded for review and the run carries on.

The design decisions are recorded in the [blueprint](docs/blueprint/blueprint.md) and the [architecture decision records](docs/blueprint/adr/).

## Tech stack

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Collection | requests, BeautifulSoup |
| PDF extraction | pdfplumber, Camelot, Tesseract OCR |
| Data processing | pandas, NumPy, SciPy |
| Storage | PostgreSQL 16 (Supabase), SQLAlchemy 2, psycopg 3, Parquet and DuckDB |
| Analytics | statsmodels (STL, SARIMA, negative binomial), scikit-learn, XGBoost |
| Orchestration | GitHub Actions, Apache Airflow 3.3 (GitHub Codespaces), Docker Compose |
| Dashboard | Streamlit, Plotly |
| Quality | pytest, Ruff, GitHub Actions CI |

## Getting started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/), **or** Python 3.11 and PostgreSQL 14 or later
- Tesseract OCR (optional, only needed to read the Lassa fever state tables)

### Quick start with Docker

```bash
git clone https://github.com/Collinsthe4th/infectious-disease-data-pipeline.git
cd infectious-disease-data-pipeline
cp .env.example .env

docker compose up -d db dashboard                 # start PostgreSQL and the dashboard
docker compose run --rm app init-db               # create the tables and reference data
docker compose run --rm app backfill --max-files 3   # a quick trial: 3 real reports per disease
```

Open **http://localhost:8501**. To load the full 2021 to 2026 history, run `docker compose run --rm app backfill` (this takes several hours because the crawler waits between downloads).

A synthetic demo dataset is also available (`docker compose run --rm app demo`) for trying every feature without downloading anything. It refuses to run on a database that already holds real data, and the dashboard shows a banner whenever synthetic data is loaded.

### Local Python setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ncdc
python -m src.orchestration.cli init-db
streamlit run dashboard/app.py
```

All settings are environment variables, listed in [`.env.example`](.env.example) and the [configuration guide](docs/engineering/configuration.md).

## Running the pipeline

### Commands

Run with `docker compose run --rm app <command>` or `python -m src.orchestration.cli <command>`.

| Command | What it does |
|---|---|
| `init-db` | Creates the tables and loads the state reference data (safe to repeat) |
| `run` | One weekly run: crawl, process, analytics and Parquet export |
| `backfill` | Loads the 2021 to 2026 history with the same code (`--diseases`, `--max-files` to limit) |
| `process` | Processes files already downloaded or placed in `data/inbox/` |
| `reread --disease CODE` | Reads one or more diseases' reports again after a parser improvement |
| `analytics` | Recomputes trends, alerts and forecasts |
| `parse-check` | Reads PDFs without a database and prints what was extracted |
| `status` | Shows the latest runs and table sizes |

### Scheduling

- **GitHub Actions (always on):** the [Load data into Supabase](.github/workflows/load-database.yml) workflow runs the pipeline every Monday at about 06:17 West Africa Time and writes to the hosted database.
- **Apache Airflow:** the DAG in [`dags/ncdc_disease_pipeline.py`](dags/ncdc_disease_pipeline.py) runs the same steps as six tasks with three retries and exponential backoff. It runs in a free GitHub Codespace ([guide](docs/operations/airflow-codespaces.md)) and is tested end to end in CI.
- **Without Airflow:** `docker compose --profile scheduler up -d` or a cron job calling the CLI.

Every scheduler writes to the same run log (`ops.pipeline_run_log`), so pipeline reliability is measured the same way whichever one runs.

## The dashboard

**Live:** [infectious-disease-data-pipeline.streamlit.app](https://infectious-disease-data-pipeline.streamlit.app)

| Page | What it shows |
|---|---|
| **Overview** | The chance of an outbreak for all seven diseases, this week's reports with 12-week sparklines, and the evidence behind the selected disease's risk level |
| **Trends over time** | Weekly and year-to-date figures for any disease and place, with the STL trend, seasonal pattern and unusual weeks |
| **By state** | A map of Nigeria shaded by cases per 100,000 people, a state ranking and weekly lines per state |
| **Forecasts** | The next four weeks with a 95% interval, accuracy of each method and the Diebold-Mariano test |
| **Outbreak alerts** | Weeks flagged by each alert method and what each method looks for |
| **Data pipeline health** | Run history, files read, rows held back and figures revised |

Forecasts and alerts are research outputs. They show where to look first and are not official NCDC alerts.

## The database

PostgreSQL, organised in five schemas:

| Schema | Main tables | Purpose |
|---|---|---|
| `raw` | `documents` | Every downloaded file with its URL, SHA-256 fingerprint and processing status |
| `staging` | `weekly_cases` | Validated rows waiting to be merged |
| `curated` | `fact_weekly_cases`, `dim_disease`, `dim_location`, `dim_epi_week`, `dim_source_report`, `revision_history` | The analysis-ready star schema and audit trail |
| `ops` | `pipeline_run_log`, `validation_rule`, `quarantine`, `data_quality_flag` | Run history and data quality |
| `analytics` | `stl_component`, `forecast`, `model_evaluation`, `alert` | Model outputs read by the dashboard |

```mermaid
erDiagram
    dim_disease       ||--o{ fact_weekly_cases : disease_id
    dim_location      ||--o{ fact_weekly_cases : location_id
    dim_epi_week      ||--o{ fact_weekly_cases : week_id
    dim_source_report ||--o{ fact_weekly_cases : "report_id (latest)"
    fact_weekly_cases ||--o{ revision_history  : fact_id
    raw_documents     ||--o{ dim_source_report : document_id
    fact_weekly_cases {
        smallint disease_id FK
        int location_id FK
        int week_id FK
        int report_id FK
        int suspected
        int confirmed
        int deaths
        int cum_suspected
        int cum_confirmed
        int cum_deaths
        numeric cfr_pct
        bool is_provisional
    }
    revision_history {
        text field_name
        numeric old_value
        numeric new_value
        int old_report_id FK
        int new_report_id FK
    }
```

One fact row is one **disease, location and epidemiological week**. The database enforces the key rules itself (no negative counts, confirmed cases never above suspected cases, valid ISO weeks, one row per disease, place and week). Full details are in the [database schema](docs/engineering/database_schema.md).

## Results

The full results, with charts, tables and plain explanations of each statistical test, are in **[results/README.md](results/README.md)**. That page is rebuilt from the real data after every database load and covers:

- the data collected for each disease and the pipeline's run history
- data quality (rows held back and the rules that stopped them) and revisions found in later reports
- trends, seasonality and outbreak alerts
- the forecasting comparison (MAE, RMSE, MASE and the Diebold-Mariano test against SARIMA)
- a **reproducibility check**: the model comparison is re-run from a saved data snapshot without the database. The numbers are identical on the same type of processor, and on a different type the method ranking and every test result stay the same

To reproduce the forecasting results:

```bash
pip install -r requirements.txt
python scripts/reproduce_results.py results/data
```

## Testing and quality

```bash
pytest tests/unit                      # fast, no database
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/scratch pytest tests/integration
ruff check src tests dashboard scripts dags
```

On every push, GitHub Actions runs the unit and integration tests on PostgreSQL (including a full end-to-end pipeline run and every dashboard page), checks that the Airflow DAG loads, runs the DAG end to end in a real Airflow instance, lints the code and builds the Docker image. Once a week it also downloads current NCDC reports to check that the parser still reads them. See the [test strategy](docs/qa/test-strategy.md).

## Project structure

```
├── src/
│   ├── common/          configuration, logging, epidemiological week calendar, disease catalogue
│   ├── ingestion/       NCDC crawler, HTTP client with retries, manual inbox
│   ├── extraction/      report classifier, layout templates, table, text and OCR readers
│   ├── validation/      the 12 rules and the engine that routes errors and warnings
│   ├── storage/         schema.sql, repository and database helpers
│   ├── analytics/       STL, forecasting, evaluation, alerts and outbreak risk
│   ├── orchestration/   pipeline steps and the command-line tool
│   └── demo/            synthetic dataset generator
├── dags/                Apache Airflow DAG
├── dashboard/           Streamlit app (app.py and pages/)
├── results/             results page, charts and the data snapshot for reproduction
├── tests/               unit/ (no database) and integration/ (PostgreSQL, end to end, dashboard)
├── data/reference/      states, name aliases, populations and state boundaries
├── notebooks/           DuckDB exploration of the Parquet exports
├── scripts/             results report, reproduction, reference data and maintenance tools
├── docs/                blueprint, decision records, requirements, engineering, QA, operations, security
└── .github/workflows/   CI, weekly load, results report, Airflow test and maintenance workflows
```

## Data sources and ethics

| Source | Used for | Licence or terms |
|---|---|---|
| [NCDC situation reports](https://ncdc.gov.ng/diseases/sitreps) | All disease figures | Public, aggregated reports. NCDC remains the authoritative source |
| [HDX: Nigeria subnational population statistics](https://data.humdata.org/dataset/cod-ps-nga) | Cases per 100,000 people | CC BY-IGO |
| [geoBoundaries (gbOpen NGA ADM1)](https://www.geoboundaries.org/) | State outlines on the map | CC BY 4.0 |

All data are public and aggregated. No personal or patient-level data are collected. The crawler identifies itself, waits between requests, respects robots.txt and never downloads the same file twice. Recent weeks are marked as provisional because NCDC may revise them, and forecasts and alerts are labelled as research outputs, not official projections.

## Limitations

- NCDC publishes Lassa fever weekly, but the other diseases appear in short runs during outbreaks or monthly. Forecasts are only made for diseases with about two years of regular, recent weekly reports.
- The Lassa fever state table is an image inside the PDF. It is read with OCR and used only when every column adds up to its total and matches the national figure, so some weeks have no state figures.
- Some reports use layouts that cannot be read with confidence. These go to a manual review queue instead of being guessed.

## Citation

If you use this work, please cite it as:

```
Akagha, C. C. (2026). Design and implementation of an automated data pipeline for monitoring and
analysing infectious disease trends in Nigeria [Undergraduate thesis, Miva Open University].
https://github.com/Collinsthe4th/infectious-disease-data-pipeline
```

## License

The code is released under the [MIT License](LICENSE). The disease figures belong to NCDC and remain subject to NCDC's terms, and the reference data keep their own licences (see [Data sources and ethics](#data-sources-and-ethics)).

## Author

**Akagha Collins Chisom**, BSc Data Science, School of Computing, Miva Open University
GitHub: [@Collinsthe4th](https://github.com/Collinsthe4th)
