"""Real provider tests.

Structure and error handling are tested offline with a stubbed transport; the
live smoke tests run only when a key is present, and skip otherwise, so a clone
with no credentials still gets a fully green suite.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.llm import build_llm_provider
from app.llm.base import (
    RERANK_JSON_SCHEMA,
    LLMError,
    LLMProvider,
    build_rerank_input,
    enforce_candidate_codes,
    load_prompt,
    parse_rerank_payload,
)
from app.llm.openai_compat import OpenAICompatLLMProvider
from app.models.candidate import Candidate

CANDIDATES = [
    Candidate(
        system="icd10se",
        version="2026-sample",
        code="I10",
        preferred_term="Essentiell hypertoni (högt blodtryck utan känd orsak)",
        synonyms=["Hypertonia essentialis", "Högt blodtryck"],
        sources=["lexical", "vector"],
        lexical_score=1.0,
        vector_score=0.42,
    ),
    Candidate(
        system="icd10se",
        version="2026-sample",
        code="I15.9",
        preferred_term="Sekundär hypertoni, ospecificerad",
        sources=["lexical"],
        lexical_score=0.6,
    ),
]


# ------------------------------------------------------------ model input


def test_rerank_input_is_json_with_the_needed_fields() -> None:
    payload = json.loads(
        build_rerank_input(
            "högt blodtryck",
            CANDIDATES,
            target_system="icd10se",
            terminology_version="2026-sample",
        )
    )
    assert payload["query"] == "högt blodtryck"
    assert payload["target_system"] == "icd10se"
    assert payload["terminology_version"] == "2026-sample"
    assert [c["code"] for c in payload["candidates"]] == ["I10", "I15.9"]
    assert payload["candidates"][0]["retrieval"]["lexical_score"] == 1.0
    assert "Högt blodtryck" in payload["candidates"][0]["synonyms"]


def test_rerank_input_truncates_long_synonym_lists() -> None:
    """A concept with forty inclusion terms must not crowd out the others."""
    from app.llm.base import MAX_SYNONYMS_SHOWN

    crowded = CANDIDATES[0].model_copy(update={"synonyms": [f"s{i}" for i in range(40)]})
    payload = json.loads(
        build_rerank_input("q", [crowded], target_system="icd10se", terminology_version="v")
    )
    assert len(payload["candidates"][0]["synonyms"]) == MAX_SYNONYMS_SHOWN


def test_schema_and_parser_agree() -> None:
    """The schema a provider enforces server-side must accept exactly what the
    parser accepts -- otherwise a reply passes one and fails the other."""
    valid = {
        "ranked": [{"code": "I10", "confidence": 0.9, "reason": "r"}],
        "no_good_match": False,
    }
    assert parse_rerank_payload(json.dumps(valid))

    assert RERANK_JSON_SCHEMA["additionalProperties"] is False
    assert set(RERANK_JSON_SCHEMA["required"]) == {"ranked", "no_good_match"}  # type: ignore[arg-type]
    item = RERANK_JSON_SCHEMA["properties"]["ranked"]["items"]  # type: ignore[index]
    assert set(item["required"]) == {"code", "confidence", "reason"}
    assert item["additionalProperties"] is False


# ------------------------------------------------- openai_compat, stubbed


def _stub_post(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, **kwargs})
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_openai_compat_sends_the_prompt_and_parses_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = json.dumps(
        {
            "ranked": [{"code": "I10", "confidence": 0.9, "reason": "matchar"}],
            "no_good_match": False,
        }
    )
    sent = _stub_post(
        monkeypatch, {"choices": [{"message": {"content": reply}, "finish_reason": "stop"}]}
    )

    provider = OpenAICompatLLMProvider(
        base_url="https://example.invalid/v1/", api_key="k", model_id="m"
    )
    result = provider.rerank("högt blodtryck", CANDIDATES, load_prompt())

    assert result.ranked[0].code == "I10"
    request = sent[0]
    assert request["url"] == "https://example.invalid/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer k"
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["messages"][0]["role"] == "system"
    assert "Only use codes from" in request["json"]["messages"][0]["content"]


def test_openai_compat_repairs_a_malformed_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = [
        "sorry, here is some prose",
        json.dumps({"ranked": [], "no_good_match": True}),
    ]
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append(kwargs)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": replies[len(sent) - 1]}, "finish_reason": "stop"}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    provider = OpenAICompatLLMProvider(
        base_url="https://example.invalid/v1", api_key=None, model_id="m"
    )
    result = provider.rerank("q", CANDIDATES, load_prompt())

    assert result.no_good_match is True
    assert len(sent) == 2
    # The repair instruction rides along with the second request.
    assert "not valid JSON" in sent[1]["json"]["messages"][1]["content"]


def test_openai_compat_reports_a_truncated_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A length-truncated reply must not be fed to the parser as if it were an
    ordinary formatting mistake."""
    _stub_post(
        monkeypatch,
        {"choices": [{"message": {"content": '{"ranked": ['}, "finish_reason": "length"}]},
    )
    provider = OpenAICompatLLMProvider(
        base_url="https://example.invalid/v1", api_key=None, model_id="m"
    )
    with pytest.raises(LLMError, match="token limit"):
        provider.rerank("q", CANDIDATES, load_prompt())


