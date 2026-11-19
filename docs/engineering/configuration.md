# Configuration reference

All settings are environment variables (or lines in `.env`). Defaults follow Chapter 3.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://pipeline:pipeline@localhost:5432/disease_surveillance` | SQLAlchemy URL |
| `DATA_DIR` | `./data` | raw, inbox, quarantine, Parquet and reference folders |
| `NCDC_BASE_URL` | `https://ncdc.gov.ng` | |
| `USER_AGENT` | project name + repo URL | sent with every request |
| `REQUEST_DELAY_S` | 5 | minimum seconds between requests |
| `REQUEST_TIMEOUT_S` | 60 | per request |
| `MAX_RETRIES` / `BACKOFF_BASE_S` | 3 / 10 | waits 10, 20, 40 s |
| `STUDY_START_YEAR` / `STUDY_END_YEAR` | 2021 / 2026 | reports outside are marked `ignored` |
| `CFR_TOLERANCE_PP` | 0.1 | rule 5 |
| `TIMELINESS_DAYS` | 14 | rule 11 |
| `PROVISIONAL_WEEKS` | 4 | recent weeks flagged provisional |
| `FORECAST_DISEASES` | `ALL` | diseases to forecast (comma-separated codes, or ALL). Each must also have about two years of regular, recent weekly reports (`forecast_eligibility`) |
| `FORECAST_LOCATIONS` | `NG` | comma-separated p-codes (`NG` = national, e.g. `NG,NG029,NG012`) |
| `FORECAST_HORIZONS` | 4 | weeks ahead |
| `EVALUATION_WEEKS` | 52 | rolling-origin test window |
| `SARIMA_REFIT_EVERY` | 13 | origins between SARIMA re-estimations |
| `EARS_THRESHOLD` / `RESIDUAL_Z_THRESHOLD` | 3 / 2 | alert thresholds |
| `RANDOM_SEED` | 42 | models and demo data |
| `DASHBOARD_CACHE_SECONDS` | 300 | dashboard query cache |

Watched values: changing a validation threshold, the study window or the evaluation settings changes thesis results. Note the values used in Chapter 4.
