# Design Change Plan — Validator UI (Al-Noor Design System v1.1)

**Status: PLAN ONLY. Nothing in this document has been implemented.**

Source: `Clinical mapping.zip` → `design_handoff_terminology_mapper/`
(`README.md`, `DESIGN-SYSTEM-v1.1.md`, `Terminology Mapper v2.dc.html`).

This document reviews the handoff against the actual codebase, states what can
be adopted directly, what needs a decision from you first, and in what order the
work should be done.

---

## 1. Summary

The handoff is unusually good. It is specific, internally consistent, names
exact tokens, and explicitly says the prototypes are *design references to
recreate*, not code to copy. It also correctly identifies our architecture
(Jinja + `static/validator.css` + `static/validator.js`, no framework) and asks
that the existing DOM ids be preserved.

**Verified: all eight DOM ids it asks us to keep already exist** in
`templates/validator.html` — `#map-form`, `#state-suggestion`, `#state-nomatch`,
`#correct-block`, `#confirm-block`, `#state-decided`, `#evidence`, `#cand-rows`.

The visual direction is compatible with the project. Three things are not, and
one of them is a product-safety question, not a styling question.

| | Item | Verdict |
| --- | --- | --- |
| 🔴 | **Match percentage on every candidate** | **Blocked — needs your decision** |
| 🟠 | Autocomplete dropdown | New backend endpoint required |
| 🟠 | Bilingual sv/EN toggle | New capability, not restyling |
| 🟠 | Inter + JetBrains Mono webfonts | Conflicts with our offline rule |
| 🟡 | 9 colour pairs fail our WCAG gate | Fixable, corrections computed below |
| 🟡 | Candidate table becomes flex `<div>`s | Accessibility regression risk |
| 🟢 | Everything else (layout, cards, states, tokens) | Adopt as specified |

---

## 2. 🔴 Blocking issue: the match percentage

### What the design asks for

> "**Match percentage out of 100 on every candidate**, bold tabular figure …
> colour-graded: green ≥70, amber 30–69, gray <30; larger on the best match and
> repeated on the proposal hero." — `DESIGN-SYSTEM-v1.1.md` §5

### Why this is blocked

**The prototype's percentages are hand-written constants. There is no formula.**

```js
{ code: 'I10',   pct: 96, lex: 1.0,  vec: 0.424 }
{ code: 'I15',   pct: 84, lex: 1.0,  vec: 0.371 }
{ code: 'I15.1', pct: 22, lex: null, vec: 0.112 }
```

`I10` and `I15` have *identical* lexical scores (1.0) but are shown as 96% and
84%. No function of the real data produces those numbers. The handoff README
confirms it: *"the simulated match % is placeholder — wire to the real API
responses."* But our API has no such field, and inventing one is the problem.

This collides directly with the safety work already in the product:

- We removed `model_confidence` from display in test mode **specifically
  because a number in a screenshot travels without its caveat**.
- We added the retrieval gate **specifically because** `banan` returning
  *E11 Diabetes mellitus typ 2* at a confident-looking 0.90 was the worst
  failure mode this system can have.
- `ARCHITECTURE.md` and `README.md` both state that no calibrated probability
  is available and none is claimed.

A bold green **96 %** beside a diagnosis code reads to a clinician as *"the
system is 96% sure."* Nothing in the pipeline supports that claim. Note the
prototype even shows percentages (7%, 6%, 4%) in the **no-good-match** state —
the one state whose entire purpose is to say *we found nothing*.

### Options

**Option A — Drop the percentage. Keep the visual slot.** *(recommended)*
Use the slot for the evidence we can defend: the source badges (Lexikal /
Vektor), the score bars, and `matched_field` (Term / Synonym / Beskrivning).
Keeps the design's rhythm and colour grading, drops the unsupported claim.

**Option B — Relabel it as retrieval strength, not confidence.**
Show the existing RRF or normalised lexical score as a 0–100 figure, labelled
`SÖKSTYRKA` (retrieval strength), never `TRÄFFGRAD` / `MATCH`. Requires a
written definition in `ARCHITECTURE.md`, a caption on the page, and it must be
**hidden in the no-good-match state**. Honest, but a percentage still invites
misreading.

**Option C — Implement as designed.** Not recommended. It would contradict
documented product guarantees and re-open the exact defect the gate was built to
close.

**Decision needed before Phase 2 can start.**

---

## 3. 🟠 Autocomplete requires new backend work

The design specifies a live dropdown after 2 characters, with rows showing
*blue mono code + term + kind tag (synonym/code)*.

**We have no endpoint for this.** Current API surface is `POST /map`,
`GET /proposals/{id}`, `POST /decisions`, `GET /`, `GET /health`.

This needs:

