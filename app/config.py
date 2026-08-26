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
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

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
    llm_max_output_tokens: int = 2048

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

    # --- Terminology --------------------------------------------------------
    default_terminology_version: str = "2026"

    # --- Service ------------------------------------------------------------
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
