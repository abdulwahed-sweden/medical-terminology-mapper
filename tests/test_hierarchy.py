"""KVÅ hierarchy derived from code structure, and headings as non-assignable.

The KVÅ workbook carries codes but not their ancestors. The classification
encodes its hierarchy in the code itself, so reading it back out is reading the
classification as designed -- but it is recorded as `derived`, never presented
as publisher-supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.normalize.swedish import normalize
from app.retrieval.lexical import lexical_search
from app.retrieval.vector import vector_search
from app.terminology.icd10se import ICD10SE
from app.terminology.kva import KVA, ancestors, derive_chapter, derive_parent

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = get_settings()


# ------------------------------------------------------- structural derivation


@pytest.mark.parametrize(
    ("code", "parent", "chapter"),
    [
        ("FNG02", "FNG", "F"),  # KKÅ: chapter F, section FN, group FNG
        ("AAA00", "AAA", "A"),
        ("EMA00", "EMA", "E"),
        ("EMA", "EM", "E"),  # a group heading sits under its section
        ("AF015", "AF", "AF"),  # KMÅ: two-letter chapter, three digits
        ("SS104", "SS", "SS"),
    ],
)
def test_parent_and_chapter_follow_the_code_structure(code: str, parent: str, chapter: str) -> None:
    assert derive_parent(code) == parent
    assert derive_chapter(code) == chapter


def test_the_full_chain_is_reported_outermost_first() -> None:
    assert ancestors("FNG02") == ["F", "FN", "FNG"]
    assert ancestors("AF015") == ["AF"]
    assert ancestors("EMA00") == ["E", "EM", "EMA"]


def test_a_two_letter_code_is_not_guessed_at() -> None:
    """`FN` is a KKÅ section and `AF` is a KMÅ chapter; the code alone cannot
    say which, so no parent is invented."""
    assert derive_parent("FN") is None
    assert derive_parent("AF") is None


# --------------------------------------------------------------- KVÅ loading


@pytest.fixture(scope="module")
def kva_concepts() -> dict[str, object]:
    loader = KVA()
    out: dict[str, object] = {}
    for name in ("kva_kka_sample.txt", "kva_kma_sample.txt"):
        for concept in loader.load(FIXTURES / name, "2026-sample"):
            out[concept.code] = concept
    return out


def test_kva_parents_are_marked_derived(kva_concepts: dict[str, object]) -> None:
    ema00 = kva_concepts["EMA00"]
    assert ema00.parent_code == "EMA"  # type: ignore[attr-defined]
    # The fixture states this parent in its Överordnad kod column, so it wins.
    assert ema00.parent_source == "column"  # type: ignore[attr-defined]

    af015 = kva_concepts["AF015"]
    assert af015.parent_code == "AF"  # type: ignore[attr-defined]
    assert af015.parent_source == "derived"  # type: ignore[attr-defined]


def test_a_column_parent_is_never_overwritten_by_derivation(
    kva_concepts: dict[str, object],
) -> None:
    """EMA00's structural parent happens to equal its stated one, so use a code
    whose stated parent the structure would not produce."""
    loader = KVA()
    from app.terminology.base import Concept

    concepts = [
        Concept(
            system="kva",
            version="v",
            code="FNG02",
            preferred_term="x",
            parent_code="EMA",
            is_leaf=True,
        ),
    ]
    from app.terminology.base import assign_hierarchy
    from app.terminology.kva import derive_chapter as dc
    from app.terminology.kva import derive_parent as dp

    assign_hierarchy(concepts, derive_parent=dp, derive_chapter=dc)
    assert concepts[0].parent_code == "EMA"
    assert concepts[0].parent_source == "column"
    assert loader.system_id == "kva"


def test_headings_load_as_non_assignable(kva_concepts: dict[str, object]) -> None:
    ema = kva_concepts["EMA"]
    assert ema.assignable is False  # type: ignore[attr-defined]
    assert ema.is_leaf is False  # type: ignore[attr-defined]
    assert kva_concepts["EMA00"].assignable is True  # type: ignore[attr-defined]


# ------------------------------------------------------------- ICD-10-SE


def test_icd10se_parents_come_from_the_column() -> None:
    by_code = {c.code: c for c in ICD10SE().load(FIXTURES / "icd10se_sample.txt", "v")}
    assert by_code["I10"].parent_source == "column"
    assert by_code["I10"].parent_code == "I10-I15"
    assert by_code["I00-I99"].parent_source is None


def test_icd10se_range_headings_are_not_assignable() -> None:
    by_code = {c.code: c for c in ICD10SE().load(FIXTURES / "icd10se_sample.txt", "v")}
    for heading in ("I00-I99", "I10-I15", "I20-I25", "J00-J99", "E00-E90"):
        assert by_code[heading].assignable is False, heading
        assert by_code[heading].is_leaf is False, heading
    assert by_code["I10"].assignable is True


# ------------------------------------------------------ retrieval exclusion


@pytest.mark.requires_db
def test_headings_never_reach_the_lexical_candidates(db_session: Session, kva_loaded: str) -> None:
    """Filtered in SQL, so a heading never consumes a top-K slot.

    "biopsi av tonsill" matches the heading EMA at 0.696 and two codes beneath
    it, so the heading would be returned if nothing excluded it.
    """
    results = lexical_search(
        db_session,
        query=normalize("biopsi av tonsill").normalized,
        system="kva",
        version=kva_loaded,
        top_k=20,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    codes = [c.code for c in results]
    assert "EMA10" in codes  # codes under the heading are returned
    assert "EMA" not in codes  # the heading itself never is


@pytest.mark.requires_db
def test_headings_never_reach_the_vector_candidates(db_session: Session, kva_embedded: str) -> None:
    provider = FakeEmbeddingProvider(dim=SETTINGS.embedding_dim)
    results = vector_search(
        db_session,
        query_vector=provider.embed(["incisioner i tonsiller"])[0],
        system="kva",
        version=kva_embedded,
        provider=provider.provider_id,
        model=provider.model_id,
        top_k=20,
    )
    assert results
    assert all(c.code != "EMA" for c in results)


@pytest.mark.requires_db
def test_icd10se_headings_never_reach_the_candidates(
    db_session: Session, icd10se_loaded: str
) -> None:
    results = lexical_search(
        db_session,
        query=normalize("hypertonisjukdomar högt blodtryck").normalized,
        system="icd10se",
        version=icd10se_loaded,
        top_k=20,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert results
    assert not any("-" in c.code for c in results)


# --------------------------------------------------------------- hierarchy API


@pytest.mark.requires_db
def test_hierarchy_chain_is_resolved_with_titles_where_known(
    db_session: Session, kva_loaded: str, icd10se_loaded: str
) -> None:
    from app.db.models import hierarchy_for

    kva_chain = hierarchy_for(db_session, system="kva", version=kva_loaded, code="EMA00")
    assert [n["code"] for n in kva_chain] == ["E", "EM", "EMA"]
    # EMA is a row in the release and carries a title; E and EM are not.
    assert next(n for n in kva_chain if n["code"] == "EMA")["title"]
    assert next(n for n in kva_chain if n["code"] == "E")["title"] is None

    icd_chain = hierarchy_for(db_session, system="icd10se", version=icd10se_loaded, code="I11.0")
    assert [n["code"] for n in icd_chain] == ["I00-I99", "I10-I15", "I11"]
    assert all(n["title"] for n in icd_chain)


# --------------------------------------------------- Ej huvuddiagnos flag


def test_loader_reads_the_ej_huvuddiagnos_column() -> None:
    """I32 is a manifestation (asterisk) code. The publication states an
    asterisk code "ska alltid dubbelklassificeras med en etiologisk kod", so it
    cannot stand alone as a primary diagnosis."""
    by_code = {c.code: c for c in ICD10SE().load(FIXTURES / "icd10se_sample.txt", "v")}
    assert by_code["I32"].not_primary_diagnosis is True
    assert by_code["I32"].assignable is True  # it is codable, just not primary
    assert by_code["I10"].not_primary_diagnosis is False


def test_kva_has_no_such_marker() -> None:
    """KVÅ is a procedure classification; the column does not exist there."""
    concepts = list(KVA().load(FIXTURES / "kva_kma_sample.txt", "v"))
    assert all(c.not_primary_diagnosis is False for c in concepts)


@pytest.mark.requires_db
def test_the_flag_reaches_retrieval(db_session: Session, icd10se_loaded: str) -> None:
    results = lexical_search(
        db_session,
        query=normalize("perikardit").normalized,
        system="icd10se",
        version=icd10se_loaded,
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    hit = next(c for c in results if c.code == "I32")
    assert hit.not_primary_diagnosis is True


def test_the_flag_is_shown_to_the_reranker() -> None:
    """Surfaced without revising rerank_v1.md -- the prompt already tells the
    model the candidates carry fields."""
    import json

    from app.llm.base import build_rerank_input
    from app.models.candidate import Candidate

    payload = json.loads(
        build_rerank_input(
            "perikardit",
            [
                Candidate(
                    system="icd10se",
                    version="v",
                    code="I32",
                    preferred_term="Perikardit vid sjukdomar som klassificeras på annan plats",
                    not_primary_diagnosis=True,
                )
            ],
            target_system="icd10se",
            terminology_version="v",
        )
    )
    assert payload["candidates"][0]["not_primary_diagnosis"] is True


@pytest.mark.requires_db
def test_a_not_primary_code_can_still_be_accepted(
    db_session: Session, icd10se_embedded: str
) -> None:
    """The validator may legitimately be coding a secondary diagnosis, so the
    flag informs and never blocks."""
    from app.embeddings.fake import FakeEmbeddingProvider
    from app.llm.base import load_prompt
    from app.llm.fake import FakeLLMProvider
    from app.pipeline.map_term import map_term
    from app.validation.decisions import record_decision

    outcome = map_term(
        db_session,
        text="perikardit",
        target_system="icd10se",
        version=icd10se_embedded,
        trace_id="t",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=FakeLLMProvider(),
        prompt=load_prompt(),
    )
    assert outcome.proposal.suggested_code == "I32"
    row = record_decision(
        db_session,
        proposal_id=outcome.proposal.id,
        decision="accept",
        validator_id="coder",
    )
    assert row.final_code == "I32"
