"""The retrieval gate: is there enough evidence to ask a model at all?

Runs after merge and before the LLM. If nothing clears it, the LLM is never
called -- no cost, and no opportunity for it to rank noise into a confident
suggestion. The proposal is still written, with the full candidate list, so the
claim "the system found nothing" stays checkable.

WHY THE RULE IS SHAPED THIS WAY
-------------------------------
The rule is deliberately built on *lexical* evidence. The reason is not a
preference for lexical search; it is that the vector evidence available during
Phase 1 is unusable for this purpose. The bundled embedding provider derives
vectors from hashed character trigrams, so a similarity between two of them is
noise. A query like "banan" scoring 0.070 against a hypertension code says
nothing whatsoever, and a real embedding model would score it roughly 0.3-0.5
against almost anything. Tuning any threshold on those numbers would produce a
rule that looks measured and is worthless.

So the vector component exists, is keyed by vector space, and is **off by
default**. See `Settings.gate_vector_floors`.

THE MEASUREMENT BEHIND THE DEFAULTS
-----------------------------------
Measured against the real KVÅ 2026 release (11 888 concepts), with 29 everyday
Swedish non-clinical words as negatives and 30 mechanically-introduced
misspellings of real terms as positives:

* `ts_rank > 0` -- a real match under the `swedish` configuration -- fired for
  0 of 29 negatives. Every correctly-spelled clinical query has it.
* `strict_word_similarity` alone cannot separate the two classes: the worst
  misspelling scores 0.529 and the best negative 0.571. **They overlap.**
* Combining them at 0.60 admits 29 of 30 misspellings and 0 of 29 negatives.
  The plateau runs 0.58-0.62; 0.60 is its middle rather than a value chosen to
  make two examples work.

The one misspelling this rejects is `adenoisntest` (two transpositions in one
word). The nearest negative sits 0.029 below the threshold, which is not much
room -- recorded as a known fragility in ARCHITECTURE.md.

A query of ordinary Swedish words that genuinely occur in the terminology
("patient", "behandling") passes the gate, correctly: there *is* lexical
evidence. Judging whether that evidence means anything is the reranker's job,
and its own `no_good_match` flag is the second, independent signal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models.candidate import Candidate

GATE_ID = "lexical_evidence"
GATE_VERSION = "1"


@dataclass(frozen=True)
class GateOutcome:
    """`fired` means the gate blocked: there was not enough evidence."""

    fired: bool
    reason: str
    values: dict[str, Any]

    @property
    def admitted(self) -> bool:
        return not self.fired


def evaluate_gate(
    candidates: Sequence[Candidate],
    *,
    settings: Settings,
    embedding_provider_id: str,
    embedding_model_id: str,
) -> GateOutcome:
    """Decide whether the candidate set carries enough evidence to rerank."""
    vector_space = f"{embedding_provider_id}/{embedding_model_id}"
    vector_floor = settings.gate_vector_floors.get(vector_space)

    best_ts = max((c.ts_rank or 0.0 for c in candidates), default=0.0)
    best_strict = max((c.strict_similarity or 0.0 for c in candidates), default=0.0)
    best_vector = max((c.vector_score or 0.0 for c in candidates), default=0.0)
    best_rrf = max((c.fused_score or 0.0 for c in candidates), default=0.0)
    lexical_hits = sum(1 for c in candidates if "lexical" in c.sources)

    values: dict[str, Any] = {
        "candidate_count": len(candidates),
        "lexical_hit_count": lexical_hits,
        "best_ts_rank": round(best_ts, 6),
        "best_strict_similarity": round(best_strict, 6),
        "best_rrf": round(best_rrf, 6),
        "best_vector_score": round(best_vector, 6),
        "vector_space": vector_space,
        "vector_considered": vector_floor is not None,
        "vector_floor": vector_floor,
        "min_ts_rank": settings.gate_min_ts_rank,
        "min_strict_similarity": settings.gate_min_strict_similarity,
    }

    if not candidates:
        return GateOutcome(True, "inga kandidater återfanns", values)

    if best_ts > settings.gate_min_ts_rank:
        return GateOutcome(
            False,
            f"fulltextträff i kodverket (ts_rank {best_ts:.3f})",
            values,
        )

    if best_strict >= settings.gate_min_strict_similarity:
        return GateOutcome(
            False,
            f"stark teckenlikhet mot en term (strict_word_similarity {best_strict:.3f})",
            values,
        )

    if vector_floor is not None and best_vector >= vector_floor:
        return GateOutcome(
            False,
            f"vektorlikhet över tröskeln för {vector_space} ({best_vector:.3f})",
            values,
        )

    return GateOutcome(
        True,
        (
            f"ingen fulltextträff (ts_rank {best_ts:.3f}) och högsta teckenlikhet "
            f"{best_strict:.3f} når inte {settings.gate_min_strict_similarity:.2f}"
        ),
        values,
    )
