# Terminology licensing and provenance

**All information below was checked on 2026-08-26.** URLs and organisational
responsibilities in this area change; re-check before relying on them.

This repository contains **no terminology content**. It contains loaders, a
documented description of the official file formats, and small clearly-labelled
sample fixtures. To use it for real work you download the official files
yourself, under whatever terms the publisher sets.

---

## 1. Who publishes the Swedish classifications

**Responsibility moved on 1 June 2026.** Until then, Socialstyrelsen (the
National Board of Health and Welfare) published and maintained ICD-10-SE, KVÅ,
and the Swedish SNOMED CT edition. On **1 June 2026** those interoperability
tasks — the classifications, the national information structure, and the SNOMED
CT National Release Centre role — transferred to **E-hälsomyndigheten** (the
Swedish eHealth Agency), together with roughly 20 staff.

Verified directly: the old Socialstyrelsen download page now returns
`301 Moved Permanently` to E-hälsomyndigheten.

```
https://www.socialstyrelsen.se/statistik-och-data/klassifikationer-och-koder/kodtextfiler/
  -> 301 -> https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/klassifikationer/klassifikationen-icd-10/
```

| What | Where, as of 2026-08-26 |
| --- | --- |
| Classifications landing page | <https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/klassifikationer/> |
| ICD-10-SE | <https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/klassifikationer/klassifikationen-icd-10/> |
| KVÅ | <https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/klassifikationer/klassifikation-av-vardatgarder-kva/> |
| SNOMED CT | <https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/snomed-ct/> |
| Browsable search service | <https://klassifikationer.socialstyrelsen.se/> |

### Machine-readable distribution — what is actually downloadable

Re-checked **2026-08-26**. The two classifications are *not* published the same
way, which matters because it decides what the loaders can be verified against.

