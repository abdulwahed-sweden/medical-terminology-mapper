"""The OpenAI-compatible embedding provider, against a stubbed transport.

This path has never executed. Phase 3's embeddings arm depends on it entirely,
and its first live call would otherwise be inside a measurement run, where a
mis-ordered batch or a dimension mismatch reads as a bad result rather than a
bad setup.

Nothing here touches the network -- the transport is stubbed, exactly as the
LLM provider's tests do it. That is the "provider implementation test with a
stubbed transport" case the isolation rules allow, and it is what makes the one
live smoke test in `test_live_embeddings.py` a confirmation rather than a first
attempt.

The two tests worth reading are the reordering one and the dimension one. Both
guard failures that are silent: vectors attached to the wrong concepts, and a
model whose width does not match a fixed-width pgvector column.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.embeddings.base import EmbeddingError
from app.embeddings.openai_compat import OpenAICompatEmbeddingProvider

DIM = 4


def _provider(**overrides: Any) -> OpenAICompatEmbeddingProvider:
    kwargs: dict[str, Any] = {
        "base_url": "https://example.invalid/v1/",
        "api_key": "test-key",
        "model_id": "text-embedding-3-small",
        "dim": DIM,
    }
    kwargs.update(overrides)
    return OpenAICompatEmbeddingProvider(**kwargs)


def _stub(monkeypatch: pytest.MonkeyPatch, body: Any, status: int = 200) -> list[dict[str, Any]]:
    """Capture what the provider sends, and answer with `body`."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _vector(fill: float, dim: int = DIM) -> list[float]:
    return [fill] * dim


def test_it_sends_the_model_and_every_text_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(
        monkeypatch,
        {
            "data": [
                {"index": 0, "embedding": _vector(0.1)},
                {"index": 1, "embedding": _vector(0.2)},
            ]
        },
    )

    vectors = _provider().embed(["hypertoni", "astma"])

    assert vectors == [_vector(0.1), _vector(0.2)]
    assert calls[0]["url"] == "https://example.invalid/v1/embeddings"
    assert calls[0]["json"] == {
        "model": "text-embedding-3-small",
        "input": ["hypertoni", "astma"],
    }
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_a_trailing_slash_on_the_base_url_does_not_double_up() -> None:
    assert (
        _provider(base_url="https://example.invalid/v1/").base_url == "https://example.invalid/v1"
    )


def test_no_api_key_means_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-hosted endpoints often want no auth at all, and sending
    `Bearer None` is worse than sending nothing."""
    calls = _stub(monkeypatch, {"data": [{"index": 0, "embedding": _vector(0.1)}]})

    _provider(api_key=None).embed(["hypertoni"])

    assert "Authorization" not in calls[0]["headers"]


def test_an_empty_batch_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedding a full release is a paid call per batch; an empty one should
    cost nothing rather than a round trip that returns nothing."""
    calls = _stub(monkeypatch, {"data": []})

    assert _provider().embed([]) == []
    assert calls == []


def test_vectors_are_returned_in_the_order_asked_for_not_the_order_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The silent one.

    The API does not guarantee response order, and `scripts/embed_terminology.py`
    zips these vectors against the concept rows it asked about. If a reordered
    response were taken at face value, every concept in the batch would be stored
    with another concept's vector -- and nothing would raise. Retrieval would
    just be quietly, confidently wrong.
    """
    calls = _stub(
        monkeypatch,
        {
            "data": [
                {"index": 2, "embedding": _vector(0.3)},
                {"index": 0, "embedding": _vector(0.1)},
                {"index": 1, "embedding": _vector(0.2)},
            ]
        },
    )

    vectors = _provider().embed(["first", "second", "third"])

    assert calls[0]["json"]["input"] == ["first", "second", "third"]
    assert vectors == [_vector(0.1), _vector(0.2), _vector(0.3)]


def test_a_dimension_mismatch_is_refused_and_says_it_needs_a_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The expensive one.

    The pgvector column is typed with a fixed width, so a 3072-wide model does
    not fail on the first insert of a long embedding run -- it fails after the
    request has already been paid for. Catching it in the provider, on the first
    batch, is the difference between a stopped run and a bill.
    """
    _stub(monkeypatch, {"data": [{"index": 0, "embedding": _vector(0.1, dim=3072)}]})

    with pytest.raises(EmbeddingError) as exc:
        _provider().embed(["hypertoni"])

    message = str(exc.value)
    assert "3072" in message
    assert str(DIM) in message
    assert "migration" in message


def test_a_short_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer vectors than texts would silently misalign the zip downstream."""
    _stub(monkeypatch, {"data": [{"index": 0, "embedding": _vector(0.1)}]})

    with pytest.raises(EmbeddingError, match="asked for 2 embeddings, got 1"):
        _provider().embed(["hypertoni", "astma"])


@pytest.mark.parametrize(
    "body",
    [
        {"unexpected": True},
        {"data": [{"index": 0}]},
        {"data": [{"index": 0, "embedding": ["not-a-number"]}]},
        {"data": "not a list"},
    ],
    ids=["no-data", "no-embedding", "non-numeric", "data-not-a-list"],
)
def test_a_response_of_the_wrong_shape_is_refused(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    _stub(monkeypatch, body)

    with pytest.raises(EmbeddingError, match="unexpected embeddings response shape"):
        _provider().embed(["hypertoni"])


def test_an_http_error_becomes_an_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 from a mistyped key should name the provider, not surface an httpx
    traceback from four frames down."""
    _stub(monkeypatch, {"error": "unauthorized"}, status=401)

    with pytest.raises(EmbeddingError, match="embedding request failed"):
        _provider().embed(["hypertoni"])


def test_a_transport_failure_becomes_an_embedding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("name or service not known")

    monkeypatch.setattr(httpx, "post", explode)

    with pytest.raises(EmbeddingError, match="embedding request failed"):
        _provider().embed(["hypertoni"])
