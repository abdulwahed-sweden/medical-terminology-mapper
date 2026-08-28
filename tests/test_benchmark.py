"""The benchmark orchestrator: what it produces, and what it refuses to produce.

These tests are about the instrument, not the mapper. They run entirely on the
fake providers, and none of them asserts anything about mapping quality -- a
test that did would be pinning the fake reranker's sort order and calling it a
result.

What is worth holding: every arm sees the identical row set, ineligible rows are
excluded rather than quietly counted, a class that vanishes fails loudly instead
of rendering an empty table, and no percentage is ever printed without its n.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings.fake import FakeEmbeddingProvider
from app.llm.base import load_prompt
from app.llm.fake import FakeLLMProvider
from evaluation.benchmark import (
    ARMS,
    CODE_NOT_PRESENT,
    DIMENSIONS,
    REHEARSAL_MARKER,
    ArmResult,
    EmptyClassError,
    build_manifest,
    pair,
    parse_gold_rows,
    render_report,
    run_arm,
    select_eligible,
    sha256_file,
    terminology_fingerprint,
)
from evaluation.run_eval import read_gold

pytestmark = pytest.mark.requires_db

SETTINGS = get_settings()
REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_GOLD = REPO_ROOT / "evaluation" / "gold" / "sample_icd10se.csv"
SYSTEM = "icd10se"


def _providers() -> tuple[FakeEmbeddingProvider, FakeLLMProvider]:
    return FakeEmbeddingProvider(dim=SETTINGS.embedding_dim), FakeLLMProvider()


def _eligible(session: Session, version: str, extra: list[dict[str, str]] | None = None):  # type: ignore[no-untyped-def]
    raw = read_gold(SAMPLE_GOLD, allow_negative=True)
    if extra:
        raw = [*raw, *extra]
    rows, malformed = parse_gold_rows(raw)
    eligible, ineligible = select_eligible(session, rows, system=SYSTEM, version=version)
    return rows, eligible, [*malformed, *ineligible]


def _run_all(session_factory, eligible, version: str) -> dict[str, list[ArmResult]]:  # type: ignore[no-untyped-def]
    embeddings, llm = _providers()
    return {
        arm: run_arm(
            session_factory,
            eligible,
            arm=arm,
            system=SYSTEM,
            version=version,
            settings=SETTINGS,
            embedding_provider=embeddings,
            llm_provider=llm,
            prompt=load_prompt("rerank_v1"),
            run_id="test",
            # The CLI hands each row its own session, so rolling back there
            # discards that row's proposal and nothing else. Here every row
            # shares the one test transaction, and a rollback would take the
            # fixture data with it. The `connection` fixture rolls the whole
            # thing back at teardown regardless.
            dry_run=False,
        )
        for arm in ARMS
    }


@pytest.fixture
def factory(db_session: Session):  # type: ignore[no-untyped-def]
    """Hand every arm the one test transaction, so the run rolls back with it."""
    from contextlib import contextmanager

    @contextmanager
    def scope():  # type: ignore[no-untyped-def]
        yield db_session

    return scope


def test_all_three_arms_run_on_the_identical_row_set(
    db_session: Session, factory, icd10se_embedded: str
) -> None:
    """The comparison is meaningless if the arms saw different rows."""
    _, eligible, _ = _eligible(db_session, icd10se_embedded)
    results = _run_all(factory, eligible, icd10se_embedded)

    ids = {arm: [r.row_id for r in results[arm]] for arm in ARMS}
    assert ids["lexical"] == ids["hybrid"] == ids["full"]
    assert ids["lexical"] == [row.row_id for row in eligible]


def test_a_row_whose_expected_code_is_not_loaded_is_excluded_and_counted(
    db_session: Session, icd10se_embedded: str
) -> None:
    """It measures the loader, not the mapper, so it leaves every denominator."""
    synthetic = [
        {
            "row_id": "999",
            "term": "något som inte finns",
            "target_system": SYSTEM,
            "expected_code": "Z99.9",
            "phrasing": "exact",
            "target": "plain",
            "source": "synthetic test row",
            "note": "",
        }
    ]
    rows, eligible, excluded = _eligible(db_session, icd10se_embedded, extra=synthetic)

    assert len(rows) == len(eligible) + 1
    assert [e.reason for e in excluded] == [CODE_NOT_PRESENT]
    assert 999 not in [row.row_id for row in eligible]


def test_negative_rows_are_eligible_without_an_expected_code(
    db_session: Session, icd10se_embedded: str
) -> None:
    """A benchmark that cannot measure false positives measures half the problem."""
    synthetic = [
        {
            "row_id": "998",
            "term": "banan",
            "target_system": SYSTEM,
            "expected_code": "",
            "phrasing": "exact",
            "target": "negative",
            "source": "synthetic test row",
            "note": "",
        }
    ]
    _, eligible, excluded = _eligible(db_session, icd10se_embedded, extra=synthetic)

    assert 998 in [row.row_id for row in eligible]
    assert not excluded


def test_the_negative_row_rule(db_session: Session, factory, icd10se_embedded: str) -> None:
    """Declining is correct; any code, however plausible, is not."""
    synthetic = [
        {
            "row_id": "998",
            "term": "banan",
            "target_system": SYSTEM,
            "expected_code": "",
            "phrasing": "exact",
            "target": "negative",
            "source": "synthetic test row",
            "note": "",
        }
    ]
    _, eligible, _ = _eligible(db_session, icd10se_embedded, extra=synthetic)
    negative = [row for row in eligible if row.row_id == 998]
    results = _run_all(factory, negative, icd10se_embedded)

    for arm in ARMS:
        result = results[arm][0]
        assert result.status == "no_good_match"
        assert result.correct
        assert result.failure is None


def test_paired_comparison_classifies_all_three_verdicts() -> None:
    def _result(row_id: int, arm: str, suggested: str | None) -> ArmResult:
        return ArmResult(
            row_id=row_id,
            arm=arm,
            term="t",
            phrasing="exact",
            target="plain",
            expected_code="I10",
            suggested_code=suggested,
            status="pending",
            ranked_codes=[suggested] if suggested else [],
            candidate_codes=[suggested] if suggested else [],
            matched_field="title",
            gate_fired=False,
            gate_reason="",
            gate_values={},
            proposal_id="p",
            rerank_is_null=False,
        )

    before = [
        _result(1, "lexical", "J45"),
        _result(2, "lexical", "I10"),
        _result(3, "lexical", "I10"),
    ]
    after = [_result(1, "hybrid", "I10"), _result(2, "hybrid", "J45"), _result(3, "hybrid", "I10")]

    verdicts = {c.row_id: c.verdict for c in pair(before, after, "hybrid vs lexical")}
    assert verdicts == {1: "improved", 2: "worsened", 3: "unchanged"}


def test_the_negative_rule_holds_in_the_paired_comparison() -> None:
    """Suggesting a code on a negative row is a worsening, not an improvement."""

    def _negative(row_id: int, arm: str, suggested: str | None, status: str) -> ArmResult:
        return ArmResult(
            row_id=row_id,
            arm=arm,
            term="banan",
            phrasing="exact",
            target="negative",
            expected_code="",
            suggested_code=suggested,
            status=status,
            ranked_codes=[],
            candidate_codes=[],
            matched_field=None,
            gate_fired=status == "no_good_match",
            gate_reason="",
            gate_values={},
            proposal_id="p",
            rerank_is_null=True,
        )

    before = [_negative(1, "lexical", None, "no_good_match")]
    after = [_negative(1, "hybrid", "I10", "pending")]

    assert pair(before, after, "full vs hybrid")[0].verdict == "worsened"


def test_a_class_that_produces_no_rows_fails_loudly(
    db_session: Session, factory, icd10se_embedded: str
) -> None:
    """An empty table reads as 'covered, scored zero'. It is neither."""
    _, eligible, excluded = _eligible(db_session, icd10se_embedded)
    results = _run_all(factory, eligible, icd10se_embedded)
    manifest = _manifest(db_session, eligible, excluded, icd10se_embedded)

    labels = {
        "phrasing": sorted({row.phrasing for row in eligible} | {"abbreviation"}),
        "target": sorted({row.target for row in eligible}),
    }
    with pytest.raises(EmptyClassError, match="abbreviation"):
        render_report(
            manifest=manifest, results=results, labels=labels, excluded=excluded, paired=[]
        )


def _manifest(session: Session, eligible, excluded, version: str):  # type: ignore[no-untyped-def]
    embeddings, llm = _providers()
    return build_manifest(
        run_id="test",
        run_kind="rehearsal",
        gold_path=SAMPLE_GOLD,
        total_rows=len(eligible) + len(excluded),
        eligible=eligible,
        excluded=excluded,
        system=SYSTEM,
        version=version,
        fingerprint=terminology_fingerprint(session, system=SYSTEM, version=version),
        settings=SETTINGS,
        embedding_provider=embeddings,
        llm_provider=llm,
        prompt=load_prompt("rerank_v1"),
    )


def test_the_manifest_carries_every_required_field(
    db_session: Session, icd10se_embedded: str
) -> None:
    _, eligible, excluded = _eligible(db_session, icd10se_embedded)
    manifest = _manifest(db_session, eligible, excluded, icd10se_embedded)

    for key in (
        "run_id",
        "run_kind",
        "timestamp_utc",
        "git_sha",
        "dataset",
        "terminology",
        "arms",
        "providers",
        "prompt",
        "gate",
        "retrieval",
    ):
        assert key in manifest, key
    for key in (
        "path",
        "sha256",
        "total_rows",
        "eligible_rows",
        "excluded_rows",
        "excluded_by_reason",
        "label_counts",
    ):
        assert key in manifest["dataset"], key
    assert set(manifest["arms"]) == set(ARMS)
    assert manifest["terminology"]["fingerprint"]["concept_count"] > 0
    assert manifest["providers"]["embeddings"]["dimension"] == SETTINGS.embedding_dim
    assert (
        manifest["gate"]["configuration"]["gate_min_query_chars"] == SETTINGS.gate_min_query_chars
    )
    assert manifest["retrieval"]["rrf_k"] == SETTINGS.rrf_k
    # Both dimensions counted, separately. The sample is ten preferred terms out
    # of twelve, which a single column reported as three.
    assert set(manifest["dataset"]["label_counts"]) == set(DIMENSIONS)
    assert manifest["dataset"]["label_counts"]["phrasing"]["exact"] == 10
    assert manifest["dataset"]["label_counts"]["target"]["plain"] == 5


def test_the_dataset_hash_is_stable_across_two_reads() -> None:
    assert sha256_file(SAMPLE_GOLD) == sha256_file(SAMPLE_GOLD)


def test_the_report_never_prints_a_percentage_without_its_n(
    db_session: Session, factory, icd10se_embedded: str
) -> None:
    import re

    _, eligible, excluded = _eligible(db_session, icd10se_embedded)
    results = _run_all(factory, eligible, icd10se_embedded)
    labels = {d: sorted({row.label(d) for row in eligible}) for d in DIMENSIONS}
    report = render_report(
        manifest=_manifest(db_session, eligible, excluded, icd10se_embedded),
        results=results,
        labels=labels,
        excluded=excluded,
        paired=pair(results["lexical"], results["hybrid"], "hybrid vs lexical"),
    )

    for match in re.finditer(r"\d+%", report):
        tail = report[match.end() : match.end() + 12]
        assert tail.lstrip().startswith("("), f"bare percentage at {match.group()}{tail!r}"

    assert "LOW N" in report  # every class in the sample is far below 30
    assert report.index("## A.") < report.index("## D.")  # per class before overall
    assert REHEARSAL_MARKER in report


def test_measurement_arms_store_no_rerank(
    db_session: Session, factory, icd10se_embedded: str
) -> None:
    """No model ran, so nothing may be recorded as though one had."""
    _, eligible, _ = _eligible(db_session, icd10se_embedded)
    results = _run_all(factory, eligible[:2], icd10se_embedded)

    assert all(r.rerank_is_null for r in results["lexical"])
    assert all(r.rerank_is_null for r in results["hybrid"])


def test_a_legacy_single_column_file_still_parses() -> None:
    """Gold files written before the split still load.

    A `class` naming a trap tells us the target and nothing about the phrasing,
    so the phrasing is recorded as unclassified rather than guessed as `exact` --
    guessing is how a file quietly acquires labels nobody assigned.
    """
    legacy = [
        {
            "row_id": "1",
            "term": "a",
            "target_system": SYSTEM,
            "expected_code": "I10",
            "class": "synonym",
            "source": "s",
            "note": "",
        },
        {
            "row_id": "2",
            "term": "b",
            "target_system": SYSTEM,
            "expected_code": "I11.0",
            "class": "distinction",
            "source": "s",
            "note": "",
        },
        {
            "row_id": "3",
            "term": "c",
            "target_system": SYSTEM,
            "expected_code": "",
            "class": "no_good_match",
            "source": "s",
            "note": "",
        },
    ]
    rows, malformed = parse_gold_rows(legacy)

    assert not malformed
    assert [(r.phrasing, r.target) for r in rows] == [
        ("synonym", "plain"),
        ("unclassified", "distinction"),
        ("unclassified", "negative"),
    ]


# --------------------------------------------------------- the committed rehearsal

REHEARSAL = REPO_ROOT / "evaluation" / "runs" / "rehearsal"


def test_the_rehearsal_is_marked_as_not_a_quality_result() -> None:
    """Its own filename says so, so a reader cannot reach the numbers first."""
    marker = REHEARSAL / "REHEARSAL-FAKE-PROVIDERS-NOT-A-QUALITY-RESULT.md"
    assert marker.exists()

    report = (REHEARSAL / "report.md").read_text(encoding="utf-8")
    assert report.startswith(f"# {REHEARSAL_MARKER}")
    assert "NOT A MAPPING-QUALITY RESULT" in report

    manifest = json.loads((REHEARSAL / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_kind"] == "rehearsal"
    assert manifest["providers"]["fake_providers"]


def test_the_rehearsal_contains_every_artefact() -> None:
    for name in (
        "manifest.json",
        "lexical.csv",
        "hybrid.csv",
        "full.csv",
        "paired_changes.csv",
        "misses.csv",
        "report.md",
    ):
        assert (REHEARSAL / name).exists(), name


def test_the_rehearsal_arm_csvs_cover_the_same_rows() -> None:
    def ids(name: str) -> list[str]:
        with (REHEARSAL / name).open(encoding="utf-8") as handle:
            return [row["row_id"] for row in csv.DictReader(handle)]

    assert ids("lexical.csv") == ids("hybrid.csv") == ids("full.csv")
