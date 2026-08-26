"""The LLM provider contract, prompt versioning, and the guards around output.

Three things live here because all providers need them identically, and a guard
that each provider implements for itself is a guard that one provider will get
wrong:

  * `PromptSpec` / `load_prompt` -- the prompt is a versioned file, hashed by
    content. Changing the file changes the hash, so a behaviour change without
    a rename is still traceable on every proposal it affected.
  * `parse_rerank_payload` / `run_with_one_repair` -- strict JSON parsing and
    the single repair retry.
  * `enforce_candidate_codes` -- the hallucinated-code guard.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from app.models.candidate import Candidate
from app.models.rerank import RankedCode, RerankResult

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"


class LLMError(RuntimeError):
    """Transport-level failure talking to a provider."""


class RerankParseError(LLMError):
    """The provider returned something that is not the agreed JSON object."""


class RerankFailed(LLMError):
    """Reranking could not produce a valid result, including after the repair retry.

    The pipeline turns this into a proposal with status `rerank_failed`. The
    proposal is still written: a failed mapping attempt is part of the audit
    trail, not something to discard.
    """


# --------------------------------------------------------------------------- #
# Prompt versioning
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    text: str
    sha256: str


@lru_cache(maxsize=8)
def load_prompt(prompt_id: str = "rerank_v1") -> PromptSpec:
    """Load a prompt file and hash its exact bytes.

    Hashing bytes rather than the decoded string means a change in line endings
    or a stray trailing newline also changes the hash. That is intended: the
    hash answers "were these the exact instructions?", not "were they roughly
    the same?".
    """
    path = PROMPT_DIR / f"{prompt_id}.md"
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise LLMError(f"no prompt file for prompt_id {prompt_id!r} at {path}") from exc
    return PromptSpec(
        prompt_id=prompt_id,
        text=raw.decode("utf-8"),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


# --------------------------------------------------------------------------- #
# Provider contract
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMProvider(Protocol):
    provider_id: str
    model_id: str

    def rerank(
        self, query: str, candidates: list[Candidate], prompt: PromptSpec
    ) -> RerankResult: ...


# --------------------------------------------------------------------------- #
# Strict JSON parsing
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON matching the required schema. "
    "Reply again with ONLY the JSON object -- no prose, no explanation and no "
    "code fence. It must have exactly the keys `ranked` (array of objects with "
    "`code`, `confidence`, `reason`), `no_good_match` (boolean) and optionally "
    "`notes` (string). No other keys are permitted."
)


def parse_rerank_payload(raw: str) -> RerankResult:
    """Parse a provider's reply into a `RerankResult`, strictly.

    A code fence is tolerated because models add them habitually despite being
    told not to, and refusing over formatting the human never sees would waste
    a retry on nothing. Everything about the *content* is strict.
    """
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    if not text:
        raise RerankParseError("provider returned an empty response")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RerankParseError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RerankParseError(f"expected a JSON object, got {type(payload).__name__}")

    try:
        return RerankResult.model_validate(payload)
    except ValidationError as exc:
        raise RerankParseError(f"response does not match the schema: {exc}") from exc


def run_with_one_repair(call: Callable[[str | None], str]) -> RerankResult:
    """Call the model, and on a malformed reply retry exactly once with a repair
    instruction before giving up.

    One retry, not a loop: a model that cannot produce the schema twice is not
    going to produce it on the fifth attempt, and an unbounded retry loop turns
    a bad response into a bill and a hung request.
    """
    try:
        return parse_rerank_payload(call(None))
    except RerankParseError as first:
        logger.warning("rerank_response_malformed", extra={"attempt": 1, "reason": str(first)})
        try:
            result = parse_rerank_payload(call(REPAIR_INSTRUCTION))
        except RerankParseError as second:
            logger.error("rerank_response_malformed", extra={"attempt": 2, "reason": str(second)})
            raise RerankFailed(
                f"provider returned an unusable response twice: {second}"
            ) from second
        logger.info("rerank_repair_succeeded", extra={"attempt": 2})
        return result


# --------------------------------------------------------------------------- #
# What the model is shown
# --------------------------------------------------------------------------- #

# The JSON Schema the reply must satisfy. Kept next to the parser so the schema
# a provider enforces server-side and the schema the parser checks cannot drift
# apart.
RERANK_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["code", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "no_good_match": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["ranked", "no_good_match"],
    "additionalProperties": False,
}

# Synonyms are truncated per candidate: a handful of alternative surface forms
# helps the model recognise a term, but a concept carrying forty inclusion terms
# would crowd out the other candidates for no gain.
MAX_SYNONYMS_SHOWN = 8


def build_rerank_input(
    query: str,
    candidates: Sequence[Candidate],
    *,
    target_system: str,
    terminology_version: str,
) -> str:
    """Render the model's input as JSON.

    One renderer for every provider, so "what the model saw" means the same
    thing regardless of which one produced a proposal.
    """
    payload = {
        "query": query,
        "target_system": target_system,
        "terminology_version": terminology_version,
        "candidates": [
            {
                "code": candidate.code,
                "preferred_term": candidate.preferred_term,
                "synonyms": candidate.synonyms[:MAX_SYNONYMS_SHOWN],
                "retrieval": {
                    "sources": candidate.sources,
                    "lexical_score": candidate.lexical_score,
                    "vector_score": candidate.vector_score,
                },
            }
            for candidate in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Hallucinated-code guard
# --------------------------------------------------------------------------- #


def enforce_candidate_codes(
    result: RerankResult, candidates: Sequence[Candidate]
) -> tuple[RerankResult, list[str]]:
    """Drop any ranked code that was not in the candidate list.

    A model asked to rank a list will sometimes return a code that is not in it
    -- recalled from training data, or invented from the pattern of the others.
    Such a code has not been retrieved from the loaded terminology version, so
    nothing here can vouch that it exists at all. Passing it to a human as a
    suggestion, next to codes that were retrieved and verified, would give it
    borrowed credibility. It is dropped and logged.

    Returns the filtered result and the list of dropped codes.
    """
    allowed = {candidate.code for candidate in candidates}

    kept: list[RankedCode] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for entry in result.ranked:
        if entry.code not in allowed:
            dropped.append(entry.code)
            continue
        # A duplicate code would double-count in the ranking; keep the first.
        if entry.code in seen:
            dropped.append(entry.code)
            continue
        seen.add(entry.code)
        kept.append(entry)

    for code in dropped:
        logger.warning(
            "hallucinated_code",
            extra={"code": code, "candidate_count": len(allowed)},
        )

    filtered = result.model_copy(update={"ranked": kept})
    if not kept and not filtered.no_good_match:
        # Every ranked code was discarded, so there is nothing to propose.
        # Saying so explicitly beats returning an empty ranking that reads as
        # "the model had no opinion".
        filtered = filtered.model_copy(update={"no_good_match": True})
    return filtered, dropped
