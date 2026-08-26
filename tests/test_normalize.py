"""Normalization tests."""

from __future__ import annotations

import unicodedata

import pytest

from app.normalize.swedish import normalize


def test_case_and_whitespace_are_folded_together() -> None:
    """The example from the specification: these must be indistinguishable."""
    assert normalize("Högt Blodtryck").normalized == normalize("högt   blodtryck").normalized
    assert normalize("Högt Blodtryck").normalized == "högt blodtryck"


@pytest.mark.parametrize(
    "raw",
    [
        "Högt Blodtryck",
        "  högt blodtryck  ",
        "HÖGT\tBLODTRYCK",
        "högt\n\nblodtryck",
        "Högt, blodtryck.",
        "högt  blodtryck!",
    ],
)
def test_surface_variants_collapse_to_one_form(raw: str) -> None:
    assert normalize(raw).normalized == "högt blodtryck"


def test_swedish_letters_survive() -> None:
    """å ä ö are letters, not decorated vowels.

    Transliterating them would merge words that differ only by them.
    """
    assert normalize("Åderbråck").normalized == "åderbråck"
    assert normalize("förmaksflimmer").normalized == "förmaksflimmer"
    assert normalize("hjärtinfarkt").normalized == "hjärtinfarkt"
    assert "a" not in normalize("Öron").normalized
    assert normalize("hår").normalized != normalize("har").normalized


def test_decomposed_and_composed_forms_agree() -> None:
    """NFD "o + combining diaeresis" must equal NFC "ö".

    The two forms are built here rather than typed as literals: an editor or a
    git filter that normalises the source file would otherwise quietly turn
    this into a test of nothing.
    """
    composed = unicodedata.normalize("NFC", "högt blodtryck")
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed  # guards the premise of the test
    assert normalize(decomposed).normalized == normalize(composed).normalized


def test_word_internal_hyphens_are_kept() -> None:
    assert normalize("non-invasiv").normalized == "non-invasiv"
    assert normalize("non-invasiv").tokens == ["non-invasiv"]


def test_edge_hyphens_are_dropped() -> None:
    """ "hjärt- och njursjukdom" keeps the words, drops the dangling hyphen."""
    assert normalize("hjärt- och njursjukdom").tokens == ["hjärt", "och", "njursjukdom"]
    assert normalize("-lumbago-").normalized == "lumbago"


def test_punctuation_is_stripped() -> None:
    assert normalize("Astma, ospecificerad").normalized == "astma ospecificerad"
    assert normalize("Hypertoni (arteriell)").normalized == "hypertoni arteriell"
    assert normalize("I21.9: akut hjärtinfarkt").normalized == "i21 9 akut hjärtinfarkt"


def test_tokens_match_the_normalized_string() -> None:
    result = normalize("Essentiell hypertoni (högt blodtryck)")
    assert result.tokens == ["essentiell", "hypertoni", "högt", "blodtryck"]
    assert " ".join(result.tokens) == result.normalized


def test_original_is_preserved_verbatim() -> None:
    """The proposal stores both; the untouched input is part of the audit trail."""
    raw = "  Högt   Blodtryck!  "
    assert normalize(raw).original == raw


def test_empty_and_punctuation_only_input() -> None:
    for raw in ["", "   ", "...", "!!!"]:
        result = normalize(raw)
        assert result.normalized == ""
        assert result.tokens == []


def test_normalization_is_idempotent() -> None:
    once = normalize("Högt Blodtryck!").normalized
    assert normalize(once).normalized == once


def test_digits_are_kept() -> None:
    """Terms such as "diabetes mellitus typ 2" carry meaning in the digit."""
    assert normalize("Diabetes mellitus typ 2").normalized == "diabetes mellitus typ 2"
