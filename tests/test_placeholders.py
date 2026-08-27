"""U-code placeholders, and the evaluation dry run.

The publisher ships 63 U-codes in a separate file: reserved slots that let a new
code be put into use at short notice, as happened with covid-19. They are real
codes, but until one is put into use it stands for nothing, so proposing it
would be wrong.

These tests build their own file rather than adding a U-code to the committed
sample fixture. Which specific U-codes are reserved is a fact about the
separate distribution file, and this repository has not seen it.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.normalize.swedish import normalize
from app.retrieval.lexical import lexical_search
from app.terminology.icd10se import ICD10SE

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = get_settings()


def _file_with_a_u_code(tmp_path: Path) -> Path:
    header = ["Kod", "Giltig från", "Överordnad kod", "Titel"]
    rows = [
        ["I10", "1997-01-01", "", "Essentiell hypertoni"],
        ["U07.1", "2020-04-01", "", "Reserverad kodplats för snabb ibruktagning"],
    ]
    path = tmp_path / "with_u.txt"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle, delimiter="\t", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\r\n"
        )
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_u_codes_are_excluded_by_default(tmp_path: Path) -> None:
    codes = {c.code for c in ICD10SE().load(_file_with_a_u_code(tmp_path), "2026")}
    assert codes == {"I10"}


def test_u_codes_load_when_asked_for_and_are_marked(tmp_path: Path) -> None:
    concepts = {
        c.code: c for c in ICD10SE(include_u_codes=True).load(_file_with_a_u_code(tmp_path), "2026")
    }
    assert set(concepts) == {"I10", "U07.1"}
    u = concepts["U07.1"]
    assert u.placeholder is True
    # It is a well-formed code, not a heading -- it simply stands for nothing yet.
    assert u.assignable is True
    assert concepts["I10"].placeholder is False


def test_they_go_into_the_same_system_and_version(tmp_path: Path) -> None:
    concepts = list(ICD10SE(include_u_codes=True).load(_file_with_a_u_code(tmp_path), "2026"))
    assert {c.version for c in concepts} == {"2026"}
    assert {c.system for c in concepts} == {"icd10se"}


@pytest.mark.requires_db
def test_placeholders_never_reach_the_candidates(db_session: Session, tmp_path: Path) -> None:
    """Excluded in SQL, the same way headings are."""
    from app.db.models import upsert_concepts

    upsert_concepts(
        db_session,
        ICD10SE(include_u_codes=True).load(_file_with_a_u_code(tmp_path), "u-test"),
    )
    results = lexical_search(
        db_session,
        query=normalize("reserverad kodplats").normalized,
        system="icd10se",
        version="u-test",
        top_k=10,
        trigram_threshold=SETTINGS.trigram_threshold,
    )
    assert all(c.code != "U07.1" for c in results)


@pytest.mark.requires_db
def test_correct_to_a_placeholder_warns_then_allows(
    db_session: Session, tmp_path: Path, icd10se_embedded: str
) -> None:
    """Not a refusal. A human may deliberately record a U-code, so the first
    attempt explains and the second, acknowledged, succeeds."""
    from app.db.models import upsert_concepts
    from app.embeddings.fake import FakeEmbeddingProvider
    from app.llm.base import load_prompt
    from app.llm.fake import FakeLLMProvider
    from app.pipeline.map_term import map_term
    from app.validation.decisions import PlaceholderCodeNotAcknowledged, record_decision

    upsert_concepts(
        db_session,
        ICD10SE(include_u_codes=True).load(_file_with_a_u_code(tmp_path), icd10se_embedded),
    )
    outcome = map_term(
        db_session,
        text="essentiell hypertoni",
        target_system="icd10se",
        version=icd10se_embedded,
        trace_id="t",
        settings=SETTINGS,
        embedding_provider=FakeEmbeddingProvider(dim=SETTINGS.embedding_dim),
        llm_provider=FakeLLMProvider(),
        prompt=load_prompt(),
    )

    with pytest.raises(PlaceholderCodeNotAcknowledged, match="platshållarkod"):
        record_decision(
            db_session,
            proposal_id=outcome.proposal.id,
            decision="correct",
            final_code="U07.1",
            validator_id="coder",
        )

    row = record_decision(
        db_session,
        proposal_id=outcome.proposal.id,
        decision="correct",
        final_code="U07.1",
        validator_id="coder",
        acknowledge_placeholder=True,
    )
    assert row.final_code == "U07.1"


# ------------------------------------------------------------ eval dry run


def test_dry_run_flag_exists_and_is_documented() -> None:
    from evaluation.run_eval import main

    assert main.__module__
    text = (Path(__file__).parent.parent / "evaluation" / "run_eval.py").read_text(encoding="utf-8")
    assert "--dry-run" in text
    assert "session.rollback()" in text


def test_version_convention_is_documented() -> None:
    """The publisher's release year as an opaque string."""
    architecture = (Path(__file__).parent.parent / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "release year" in architecture
    assert get_settings().default_terminology_version == "2026"
