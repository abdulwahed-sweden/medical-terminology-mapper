"""Reranking: prompt versioning, strict parsing, repair retry, and the
hallucinated-code guard.

No database and no network: this is all pure logic, and it is the part a
reviewer most needs to be able to check.
"""

from __future__ import annotations

import pytest

from app.llm.base import (
    REPAIR_INSTRUCTION,
    LLMProvider,
    RerankFailed,
    RerankParseError,
    enforce_candidate_codes,
    load_prompt,
    parse_rerank_payload,
    run_with_one_repair,
)
from app.llm.fake import FakeLLMProvider
from app.models.candidate import Candidate
from app.models.rerank import RankedCode, RerankResult


def _candidate(code: str, lexical_score: float | None = None) -> Candidate:
    return Candidate(
        system="icd10se",
        version="2026-sample",
        code=code,
        preferred_term=f"term for {code}",
        sources=["lexical"],
        lexical_score=lexical_score,
    )


# ------------------------------------------------------------ prompt version


def test_prompt_is_loaded_and_hashed() -> None:
    spec = load_prompt("rerank_v1")
    assert spec.prompt_id == "rerank_v1"
    assert len(spec.sha256) == 64
    assert "Only use codes from `candidates`" in spec.text


def test_prompt_hash_is_stable_across_calls() -> None:
    assert load_prompt("rerank_v1").sha256 == load_prompt("rerank_v1").sha256


def test_prompt_hash_tracks_content(tmp_path: object) -> None:
    """The hash must change when the file changes -- that is its whole job."""
    import hashlib

    from app.llm.base import PROMPT_DIR

    raw = (PROMPT_DIR / "rerank_v1.md").read_bytes()
    assert load_prompt("rerank_v1").sha256 == hashlib.sha256(raw).hexdigest()
    assert hashlib.sha256(raw + b"\n").hexdigest() != load_prompt("rerank_v1").sha256


def test_unknown_prompt_id_is_an_error() -> None:
    with pytest.raises(Exception, match="no prompt file"):
        load_prompt("does_not_exist_v9")


# ------------------------------------------------------------ strict parsing


def test_valid_payload_parses() -> None:
    result = parse_rerank_payload(
        '{"ranked": [{"code": "I10", "confidence": 0.91, "reason": "matchar"}],'
        ' "no_good_match": false, "notes": "kort"}'
    )
    assert result.ranked[0].code == "I10"
    assert result.ranked[0].confidence == 0.91
    assert result.no_good_match is False


def test_code_fence_is_tolerated() -> None:
    """Models add fences habitually; refusing would waste the one retry on
    formatting no human ever sees."""
    result = parse_rerank_payload('```json\n{"ranked": [], "no_good_match": true}\n```')
    assert result.no_good_match is True


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("", "empty"),
        ("not json at all", "not JSON"),
        ("[1, 2, 3]", "not an object"),
        (
            '{"ranked": [{"code": "I10", "confidence": 1.4, "reason": "x"}],'
            ' "no_good_match": false}',
            "confidence out of range",
        ),
        (
            '{"ranked": [{"code": "I10", "confidence": -0.1, "reason": "x"}],'
            ' "no_good_match": false}',
            "negative confidence",
        ),
        (
            '{"ranked": [{"code": "I10", "confidence": 0.5}], "no_good_match": false}',
            "missing reason",
        ),
        (
            '{"ranked": [{"code": "I10", "confidence": 0.5, "reason": "x",'
            ' "extra": 1}], "no_good_match": false}',
            "extra key",
        ),
        ('{"ranked": [], "no_good_match": false, "surprise": true}', "extra top-level key"),
    ],
)
def test_malformed_payloads_are_rejected(payload: str, why: str) -> None:
    with pytest.raises(RerankParseError):
        parse_rerank_payload(payload)


# -------------------------------------------------------------- repair retry


def test_repair_retry_recovers_from_one_bad_reply() -> None:
    calls: list[str | None] = []

    def call(repair: str | None) -> str:
        calls.append(repair)
        if repair is None:
            return "here you go: not actually json"
        return (
            '{"ranked": [{"code": "I10", "confidence": 0.9, "reason": "ok"}],'
            ' "no_good_match": false}'
        )

    result = run_with_one_repair(call)

    assert result.ranked[0].code == "I10"
    assert calls == [None, REPAIR_INSTRUCTION]


def test_repair_is_attempted_exactly_once() -> None:
    """Two failures fail the proposal. A model that cannot produce the schema
    twice will not produce it on the fifth try, and an unbounded loop turns a
    bad response into a bill and a hung request."""
    calls: list[str | None] = []

    def call(repair: str | None) -> str:
        calls.append(repair)
        return "still not json"

    with pytest.raises(RerankFailed):
        run_with_one_repair(call)

    assert calls == [None, REPAIR_INSTRUCTION]


