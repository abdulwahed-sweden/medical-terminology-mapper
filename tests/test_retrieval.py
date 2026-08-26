"""Retrieval tests: lexical, vector, and the merge of the two.

All of these need a real PostgreSQL -- the `swedish` text search configuration,
pg_trgm and pgvector are the subject, not an implementation detail.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.models.candidate import Candidate
from app.normalize.swedish import normalize
from app.retrieval.lexical import lexical_search
from app.retrieval.merge import merge_candidates
from app.retrieval.vector import vector_search

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()


def _lexical(session: Session, query: str, version: str, **kwargs: object) -> list[str]:
    params: dict[str, object] = {
        "top_k": 10,
        "trigram_threshold": SETTINGS.trigram_threshold,
    }
    params.update(kwargs)
    results = lexical_search(
        session,
        query=normalize(query).normalized,
        system="icd10se",
        version=version,
        **params,  # type: ignore[arg-type]
    )
    return [c.code for c in results]


# ------------------------------------------------------------------- lexical


def test_exact_term_is_retrieved(db_session: Session, icd10se_loaded: str) -> None:
    assert "J45" in _lexical(db_session, "astma", icd10se_loaded)


def test_inclusion_term_retrieves_its_code(db_session: Session, icd10se_loaded: str) -> None:
    """ "Högt blodtryck" is an Innefattar term on I10, not its preferred term."""
    codes = _lexical(db_session, "högt blodtryck", icd10se_loaded)
    assert "I10" in codes


def test_misspelling_is_tolerated_by_trigrams(db_session: Session, icd10se_loaded: str) -> None:
    """ "hjartinfarkt" lacks the ä; full-text search alone would find nothing."""
    codes = _lexical(db_session, "hjartinfarkt", icd10se_loaded)
    assert "I21" in codes
    assert "I21.9" in codes


def test_full_text_and_trigram_scores_are_both_reported(
    db_session: Session, icd10se_loaded: str
) -> None:
    results = lexical_search(
        db_session,
        query="astma",
        system="icd10se",
        version=icd10se_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    top = results[0]
    assert top.ts_rank is not None and top.trgm_similarity is not None
    # The reported lexical score is the stronger of the two signals.
    assert top.lexical_score == pytest.approx(max(top.ts_rank, top.trgm_similarity))
    assert top.sources == ["lexical"]
    assert top.lexical_rank == 1


def test_results_are_ordered_by_score(db_session: Session, icd10se_loaded: str) -> None:
    results = lexical_search(
        db_session,
        query=normalize("hypertoni med njursjukdom").normalized,
        system="icd10se",
        version=icd10se_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    scores = [c.lexical_score for c in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].code == "I12"  # the exact title match wins
    assert [c.lexical_rank for c in results] == list(range(1, len(results) + 1))


def test_code_intervals_are_never_candidates(db_session: Session, icd10se_loaded: str) -> None:
    """I10-I15 is a section heading. Proposing it as a diagnosis code is a bug."""
    codes = _lexical(db_session, "hypertonisjukdomar högt blodtryck", icd10se_loaded)
    assert codes  # the query does match the section's text
    assert not any("-" in code for code in codes)
    assert "I10-I15" not in codes


def test_top_k_is_respected(db_session: Session, icd10se_loaded: str) -> None:
    codes = _lexical(db_session, "hypertoni", icd10se_loaded, top_k=3)
    assert len(codes) <= 3


def test_search_is_scoped_to_system_and_version(
    db_session: Session, icd10se_loaded: str, kva_loaded: str
) -> None:
    """Both systems are loaded; an ICD-10-SE search must not return KVÅ codes."""
    icd_codes = _lexical(db_session, "blodtryck", icd10se_loaded)
    assert icd_codes
    assert all(not code.startswith("AF") for code in icd_codes)

    other_version = lexical_search(
        db_session,
        query="astma",
        system="icd10se",
        version="1999-does-not-exist",
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert other_version == []


def test_kva_is_searchable_in_its_own_system(db_session: Session, kva_loaded: str) -> None:
    results = lexical_search(
        db_session,
        query=normalize("blodtrycksmätning").normalized,
        system="kva",
        version=kva_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    codes = [c.code for c in results]
    assert "AF015" in codes


def test_empty_query_returns_nothing(db_session: Session, icd10se_loaded: str) -> None:
    assert _lexical(db_session, "   ", icd10se_loaded) == []


def test_nonsense_query_returns_nothing(db_session: Session, icd10se_loaded: str) -> None:
    assert _lexical(db_session, "qzxwvk lorem ipsum", icd10se_loaded) == []


# -------------------------------------------------------------------- vector


def test_vector_search_returns_neighbours(
    db_session: Session, icd10se_embedded: str, embedding_provider: FakeEmbeddingProvider
) -> None:
    query_vector = embedding_provider.embed([normalize("högt blodtryck").normalized])[0]
    results = vector_search(
        db_session,
        query_vector=query_vector,
        system="icd10se",
        version=icd10se_embedded,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=5,
    )
    assert results
    assert "I10" in [c.code for c in results]
    assert results[0].sources == ["vector"]
    assert results[0].vector_rank == 1


def test_vector_scores_are_similarities_not_distances(
    db_session: Session, icd10se_embedded: str, embedding_provider: FakeEmbeddingProvider
) -> None:
    """Larger must mean better, like every other score in the pipeline."""
    query_vector = embedding_provider.embed([normalize("astma").normalized])[0]
    results = vector_search(
        db_session,
        query_vector=query_vector,
        system="icd10se",
        version=icd10se_embedded,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=10,
    )
    scores = [c.vector_score for c in results]
    assert all(score is not None and -1.0 <= score <= 1.0 for score in scores)
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]


def test_vector_search_is_scoped_to_its_vector_space(
    db_session: Session, icd10se_embedded: str, embedding_provider: FakeEmbeddingProvider
) -> None:
    """Vectors from a different model live in an unrelated space.

    Silently searching across models would return confident nonsense, so the
    filter must exclude rather than fall back.
    """
    query_vector = embedding_provider.embed(["astma"])[0]
    results = vector_search(
        db_session,
        query_vector=query_vector,
        system="icd10se",
        version=icd10se_embedded,
        provider=embedding_provider.provider_id,
        model="some-other-model",
        top_k=5,
    )
    assert results == []


def test_vector_search_excludes_code_intervals(
    db_session: Session, icd10se_embedded: str, embedding_provider: FakeEmbeddingProvider
) -> None:
    query_vector = embedding_provider.embed(["cirkulationsorganens sjukdomar"])[0]
    results = vector_search(
        db_session,
        query_vector=query_vector,
        system="icd10se",
        version=icd10se_embedded,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=20,
    )
    assert results
    assert not any("-" in c.code for c in results)


# --------------------------------------------------------------------- merge


def _candidate(code: str, **kwargs: object) -> Candidate:
    base: dict[str, object] = {
        "system": "icd10se",
        "version": "2026-sample",
        "code": code,
        "preferred_term": f"term for {code}",
    }
    base.update(kwargs)
    return Candidate(**base)  # type: ignore[arg-type]


def test_merge_deduplicates_and_keeps_both_scores() -> None:
    lexical = [_candidate("I10", sources=["lexical"], lexical_score=0.9, lexical_rank=1)]
    vector = [_candidate("I10", sources=["vector"], vector_score=0.42, vector_rank=3)]

    merged = merge_candidates(lexical, vector)

    assert len(merged) == 1
    only = merged[0]
    assert only.sources == ["lexical", "vector"]
    assert only.lexical_score == 0.9
    assert only.vector_score == 0.42
    assert only.lexical_rank == 1
    assert only.vector_rank == 3


def test_merge_leaves_absent_scores_null() -> None:
    merged = merge_candidates(
        [_candidate("I10", sources=["lexical"], lexical_score=0.9, lexical_rank=1)],
        [_candidate("I15", sources=["vector"], vector_score=0.5, vector_rank=1)],
    )
    by_code = {c.code: c for c in merged}
    assert by_code["I10"].vector_score is None
    assert by_code["I10"].vector_rank is None
    assert by_code["I15"].lexical_score is None
    assert by_code["I15"].ts_rank is None


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    """A concept both stages ranked well beats one only a single stage liked,
    even when that single stage ranked it first."""
    lexical = [
        _candidate("BOTH", sources=["lexical"], lexical_score=0.5, lexical_rank=2),
        _candidate("LEX_ONLY", sources=["lexical"], lexical_score=0.99, lexical_rank=1),
    ]
    vector = [
        _candidate("BOTH", sources=["vector"], vector_score=0.5, vector_rank=2),
        _candidate("VEC_ONLY", sources=["vector"], vector_score=0.99, vector_rank=1),
    ]

    merged = merge_candidates(lexical, vector, rrf_k=60)

    assert merged[0].code == "BOTH"
    assert merged[0].fused_score == pytest.approx(2 / 62)
    assert merged[1].fused_score == pytest.approx(1 / 61)


def test_merge_respects_the_cap() -> None:
    lexical = [
        _candidate(f"X{i:02d}", sources=["lexical"], lexical_score=1.0 - i / 100, lexical_rank=i)
        for i in range(1, 21)
    ]
    assert len(merge_candidates(lexical, [], cap=15)) == 15
    assert len(merge_candidates(lexical, [])) == 20


def test_merge_is_deterministic_for_tied_scores() -> None:
    """Identical inputs must produce an identical ordering.

    A proposal whose candidate order varies run to run is not reproducible, and
    a non-reproducible proposal is a poor audit record.
    """
    lexical = [
        _candidate("I15", sources=["lexical"], lexical_score=1.0, lexical_rank=1),
        _candidate("I10", sources=["lexical"], lexical_score=1.0, lexical_rank=1),
    ]
    first = [c.code for c in merge_candidates(lexical, [])]
    second = [c.code for c in merge_candidates(list(reversed(lexical)), [])]
    assert first == second == ["I10", "I15"]


def test_merge_of_two_empty_lists() -> None:
    assert merge_candidates([], []) == []


def test_merge_does_not_mutate_its_inputs() -> None:
    lexical = [_candidate("I10", sources=["lexical"], lexical_score=0.9, lexical_rank=1)]
    vector = [_candidate("I10", sources=["vector"], vector_score=0.4, vector_rank=1)]

    merge_candidates(lexical, vector)

    assert lexical[0].sources == ["lexical"]
    assert lexical[0].vector_score is None
    assert lexical[0].fused_score == 0.0


def test_full_retrieval_path_merges_both_stages(
    db_session: Session, icd10se_embedded: str, embedding_provider: FakeEmbeddingProvider
) -> None:
    """End to end: both stages contribute, and the overlap rises to the top."""
    query = normalize("högt blodtryck").normalized
    lexical = lexical_search(
        db_session,
        query=query,
        system="icd10se",
        version=icd10se_embedded,
        top_k=20,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    vector = vector_search(
        db_session,
        query_vector=embedding_provider.embed([query])[0],
        system="icd10se",
        version=icd10se_embedded,
        provider=embedding_provider.provider_id,
        model=embedding_provider.model_id,
        top_k=20,
    )
    merged = merge_candidates(lexical, vector, rrf_k=SETTINGS.rrf_k, cap=15)

    assert merged[0].code == "I10"
    assert merged[0].sources == ["lexical", "vector"]
    # Vector-only candidates the lexical stage never saw are still present.
    assert any(c.sources == ["vector"] for c in merged)


# ------------------------------------------------- field attribution


def test_matched_field_attributes_a_title_hit(db_session: Session, icd10se_loaded: str) -> None:
    results = lexical_search(
        db_session,
        query="astma",
        system="icd10se",
        version=icd10se_loaded,
        top_k=5,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert results[0].code == "J45"
    assert results[0].matched_field == "title"


def test_matched_field_attributes_a_synonym_hit(db_session: Session, icd10se_loaded: str) -> None:
    """ "Högt blodtryck" is an Innefattar term on I10, not its preferred term."""
    results = lexical_search(
        db_session,
        query=normalize("högt blodtryck").normalized,
        system="icd10se",
        version=icd10se_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    i10 = next(c for c in results if c.code == "I10")
    assert i10.matched_field in {"title", "synonym"}


def test_strict_similarity_is_reported(db_session: Session, icd10se_loaded: str) -> None:
    """The gate reads this separately from ts_rank, so it must be populated."""
    results = lexical_search(
        db_session,
        query="hjartinfarkt",
        system="icd10se",
        version=icd10se_loaded,
        top_k=5,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert results
    assert results[0].strict_similarity is not None
    assert results[0].strict_similarity > 0.6
    assert results[0].ts_rank == 0.0  # a misspelling has no full-text match


def test_strict_word_similarity_rejects_a_mid_word_extent(
    db_session: Session, kva_loaded: str
) -> None:
    """The reason for `strict_word_similarity`.

    Plain `word_similarity` matched "banan" against "An*nan ban*dnings..." at
    0.833 on the real release, above a legitimate misspelling. The strict
    variant requires word boundaries.
    """
    results = lexical_search(
        db_session,
        query="banan",
        system="kva",
        version=kva_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert all((c.strict_similarity or 0.0) < 0.6 for c in results)


# ------------------------------------- description indexing (the PTCA cases)


def test_description_term_retrieves_its_code(db_session: Session, kva_loaded: str) -> None:
    """FNG02's title is "Perkutan transluminal koronarangioplastik (PTCA)".

    "ballongdilatation" appears only in its Beskrivning. Before descriptions
    were indexed this was a retrieval miss, and no reranking could recover it.
    """
    results = lexical_search(
        db_session,
        query="ballongdilatation",
        system="kva",
        version=kva_loaded,
        top_k=15,
        trigram_threshold=SETTINGS.trigram_threshold,
        index_descriptions=True,
    )
    hit = next((c for c in results if c.code == "FNG02"), None)
    assert hit is not None
    assert hit.matched_field == "description"


def test_description_term_misses_when_descriptions_are_off(
    db_session: Session, kva_loaded: str
) -> None:
    results = lexical_search(
        db_session,
        query="ballongdilatation",
        system="kva",
        version=kva_loaded,
        top_k=15,
        trigram_threshold=SETTINGS.trigram_threshold,
        index_descriptions=False,
    )
    assert all(c.code != "FNG02" for c in results)


@pytest.mark.parametrize("query", ["PTCA", "koronarangioplastik"])
def test_title_terms_find_the_code_either_way(
    db_session: Session, kva_loaded: str, query: str
) -> None:
    """Turning descriptions on must not disturb what already worked."""
    for descriptions in (True, False):
        results = lexical_search(
            db_session,
            query=normalize(query).normalized,
            system="kva",
            version=kva_loaded,
            top_k=15,
            trigram_threshold=SETTINGS.trigram_threshold,
            index_descriptions=descriptions,
        )
        assert results[0].code == "FNG02", (query, descriptions)
        assert results[0].matched_field == "title"