| Classification | Machine-readable file | Status |
| --- | --- | --- |
| **KVÅ** | [`kva-inkl-beskrivningstexter-2026.xlsx`](https://www.ehalsomyndigheten.se/siteassets/ehm/2_verksamhet/ndi/interoperabilitet/klassifikationer/kva/kva-inkl-beskrivningstexter-2026.xlsx) (501 KB) | **Publicly downloadable.** Complete classification, 11 888 codes. Downloaded and parsed on 2026-08-26 — see §3. |
| **ICD-10-SE** | none | **Not publicly downloadable.** The ICD-10 page offers only the three PDF volumes and the coding guidance. |

The tab-separated **code-text files** (`.tsv`) that Socialstyrelsen previously
published for both classifications are no longer linked from either public
page. They appear to have moved behind E-hälsomyndigheten's collaboration
portal (`samarbetsyta.ehalsomyndigheten.se`), which was not publicly reachable
when this was checked.

For ICD-10-SE specifically, no machine-readable file could be obtained:
the page HTML contains no `.xlsx`, `.tsv`, `.csv` or `.zip` reference at all,
and eleven candidate filenames following both sibling naming conventions
(KVÅ's `<name>-inkl-beskrivningstexter-<year>.xlsx` and KSI's
`<name>-<year>.xlsx`) all returned HTTP 404. If you have portal access or a
copy, one file is all it takes to lift the remaining `FORMAT_UNVERIFIED` mark —
see `PHASE1_REPORT.md`. The published contact address is
`klassif@socialstyrelsen.se`.

**The loaders read both formats** (`.tsv` and `.xlsx`), dispatching on the file
extension, with the same tolerant header matching either way.

### Terms of use

Neither authority page stated an explicit licence for the classification files
at the date checked. The ICD-10-SE publications carry:

> © World Health Organization 1992 — "Denna publikation skyddas av
> upphovsrättslagen. Vid citat ska källan uppges."
> (This publication is protected by copyright law. Cite the source.)

ICD-10 itself is published by WHO; Socialstyrelsen is stated to be solely
responsible for the Swedish translation. **Establish the terms that apply to
your use with the publisher before redistributing any of this content.** This
repository redistributes none of it.

---

## 2. ICD-10-SE

**ICD-10-SE is derived from WHO ICD-10.** The underlying classification is
*International Statistical Classification of Diseases and Related Health
Problems, Tenth Revision (ICD-10)*, published by the **World Health
Organization** in 1992 and © World Health Organization 1992. ICD-10-SE is the
Swedish-language version of it; the publications state that Socialstyrelsen is
solely responsible for the Swedish translation. Any use of ICD-10-SE content is
therefore subject to WHO's rights in ICD-10 as well as to the Swedish
publisher's terms.

- Current release: **svensk version 2026**, valid from **2026-01-01**.
- OID: `1.2.752.116.1.1.1`
- Scale of the release: 82 490 rows, 38 928 unique codes.
- Code-text file: UTF-8, tab-separated, **16 columns**, header row, every cell
  quoted, and one code spanning several rows when it carries repeated
  properties.
- U-codes that can be brought into use at short notice (63 of them) are
  distributed in a **separate file**.

Source for the format: the publisher's file-description document,
`beskrivning-filinnehall-icd-10-se.pdf`, and the systematic listing
(artikelnummer 2026-1-9989 / -9990 / -9991).

### ICD-10-SE is not ICD-10-CM

ICD-10-SE is the **Swedish** adaptation of WHO ICD-10. ICD-10-CM is the
**United States** clinical modification. They have different code spaces,
different levels of subdivision, and different coding rules.

Tools built for ICD-10-CM must not be assumed compatible. A US validator would
accept codes that do not exist in Sweden (`T81.4XXA`, `S72.001A` — seven
characters with placeholder `X`) and would apply the wrong rules to the ones
that do. `app/terminology/icd10se.py` implements the ICD-10-SE code shape
directly and rejects ICD-10-CM shapes; there is a test for exactly this.

The same caution applies to any ICD-10 lookup service, MCP server, or dataset
that does not say explicitly which national version it carries.

---

## 3. KVÅ (Klassifikation av vårdåtgärder)

KVÅ is the union of two published classifications, distributed as two files:

| Part | Contents | OID | Format |
| --- | --- | --- | --- |
| **KKÅ** | Kirurgiska åtgärder (surgical) | `1.2.752.116.1.3.2.3.6` | TSV, **9 columns** |
| **KMÅ** | Medicinska åtgärder (medical) | `1.2.752.116.1.3.2.3.5` | TSV, **10 columns** |

- Current release: valid from **2026-01-01**; 11 888 five-position codes
  (KKÅ 7 115 + KMÅ 4 773).
- Code shapes, verified against the published 2026 release:
  **KKÅ = three letters + two digits** (`AAA00`, `FNG05`); **KMÅ = two letters
  + three digits** (`AA001`, `AF015`). Bare three-letter entries such as `EMA`
  appear as group headings and are not assignable procedure codes.
- KVÅ is mandatory for reporting to Socialstyrelsen's health data registers.

### The published workbook, as parsed on 2026-08-26

| | |
| --- | --- |
| File | `kva-inkl-beskrivningstexter-2026.xlsx` (501 KB) |
| URL | <https://www.ehalsomyndigheten.se/siteassets/ehm/2_verksamhet/ndi/interoperabilitet/klassifikationer/kva/kva-inkl-beskrivningstexter-2026.xlsx> |
| Downloaded | 2026-08-26 |
| Sheets | `Läs mig` (metadata), `KVÅ – (KKÅ+KMÅ)` (data, header on row 1) |
| Rows | 11 888 data rows, one per code — KKÅ and KMÅ merged into one sheet |
| Columns | `Klassifikation`, `Kod`, `Titel`, `Beskrivning`, `Exempel`, `Innefattar`, `Utesluter`, `Kodningsinformation`, `Anmärkning`, `Relaterad ICF-kod`, plus one trailing empty column |

Loading this file with `scripts/load_terminology.py` produces exactly 11 888
concepts, matching the count the workbook states for itself.

Two differences from the TSV layout the file-description PDFs document:

- The workbook adds a **`Klassifikation`** column (KKÅ or KMÅ), which the TSVs
  do not have — it exists because the workbook merges the two files. It maps to
  no `Concept` field and is ignored.
- The workbook has **no `Överordnad kod` column**, so KVÅ loaded from it has no
  parent links: `chapter` is empty and every concept is a leaf. This is a real
  loss of information relative to the TSV, not a parsing bug, and the loader
  logs a warning (`classification_source_has_no_parent_column`) so it cannot
  pass unnoticed.

Source for the TSV formats: `beskrivning-filinnehall-kka.pdf` and
`beskrivning-filinnehall-kma.pdf`.

Load both files with the same `--version`; see the repository README.

---

## 4. SNOMED CT — licence required, no content shipped

**This repository ships no SNOMED CT content and implements no SNOMED CT
loader.** `app/terminology/snomed.py` implements the adapter interface, and its
`load()` raises `TerminologyLicenceRequired` pointing here. Implementing it is
Phase 4.

SNOMED CT is licensed by SNOMED International. Sweden is a member country, so
use within Sweden is covered by the national licence, administered by the
**National Release Centre** — a role that transferred from Socialstyrelsen to
**E-hälsomyndigheten** on 1 June 2026.

As checked on 2026-08-26: the national licence for access to SNOMED CT in text
format is **free of charge** and covers both the international and the Swedish
editions. The Swedish edition contains roughly 375 000 active concepts with
recommended Swedish terms. **The licence is granted to you, not to this
repository** — apply for it yourself before loading any SNOMED CT content, and
do not commit that content to a public repository.

Start at <https://www.ehalsomyndigheten.se/verksamhet/ndi/interoperabilitet/snomed-ct/>.

---

## 5. Sample data in this repository

Everything below is **hand-built sample data**. It is not a terminology release
and must not be used for coding, statistics, reimbursement, or any production
purpose.

| Path | What it is |
| --- | --- |
| `tests/fixtures/icd10se_sample.txt` | 25 codes in the 16-column ICD-10-SE layout |
| `tests/fixtures/kva_kka_sample.txt` | 10 codes in the 9-column KKÅ layout |
| `tests/fixtures/kva_kma_sample.txt` | 9 codes in the 10-column KMÅ layout |
| `evaluation/gold/sample_icd10se.csv` | 12-row sample gold set, marked `SAMPLE ONLY` |

### Provenance of the rows

Every row in these files is an **excerpt transcribed from a publication issued
by E-hälsomyndigheten**, retrieved on **2026-08-26** from the URLs below. They
are excerpts — a few dozen entries out of tens of thousands — reproduced so that
retrieval and evaluation are exercised against real terminology rather than
invented strings. **Nothing was invented**, and no complete classification, or
any substantial part of one, is reproduced.

| Sample file | Transcribed from | Retrieved |
| --- | --- | --- |
| `tests/fixtures/icd10se_sample.txt` and `evaluation/gold/sample_icd10se.csv` | *ICD-10-SE — Systematisk förteckning, svensk version 2026*, Del 1–3 (artikelnummer `2026-1-9989`, `-9990`, `-9991`), PDF, `ehalsomyndigheten.se/siteassets/ehm/2_verksamhet/ndi/interoperabilitet/klassifikationer/icd-10/` | 2026-08-26 |
| `tests/fixtures/kva_kka_sample.txt`, `tests/fixtures/kva_kma_sample.txt` | *KVÅ 2026 inkl. beskrivningstexter*, `kva-inkl-beskrivningstexter-2026.xlsx`, `ehalsomyndigheten.se/siteassets/ehm/2_verksamhet/ndi/interoperabilitet/klassifikationer/kva/` | 2026-08-26 |

The ICD-10-SE excerpts carry the WHO copyright described in §2, since ICD-10-SE
is a Swedish-language version of WHO ICD-10; the source is cited here as those
publications require. The `Giltig från` values in the fixtures are
placeholders, not transcribed, and the loader ignores that column. The KVÅ
fixtures' `Överordnad kod` values are inferred from the code structure, because
the published workbook carries no parent column.

They are nonetheless tiny, partial, and structurally incomplete: `chapter` and
`is_leaf` are correct *within the sample* and meaningless outside it. See
`tests/fixtures/README.md`.

---

## 6. Software licence

The **code** in this repository is licensed under the terms in
[`LICENSE`](LICENSE). That licence covers the software only. It grants no
rights whatsoever in ICD-10, ICD-10-SE, KVÅ, or SNOMED CT, none of which are
included here.
