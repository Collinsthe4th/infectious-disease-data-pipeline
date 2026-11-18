# Development guide

## Option A: Docker (recommended, nothing else to install)
Install Docker Desktop, then from the repository folder:

```bash
docker compose up -d db dashboard        # PostgreSQL + dashboard (http://localhost:8501)
docker compose run --rm app init-db      # create tables and load reference data
docker compose run --rm app demo         # load the SYNTHETIC demo (about 5-10 minutes)
docker compose run --rm app status       # what is in the database
```

Real data:

```bash
docker compose down -v && docker compose up -d db dashboard   # fresh, empty database
docker compose run --rm app init-db
docker compose run --rm app run --diseases LASSA --max-files 3   # small test against ncdc.gov.ng
docker compose run --rm app backfill                             # full 2021-2026 history (hours)
```

## Option B: local Python
Python 3.10+ and a PostgreSQL 14+ server.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # set DATABASE_URL
python -m src.orchestration.cli init-db
python -m src.orchestration.cli demo
streamlit run dashboard/app.py
```

`pip install -e .` also gives you a `pipeline` command (`pipeline status`, `pipeline run`, ...).

## Commands

| Command | What it does |
|---|---|
| `init-db` | Create/upgrade the schema, load states and aliases. Safe to repeat. |
| `run [--mode weekly\|daily_check\|manual] [--diseases LASSA,CHOLERA] [--max-files N] [--no-crawl] [--no-analytics] [--no-forecast]` | One full run |
| `backfill [...]` | Same as `run` but logged as a backfill and retries previously failed files |
| `process` | Process what is already downloaded or in `data/inbox` |
| `analytics [--no-forecast]` | Recompute STL, alerts, forecasts |
| `demo [--no-forecast]` | Load the synthetic dataset through the pipeline |
| `parse-check [files...] [--download N]` | Parse PDFs without a database and print what was read |
| `refresh-population` | Fill state populations from HDX into `data/reference/nga_states.csv` |
| `status` | Last runs and table sizes |

Add `--plain-logs` before the command for human-readable logs (JSON by default).

## Running tests

```bash
pytest tests/unit                                     # no database needed
export TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test_pipeline   # an EMPTY scratch database
pytest tests/integration                              # drops and recreates the schemas in that database!
```

## Adding a layout template
1. Get a sample PDF (`parse-check --download 1 --diseases MEASLES`).
2. Look at `parse_report.json`: which block failed?
3. Add a `LayoutTemplate` in `src/extraction/templates/__init__.py` with a `valid_from` date; adjust header words in `standardise.py` only if NCDC uses a new word.
4. Add a test in `tests/unit/test_extract_pdf.py` (generate a similar PDF with `src/demo/pdf_writer.py`).
