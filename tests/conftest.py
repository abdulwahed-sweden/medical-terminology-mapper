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


# --------------------------------------------------------------------------- #
# Provider isolation
# --------------------------------------------------------------------------- #
#
# The application builds its providers from `Settings`, which reads the ambient
# environment and `.env`. So a developer with real credentials configured had
# ordinary API and MCP tests constructing live providers and calling
# api.openai.com -- 40 failures, real requests, and with a working key it would
# have been real spend instead of a 401.
#
# Ordinary tests therefore do not merely *default* to fakes; the provider
# settings are pinned before anything can read them. Application credentials are
# never test credentials. Live provider tests take their configuration from
# TEST_* variables and run only when explicitly asked for -- see
# `--live-providers` below.

# Loopback, and the discard port, where nothing listens. If a code path ever
# constructs an HTTP provider despite the pinning above, it fails at connect()
# against a dead local port instead of quietly reaching a vendor.
UNREACHABLE_BASE_URL = "http://127.0.0.1:9/v1"

FAKE_PROVIDER_ENV = {
    "EMBEDDING_PROVIDER": "fake",
    "EMBEDDING_MODEL": "fake-hash-v1",
    "LLM_PROVIDER": "fake",
    "LLM_MODEL": "fake-rerank-v1",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_EMBEDDINGS_BASE_URL": UNREACHABLE_BASE_URL,
    "OPENAI_CHAT_BASE_URL": UNREACHABLE_BASE_URL,
}

LOOPBACK_HOSTS = ["127.0.0.1", "::1", "localhost"]


def _pin_provider_settings() -> None:
    """Force the provider settings, then drop any Settings built before now.

    Environment variables outrank `.env` in pydantic-settings, so writing them
    here is enough to override both a poisoned shell and a developer's real
    `.env`. The cache clear matters as much as the assignment: `get_settings`
    is `lru_cache`d, and a Settings object built during plugin loading would
    otherwise survive and hand live providers to the whole session.
    """
    os.environ.update(FAKE_PROVIDER_ENV)

    from app.config import get_settings

    get_settings.cache_clear()


# Called at import, not from a fixture or a hook. pytest imports this conftest
# before it collects -- let alone imports -- any test module, so this runs
# before any test module can resolve Settings at import time. A session-scoped
# autouse fixture would run too late for that.
_pin_provider_settings()


def _infrastructure_hosts() -> list[str]:
    """Hosts the ordinary suite is allowed to reach: the database, and that is it."""
    from sqlalchemy.engine import make_url

    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.config import get_settings

        url = get_settings().database_url
    try:
        host = make_url(url).host
    except Exception:  # pragma: no cover - a malformed URL fails later, loudly
        return []
    return [host] if host else []


def _live_provider_hosts() -> list[str]:
    """Hosts an opted-in live test may reach, taken from its TEST_* configuration."""
    from urllib.parse import urlparse

    hosts = ["api.anthropic.com"]
    for variable in ("TEST_OPENAI_CHAT_BASE_URL", "TEST_OPENAI_EMBEDDINGS_BASE_URL"):
        host = urlparse(os.environ.get(variable, "")).hostname
        if host:
            hosts.append(host)
    return hosts


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-providers",
        action="store_true",
        default=False,
        help=(
            "Run the tests marked `requires_api_key` against real providers. "
            "They need TEST_* credentials and they cost money, so having a key "
            "in the environment is deliberately not enough on its own."
        ),
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live-provider tests unless a human asked for them by name."""
    if config.getoption("--live-providers"):
        return

    skip = pytest.mark.skip(reason="live-provider test; pass --live-providers to run it")
    for item in items:
        if item.get_closest_marker("requires_api_key"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _settings_cache_is_isolated() -> Iterator[None]:
    """No Settings object crosses a test boundary, in either direction.

    Cleared before, so a test never inherits Settings another test rebound;
    cleared after, so a test that rebinds the environment cannot leave a
    poisoned Settings cached for whatever runs next.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _network_is_local_only(request: pytest.FixtureRequest) -> None:
    """Second line of defence: ordinary tests reach local infrastructure only.

    The pinned settings above are what stops a provider being built at all.
    This is what makes a mistake in that pinning fail loudly instead of
    silently reaching a vendor -- and it does not depend on the machine
    happening to be offline. pytest-socket lifts the restriction itself at
    teardown.
    """
    from pytest_socket import socket_allow_hosts

    hosts = [*LOOPBACK_HOSTS, *_infrastructure_hosts()]
    if request.node.get_closest_marker("requires_api_key"):
        hosts += _live_provider_hosts()

    socket_allow_hosts(hosts, allow_unix_socket=True)


def _database_url() -> str:
    """DATABASE_URL from the environment, else whatever the app is configured with.

    Falling back to the application settings means `.env` works for the test
    suite too. Without it, a developer whose database is not on the default port
    would see every DB-backed test skip rather than run -- a silent pass, which
    is the worst possible outcome for a suite whose job is to prove a database
    guarantee.
    """
    from app.config import get_settings

    return os.environ.get("DATABASE_URL") or get_settings().database_url


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
    # `create_savepoint` keeps test isolation now that the write routes commit
    # explicitly: the session's commit releases a SAVEPOINT, and the outer
    # transaction this fixture owns is still rolled back at teardown.
    maker = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
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


@pytest.fixture
def kva_embedded(db_session: Session, kva_loaded: str) -> str:
    """Embed the loaded KVÅ sample with the deterministic fake provider."""
    import sqlalchemy as sa

    from app.config import get_settings
    from app.db.models import ConceptEmbeddingRow, ConceptRow
    from app.embeddings.fake import FakeEmbeddingProvider

    provider = FakeEmbeddingProvider(dim=get_settings().embedding_dim)
    rows = db_session.execute(
        sa.select(ConceptRow.code, ConceptRow.search_text)
        .where(ConceptRow.system == "kva", ConceptRow.version == kva_loaded)
        .order_by(ConceptRow.code)
    ).all()
    vectors = provider.embed([r.search_text for r in rows])
    db_session.add_all(
        [
            ConceptEmbeddingRow(
                system="kva",
                version=kva_loaded,
                code=row.code,
                provider=provider.provider_id,
                model=provider.model_id,
                dim=provider.dim,
                embedding=vector,
            )
            for row, vector in zip(rows, vectors, strict=True)
        ]
    )
    db_session.flush()
    return kva_loaded


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """The MCP tests are async; anyio drives them on asyncio."""
    return "asyncio"
