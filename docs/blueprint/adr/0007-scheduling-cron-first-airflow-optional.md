# 0007 Scheduling: a scheduler container by default, Airflow optional

Status: accepted

**Context.** Chapter 3 describes Airflow during development with cron as a fallback on a low-cost server.

**Decision.** Pipeline steps are plain Python functions (`src/orchestration/pipeline.py`). Three ways to schedule them use the same functions: (1) `scripts/scheduler.py` in the `scheduler` Compose profile (weekly full run Monday 06:00 WAT, daily light check, three retries at 10/20/40 minutes); (2) an Airflow DAG in `src/orchestration/dags/` with the same steps as tasks; (3) system cron calling the CLI.

**Why.** Airflow needs its own database, web server and scheduler: heavy for a single weekly job. Keeping the steps framework-free means switching costs nothing.

**Consequences.** Run history is in `ops.pipeline_run_log` whichever scheduler is used, so the run-success metric (RQ1) is comparable. See [operations/scheduling.md](../../operations/scheduling.md).
