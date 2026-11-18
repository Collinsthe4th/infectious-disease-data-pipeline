# Runbook

## 1. First-time setup (real data)
```bash
docker compose down -v                    # start from an EMPTY database (removes demo data)
docker compose up -d db dashboard
docker compose run --rm app refresh-population   # optional, needs internet: state populations from HDX
docker compose run --rm app init-db
```

## 2. Backfill 2021-2026 (one-off)
The backfill uses exactly the same code as weekly runs (Section 3.4.3).

```bash
# a small trial first: 3 newest reports per disease, no forecasting
docker compose run --rm app backfill --max-files 3 --no-forecast
docker compose run --rm app status

# the full history (several hours: about 900 PDFs at 5 s per request)
docker compose run --rm app backfill
```

What happens:
1. Every listing page is read; each PDF is downloaded once (known URLs and hashes are skipped, so you can stop and restart safely).
2. Reports are processed in epi-week order so that "newest report wins" holds.
3. Reports outside 2021-2026 are marked `ignored`; unreadable ones go to the **manual queue**.
4. Analytics and the Parquet export run at the end.

You can run the backfill one disease at a time: `backfill --diseases LASSA`.

## 3. Weekly runs
Pick one way of scheduling ([scheduling.md](scheduling.md)). The simplest:
```bash
docker compose --profile scheduler up -d      # Monday 06:00 WAT full run, daily light check
```
Or by hand: `docker compose run --rm app run`.

## 4. Reviewing problems (weekly, 10 minutes)
Open the dashboard → **Pipeline health**.

| You see | Do this |
|---|---|
| Rows in **Quarantine** | Read the rule and payload. If the PDF really says that, mark it reviewed (see below). If the parser misread it, fix the template and re-run `process`. |
| Documents in `manual_queue` | Open the PDF. Either add a template, or type the figures into a CSV (columns: `disease_code,state,epi_year,epi_week,suspected,confirmed,deaths`) and drop it in `data/inbox/`, then `process`. |
| Lines in `data/quarantine/manual_download_queue.jsonl` | NCDC refused or timed out. Download the PDF in a browser, put it in `data/inbox/`, run `process`. |
| Run status `partial` | One or more reports failed to load and were rolled back. See the logs (`docker compose logs app`) for the report code. |
| Run status `failed` | Read `error_message` in the run table; fix; re-run. Nothing half-loaded remains (transactions). |

Mark quarantine rows reviewed:
```sql
UPDATE ops.quarantine SET review_status = 'confirmed_invalid', reviewed_at = now(), review_note = 'NCDC typo'
 WHERE quarantine_id = 123;
```
(`docker compose exec db psql -U pipeline disease_surveillance` opens a SQL prompt.)

## 5. When NCDC changes a layout
The weekly **NCDC parse check** on GitHub turns red or shows `ok: false`. Follow "Adding a layout template" in the [development guide](../engineering/development.md), then run `process --` with `backfill` to retry failed documents (`backfill --no-crawl`).

## 6. Backups
```bash
docker compose exec db pg_dump -U pipeline disease_surveillance | gzip > backup_$(date +%F).sql.gz
```
`data/raw/` holds every original PDF; with it and the code, the database can always be rebuilt.

## 7. Numbers for Chapter 4
- Run success rate (RQ1): Pipeline health page, or `SELECT status, count(*) FROM ops.pipeline_run_log WHERE run_type <> 'backfill' GROUP BY 1;`
- Revisions (RQ2): `curated.revision_history` (export from the dashboard or `data/curated/parquet/revision_history.parquet`).
- Seasonality and alerts (RQ3): Trends and Alerts pages; `analytics.stl_component`, `analytics.alert`.
- Forecast accuracy and DM tests (RQ4): Forecasts page; `analytics.model_evaluation` for the latest run.
