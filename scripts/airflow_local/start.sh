#!/usr/bin/env bash
# Start Airflow (web screen on port 8080). DATABASE_URL must be set:
# in Codespaces add it once under GitHub -> Settings -> Codespaces -> Secrets.
set -euo pipefail
. "$(dirname "$0")/env.sh"
if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set. Add it as a Codespaces secret (GitHub -> Settings -> Codespaces -> Secrets),"
  echo "give this repository access, then rebuild the Codespace."; exit 1
fi
[ -d "$REPO/.venv-airflow" ] || bash "$REPO/scripts/airflow_local/setup.sh"
airflow db migrate | tail -3
airflow dags reserialize | tail -3
echo "Airflow is starting: open the 'Ports' tab and click the globe next to port 8080."
exec airflow standalone
