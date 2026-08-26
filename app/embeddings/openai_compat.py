"""Embeddings from any OpenAI-compatible `/embeddings` endpoint.

Deliberately spelled "OpenAI-compatible" rather than "OpenAI": the same code
path serves Azure OpenAI, a self-hosted vLLM or Ollama instance, or a Swedish
regional inference service. For clinical text that matters -- where the text is
allowed to travel is a governance decision, not a code change.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.embeddings.base import EmbeddingError


class OpenAICompatEmbeddingProvider:
    provider_id = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_id: str,
        dim: int,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.dim = dim
        self.timeout = timeout

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {"model": self.model_id, "input": list(texts)}

        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        try:
            # The API does not guarantee order; `index` does.
            items = sorted(body["data"], key=lambda item: item["index"])
            vectors = [list(map(float, item["embedding"])) for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"unexpected embeddings response shape: {body!r}") from exc

        if len(vectors) != len(texts):
            raise EmbeddingError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingError(
                    f"model {self.model_id!r} returned {len(vector)}-dimensional vectors "
                    f"but EMBEDDING_DIM is {self.dim}. The pgvector column is typed with a "
                    f"fixed dimension, so this needs a migration, not a config change."
                )
        return vectors
