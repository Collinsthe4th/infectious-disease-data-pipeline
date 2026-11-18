"""Apache Airflow DAG for the weekly pipeline (Section 3.3.4).

Optional: the project also runs with cron or GitHub Actions. To use Airflow,
copy (or symlink) this file into your Airflow ``dags/`` folder, make the
repository importable (``PYTHONPATH``) and set ``DATABASE_URL`` for the workers.

Schedule: every Monday 06:00 West Africa Time, plus a light daily check.
Failed tasks retry three times with a growing wait (10, 20, 40 minutes).
"""

from __future__ import annotations

from datetime import datetime, timedelta

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
    from airflow.utils.trigger_rule import TriggerRule
except ImportError:  # lets the module be imported (and tested) without Airflow
    DAG = None  # type: ignore

DEFAULT_ARGS = {
    "owner": "thesis",
    "retries": 3,
    "retry_delay": timedelta(minutes=10),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=40),
}


def _ctx(ti):
    import uuid

    from src.common.config import get_settings
    from src.orchestration.pipeline import RunContext
    from src.storage import repository as repo
    from src.storage.db import get_engine

    settings = get_settings()
    run_id = uuid.UUID(ti.xcom_pull(task_ids="start_run"))
    return RunContext(engine=get_engine(settings.database_url), settings=settings, run_id=run_id,
                      stats=repo.RunStats())


def start_run(run_type: str, **_):
    from src.common.config import get_settings
    from src.orchestration.pipeline import begin_run

    return str(begin_run(get_settings(), run_type, trigger_source="airflow", dag_id="disease_pipeline").run_id)


def step(name: str, ti=None, **_):
    from src.orchestration import pipeline as p

    ctx = _ctx(ti)
    {"ingest_ncdc": p.ingest_ncdc, "ingest_inbox": p.ingest_inbox, "process": p.process_documents,
     "post_load": p.post_load, "analytics": p.analytics, "export": p.export_parquet}[name](ctx)
    ctx.flush()


def finish_run(ti=None, dag_run=None, **_):
    from src.orchestration.pipeline import end_run

    failed = [t.task_id for t in dag_run.get_task_instances() if t.state == "failed"]
    end_run(_ctx(ti), error=f"failed tasks: {failed}" if failed else None)


def _build(dag_id: str, schedule: str, run_type: str, steps: list[str]):
    with DAG(dag_id, default_args=DEFAULT_ARGS, schedule=schedule, start_date=datetime(2026, 1, 1),
             catchup=False, max_active_runs=1, tags=["thesis", "ncdc"]) as dag:
        first = PythonOperator(task_id="start_run", python_callable=start_run, op_kwargs={"run_type": run_type})
        previous = first
        for name in steps:
            task = PythonOperator(task_id=name, python_callable=step, op_kwargs={"name": name})
            previous >> task
            previous = task
        last = PythonOperator(task_id="finish_run", python_callable=finish_run, trigger_rule=TriggerRule.ALL_DONE)
        previous >> last
    return dag


if DAG is not None:
    # 05:00 UTC = 06:00 WAT
    weekly = _build("disease_pipeline", "0 5 * * 1", "scheduled",
                    ["ingest_ncdc", "ingest_inbox", "process", "post_load", "analytics", "export"])
    daily = _build("disease_pipeline_daily_check", "0 5 * * 2-7", "daily_check",
                   ["ingest_ncdc", "ingest_inbox", "process", "post_load"])
