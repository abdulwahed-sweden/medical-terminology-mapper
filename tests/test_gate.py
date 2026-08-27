"""The retrieval gate.

Unit tests drive the rule with synthetic scores; integration tests drive it
through the real pipeline against the fixture. Both matter: the rule has to be
correct in isolation, and it has to be wired to the thing that calls it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.llm.base import load_prompt
from app.llm.fake import FakeLLMProvider
from app.models.candidate import Candidate
from app.pipeline.gate import GATE_ID, GATE_VERSION, evaluate_gate
from app.pipeline.map_term import map_term

SETTINGS = get_settings()


def _cand(
    code: str = "I10",
    *,
    ts_rank: float | None = None,
    strict: float | None = None,
    vector: float | None = None,
) -> Candidate:
    sources = []
    if ts_rank is not None or strict is not None:
        sources.append("lexical")
    if vector is not None:
        sources.append("vector")
    return Candidate(
        system="icd10se",
        version="2026-sample",
        code=code,
        preferred_term="term",
        sources=sources,  # type: ignore[arg-type]
        ts_rank=ts_rank,
        strict_similarity=strict,
        vector_score=vector,
    )


def _gate(candidates: list[Candidate], settings: Settings | None = None):  # type: ignore[no-untyped-def]
    """Evidence-rule tests supply a normal clinical query, so the stopword and
    length guards ahead of it never interfere."""
    return evaluate_gate(
        candidates,
        settings=settings or SETTINGS,
        embedding_provider_id="fake",
        embedding_model_id="fake-hash-v1",
        query="högt blodtryck",
        tokens=["högt", "blodtryck"],
    )


# ----------------------------------------------------------------- the rule


def test_no_candidates_at_all_is_blocked() -> None:
    outcome = _gate([])
    assert outcome.fired is True
    assert "inga kandidater" in outcome.reason


def test_a_full_text_match_admits() -> None:
    """Any real match under the `swedish` configuration is enough.

    Measured: no non-clinical negative produced one, on either corpus.
    """
    outcome = _gate([_cand(ts_rank=0.048, strict=0.2)])
    assert outcome.admitted
    assert "fulltextträff" in outcome.reason


def test_strong_fuzzy_similarity_admits_a_misspelling() -> None:
    """ "hjartinfarkt" has no full-text match at all; it must still get through."""
    outcome = _gate([_cand(ts_rank=0.0, strict=0.625)])
    assert outcome.admitted
    assert "teckenlikhet" in outcome.reason


def test_uniformly_weak_candidates_are_blocked() -> None:
    """The measured worst case: "banan" against the real KVÅ release."""
    outcome = _gate([_cand(ts_rank=0.0, strict=0.571, vector=0.267)])
    assert outcome.fired is True
    assert "0.60" in outcome.reason


def test_vector_similarity_alone_never_admits_by_default() -> None:
    """The bundled embedder's similarities are hash noise.

    A rule that trusted them would be tuned on numbers that mean nothing.
    """
    outcome = _gate([_cand(ts_rank=0.0, strict=0.0, vector=0.99)])
    assert outcome.fired is True
    assert outcome.values["vector_considered"] is False


def test_a_configured_vector_floor_can_admit() -> None:
    """The mechanism exists and is keyed by vector space -- but it is off unless
    a floor is configured for that exact provider/model."""
    settings = SETTINGS.model_copy(update={"gate_vector_floors": {"fake/fake-hash-v1": 0.5}})
    outcome = _gate([_cand(ts_rank=0.0, strict=0.0, vector=0.7)], settings)
    assert outcome.admitted
    assert outcome.values["vector_considered"] is True
    assert outcome.values["vector_floor"] == 0.5

    other = SETTINGS.model_copy(update={"gate_vector_floors": {"openai/other": 0.5}})
    assert _gate([_cand(ts_rank=0.0, strict=0.0, vector=0.7)], other).fired is True


def test_thresholds_are_configurable() -> None:
    weak = [_cand(ts_rank=0.0, strict=0.55)]
    assert _gate(weak).fired is True
    lenient = SETTINGS.model_copy(update={"gate_min_strict_similarity": 0.5})
    assert _gate(weak, lenient).admitted


def test_gate_records_every_value_it_judged() -> None:
    outcome = _gate([_cand(ts_rank=0.1, strict=0.9, vector=0.4)])
    for key in (
        "candidate_count",
        "lexical_hit_count",
        "best_ts_rank",
        "best_strict_similarity",
        "best_rrf",
        "best_vector_score",
        "vector_space",
        "vector_considered",
        "min_ts_rank",
        "min_strict_similarity",
    ):
        assert key in outcome.values, key


# --------------------------------------------------------- through the pipeline

pytestmark_db = pytest.mark.requires_db


def _run(session: Session, version: str, text: str):  # type: ignore[no-untyped-def]
    return map_term(
        session,
        text=text,
        target_system="icd10se",
        version=version,
        trace_id="t",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=FakeLLMProvider(),
        prompt=load_prompt(),
    )


@pytest.mark.requires_db
def test_nonsense_term_produces_no_good_match(db_session: Session, icd10se_embedded: str) -> None:
    """The defect this path exists for: `banan` used to return E11 at 0.90."""
    outcome = _run(db_session, icd10se_embedded, "banan")
    proposal = outcome.proposal

    assert proposal.status == "no_good_match"
    assert proposal.suggested_code is None
    assert proposal.model_confidence is None
    assert proposal.gate_fired is True
    assert proposal.rerank is None  # the model was never asked
    # The evidence is still recorded in full, so the claim stays checkable.
    assert proposal.candidates
    assert proposal.gate_values["best_ts_rank"] == 0.0


@pytest.mark.requires_db
@pytest.mark.parametrize("text", ["banan", "cykel", "pizza", "skruvmejsel", "xyzzy qwerty"])
def test_non_clinical_words_are_all_blocked(
    db_session: Session, icd10se_embedded: str, text: str
) -> None:
    assert _run(db_session, icd10se_embedded, text).proposal.status == "no_good_match"


@pytest.mark.requires_db
def test_a_real_term_still_produces_a_suggestion(
    db_session: Session, icd10se_embedded: str
) -> None:
    outcome = _run(db_session, icd10se_embedded, "högt blodtryck")
    proposal = outcome.proposal

    assert proposal.status == "pending"
    assert proposal.suggested_code == "I10"
    assert proposal.gate_fired is False
    assert proposal.gate_values["best_ts_rank"] > 0
    # The candidate set is unchanged by the gate -- it only decides whether to
    # rerank, never what was retrieved.
    assert [c["code"] for c in proposal.candidates][:2] == ["I10", "I15"]


@pytest.mark.requires_db
def test_a_misspelling_still_gets_through(db_session: Session, icd10se_embedded: str) -> None:
    """Misspelling tolerance is a documented feature; the gate must not cost it."""
    outcome = _run(db_session, icd10se_embedded, "hjartinfarkt")
    assert outcome.proposal.status == "pending"
    assert outcome.proposal.suggested_code == "I21"
    assert outcome.proposal.gate_values["best_ts_rank"] == 0.0
    assert outcome.proposal.gate_values["best_strict_similarity"] >= 0.6


@pytest.mark.requires_db
def test_the_gate_is_recorded_on_successful_proposals_too(
    db_session: Session, icd10se_embedded: str
) -> None:
    """A no-match verdict must be exactly as auditable as a match, which means
    the gate is stamped on every proposal, not only the ones it blocked."""
    proposal = _run(db_session, icd10se_embedded, "astma").proposal
    assert proposal.gate_id == GATE_ID
    assert proposal.gate_version == GATE_VERSION
    assert proposal.gate_fired is False
    assert proposal.gate_values["best_strict_similarity"] > 0
    assert "reason" in proposal.gate_values


# ------------------------------------------------- stopword and length guard


def _gate_q(query: str, tokens: list[str], settings: Settings | None = None):  # type: ignore[no-untyped-def]
    """Gate a query with a candidate that would otherwise pass on evidence."""
    strong = _cand(ts_rank=0.5, strict=1.0)
    return evaluate_gate(
        [strong],
        settings=settings or SETTINGS,
        embedding_provider_id="fake",
        embedding_model_id="fake-hash-v1",
        query=query,
        tokens=tokens,
    )


@pytest.mark.parametrize(
    ("query", "tokens"),
    [("och", ["och"]), ("av", ["av"]), ("och av den", ["och", "av", "den"])],
)
def test_a_query_of_only_stopwords_is_blocked(query: str, tokens: list[str]) -> None:
    """`och` reaches strict_word_similarity 1.000 against the whole terminology
    -- it occurs in thousands of titles -- so the evidence rule alone would let
    it through and the stand-in would suggest a code for a conjunction."""
    outcome = _gate_q(query, tokens)
    assert outcome.fired is True
    assert outcome.reason == "frågan består bara av stoppord"
    assert outcome.values["stopword_token_count"] == len(tokens)
    assert outcome.values["query_token_count"] == len(tokens)


def test_a_too_short_query_is_blocked() -> None:
    outcome = _gate_q("ab", ["ab"])
    assert outcome.fired is True
    assert "för kort" in outcome.reason
    assert outcome.values["query_chars"] == 2
    assert outcome.values["stopword_token_count"] == 0


def test_a_real_three_letter_abbreviation_is_not_blocked_by_the_guard() -> None:
    """AKS (akut koronart syndrom) is a real abbreviation. It may still fail on
    evidence, but it must not be stopped by the length or stopword rule."""
    outcome = _gate_q("aks", ["aks"])
    assert outcome.admitted
    assert "stoppord" not in outcome.reason
    assert "för kort" not in outcome.reason


def test_a_clinical_phrase_is_unaffected() -> None:
    outcome = _gate_q("högt blodtryck", ["högt", "blodtryck"])
    assert outcome.admitted
    assert outcome.values["stopword_token_count"] == 0


def test_a_phrase_mixing_content_and_stopwords_passes() -> None:
    """ "hypertensiv hjärtsjukdom utan hjärtsvikt" contains a stopword; only an
    all-stopword query is blocked."""
    outcome = _gate_q(
        "hypertensiv hjärtsjukdom utan hjärtsvikt",
        ["hypertensiv", "hjärtsjukdom", "utan", "hjärtsvikt"],
    )
    assert outcome.admitted
    assert outcome.values["stopword_token_count"] == 1


def test_the_minimum_length_is_configurable() -> None:
    lenient = SETTINGS.model_copy(update={"gate_min_query_chars": 2})
    assert _gate_q("ab", ["ab"], lenient).admitted


def test_gate_version_is_two() -> None:
    """Existing proposals keep v1; the UI shows the version in Spårbarhet."""
    assert GATE_VERSION == "2"


def test_no_clinical_word_is_in_the_stopword_list() -> None:
    """A false entry here silently blocks a real query."""
    from app.normalize.stopwords_sv import SWEDISH_STOPWORDS

    for word in (
        "akut",
        "kronisk",
        "svår",
        "höger",
        "vänster",
        "övre",
        "nedre",
        "hjärta",
        "blod",
        "astma",
        "infarkt",
        "hypertoni",
        "smärta",
        "feber",
    ):
        assert word not in SWEDISH_STOPWORDS, word
