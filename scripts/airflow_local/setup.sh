#!/usr/bin/env bash
# One-time setup of a free, local Airflow (used by GitHub Codespaces; also works on Linux/WSL).
# Two separate Python environments so Airflow's libraries never clash with the pipeline's:
#   .venv-airflow   Apache Airflow 3.3.2 (same version as the Astro image)
#   .venv-pipeline  the pipeline (pandas, statsmodels, xgboost, ...)
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-python3}
AIRFLOW_VERSION=3.3.2
PYV=$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')

echo ">> OCR engine (reads the image-only Lassa state table)"
if ! command -v tesseract >/dev/null; then
  (sudo apt-get update -qq && sudo apt-get install -y -qq tesseract-ocr) >/dev/null 2>&1 \
    || echo "   could not install tesseract: state tables will be skipped (national figures still load)"
fi

echo ">> pipeline environment"
$PY -m venv .venv-pipeline
.venv-pipeline/bin/pip install -q --upgrade pip
.venv-pipeline/bin/pip install -q -r astro/pipeline-requirements.txt

echo ">> Airflow ${AIRFLOW_VERSION} environment (Python ${PYV})"
$PY -m venv .venv-airflow
.venv-airflow/bin/pip install -q --upgrade pip
.venv-airflow/bin/pip install -q "apache-airflow==${AIRFLOW_VERSION}" apache-airflow-providers-standard \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYV}.txt"
echo ">> done. Start Airflow with: bash scripts/airflow_local/start.sh"
