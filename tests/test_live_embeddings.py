"""One live embedding call, before a benchmark makes thousands.

The embeddings path has never made a real request: `app/embeddings/openai_compat.py`
sits far below the coverage of everything around it, and phase 3 would otherwise
have its first real call happen inside a measurement run, where a dimension
mismatch or a model that does not handle Swedish would look like a bad result
rather than a bad setup.

Double-gated like every live test: `--live-providers` selects it, and the TEST_*
credentials configure it. Neither alone runs anything. It never reads the
application's own API key -- see tests/conftest.py.
"""

from __future__ import annotations

import math
import os

import pytest

from app.config import Settings
from app.embeddings import build_embedding_provider

pytestmark = pytest.mark.requires_api_key

# Two ways of saying the same thing, and one unrelated word. A model that cannot
# separate these is not usable for Swedish clinical text, whatever it scores
# elsewhere.
SAME_MEANING = ("hypertoni", "högt blodtryck")
UNRELATED = "banan"


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


@pytest.mark.skipif(
    not (
        os.environ.get("TEST_OPENAI_API_KEY") and os.environ.get("TEST_OPENAI_EMBEDDINGS_BASE_URL")
    ),
    reason=(
        "TEST_OPENAI_API_KEY / TEST_OPENAI_EMBEDDINGS_BASE_URL are not set; "
        "skipping the live embedding smoke test"
    ),
)
def test_live_embedding_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(
        embedding_provider="openai_compat",
        embedding_model=os.environ.get("TEST_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        openai_api_key=os.environ["TEST_OPENAI_API_KEY"],
        openai_embeddings_base_url=os.environ["TEST_OPENAI_EMBEDDINGS_BASE_URL"],
    )
    provider = build_embedding_provider(settings)

    hypertoni, blodtryck, banan = provider.embed([*SAME_MEANING, UNRELATED])

    # The assertion that matters most, and the cheapest one to get wrong: the
    # pgvector column is typed `vector(EMBEDDING_DIM)`, so a model returning a
    # different width fails at insert time, thousands of rows into an embedding
    # run. Catch it on three strings instead.
    assert len(hypertoni) == settings.embedding_dim, (
        f"{settings.embedding_model} returned {len(hypertoni)} dimensions, but the "
        f"vector column is {settings.embedding_dim}. Changing EMBEDDING_DIM is a "
        f"migration and a full re-embed -- see ARCHITECTURE.md."
    )
    assert len({len(hypertoni), len(blodtryck), len(banan)}) == 1

    related = _cosine(hypertoni, blodtryck)
    unrelated = _cosine(hypertoni, banan)

    with capsys.disabled():
        print(
            f"\n  model      {settings.embedding_model}"
            f"\n  dimensions {len(hypertoni)}"
            f"\n  cosine(hypertoni, högt blodtryck) = {related:.4f}"
            f"\n  cosine(hypertoni, banan)          = {unrelated:.4f}"
        )

    # The minimum a Swedish-capable model must clear. Deliberately not a
    # threshold on the absolute value: what matters is the ordering, and a floor
    # picked without a real embedding space to measure would be a guess.
    assert related > unrelated, (
        f"{settings.embedding_model} rates 'hypertoni' closer to 'banan' "
        f"({unrelated:.4f}) than to 'högt blodtryck' ({related:.4f}). It is not "
        f"usable for Swedish clinical text."
    )