def test_a_good_first_reply_is_not_retried() -> None:
    calls: list[str | None] = []

    def call(repair: str | None) -> str:
        calls.append(repair)
        return '{"ranked": [], "no_good_match": true}'

    run_with_one_repair(call)
    assert calls == [None]


# ------------------------------------------------------ hallucinated codes


def test_codes_outside_the_candidate_list_are_dropped() -> None:
    candidates = [_candidate("I10"), _candidate("I15.9")]
    result = RerankResult(
        ranked=[
            RankedCode(code="I10", confidence=0.9, reason="a"),
            # Plausible-looking, real in ICD-10-SE, but never retrieved here.
            RankedCode(code="I11.9", confidence=0.8, reason="b"),
            RankedCode(code="I15.9", confidence=0.1, reason="c"),
        ],
        no_good_match=False,
    )

    filtered, dropped = enforce_candidate_codes(result, candidates)

    assert [r.code for r in filtered.ranked] == ["I10", "I15.9"]
    assert dropped == ["I11.9"]


def test_hallucinated_code_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        enforce_candidate_codes(
            RerankResult(
                ranked=[RankedCode(code="Z99.9", confidence=0.9, reason="x")],
                no_good_match=False,
            ),
            [_candidate("I10")],
        )
    events = [r for r in caplog.records if r.getMessage() == "hallucinated_code"]
    assert len(events) == 1
    assert events[0].code == "Z99.9"


def test_dropping_every_code_sets_no_good_match() -> None:
    """An empty ranking would read as "the model had no opinion". It had one;
    all of it was unusable."""
    filtered, dropped = enforce_candidate_codes(
        RerankResult(
            ranked=[RankedCode(code="Z99.9", confidence=0.9, reason="x")],
            no_good_match=False,
        ),
        [_candidate("I10")],
    )
    assert filtered.ranked == []
    assert filtered.no_good_match is True
    assert dropped == ["Z99.9"]


def test_duplicate_codes_are_dropped() -> None:
    filtered, dropped = enforce_candidate_codes(
        RerankResult(
            ranked=[
                RankedCode(code="I10", confidence=0.9, reason="a"),
                RankedCode(code="I10", confidence=0.4, reason="b"),
            ],
            no_good_match=False,
        ),
        [_candidate("I10")],
    )
    assert [r.code for r in filtered.ranked] == ["I10"]
    assert filtered.ranked[0].confidence == 0.9
    assert dropped == ["I10"]


def test_clean_result_passes_through_untouched() -> None:
    candidates = [_candidate("I10"), _candidate("I15.9")]
    result = RerankResult(
        ranked=[
            RankedCode(code="I10", confidence=0.9, reason="a"),
            RankedCode(code="I15.9", confidence=0.1, reason="b"),
        ],
        no_good_match=False,
        notes="n",
    )
    filtered, dropped = enforce_candidate_codes(result, candidates)
    assert dropped == []
    assert filtered == result


# ------------------------------------------------------------ fake provider


def test_fake_provider_satisfies_the_protocol() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)


def test_fake_provider_ranks_by_lexical_score_descending() -> None:
    candidates = [
        _candidate("I15", lexical_score=0.4),
        _candidate("I10", lexical_score=0.9),
        _candidate("I12", lexical_score=0.6),
    ]
    result = FakeLLMProvider().rerank("högt blodtryck", candidates, load_prompt())
    assert [r.code for r in result.ranked] == ["I10", "I12", "I15"]
    assert result.ranked[0].confidence == 0.90
    assert result.ranked[1].confidence == 0.75
    assert result.no_good_match is False


def test_fake_provider_is_deterministic() -> None:
    candidates = [_candidate("I10", 0.9), _candidate("I15", 0.4)]
    prompt = load_prompt()
    first = FakeLLMProvider().rerank("q", candidates, prompt)
    second = FakeLLMProvider().rerank("q", list(reversed(candidates)), prompt)
    assert first.model_dump() == second.model_dump()


def test_fake_provider_sorts_scoreless_candidates_last() -> None:
    candidates = [_candidate("VEC", None), _candidate("LEX", 0.5)]
    result = FakeLLMProvider().rerank("q", candidates, load_prompt())
    assert [r.code for r in result.ranked] == ["LEX", "VEC"]


def test_fake_provider_reports_no_good_match_for_no_candidates() -> None:
    result = FakeLLMProvider().rerank("q", [], load_prompt())
    assert result.ranked == []
    assert result.no_good_match is True


def test_fake_provider_output_survives_the_guard() -> None:
    candidates = [_candidate("I10", 0.9), _candidate("I15", 0.4)]
    result = FakeLLMProvider().rerank("q", candidates, load_prompt())
    filtered, dropped = enforce_candidate_codes(result, candidates)
    assert dropped == []
    assert len(filtered.ranked) == 2