- A new `GET /suggest?q=&system=&version=` route returning a small, capped list.
- A query path optimised for prefix/short input. Our current lexical search is
  tuned for whole phrases, and there is a known performance note: the trigram
  predicate is a **sequential scan (~145 ms over 11 888 concepts)** because the
  planner does not choose the GIN index. Typeahead fires on every keystroke, so
  this must be addressed — likely a prefix index on `preferred_term` plus a
  strict result cap, not the full hybrid retrieval.
- Debouncing, request cancellation, and an ARIA combobox pattern
  (`role="combobox"`, `aria-expanded`, `aria-activedescendant`) for keyboard use.

This is **feature work, not restyling.** It should be its own phase and its own
review.

---

## 4. 🟠 Bilingual sv/EN

Today the app is Swedish-only and a test asserts `lang="sv"`. The design wants a
full SV/EN toggle with all strings in both languages.

The prototype holds strings in a JS `L` object. Recreating that gives us:

- A string table in `validator.js` for client-rendered text.
- Jinja-side strings in `validator.html` also need both languages.
- `lang` must update on `<html>` when toggled, and the choice should persist
  (`localStorage`).
- **Server-produced strings are the catch.** Our gate reasons, decision errors
  and validation messages are generated in Python **in Swedish**
  (`"koden {code} är en rubrik…"`, `"ingen fulltextträff…"`). A language toggle
  that leaves half the page Swedish is worse than no toggle.

**Recommendation:** treat i18n as a separate phase. Either translate the backend
messages too (move them to message keys), or ship SV-only now and add the toggle
when the backend strings are keyed.

---

## 5. 🟠 Fonts vs. the offline requirement

The design specifies **Inter + JetBrains Mono**. The prototype loads them from
`fonts.googleapis.com`. The handoff says "self-host for the offline
requirement".

We enforce this with a test that fails on any `http://` or `https://` in the
template, CSS or JS. That test exists because the product is meant to run in an
environment where clinical text must not leave the server.

| Approach | Cost |
| --- | --- |
| Self-host full families | ~6 font files, several hundred KB of **binaries** in a repo that currently has **zero** binary files |
| Self-host subset (latin + latin-ext, weights actually used) | ~4 WOFF2 files, roughly 100–160 KB |
| Keep system font stack | 0 KB, loses the specified typography |

**Recommendation:** self-host a **subset** — latin + latin-ext (Swedish needs
å ä ö), only the weights the design uses (Inter 500/600/700/800, JetBrains Mono
400/600). Add `docs/FONTS.md` recording source, version and licence (both are
SIL OFL, redistribution permitted). Keep a full system-font fallback stack so
the page is still correct if the fonts fail to load.

---

## 6. 🟡 Accessibility: 9 contrast failures

I measured every colour pair in `DESIGN-SYSTEM-v1.1.md` against the WCAG 2.1 AA
thresholds our test suite already enforces.

**Passing (13 of 22 text pairs):** ink-900, ink-700, ink-500, teal, data blue,
green, amber, red, brand subtitle, test-notice text, terminal value, confirm
panel text. The palette is largely sound.

**Failing, must fix:**

| Token / use | Current | Measured | Needs | Suggested |
| --- | --- | --- | --- | --- |
| `ink-300` — placeholders, CAPS match label (10.5px), dropdown kind tags | `#8FA09C` | **2.74:1** | 4.5:1 | `#687A76` (4.53:1) |
| Terminal label column | `#75806D` | **3.70:1** | 4.5:1 | `#687161` (4.54:1) |
| Input / secondary-button border (1.5px) | `#C6C1B2` | **1.80:1** | 3.0:1 | `#9D947B` (3.02:1) |
| Vector score bar on its track | `#A9C2EC` | **1.50:1** | 3.0:1 | `#5586D9` (3.01:1) |

Suggested values preserve hue and saturation and only reduce lightness to the
minimum needed, so the design intent survives.

**Failing but defensible — no change needed:**

- Card border `#DCD8CC` (1.42:1) and row separator `#EDEAE0` (1.20:1) are
  *decorative separation inside a card*, not component boundaries. WCAG 1.4.11
  covers controls and meaningful graphics, not dividers. This is the same
  position the current stylesheet already takes and documents.
- Disabled button text `#8A9591` on `#EDEAE0` (2.57:1). WCAG 1.4.3 and 1.4.11
  both **exempt inactive components**.

**Also to fix:** the row-level *Choose* button is specified at `8px × 17px`
padding, giving roughly **33 px** height. Our rule and WCAG 2.5.5 target 44 px.
Either enlarge it or extend the hit area with padding while keeping the visual
size.

---

## 7. 🟡 Candidate table becomes `<div>`s

The prototype contains **zero** `<table>`, `<tr>` or `<th>` elements — the
candidate list is 30 flex containers. Our current template uses a real table
with `<caption>` and `<th scope="col">`.

The candidate list *is* tabular: code, term, source, scores, action. Rebuilding
it as flex `<div>`s loses row/column association for screen readers, which is a
real regression for a product whose accessibility we test.

