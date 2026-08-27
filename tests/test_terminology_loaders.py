"""Loader tests.

These run against the sample fixtures, which replicate the *structure* the
publisher documents (16-column ICD-10-SE, 9-column KKÅ, 10-column KMÅ; header
row; one code spanning several rows). They therefore prove the loader handles
the documented shape; they cannot prove the header spellings match a real
release byte for byte -- see PHASE1_REPORT.md, marked ASSUMED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.terminology.base import (
    Concept,
    TerminologyFormatError,
    TerminologyLicenceRequired,
    TerminologySystem,
    is_code_interval,
)
from app.terminology.icd10se import ICD10SE
from app.terminology.kva import KVA
from app.terminology.snomed import SnomedCT

FIXTURES = Path(__file__).parent / "fixtures"
ICD10SE_SAMPLE = FIXTURES / "icd10se_sample.txt"


@pytest.fixture(scope="module")
def icd10se_concepts() -> dict[str, Concept]:
    return {c.code: c for c in ICD10SE().load(ICD10SE_SAMPLE, "2026-sample")}


# --------------------------------------------------------------------- loading


def test_loader_satisfies_the_protocol() -> None:
    assert isinstance(ICD10SE(), TerminologySystem)
    assert isinstance(SnomedCT(), TerminologySystem)


def test_every_code_is_loaded_once(icd10se_concepts: dict[str, Concept]) -> None:
    concepts = list(ICD10SE().load(ICD10SE_SAMPLE, "2026-sample"))
    assert len(concepts) == len(icd10se_concepts) == 27


def test_multi_row_code_is_merged_into_one_concept(
    icd10se_concepts: dict[str, Concept],
) -> None:
    """I10 spans two rows in the fixture because it has two Innefattar values."""
    i10 = icd10se_concepts["I10"]
    assert i10.preferred_term == "Essentiell hypertoni (högt blodtryck utan känd orsak)"
    assert "Högt blodtryck" in i10.synonyms
    assert any("arteriell" in s for s in i10.synonyms)


def test_latin_term_becomes_a_synonym(icd10se_concepts: dict[str, Concept]) -> None:
    assert "Hypertonia essentialis" in icd10se_concepts["I10"].synonyms


def test_exclusion_terms_are_never_synonyms(icd10se_concepts: dict[str, Concept]) -> None:
    """`Utesluter` lists what a code does NOT cover.

    Indexing it would make I21 (acute myocardial infarction) retrievable by
    "Gammal hjärtinfarkt", the exact term that rules it out.
    """
    i21 = icd10se_concepts["I21"]
    assert i21.preferred_term == "Akut hjärtinfarkt"
    assert not any("Gammal" in s for s in i21.synonyms)

    j45 = icd10se_concepts["J45"]
    assert not any("J46" in s or "svår astma" in s for s in j45.synonyms)


def test_version_is_recorded_on_every_concept(icd10se_concepts: dict[str, Concept]) -> None:
    assert {c.version for c in icd10se_concepts.values()} == {"2026-sample"}
    assert {c.system for c in icd10se_concepts.values()} == {"icd10se"}


# ----------------------------------------------------------------- hierarchy


def test_parent_links_are_read(icd10se_concepts: dict[str, Concept]) -> None:
    assert icd10se_concepts["I10"].parent_code == "I10-I15"
    assert icd10se_concepts["I11.0"].parent_code == "I11"
    assert icd10se_concepts["I00-I99"].parent_code is None


def test_is_leaf_is_derived_structurally(icd10se_concepts: dict[str, Concept]) -> None:
    assert icd10se_concepts["I11"].is_leaf is False  # has I11.0 and I11.9
    assert icd10se_concepts["I11.9"].is_leaf is True
    assert icd10se_concepts["I10"].is_leaf is True  # no subdivisions in ICD-10-SE
    assert icd10se_concepts["I00-I99"].is_leaf is False


def test_chapter_is_the_topmost_ancestor(icd10se_concepts: dict[str, Concept]) -> None:
    assert icd10se_concepts["I11.0"].chapter == "I00-I99"
    assert icd10se_concepts["I15.9"].chapter == "I00-I99"
    assert icd10se_concepts["J45.9"].chapter == "J00-J99"
    assert icd10se_concepts["E11"].chapter == "E00-E90"
    # A chapter has no chapter above it.
    assert icd10se_concepts["I00-I99"].chapter is None


def test_code_intervals_are_recognised(icd10se_concepts: dict[str, Concept]) -> None:
    assert is_code_interval("I00-I99")
    assert is_code_interval("I10-I15")
    assert not is_code_interval("I10")
    assert not is_code_interval("I11.0")
    # Intervals are loaded (the hierarchy needs them) but are not codes.
    assert "I10-I15" in icd10se_concepts


# ------------------------------------------------------------ code validation


@pytest.mark.parametrize("code", ["I10", "I21.9", "E11", "J45", "I11.0", "Z99.9", "U07.1"])
def test_valid_icd10se_codes(code: str) -> None:
    assert ICD10SE().validate_code_format(code) is True


@pytest.mark.parametrize(
    "code",
    [
        "",
        "I1",  # too few digits
        "I100",  # missing separator
        "1I0",  # digit first
        "I10.",  # trailing separator
        "I10.123",  # too many characters after the separator
        "I00-I99",  # an interval names a group, not an assignable code
        "I10 ",  # handled by stripping, but the empty variant must still fail
    ][:-1],
)
def test_invalid_icd10se_codes(code: str) -> None:
    assert ICD10SE().validate_code_format(code) is False


def test_code_validation_tolerates_surrounding_whitespace_and_case() -> None:
    assert ICD10SE().validate_code_format("  i21.9  ") is True


def test_icd10se_is_not_icd10cm() -> None:
    """ICD-10-CM codes can run to seven characters with a placeholder `X`.

    Accepting them here would silently admit codes that do not exist in Sweden.
    """
    assert ICD10SE().validate_code_format("S72.001A") is False
    assert ICD10SE().validate_code_format("T81.4XXA") is False


# ------------------------------------------------------------------- failures


def test_missing_required_column_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text('"Kod"\t"Latin"\n"I10"\t"Hypertonia essentialis"\n', encoding="utf-8")
    with pytest.raises(TerminologyFormatError, match="missing required column"):
        list(ICD10SE().load(bad, "2026"))


def test_empty_file_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "empty.txt"
    bad.write_text("", encoding="utf-8")
    with pytest.raises(TerminologyFormatError, match="empty"):
        list(ICD10SE().load(bad, "2026"))


# --------------------------------------------------------------- SNOMED stub


def test_snomed_load_refuses_with_a_licensing_message(tmp_path: Path) -> None:
    with pytest.raises(TerminologyLicenceRequired, match=r"LICENSING\.md"):
        list(SnomedCT().load(tmp_path / "anything.txt", "2026"))


def test_snomed_identifier_shape_is_checked() -> None:
    snomed = SnomedCT()
    assert snomed.validate_code_format("38341003") is True
    assert snomed.validate_code_format("12345") is False
    assert snomed.validate_code_format("I10") is False


# ------------------------------------------------------------------------ KVÅ

KKA_SAMPLE = FIXTURES / "kva_kka_sample.txt"
KMA_SAMPLE = FIXTURES / "kva_kma_sample.txt"


@pytest.fixture(scope="module")
def kva_concepts() -> dict[str, Concept]:
    """Both published KVÅ files load into the one `kva` system."""
    loader = KVA()
    concepts: dict[str, Concept] = {}
    for path in (KKA_SAMPLE, KMA_SAMPLE):
        for concept in loader.load(path, "2026-sample"):
            concepts[concept.code] = concept
    return concepts


def test_kva_loader_satisfies_the_protocol() -> None:
    assert isinstance(KVA(), TerminologySystem)


def test_both_kva_column_layouts_load(kva_concepts: dict[str, Concept]) -> None:
    """KKÅ has 9 columns and KMÅ has 10; the header drives the parse."""
    assert kva_concepts["EMA00"].preferred_term == "Incision i tonsill"  # 9-column file
    assert kva_concepts["AF015"].preferred_term == "Blodtrycksmätning standard"  # 10-column
    assert len(kva_concepts) == 19


def test_kva_concepts_share_one_system(kva_concepts: dict[str, Concept]) -> None:
    assert {c.system for c in kva_concepts.values()} == {"kva"}


def test_kva_hierarchy(kva_concepts: dict[str, Concept]) -> None:
    assert kva_concepts["EMA00"].parent_code == "EMA"
    assert kva_concepts["EMA"].is_leaf is False
    assert kva_concepts["EMA00"].is_leaf is True
    # The chapter comes from the code structure, so it is the topmost level (E)
    # and not merely the last ancestor that happens to be a row in the file.
    assert kva_concepts["EMA00"].chapter == "E"
    assert kva_concepts["EMA00"].parent_source == "column"
    # KMÅ codes carry no Överordnad kod here, so their hierarchy is derived.
    assert kva_concepts["AF015"].parent_code == "AF"
    assert kva_concepts["AF015"].parent_source == "derived"
    assert kva_concepts["AF015"].chapter == "AF"


def test_kva_exclusion_terms_are_not_synonyms(kva_concepts: dict[str, Concept]) -> None:
    assert not any("AAG00" in s for s in kva_concepts["AAA10"].synonyms)


@pytest.mark.parametrize("code", ["AAA00", "FNG05", "EMA00", "AA001", "AF015", "SS104"])
def test_valid_kva_codes(code: str) -> None:
    assert KVA().validate_code_format(code) is True


@pytest.mark.parametrize(
    "code",
    [
        "",
        "EMA",  # a group heading, not an assignable procedure code
        "AAA0",  # four characters
        "AAA000",  # six characters
        "A1234",  # only one leading letter
        "AAAA0",  # four leading letters
        "I10",  # an ICD-10-SE code is not a KVÅ code
    ],
)
def test_invalid_kva_codes(code: str) -> None:
    assert KVA().validate_code_format(code) is False


# ---------------------------------------------------------------- XLSX input

# The classifications are published as spreadsheets as well as tab-separated
# text -- KVÅ 2026 is distributed publicly only as .xlsx. Both formats must
# produce byte-identical concepts, because which one an operator happens to
# download must not change what ends up in the database.


def _write_xlsx(
    path: Path,
    header: list[object],
    rows: list[list[object]],
    *,
    sheet_title: str = "Data",
    preamble_sheet: str | None = None,
    lead_rows: list[list[object]] | None = None,
) -> Path:
    import openpyxl

    workbook = openpyxl.Workbook()
    first = workbook.active
    if preamble_sheet is not None:
        # Mirrors the published KVÅ workbook, which opens with a "Läs mig"
        # sheet carrying no header row at all.
        first.title = preamble_sheet
        first.append(["KVÅ 2026 (giltig från 2026-01-01)"])
        first.append(["2025-10-24 - version 1.0"])
        sheet = workbook.create_sheet(sheet_title)
    else:
        first.title = sheet_title
        sheet = first

    for lead in lead_rows or []:
        sheet.append(lead)
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _tsv_to_xlsx(source: Path, target: Path, **kwargs: object) -> Path:
    """Rewrite a fixture TSV as a workbook, cell for cell."""
    import csv as _csv

    with source.open("r", encoding="utf-8", newline="") as handle:
        table = list(_csv.reader(handle, delimiter="\t", quotechar='"'))
    return _write_xlsx(target, list(table[0]), [list(r) for r in table[1:]], **kwargs)  # type: ignore[arg-type]


def test_icd10se_xlsx_and_tsv_agree(tmp_path: Path) -> None:
    """The strongest form of the guarantee: same concepts, either format."""
    workbook = _tsv_to_xlsx(ICD10SE_SAMPLE, tmp_path / "icd10se.xlsx")

    from_tsv = list(ICD10SE().load(ICD10SE_SAMPLE, "2026-sample"))
    from_xlsx = list(ICD10SE().load(workbook, "2026-sample"))

    assert len(from_xlsx) == 27
    assert [c.model_dump() for c in from_xlsx] == [c.model_dump() for c in from_tsv]


def test_kva_xlsx_and_tsv_agree(tmp_path: Path) -> None:
    workbook = _tsv_to_xlsx(KKA_SAMPLE, tmp_path / "kka.xlsx")
    from_tsv = list(KVA().load(KKA_SAMPLE, "2026-sample"))
    from_xlsx = list(KVA().load(workbook, "2026-sample"))
    assert [c.model_dump() for c in from_xlsx] == [c.model_dump() for c in from_tsv]


def test_multi_row_merge_works_in_xlsx_too(tmp_path: Path) -> None:
    """I10 spans two rows for its two Innefattar values, in either format."""
    workbook = _tsv_to_xlsx(ICD10SE_SAMPLE, tmp_path / "icd10se.xlsx")
    by_code = {c.code: c for c in ICD10SE().load(workbook, "2026-sample")}
    assert "Högt blodtryck" in by_code["I10"].synonyms
    assert any("arteriell" in s for s in by_code["I10"].synonyms)


def test_leading_metadata_sheet_is_skipped(tmp_path: Path) -> None:
    """The published KVÅ workbook opens with a 'Läs mig' sheet."""
    workbook = _tsv_to_xlsx(
        KKA_SAMPLE, tmp_path / "kka.xlsx", preamble_sheet="Läs mig", sheet_title="KVÅ"
    )
    codes = {c.code for c in KVA().load(workbook, "2026-sample")}
    assert "EMA00" in codes
    assert "KVÅ 2026 (giltig från 2026-01-01)" not in codes


def test_header_below_a_title_row_is_found(tmp_path: Path) -> None:
    workbook = _tsv_to_xlsx(
        KKA_SAMPLE,
        tmp_path / "kka.xlsx",
        lead_rows=[["Hela klassifikationen KKÅ"], [], ["giltig från 2026-01-01"]],
    )
    assert {c.code for c in KVA().load(workbook, "2026-sample")} >= {"EMA00", "AAA00"}


def test_unknown_and_empty_columns_are_ignored(tmp_path: Path) -> None:
    """The real KVÅ workbook carries a `Klassifikation` column with no TSV
    counterpart, and a trailing empty column."""
    workbook = _write_xlsx(
        tmp_path / "kva.xlsx",
        ["Klassifikation", "Kod", "Titel", "Innefattar", None],
        [
            ["KVÅ-medicinska åtgärder (KMÅ)", "AF015", "Blodtrycksmätning standard", None, None],
            ["KVÅ-kirurgiska åtgärder (KKÅ)", "AAA00", "Explorativ kraniotomi", None, None],
        ],
    )
    concepts = {c.code: c for c in KVA().load(workbook, "2026")}
    assert concepts["AF015"].preferred_term == "Blodtrycksmätning standard"
    assert concepts["AAA00"].preferred_term == "Explorativ kraniotomi"
    assert concepts["AF015"].synonyms == []


def test_non_string_cells_are_coerced(tmp_path: Path) -> None:
    """A spreadsheet may hand back numbers or dates where the TSV had text."""
    import datetime as dt

    workbook = _write_xlsx(
        tmp_path / "icd.xlsx",
        ["Kod", "Giltig från", "Titel"],
        [["I10", dt.datetime(1997, 1, 1), "Essentiell hypertoni"]],
    )
    concepts = list(ICD10SE().load(workbook, "2026"))
    assert concepts[0].code == "I10"
    assert concepts[0].preferred_term == "Essentiell hypertoni"


def test_workbook_without_a_usable_header_is_reported(tmp_path: Path) -> None:
    workbook = _write_xlsx(
        tmp_path / "bad.xlsx", ["Something", "Else"], [["a", "b"]], sheet_title="Blad1"
    )
    with pytest.raises(TerminologyFormatError, match="no sheet has a header row"):
        list(ICD10SE().load(workbook, "2026"))


def test_workbook_error_names_the_sheets_examined(tmp_path: Path) -> None:
    workbook = _write_xlsx(
        tmp_path / "bad.xlsx", ["Nope"], [["x"]], sheet_title="Blad1", preamble_sheet="Läs mig"
    )
    with pytest.raises(TerminologyFormatError, match="Läs mig"):
        list(KVA().load(workbook, "2026"))


def test_a_source_without_parent_links_still_loads(tmp_path: Path) -> None:
    """The published KVÅ workbook has no `Överordnad kod` column.

    The hierarchy is not lost: KVÅ states it in the code itself, so it is read
    back out and marked `derived`. A warning is still logged, because the
    absence of the column is worth knowing.
    """
    workbook = _write_xlsx(
        tmp_path / "kva.xlsx",
        ["Kod", "Titel"],
        [["AF015", "Blodtrycksmätning standard"], ["AA001", "Adenosintest"]],
    )
    concepts = list(KVA().load(workbook, "2026"))
    assert len(concepts) == 2
    assert all(c.parent_source == "derived" for c in concepts)
    assert {c.code: c.parent_code for c in concepts} == {"AF015": "AF", "AA001": "AA"}
    assert all(c.is_leaf for c in concepts)


def test_missing_parent_column_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    workbook = _write_xlsx(tmp_path / "kva.xlsx", ["Kod", "Titel"], [["AF015", "x"]])
    with caplog.at_level("WARNING"):
        list(KVA().load(workbook, "2026"))
    assert any(
        r.getMessage() == "classification_source_has_no_parent_column" for r in caplog.records
    )
