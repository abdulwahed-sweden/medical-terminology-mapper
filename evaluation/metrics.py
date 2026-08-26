"""Metrics for a gold-set evaluation run.

Deliberately few, and each one answers a question someone actually asks:

  Top-1 accuracy      how often the code offered first is the right one -- what
                      a validator experiences as "it just proposed the answer".
  Top-3 recall        how often the right code is somewhere in the first three
                      -- what a validator experiences as "it was on screen".
  Candidate recall    how often the right code was retrieved at all, before
                      reranking. This is the diagnostic split: a miss with the
                      code in the candidate list is a ranking problem, a miss
                      without it is a retrieval problem, and the two have
                      nothing to do with each other.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class RowResult:
    term: str
    expected_code: str
    suggested_code: str | None
    ranked_codes: list[str]
    candidate_codes: list[str]
    status: str
    latency_ms_retrieval: int
    latency_ms_rerank: int
    proposal_id: str
    note: str = ""

    @property
    def top1_hit(self) -> bool:
        return self.suggested_code == self.expected_code

    @property
    def top3_hit(self) -> bool:
        return self.expected_code in self.ranked_codes[:3]

    @property
    def retrieved(self) -> bool:
        return self.expected_code in self.candidate_codes

    @property
    def miss_kind(self) -> str:
        """Why this row missed -- blank when it did not."""
        if self.top1_hit:
            return ""
        if not self.retrieved:
            return "retrieval"
        if self.top3_hit:
            return "ranking (in top 3)"
        return "ranking (retrieved, not in top 3)"


@dataclass
class EvaluationSummary:
    total: int
    top1_accuracy: float
    top3_recall: float
    candidate_recall: float
    mean_latency_ms_retrieval: float
    mean_latency_ms_rerank: float
    rerank_failures: int
    misses: list[RowResult] = field(default_factory=list)


def summarize(results: Sequence[RowResult]) -> EvaluationSummary:
    if not results:
        return EvaluationSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, [])

    total = len(results)
    return EvaluationSummary(
        total=total,
        top1_accuracy=sum(r.top1_hit for r in results) / total,
        top3_recall=sum(r.top3_hit for r in results) / total,
        candidate_recall=sum(r.retrieved for r in results) / total,
        mean_latency_ms_retrieval=statistics.fmean(r.latency_ms_retrieval for r in results),
        mean_latency_ms_rerank=statistics.fmean(r.latency_ms_rerank for r in results),
        rerank_failures=sum(r.status == "rerank_failed" for r in results),
        misses=[r for r in results if not r.top1_hit],
    )


def format_summary(summary: EvaluationSummary) -> str:
    if summary.total == 0:
        return "no rows evaluated"

    def pct(value: float) -> str:
        return f"{value * 100:5.1f}%  ({round(value * summary.total)}/{summary.total})"

    lines = [
        f"rows evaluated          {summary.total}",
        f"Top-1 accuracy          {pct(summary.top1_accuracy)}",
        f"Top-3 recall            {pct(summary.top3_recall)}",
        f"candidate recall        {pct(summary.candidate_recall)}",
        f"mean retrieval latency  {summary.mean_latency_ms_retrieval:.0f} ms",
        f"mean rerank latency     {summary.mean_latency_ms_rerank:.0f} ms",
    ]
    if summary.rerank_failures:
        lines.append(f"rerank failures         {summary.rerank_failures}")
    return "\n".join(lines)
