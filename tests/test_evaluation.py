"""Evaluation metrics and gold-set parsing.

No database: this is arithmetic and CSV handling, and both are worth pinning
down because a wrong metric quietly misreports quality rather than failing.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from evaluation.metrics import RowResult, format_summary, summarize
from evaluation.run_eval import is_sample, read_gold

GOLD_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "gold"


def _row(
    expected: str,
    suggested: str | None,
    ranked: list[str],
    candidates: list[str] | None = None,
    status: str = "pending",
) -> RowResult:
    return RowResult(
        term="t",
        expected_code=expected,
        suggested_code=suggested,
        ranked_codes=ranked,
        candidate_codes=candidates if candidates is not None else ranked,
        status=status,
        latency_ms_retrieval=10,
        latency_ms_rerank=20,
        proposal_id="p",
    )


def test_top1_and_top3() -> None:
    results = [
        _row("I10", "I10", ["I10", "I15"]),  # top-1 hit
        _row("I15", "I10", ["I10", "I11", "I15"]),  # top-3 hit only
        _row("J45", "I10", ["I10", "I11", "I12", "J45"]),  # in list, not top 3
    ]
    summary = summarize(results)
    assert summary.total == 3
    assert summary.top1_accuracy == pytest.approx(1 / 3)
    assert summary.top3_recall == pytest.approx(2 / 3)
    assert summary.candidate_recall == pytest.approx(1.0)
    assert len(summary.misses) == 2


def test_miss_kind_separates_retrieval_from_ranking() -> None:
    """The two failures have nothing to do with each other and are fixed in
    different places, so the misses CSV must tell them apart."""
    retrieval_miss = _row("E11", "I10", ["I10", "I15"], candidates=["I10", "I15"])
    ranking_miss = _row("I15", "I10", ["I10", "I15"], candidates=["I10", "I15"])
    deep_miss = _row("J45", "I10", ["I10", "I11", "I12", "J45"])

    assert retrieval_miss.miss_kind == "retrieval"
    assert retrieval_miss.retrieved is False
    assert ranking_miss.miss_kind == "ranking (in top 3)"
    assert deep_miss.miss_kind == "ranking (retrieved, not in top 3)"
    assert _row("I10", "I10", ["I10"]).miss_kind == ""


def test_latencies_are_averaged() -> None:
    results = [_row("A", "A", ["A"]), _row("B", "B", ["B"])]
    results[0].latency_ms_retrieval = 10
    results[1].latency_ms_retrieval = 30
    summary = summarize(results)
    assert summary.mean_latency_ms_retrieval == pytest.approx(20.0)
    assert summary.mean_latency_ms_rerank == pytest.approx(20.0)


def test_rerank_failures_are_counted() -> None:
    summary = summarize([_row("I10", None, [], candidates=["I10"], status="rerank_failed")])
    assert summary.rerank_failures == 1
    assert summary.top1_accuracy == 0.0
    # Retrieval still found it -- the failure was downstream.
    assert summary.candidate_recall == 1.0


def test_empty_run_does_not_divide_by_zero() -> None:
    summary = summarize([])
    assert summary.total == 0
    assert format_summary(summary) == "no rows evaluated"


def test_summary_formats_counts_alongside_percentages() -> None:
    text = format_summary(summarize([_row("I10", "I10", ["I10"]), _row("I15", "I10", ["I10"])]))
    assert "Top-1 accuracy" in text
    assert "(1/2)" in text


# --------------------------------------------------------------- gold files


def test_the_summary_names_the_arm_it_measured() -> None:
    """A figure without its arm is unattributable: `lexical` and `full` produce
    the same shape of number and mean entirely different things."""
    result = _row("I10", "I10", ["I10"])
    result.arm = "hybrid"

    assert "arm                     hybrid" in format_summary(summarize([result]))


def test_the_misses_file_records_the_arm(tmp_path: Path) -> None:
    """The misses file is what a calibration pass reads later; without the arm
    it cannot tell which pipeline produced the miss."""
    from evaluation.run_eval import _write_misses

    miss = _row("I10", "J45", ["J45"])
    miss.arm = "lexical"
    path = tmp_path / "misses.csv"
    _write_misses(path, [miss])

    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0][0] == "arm"
    assert rows[1][0] == "lexical"


def test_template_has_the_specified_columns() -> None:
    rows = read_gold(GOLD_DIR / "TEMPLATE.csv")
    assert rows == []  # header and comments only
    header = next(
        line
        for line in (GOLD_DIR / "TEMPLATE.csv").read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    assert header == "term,target_system,expected_code,source,note"


def test_sample_gold_parses_and_is_sourced() -> None:
    rows = read_gold(GOLD_DIR / "sample_icd10se.csv")
    assert len(rows) == 12
    assert all(row["target_system"] == "icd10se" for row in rows)
    # An unsourced gold set measures nothing.
    assert all(row["source"].strip() for row in rows)
    assert {"högt blodtryck", "diabetes mellitus typ 2"} <= {r["term"] for r in rows}


def test_sample_gold_is_detected_as_a_sample() -> None:
    """The banner depends on this, and a silently-missing banner is how a
    meaningless number ends up in a README."""
    assert is_sample(GOLD_DIR / "sample_icd10se.csv") is True


def test_comment_lines_are_skipped() -> None:
    raw = (GOLD_DIR / "sample_icd10se.csv").read_text(encoding="utf-8")
    assert raw.lstrip().startswith("#")
    assert "SAMPLE ONLY" in raw
    rows = read_gold(GOLD_DIR / "sample_icd10se.csv")
    assert all(not row["term"].startswith("#") for row in rows)


def test_missing_required_column_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "term,target_system,expected_code,source,note\n,icd10se,I10,src,\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="has no term"):
        read_gold(bad)
