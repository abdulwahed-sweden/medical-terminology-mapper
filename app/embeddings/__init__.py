"""Embedding provider selection."""

from __future__ import annotations

from app.config import Settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.fake import FakeEmbeddingProvider
from app.embeddings.openai_compat import OpenAICompatEmbeddingProvider

__all__ = ["EmbeddingProvider", "build_embedding_provider"]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding_dim, model_id=settings.embedding_model)
    if settings.embedding_provider == "openai_compat":
        return OpenAICompatEmbeddingProvider(
            base_url=settings.openai_embeddings_base_url,
            api_key=settings.openai_api_key,
            model_id=settings.embedding_model,
            dim=settings.embedding_dim,
        )
    raise ValueError(f"unknown embedding provider {settings.embedding_provider!r}")