def test_openai_compat_reports_an_unexpected_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_post(monkeypatch, {"unexpected": True})
    provider = OpenAICompatLLMProvider(
        base_url="https://example.invalid/v1", api_key=None, model_id="m"
    )
    with pytest.raises(LLMError, match="unexpected chat completion response shape"):
        provider.rerank("q", CANDIDATES, load_prompt())


# ------------------------------------------------------------- factory


def test_factory_returns_the_fake_by_default() -> None:
    assert build_llm_provider(Settings()).provider_id == "fake"


def test_anthropic_without_a_key_is_refused() -> None:
    settings = Settings(llm_provider="anthropic", anthropic_api_key=None)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(settings)


def test_unknown_provider_is_refused() -> None:
    settings = Settings()
    settings = settings.model_copy(update={"llm_provider": "telepathy"})
    with pytest.raises(ValueError, match="unknown LLM provider"):
        build_llm_provider(settings)


# --------------------------------------------------------- live smoke tests


@pytest.mark.requires_api_key
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping the live Anthropic smoke test",
)
def test_anthropic_live_smoke() -> None:
    settings = Settings(
        llm_provider="anthropic",
        llm_model=os.environ.get("LLM_MODEL", "claude-opus-5"),
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    provider = build_llm_provider(settings)
    assert isinstance(provider, LLMProvider)

    result = provider.rerank("högt blodtryck", CANDIDATES, load_prompt())
    filtered, dropped = enforce_candidate_codes(result, CANDIDATES)

    assert dropped == [], f"model returned codes outside the candidate list: {dropped}"
    assert filtered.ranked, "model ranked nothing"
    assert filtered.ranked[0].code == "I10"
    assert all(0.0 <= r.confidence <= 1.0 for r in filtered.ranked)


@pytest.mark.requires_api_key
@pytest.mark.skipif(
    not (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_CHAT_BASE_URL")),
    reason="OPENAI_API_KEY / OPENAI_CHAT_BASE_URL are not set; skipping the live test",
)
def test_openai_compat_live_smoke() -> None:
    settings = Settings(
        llm_provider="openai_compat",
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_chat_base_url=os.environ["OPENAI_CHAT_BASE_URL"],
    )
    provider = build_llm_provider(settings)
    result = provider.rerank("högt blodtryck", CANDIDATES, load_prompt())
    filtered, dropped = enforce_candidate_codes(result, CANDIDATES)
    assert dropped == []
    assert filtered.ranked
