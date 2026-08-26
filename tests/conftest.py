"""Shared test fixtures.

Tests run against a *real* PostgreSQL (principle: the append-only trigger and
pgvector behaviour are the point, and SQLite has neither). If no database is
reachable, DB-backed tests skip with an explanatory message rather than
silently passing against a substitute.

Isolation strategy: every test runs inside a transaction bound to one
connection, rolled back at teardown. ROLLBACK is not UPDATE or DELETE, so it
does not trip the append-only triggers -- which is exactly why the audit tables
can still be exercised in tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

DEFAULT_TEST_DB_URL = "postgresql+psycopg://mtm:mtm@localhost:5432/mtm"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_TEST_DB_URL)


@pytest.fixture(scope="session")
def engine() -> Engine:
    url = _database_url()
    eng = sa.create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"No PostgreSQL reachable at {url!r} ({exc.__class__.__name__}). "
            "Start it with `docker compose up -d db` and set DATABASE_URL."
        )

    _migrate(url)
    return eng


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


@pytest.fixture
def db_session(connection: Connection) -> Iterator[Session]:
    maker = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
    finally:
        session.close()
