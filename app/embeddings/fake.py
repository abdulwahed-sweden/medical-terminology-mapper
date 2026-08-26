"""Deterministic, offline embedding provider.

Exists so the whole pipeline -- including pgvector search -- runs in tests and
in the `docker compose` quick start with no network and no API key.

The vectors are derived from a hash of the text's character trigrams, so they
are stable across processes and machines (Python's `hash()` is salted per
process and would not be), and texts sharing trigrams land closer together than
texts that share none. That is enough for the vector stage to demonstrably
contribute candidates and for merge behaviour to be testable.

It is NOT a semantic model. It cannot match "förhöjt blodtryck" to "hypertoni",
which is the entire reason the vector stage exists in production. Any
evaluation run with this provider measures plumbing, not mapping quality --
`run_eval.py` says so in its output.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence


class FakeEmbeddingProvider:
    provider_id = "fake"

    def __init__(self, dim: int, model_id: str = "fake-hash-v1") -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.model_id = model_id

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        cleaned = " ".join(text.split()).casefold()
        if not cleaned:
            # A zero vector has no cosine distance; give empty input a fixed,
            # arbitrary-but-valid direction instead of a division by zero.
            vector[0] = 1.0
            return vector

        padded = f"  {cleaned}  "
        for i in range(len(padded) - 2):
            trigram = padded[i : i + 3]
            digest = hashlib.blake2b(trigram.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            # Sign from an independent byte, so features cancel as often as they
            # accumulate and vectors are not all crowded into one orthant.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:  # pragma: no cover - requires exact cancellation
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]
