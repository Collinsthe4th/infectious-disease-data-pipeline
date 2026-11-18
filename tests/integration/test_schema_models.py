"""schema.sql applies cleanly, is idempotent, and the ORM models match it."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from pathlib import Path  # noqa: E402

from sqlalchemy import inspect, text  # noqa: E402

from src.storage.db import apply_schema  # noqa: E402
from src.storage.models import PRIORITY_DISEASES, Base  # noqa: E402

SMOKE_SQL = Path(__file__).parent / "sql" / "schema_smoke_test.sql"


def test_schema_applies_twice(engine):
    apply_schema(engine)
    apply_schema(engine)


def test_every_model_table_matches_the_ddl(engine):
    apply_schema(engine)
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        db_cols = {c["name"] for c in insp.get_columns(table.name, schema=table.schema)}
        model_cols = {c.name for c in table.columns}
        assert model_cols == db_cols, f"{table.fullname}: model-only={model_cols - db_cols}, db-only={db_cols - model_cols}"


def test_seed_data(engine):
    apply_schema(engine)
    with engine.connect() as conn:
        assert set(conn.scalars(text("SELECT disease_code FROM curated.dim_disease"))) == set(PRIORITY_DISEASES)
        assert conn.scalar(text("SELECT count(*) FROM ops.validation_rule")) == 12
        assert conn.scalar(text("SELECT count(*) FROM curated.dim_epi_week WHERE epi_year BETWEEN 2021 AND 2026")) == 313


def test_behavioural_smoke_script(engine):
    """Constraint and revision-trigger checks written in SQL; the script rolls back."""
    apply_schema(engine)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(SMOKE_SQL.read_text(encoding="utf-8"))
    finally:
        raw.close()
