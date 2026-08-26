"""Candidate fusion.

The two retrieval stages return overlapping sets scored on scales that are not
comparable: a full-text rank and a cosine similarity do not mean the same thing
at 0.7. Reciprocal-rank fusion sidesteps that by using only each stage's
*ordering*, which is exactly the property both stages agree on.

    RRF(concept) = sum over stages of 1 / (k + rank_in_that_stage)

`k` (default 60) damps the top of each list so a single stage's first place
cannot dominate a concept that both stages ranked highly.

Every score from every stage survives the merge, null where a stage did not
return that concept. That is what lets the Phase 3 comparative benchmark be
computed from stored proposals without re-running retrieval.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.candidate import Candidate


def merge_candidates(
    lexical: Sequence[Candidate],
    vector: Sequence[Candidate],
    *,
    rrf_k: int = 60,
    cap: int | None = None,
) -> list[Candidate]:
    """Union the two candidate lists, deduplicated by `(system, version, code)`."""
    merged: dict[tuple[str, str, str], Candidate] = {}

    for candidate in list(lexical) + list(vector):
        key = (candidate.system, candidate.version, candidate.code)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate.model_copy(deep=True)
            continue

        # Same concept from the other stage: fold in the scores it carries
        # rather than letting one stage's view overwrite the other's.
        for source in candidate.sources:
            if source not in existing.sources:
                existing.sources.append(source)
        if candidate.lexical_score is not None:
            existing.lexical_score = candidate.lexical_score
            existing.lexical_rank = candidate.lexical_rank
            existing.ts_rank = candidate.ts_rank
            existing.trgm_similarity = candidate.trgm_similarity
            existing.strict_similarity = candidate.strict_similarity
            # A lexical attribution is more informative than "vector", so it
            # wins when both stages returned the same concept.
            existing.matched_field = candidate.matched_field
        if candidate.vector_score is not None:
            existing.vector_score = candidate.vector_score
            existing.vector_rank = candidate.vector_rank

    for candidate in merged.values():
        candidate.sources.sort(key=_SOURCE_ORDER.__getitem__)
        candidate.fused_score = _reciprocal_rank_fusion(candidate, rrf_k)

    ordered = sorted(
        merged.values(),
        # `code` breaks ties so the same inputs always produce the same order --
        # a proposal that is not reproducible is not much of an audit record.
        key=lambda c: (-c.fused_score, c.code),
    )
    return ordered[:cap] if cap is not None else ordered


_SOURCE_ORDER = {"lexical": 0, "vector": 1}


def _reciprocal_rank_fusion(candidate: Candidate, rrf_k: int) -> float:
    score = 0.0
    for rank in (candidate.lexical_rank, candidate.vector_rank):
        if rank is not None:
            score += 1.0 / (rrf_k + rank)
    return score
