"""
## NCDC infectious disease pipeline

Every Monday this DAG runs the five steps of the thesis pipeline, one box each:

| Box | What it does |
|---|---|
| **start_run** | opens a row in `ops.pipeline_run_log` |
| **collect_reports** | downloads new NCDC situation reports (skips ones already stored) |
| **read_check_save** | reads the PDF tables, applies the 12 rules, saves good rows, quarantines bad ones |
| **quality_checks** | marks recent weeks provisional, records missing weeks |
| **analyse** | trends (STL), outbreak alerts, Lassa fever forecasts |
| **finish_run** | closes the run as success / partial / failed (runs even if a box failed) |

The database address comes from the `DATABASE_URL` environment variable set in
the Astro Deployment (Environment Variables, marked secret).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

try:  # Airflow 3
    from airflow.providers.standard.operators.bash import BashOperator
    from airflow.sdk import DAG
except ImportError:  # Airflow 2
    from airflow import DAG
    from airflow.operators.bash import BashOperator

# Where the pipeline code and its own Python environment are. Defaults suit the Astro image;
# Codespaces / a laptop set PIPELINE_HOME and PIPELINE_PYTHON (see scripts/airflow_local/start.sh).
HOME = os.environ.get("PIPELINE_HOME", "/usr/local/airflow/include/pipeline")
PYTHON = os.environ.get("PIPELINE_PYTHON", "/opt/pipeline_venv/bin/python")
PIPELINE = f"cd {HOME} && {PYTHON} -m src.orchestration.cli --plain-logs --trigger airflow"
ENV = {"DATA_DIR": os.environ.get("PIPELINE_DATA_DIR", f"{HOME}/data"), "PYTHONUNBUFFERED": "1"}
MAX_FILES = os.environ.get("PIPELINE_MAX_FILES")  # optional: limit downloads per disease (testing)
LIMIT = f" --max-files {MAX_FILES}" if MAX_FILES else ""
RUN_ID = "{{ ti.xcom_pull(task_ids='start_run') }}"

DEFAULT_ARGS = {
    "owner": "thesis",
    "retries": 3,                           # Section 3.3.4: three retries ...
    "retry_delay": timedelta(minutes=10),   # ... waiting 10, 20, then 40 minutes
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=40),
}


def box(task_id: str, command: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=task_id, bash_command=command, env=ENV, append_env=True, **kwargs)


with DAG(
    dag_id="ncdc_disease_pipeline",
    description="Weekly: collect NCDC reports -> read, check, save -> quality checks -> analyse",
    schedule="0 5 * * 1",          # Mondays 05:00 UTC = 06:00 in Nigeria
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    doc_md=__doc__,
    tags=["thesis", "ncdc"],
):
    start = box("start_run", f"{PIPELINE} init-db >/dev/null && {PIPELINE} start-run --type scheduled --dag-id ncdc_disease_pipeline",
                retries=0)
    collect = box("collect_reports", f"{PIPELINE} step collect --run-id {RUN_ID}{LIMIT}")
    read_check_save = box("read_check_save", f"{PIPELINE} step process --run-id {RUN_ID}")
    quality = box("quality_checks", f"{PIPELINE} step quality --run-id {RUN_ID}")
    analyse = box("analyse", f"{PIPELINE} step analyse --run-id {RUN_ID}", execution_timeout=timedelta(hours=2))
    finish = box("finish_run", f"{PIPELINE} finish-run --run-id {RUN_ID}", trigger_rule="all_done", retries=0)

    start >> collect >> read_check_save >> quality >> analyse >> finish


with DAG(
    dag_id="ncdc_backfill_2021_2026",
    description="Manual, one-off: load the 2021-2026 history with the same steps",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    doc_md="One-off historical load (Section 3.4.3). Several hours: about 900 PDFs at 5 s per request.",
    tags=["thesis", "ncdc"],
):
    s = box("start_run", f"{PIPELINE} init-db >/dev/null && {PIPELINE} start-run --type backfill --dag-id ncdc_backfill_2021_2026", retries=0)
    c = box("collect_reports", f"{PIPELINE} step collect --run-id {RUN_ID}", execution_timeout=timedelta(hours=6))
    p = box("read_check_save", f"{PIPELINE} step process --retry-failed --run-id {RUN_ID}", execution_timeout=timedelta(hours=6))
    q = box("quality_checks", f"{PIPELINE} step quality --run-id {RUN_ID}")
    a = box("analyse", f"{PIPELINE} step analyse --run-id {RUN_ID}", execution_timeout=timedelta(hours=2))
    f = box("finish_run", f"{PIPELINE} finish-run --run-id {RUN_ID}", trigger_rule="all_done", retries=0)
    s >> c >> p >> q >> a >> f
