"""Deterministic offline reranker.

Ranks by lexical score descending and assigns confidences from a fixed ladder,
so a given candidate list always produces byte-identical output. That is what
makes the end-to-end pipeline test meaningful: everything except ids and
timestamps must be reproducible, and a real model cannot promise that.

It does no language understanding whatsoever. It exists to prove the pipeline,
not the mapping.
"""

from __future__ import annotations

from app.llm.base import PromptSpec
from app.models.candidate import Candidate
from app.models.rerank import RankedCode, RerankResult

# Fixed by position, not computed from scores: the point is reproducibility,
# and a derived confidence would drift with every retrieval tweak.
_CONFIDENCE_LADDER = (0.90, 0.75, 0.60, 0.45, 0.30, 0.20, 0.15, 0.10)
_TAIL_CONFIDENCE = 0.05


class FakeLLMProvider:
    provider_id = "fake"

    def __init__(self, model_id: str = "fake-rerank-v1") -> None:
        self.model_id = model_id

    def rerank(
        self, query: str, candidates: list[Candidate], prompt: PromptSpec
    ) -> RerankResult:
        if not candidates:
            return RerankResult(
                ranked=[],
                no_good_match=True,
                notes="fake provider: no candidates were retrieved",
            )

        ordered = sorted(
            candidates,
            # Candidates the lexical stage never saw sort last; `code` breaks
            # ties so the order never depends on dict or query ordering.
            key=lambda c: (-(c.lexical_score or 0.0), c.code),
        )

        ranked = [
            RankedCode(
                code=candidate.code,
                confidence=(
                    _CONFIDENCE_LADDER[position]
                    if position < len(_CONFIDENCE_LADDER)
                    else _TAIL_CONFIDENCE
                ),
                reason=(
                    f"fake provider: ranked {position + 1} by lexical score "
                    f"{candidate.lexical_score if candidate.lexical_score is not None else 0.0:.3f}"
                ),
            )
            for position, candidate in enumerate(ordered)
        ]

        return RerankResult(
            ranked=ranked,
            no_good_match=False,
            notes=f"fake provider: deterministic lexical-score ordering of {len(ranked)} candidates",
        )
