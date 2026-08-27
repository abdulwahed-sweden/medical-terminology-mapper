"""Application settings, loaded from the environment.

Every setting here is mirrored in `.env.example` with a comment. Settings are
read once and cached; call `get_settings.cache_clear()` in tests that need to
rebind the environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TargetSystem = Literal["icd10se", "kva", "snomed"]

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://mtm:mtm@localhost:5432/mtm",
        description="SQLAlchemy URL for the PostgreSQL instance (needs pgvector + pg_trgm).",
    )

    # --- Embeddings ---------------------------------------------------------
    embedding_provider: Literal["fake", "openai_compat"] = "fake"
    embedding_model: str = "fake-hash-v1"
    embedding_dim: int = Field(
        default=1536,
        description=(
            "Dimension of the `vector` column. Changing it requires a migration, "
            "because pgvector columns are typed with a fixed dimension."
        ),
    )
    openai_embeddings_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None

    # --- LLM ----------------------------------------------------------------
    llm_provider: Literal["fake", "anthropic", "openai_compat"] = "fake"
    llm_model: str = "fake-rerank-v1"
    anthropic_api_key: str | None = None
    openai_chat_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    # Generous by default: on current Claude models thinking tokens count
    # towards this ceiling, and a truncated reply wastes the one repair retry
    # on a doomed parse.
    llm_max_output_tokens: int = 8192
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_structured_output: bool = Field(
        default=True,
        description=(
            "Ask the provider to constrain output to the rerank JSON schema. "
            "Turn off for a model that does not support constrained output."
        ),
    )

    # --- Retrieval ----------------------------------------------------------
    lexical_top_k: int = 20
    vector_top_k: int = 20
    rerank_candidate_cap: int = 15
    trigram_threshold: float = Field(
        default=0.45,
        description=(
            "Minimum pg_trgm word_similarity for a concept to enter the lexical "
            "candidate set. Applies to word_similarity, not similarity, so it is "
            "on the same scale as PostgreSQL's own 0.6 default for that operator."
        ),
    )
    rrf_k: int = Field(default=60, description="Reciprocal-rank-fusion smoothing constant.")
    index_descriptions: bool = Field(
        default=True,
        description=(
            "Include the publisher's Beskrivning text in the full-text index, at "
            "the lowest tsvector weight (D). See ARCHITECTURE.md for the "
            "measurement behind the default."
        ),
    )

    # --- Retrieval gate -----------------------------------------------------
    # A deterministic check that runs after merge and before the LLM is called.
    # If no candidate clears it, the proposal is recorded as no_good_match and
    # the LLM is never asked -- it cannot be tempted to rank noise.
    gate_min_ts_rank: float = Field(
        default=0.0,
        description=(
            "Admit when the best full-text rank is strictly greater than this. "
            "The default means 'the swedish text search configuration matched at "
            "least one lexeme', which no nonsense query in the measurement did."
        ),
    )
    gate_min_strict_similarity: float = Field(
        default=0.60,
        description=(
            "Admit when the best pg_trgm strict_word_similarity reaches this. "
            "Covers misspellings, which produce no full-text match at all. "
            "Measured plateau is 0.58-0.62; see ARCHITECTURE.md."
        ),
    )
    gate_min_query_chars: int = Field(
        default=3,
        description=(
            "Normalized queries shorter than this are blocked before any "
            "evidence check. Three keeps real abbreviations such as AKS and "
            "PTCA while stopping one- and two-character noise."
        ),
    )
    gate_vector_floors: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Optional per-vector-space floors, keyed 'provider/model'. A space "
            "with no entry contributes nothing to the gate. Deliberately empty "
            "by default: the fake provider's similarities are hash noise and "
            "must never be used to tune a threshold. UNTESTED against a live "
            "embedding model."
        ),
    )

    # --- Terminology --------------------------------------------------------
    default_terminology_version: str = "2026"

    # --- Service ------------------------------------------------------------
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
