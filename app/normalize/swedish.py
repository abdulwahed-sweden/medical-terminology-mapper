"""Swedish text normalization.

Deliberately shallow: NFC, casefold, whitespace collapse, punctuation removal.
No stemming and no lemmatization in Phase 1.

Why so little: Swedish medical vocabulary is dominated by compounds
("blodtrycksmätning", "högerhjärtsvikt"), and splitting or stemming them
correctly is a research problem, not a utility function. A wrong decompounder
silently changes which codes are retrievable, and the failure is invisible
until someone audits a mapping. Cheap and deterministic beats clever and
unverifiable here. Recorded as a known limitation in ARCHITECTURE.md.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel

# `å ä ö` are letters of the Swedish alphabet, not decorated a/o. Transliterating
# them merges distinct words -- "hår" (hair) and "har" (has), "för" and "for" --
# so normalization must never touch them.
_KEEP = re.compile(r"[^\w\s-]", flags=re.UNICODE)
_WS = re.compile(r"\s+")
# A hyphen is meaningful inside a word ("non-invasiv", "hjärt- och njursjukdom")
# but is just punctuation at a word edge.
_EDGE_HYPHEN = re.compile(r"(?<!\w)-+|-+(?!\w)")


class NormalizedText(BaseModel):
    """The normalized string and its tokens, carried together.

    Both are returned because they serve different consumers: the string goes
    to PostgreSQL's text search and trigram similarity, the tokens to anything
    that needs to count or compare terms.
    """

    original: str
    normalized: str
    tokens: list[str]


def normalize(text: str) -> NormalizedText:
    """Normalize a Swedish clinical phrase.

    Unicode NFC, then casefold, then punctuation stripped except word-internal
    hyphens, then whitespace collapsed.
    """
    # NFC first, so a decomposed "a + combining ring above" becomes a single "å"
    # and compares equal to the composed form in the terminology files.
    folded = unicodedata.normalize("NFC", text).casefold()
    folded = unicodedata.normalize("NFC", folded)

    stripped = _KEEP.sub(" ", folded)
    stripped = _EDGE_HYPHEN.sub(" ", stripped)
    normalized = _WS.sub(" ", stripped).strip()

    return NormalizedText(
        original=text,
        normalized=normalized,
        tokens=normalized.split(" ") if normalized else [],
    )
