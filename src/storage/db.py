"""Database connection helpers and schema bootstrap."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

SCHEMA_SQL: Path = Path(__file__).with_name("schema.sql")
DEFAULT_URL = "postgresql+psycopg://pipeline:pipeline@localhost:5432/disease_surveillance"


def normalise_url(url: str) -> str:
    """Accept the plain URL Supabase/Heroku give (``postgresql://`` or ``postgres://``)
    and point SQLAlchemy at the psycopg 3 driver this project installs."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_database_url() -> str:
    """``DATABASE_URL`` from the environment (Streamlit Cloud exposes its secrets this way)."""
    return normalise_url(os.environ.get("DATABASE_URL", DEFAULT_URL))


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine with pre-ping enabled for long-lived jobs."""
    return create_engine(normalise_url(url or get_database_url()), echo=echo, pool_pre_ping=True, future=True)


def apply_schema(engine: Engine, schema_path: Path = SCHEMA_SQL) -> None:
    """Execute ``schema.sql`` against ``engine``. Safe to run repeatedly.

    The file manages its own transaction (BEGIN/COMMIT), so it is sent as one
    script through the raw DBAPI connection rather than split into statements.
    """
    sql = schema_path.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql)
        raw.commit()
    finally:
        raw.close()


@contextmanager
def report_transaction(engine: Engine, run_id: uuid.UUID) -> Iterator[Session]:
    """One transaction per report (Section 3.5.2).

    Every insert/update for a report happens inside this block; any exception
    rolls the whole report back so curated tables never hold part of a report.
    ``pipeline.run_id`` is set for the revision-capture trigger.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.execute(text("SELECT set_config('pipeline.run_id', :rid, true)"), {"rid": str(run_id)})
        yield session
