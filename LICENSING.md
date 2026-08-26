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

> **Where the machine-readable files are.** The public pages listed above
> currently offer the PDF publications and, for KVÅ, an XLSX. The tab-separated
> **code-text files** (`.tsv`) — the ones the loaders in this repository are
> written for — were previously linked from the Socialstyrelsen download page
> and now appear to sit behind E-hälsomyndigheten's collaboration portal
> (`samarbetsyta.ehalsomyndigheten.se`), which was not publicly reachable when
> this was checked. If you cannot find them, contact the classification team;
> the published contact address is `klassif@socialstyrelsen.se`.
> Marked **UNVERIFIED** in `PHASE1_REPORT.md`.

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

Source for the formats: `beskrivning-filinnehall-kka.pdf`,
`beskrivning-filinnehall-kma.pdf`, and `kva-inkl-beskrivningstexter-2026.xlsx`.

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

The **codes, Swedish titles, Latin terms, and inclusion/exclusion terms in these
files were transcribed from the official publications cited above**, so that
retrieval and evaluation are exercised against real terminology rather than
invented strings. Nothing was invented. The `Giltig från` values are
placeholders and the loader ignores that column.

They are nonetheless tiny, partial, and structurally incomplete: `chapter` and
`is_leaf` are correct *within the sample* and meaningless outside it. See
`tests/fixtures/README.md`.

---

## 6. Software licence

The **code** in this repository is licensed under the terms in
[`LICENSE`](LICENSE). That licence covers the software only. It grants no
rights whatsoever in ICD-10, ICD-10-SE, KVÅ, or SNOMED CT, none of which are
included here.
