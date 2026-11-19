# Scheduling options

All three run the same Python steps and write the same `ops.pipeline_run_log`, so results are comparable. **You only need one.**

| Option | Effort | Good for | How |
|---|---|---|---|
| **A. Scheduler container** (default) | None | A laptop or small server that is on at 06:00 | `docker compose --profile scheduler up -d` |
| **B. Airflow in GitHub Codespaces** (chosen, free) | Low: one click, runs in the browser | Watching and demonstrating the DAG | [airflow-codespaces.md](airflow-codespaces.md) |
| **C. GitHub Actions** (chosen, free, always on) | Low | The real weekly schedule | `load-database.yml` runs every Monday with the `DATABASE_URL` secret |
| **D. System cron** | Low | A Linux server | `0 5 * * 1 cd /path/to/repo && .venv/bin/pipeline --trigger cron run` |

Option B is the Airflow set-up described in Chapter 3; option A or C is the "simpler fallback". The DAG (`dags/ncdc_disease_pipeline.py`) runs each step as a separate box with `pipeline start-run`, `pipeline step <name>` and `pipeline finish-run`, so a failed box can be retried on its own. Retries: A retries 3 times at 10/20/40 minutes; B uses Airflow's `retries=3` with exponential backoff.
