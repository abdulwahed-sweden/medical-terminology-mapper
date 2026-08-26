"""Retrieval tests: lexical, vector, and the merge of the two.

All of these need a real PostgreSQL -- the `swedish` text search configuration,
pg_trgm and pgvector are the subject, not an implementation detail.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.normalize.swedish import normalize
from app.retrieval.lexical import lexical_search

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
    """"Högt blodtryck" is an Innefattar term on I10, not its preferred term."""
    codes = _lexical(db_session, "högt blodtryck", icd10se_loaded)
    assert "I10" in codes


def test_misspelling_is_tolerated_by_trigrams(
    db_session: Session, icd10se_loaded: str
) -> None:
    """"hjartinfarkt" lacks the ä; full-text search alone would find nothing."""
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


def test_code_intervals_are_never_candidates(
    db_session: Session, icd10se_loaded: str
) -> None:
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
