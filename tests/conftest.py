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


# --------------------------------------------------------------------------- #
# Terminology fixtures
# --------------------------------------------------------------------------- #

SAMPLE_VERSION = "2026-sample"


@pytest.fixture
def icd10se_loaded(db_session: Session) -> str:
    """Load the ICD-10-SE sample into the test transaction; return its version."""
    from app.db.models import upsert_concepts
    from app.terminology.icd10se import ICD10SE

    upsert_concepts(db_session, ICD10SE().load(FIXTURES / "icd10se_sample.txt", SAMPLE_VERSION))
    return SAMPLE_VERSION


@pytest.fixture
def kva_loaded(db_session: Session) -> str:
    from app.db.models import upsert_concepts
    from app.terminology.kva import KVA

    loader = KVA()
    concepts = [
        concept
        for name in ("kva_kka_sample.txt", "kva_kma_sample.txt")
        for concept in loader.load(FIXTURES / name, SAMPLE_VERSION)
    ]
    upsert_concepts(db_session, concepts)
    return SAMPLE_VERSION


@pytest.fixture
def embedding_provider() -> FakeEmbeddingProvider:  # noqa: F821
    from app.config import get_settings
    from app.embeddings.fake import FakeEmbeddingProvider

    return FakeEmbeddingProvider(dim=get_settings().embedding_dim)


@pytest.fixture
def icd10se_embedded(db_session: Session, icd10se_loaded: str, embedding_provider: object) -> str:
    """Embed the loaded ICD-10-SE sample with the deterministic fake provider."""
    import sqlalchemy as sa

    from app.db.models import ConceptEmbeddingRow, ConceptRow

    provider = embedding_provider
    rows = db_session.execute(
        sa.select(ConceptRow.code, ConceptRow.search_text)
        .where(ConceptRow.system == "icd10se", ConceptRow.version == icd10se_loaded)
        .order_by(ConceptRow.code)
    ).all()
    vectors = provider.embed([r.search_text for r in rows])  # type: ignore[attr-defined]
    db_session.add_all(
        [
            ConceptEmbeddingRow(
                system="icd10se",
                version=icd10se_loaded,
                code=row.code,
                provider=provider.provider_id,  # type: ignore[attr-defined]
                model=provider.model_id,  # type: ignore[attr-defined]
                dim=provider.dim,  # type: ignore[attr-defined]
                embedding=vector,
            )
            for row, vector in zip(rows, vectors, strict=True)
        ]
    )
    db_session.flush()
    return icd10se_loaded


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:  # noqa: F821
    """A TestClient bound to the test transaction.

    The dependency override matters: without it the app would open its own
    session on a different connection and see none of the fixture data, and
    nothing it wrote would be rolled back at teardown.
    """
    from fastapi.testclient import TestClient

    from app.db.session import get_session
    from app.main import create_app

    def _session_override() -> Iterator[Session]:
        # Must be a generator *function*: FastAPI decides how to handle a
        # dependency by inspecting the callable, not its return value.
        yield db_session

    app = create_app()
    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
