# Scheduling options

All three run the same Python steps and write the same `ops.pipeline_run_log`, so results are comparable. **You only need one.**

| Option | Effort | Good for | How |
|---|---|---|---|
| **A. Scheduler container** (default) | None | A laptop or small server that is on at 06:00 | `docker compose --profile scheduler up -d` |
| **B. Airflow** | High (Airflow has its own database, web server and scheduler) | Showing a DAG in the thesis, many pipelines | Copy `src/orchestration/dags/disease_pipeline_dag.py` into Airflow's `dags/`, set `PYTHONPATH` to this repo and `DATABASE_URL` |
| **C. GitHub Actions** | Low, but needs a database on the internet (e.g. a free Neon or Supabase Postgres) | No machine of your own | A scheduled workflow running `pipeline run` with `DATABASE_URL` stored as a repository secret |
| **D. System cron** | Low | A Linux server | `0 5 * * 1 cd /path/to/repo && .venv/bin/pipeline --trigger cron run` |

Option A satisfies Chapter 3's "cron job as a simpler fallback"; the DAG file satisfies "Airflow during development". Retries: A retries 3 times at 10/20/40 minutes; B uses Airflow's `retries=3` with exponential backoff.
