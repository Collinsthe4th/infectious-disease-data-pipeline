# Settings shared by start.sh and the CI test. Source it: . scripts/airflow_local/env.sh
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export AIRFLOW_HOME="$REPO/.airflow"
export AIRFLOW__CORE__DAGS_FOLDER="$REPO/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True   # no login screen (private Codespace only)
export AIRFLOW__API__EXPOSE_CONFIG=False
export PIPELINE_HOME="$REPO"
export PIPELINE_PYTHON="$REPO/.venv-pipeline/bin/python"
export PIPELINE_DATA_DIR="$REPO/data"
export PATH="$REPO/.venv-airflow/bin:$PATH"
