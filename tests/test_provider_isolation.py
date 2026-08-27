"""Ordinary tests never use application provider configuration.

The defect this guards against: the application builds its providers from
`Settings`, which reads the ambient environment and `.env`, so a developer with
real credentials configured had ordinary API and MCP tests constructing live
providers and calling `api.openai.com`. Forty tests failed with 401s, and with a
working key it would have been silent success and real spend instead.

Three separate claims are checked here, because they can fail independently:

1. the pinned settings really are what `get_settings()` returns;
2. a connection to a provider host is refused by the harness, not by the
   machine happening to be offline;
3. the API and MCP suites still pass, and stay offline, in a process that was
   *started* with hostile provider variables -- which is the original failure
   mode, and the only way to prove the import-time pinning works.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import UNREACHABLE_BASE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent

# What a developer with real credentials configured would have in their shell.
POISONED_ENV = {
    "EMBEDDING_PROVIDER": "openai_compat",
    "LLM_PROVIDER": "anthropic",
    "OPENAI_API_KEY": "dummy-should-never-be-used",
    "ANTHROPIC_API_KEY": "dummy-should-never-be-used",
    "OPENAI_EMBEDDINGS_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_CHAT_BASE_URL": "https://api.openai.com/v1",
}

# Run under the poisoned environment. These are the two suites that leaked.
LEAKY_SUITES = ["tests/test_api.py", "tests/test_mcp_server.py"]


def test_ordinary_tests_see_fake_providers() -> None:
    """Whatever the environment says, this is what the application is handed."""
    from app.config import get_settings
    from app.embeddings import build_embedding_provider
    from app.llm import build_llm_provider

    settings = get_settings()

    assert settings.embedding_provider == "fake"
    assert settings.llm_provider == "fake"
    assert not settings.openai_api_key
    assert not settings.anthropic_api_key
    # Not a vendor endpoint, so a provider built by mistake fails locally.
    assert settings.openai_embeddings_base_url == UNREACHABLE_BASE_URL
    assert settings.openai_chat_base_url == UNREACHABLE_BASE_URL

    assert build_embedding_provider(settings).provider_id == "fake"
    assert build_llm_provider(settings).provider_id == "fake"


def test_the_settings_cache_does_not_carry_a_rebound_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test that rebinds the environment must not leave it cached for the next.

    The autouse fixture in conftest clears the cache on the way out; this proves
    the mechanism it relies on actually rebuilds Settings rather than returning
    the object built before the change.
    """
    from app.config import get_settings

    assert get_settings().llm_provider == "fake"

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    assert get_settings().llm_provider == "anthropic"

    monkeypatch.undo()
    get_settings.cache_clear()
    assert get_settings().llm_provider == "fake"


@pytest.mark.parametrize("host", ["api.openai.com", "api.anthropic.com"])
def test_provider_hosts_are_unreachable_from_an_ordinary_test(host: str) -> None:
    """Blocked by the harness -- not by the machine being offline.

    pytest-socket raises before any DNS or TCP work happens, so this is the same
    result on a laptop with internet and on an isolated CI runner.
    """
    from pytest_socket import SocketConnectBlockedError

    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection((host, 443), timeout=5)


def test_the_database_is_still_reachable() -> None:
    """The block must not take local infrastructure with it."""
    from sqlalchemy.engine import make_url

    from app.config import get_settings

    url = make_url(os.environ.get("DATABASE_URL") or get_settings().database_url)
    assert url.host
    with socket.create_connection((url.host, url.port or 5432), timeout=5):
        pass


@pytest.mark.requires_db
def test_api_and_mcp_stay_offline_in_a_poisoned_process() -> None:
    """The original failure mode, reproduced at the process level.

    A child pytest is started with the hostile variables actually set, because
    that is the one thing an in-process test cannot simulate: the import-time
    pinning in conftest has to beat an environment that was already poisoned
    when the interpreter started.

    Only the two suites that leaked are run, so this cannot recurse into itself.
    """
    env = {**os.environ, **POISONED_ENV}
    env.pop("PYTEST_CURRENT_TEST", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", *LEAKY_SUITES, "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined[-4000:]
    # A 401 would mean the request left the machine, which is not a pass.
    for evidence in ("api.openai.com", "api.anthropic.com", "401 Unauthorized"):
        assert evidence not in combined, f"{evidence} appeared:\n{combined[-4000:]}"


def _run_live_selection(*extra: str, credentials: dict[str, str] | None = None) -> str:
    """Collect the live-provider tests in a child process and report skip reasons."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("TEST_")}
    env.update(credentials or {})

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_providers.py",
            "-m",
            "requires_api_key",
            "-rs",
            "-p",
            "no:cacheprovider",
            *extra,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return result.stdout + result.stderr


# Never used to authenticate: every assertion below is that the test was skipped
# before any provider was constructed.
DUMMY_TEST_CREDENTIALS = {
    "TEST_ANTHROPIC_API_KEY": "dummy-never-used",
    "TEST_OPENAI_API_KEY": "dummy-never-used",
    "TEST_OPENAI_CHAT_BASE_URL": "https://api.openai.com/v1",
}


def test_test_credentials_alone_do_not_run_live_providers() -> None:
    """Having a key must not be enough to spend money.

    This is the gate that matters: a developer with TEST_* credentials exported
    runs `pytest` and the live tests still do not fire.
    """
    output = _run_live_selection(credentials=DUMMY_TEST_CREDENTIALS)

    assert "pass --live-providers to run it" in output
    assert "2 skipped" in output


def test_asking_for_live_providers_still_requires_test_credentials() -> None:
    """The flag selects them; TEST_* credentials are what actually run them.

    Two independent gates, and this is the other one: with the flag but no test
    credentials, the skip reason changes from 'you did not ask' to 'you have no
    test credentials'.
    """
    output = _run_live_selection("--live-providers")

    assert "pass --live-providers to run it" not in output
    assert "TEST_ANTHROPIC_API_KEY is not set" in output
    assert "TEST_OPENAI_API_KEY / TEST_OPENAI_CHAT_BASE_URL are not set" in output
    assert "2 skipped" in output