**Recommendation:** keep a real `<table>` and achieve the design's look with
CSS (`display:block`/flex on rows at narrow widths, table semantics preserved).
If the visual design genuinely cannot be expressed that way, use explicit ARIA
grid roles — but a native table is better and cheaper.

---

## 8. What can be adopted as-is

No objection to any of this; it is a clear improvement on the current page:

- Brand bar, teal `#0F5E68`, 1120px container, offline pill.
- Quiet notices (13px, 3px soft left edge) — the current banners are heavier
  than they need to be.
- The numbered 1 → 2 → 3 flow (search → result → evidence).
- Card geometry: 16px radius, 1px `#DCD8CC`, the specified shadow.
- **State cards**: 2px frame + tinted header strip + step badge, in teal /
  amber / green. This strengthens exactly the thing we care about — the four
  states must never look alike.
- Uniform 46px controls with 1.5px borders and radius 10.
- The **deep-teal confirm panel** `#0B4A52`. Making the irreversible step look
  unlike any other card is good safety design.
- The **light-terminal block** for the validated mapping — mono lines, muted
  label column, blue code. Better than our current definition list.
- Example chips under the search field.
- Score bars for Lex/Vec, and "—" for absent values (never zero). This matches
  a rule we already hold: absent is not the same as zero.
- The trace footer contents map 1:1 to fields we already store.

---

## 9. Proposed phases

Each phase ends green: `pytest`, `ruff`, `ruff format`, `mypy --strict`, and CI.

| Phase | Scope | Size | Depends on |
| --- | --- | --- | --- |
| **0** | Decide the match-% question (§2). Confirm font approach (§5). Confirm i18n scope (§4). | Decision | You |
| **1** | **Token layer.** Rewrite `:root` in `validator.css` to the v1.1 palette with the §6 corrections. Extend the contrast test to cover every new pair. No markup change. | S | 0 |
| **2** | **Static restyle.** Brand bar, notices, search card, state cards, confirm panel, terminal block, evidence rows. Same DOM ids, same states, no new behaviour. Keep the `<table>` (§7). | L | 1 |
| **3** | **Fonts.** Self-hosted subset + `docs/FONTS.md` + fallback stack. | S | 0 |
| **4** | **Evidence row redesign.** Rank badges, best-match treatment, source badges, score bars, per-row *Choose* wired to the existing `correct` decision path. | M | 2 |
| **5** | **Autocomplete.** New `GET /suggest` endpoint + index work + debounced ARIA combobox. Own review. | L | 2 |
| **6** | **i18n.** Only if §4 is resolved to include backend message keys. | M | 0, 2 |

Phases 1–4 are a genuine improvement and carry low product risk. Phase 5 is
feature work. Phase 6 is feature work with a backend dependency.

---

## 10. Test impact

These existing tests will need updating (not deleting) as markup changes:

- `test_each_state_has_a_distinct_card_modifier` — class names change.
- `test_semantic_landmarks_and_table_structure` — must still pass; keep the
  table (§7).
- `test_there_is_exactly_one_candidate_table` — must still pass.
- `test_no_external_resources_are_referenced` — **must keep passing.** Self-host
  the fonts; do not weaken this test.
- `test_contrast_meets_wcag_aa` — extend to the full v1.1 palette.
- `test_touch_targets_meet_the_minimum` — the *Choose* button must comply.
- `test_stylesheet_uses_tokens_not_magic_colours` — the design specifies many
  literal hexes inline; all must land in `:root`.

New tests to add: state-card colour per state, terminal block renders the six
required fields, "—" renders for absent scores, and (Phase 5) the suggest
endpoint's cap and behaviour.

---

## 11. Open questions

1. **Match percentage — A, B, or C?** (§2) Blocks Phase 2.
2. Do we ship SV-only for now, or translate backend messages too? (§4)
3. Self-hosted font subset acceptable, given it adds the repo's first binaries?
4. The design says "no confidence figure in test mode" — with a real provider,
   should `model_confidence` appear in the hero, and with what wording?
5. The *Choose* action records a candidate as the decision. Should that be
   `correct` (a human-supplied code) even when the chosen code equals the
   suggestion — or `accept` in that one case? These mean different things in the
   audit trail.
6. `DESIGN-SYSTEM-v1.0.md` is superseded — confirm we track only v1.1.

---

## 12. Recommendation

Adopt the design. It is better than what we have, and most of it costs only
styling work.

Proceed **Phase 1 → 3 → 2 → 4** once question 1 is answered, holding phases 5
and 6 as separate pieces of feature work. My recommendation on question 1 is
**Option A**: keep the visual slot, drop the percentage, and let the source
badges and score bars carry the evidence. It preserves the design's structure
and rhythm while keeping the product's central promise intact — the system
proposes, a person decides, and the interface never claims more certainty than
the pipeline can support.
