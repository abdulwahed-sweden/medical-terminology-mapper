"""The embedding provider contract.

Providers sit behind this protocol so the pipeline never names a vendor, and so
a deterministic fake can stand in for the real thing in tests (principle 4).

Every provider reports `provider_id`, `model_id` and `dim`; all three are
recorded on the proposal, because a vector score is meaningless without knowing
which space produced it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider cannot produce vectors."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one `dim`-length vector each."""
        ...
