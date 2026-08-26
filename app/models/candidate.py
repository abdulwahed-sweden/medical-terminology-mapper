"""Candidate concepts produced by retrieval and consumed by reranking.

A candidate carries every score that contributed to it, not just the one used
for ordering. Two reasons: the proposal stores this record verbatim, so a
reviewer can see exactly what the model was shown; and the Phase 3 comparative
benchmark (lexical vs vector vs RAG+LLM) can then be computed from stored
proposals without re-running retrieval.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RetrievalSource = Literal["lexical", "vector"]
# Which part of the concept the hit came from. Recorded per candidate so a
# result can be attributed: matching a preferred term and matching a sentence
# of the publisher's prose are not the same kind of evidence.
MatchedField = Literal["title", "synonym", "description", "vector"]


class Candidate(BaseModel):
    system: str
    version: str
    code: str
    preferred_term: str
    synonyms: list[str] = Field(default_factory=list)
    chapter: str | None = None
    is_leaf: bool = True

    # Which stages produced this candidate. Order is stable: lexical, vector.
    sources: list[RetrievalSource] = Field(default_factory=list)

    # Null when the corresponding stage did not return this concept.
    lexical_score: float | None = None
    vector_score: float | None = None

    # 1-based position within each stage's own result list, kept so fusion is
    # reproducible from the stored record alone.
    lexical_rank: int | None = None
    vector_rank: int | None = None

    # The two components behind `lexical_score`, kept separately because they
    # answer different questions: full-text rank measures term overlap under the
    # `swedish` configuration, trigram similarity measures raw string closeness
    # and is what tolerates a misspelling.
    ts_rank: float | None = None
    trgm_similarity: float | None = None

    # Reciprocal-rank fusion score used for the pre-rerank ordering.
    fused_score: float = 0.0

    matched_field: MatchedField | None = None

    # pg_trgm strict_word_similarity against the concept's names. Kept beside
    # `ts_rank` because the retrieval gate reads them separately: a full-text
    # match and a fuzzy one are different strengths of evidence.
    strict_similarity: float | None = None
