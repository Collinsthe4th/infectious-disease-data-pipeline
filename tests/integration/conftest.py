"""Integration fixtures: a clean PostgreSQL database loaded with a small SYNTHETIC demo.

Needs ``TEST_DATABASE_URL`` (the CI workflow provides one). Every test in
this folder is skipped when it is not set.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
REPO = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not TEST_URL:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set"))


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    shutil.copytree(REPO / "data" / "reference", data_dir / "reference")
    env = {
        "DATABASE_URL": TEST_URL or "", "DATA_DIR": str(data_dir), "EVALUATION_WEEKS": "4",
        "SARIMA_REFIT_EVERY": "100", "FORECAST_LOCATIONS": "NG", "STUDY_END_YEAR": "2026",
    }
    os.environ.update(env)
    from src.common.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def engine(settings):
    from sqlalchemy import text

    from src.storage.db import get_engine

    eng = get_engine(settings.database_url)
    with eng.begin() as conn:
        for schema in ("analytics", "staging", "curated", "raw", "ops"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def demo_run(settings, engine):
    """init-db + a small demo (2021-W01 .. 2023-W30) through the real pipeline, analytics included."""
    from src.demo.synthetic import write_demo_files
    from src.orchestration.pipeline import init_database, run_pipeline

    init_database(settings, engine)
    files = write_demo_files(settings.inbox_dir, end=(2023, 30), pdf_weeks=2)
    run_id, status = run_pipeline(settings, mode="manual", crawl=False, forecast=True)
    return {"run_id": run_id, "status": status, "files": files}
