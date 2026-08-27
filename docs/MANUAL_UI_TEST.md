# Manual test of the validator page

Ten minutes, by hand, in a browser. Automated browser tests are **not** included
— see the note at the end for why.

## Setup

```bash
docker compose up -d
docker compose exec app alembic upgrade head
docker compose exec app python scripts/load_terminology.py \
    --system icd10se --version 2026-sample --file tests/fixtures/icd10se_sample.txt
docker compose exec app python scripts/embed_terminology.py \
    --system icd10se --version 2026-sample --provider fake
```

Open <http://localhost:8000/>. You should see a teal brand bar with an
**offline pill**, and an amber **Testläge** notice under the quiet grey one.
Fill in *Validerare* once; it stays for the session.

---

## 1 · Suggestion → accept

- [ ] Type `högt blodtryck`, press **Enter** (no mouse). The button shows
      *Söker…* and is disabled while the request runs.
- [ ] Result card is headed **Förslag**, framed 2px teal with a tinted header
      strip and step badge **2**. `I10` appears as a large blue code badge with
      its Swedish term beside it.
- [ ] Instead of a number there is a badge: **Testleverantör — ingen
      säkerhetsskattning**. No decimal anywhere near the code.
- [ ] The three buttons sit *directly under* the suggestion — you do not scroll
      past a table to reach them.
- [ ] Under **Kandidater och underlag** (step badge **3**): one table, five
      rows, a *Visa alla N kandidater* button. The first row is the best match —
      tinted, 4px teal left edge, teal rank badge, **Bästa träff** pill.
- [ ] Each row shows a **Lexikal** and **Vektor** bar; a candidate the step did
      not return shows an empty bar and **—**, never a zero.
- [ ] **No percentage figure appears anywhere.**
- [ ] Press **Godkänn förslaget**. The card turns green, headed **Beslut
      registrerat**, listing decision, validator, code, version and a timestamp
      in Swedish local format. The JSON is hidden inside a `<details>`.

## 2 · Suggestion → reject, with confirmation

- [ ] Map `astma` again, press **Avslå**.
- [ ] A confirmation card appears stating exactly what will be recorded, and
      that it cannot be undone. Press **Avbryt** — nothing is recorded.
- [ ] Press **Avslå** again, then **Ja, registrera**. Decision recorded with
      *Ingen kod registrerad*.

## 3 · Suggestion → correct

- [ ] Map `högt blodtryck`. Press **Korrigera…**. An inline code field appears
      with a format hint for the selected code system.
- [ ] Enter `I15.9`, press Enter. Confirm. Recorded as *Korrigerat*.
- [ ] Repeat with `Z99.9` → inline error: *koden Z99.9 har giltigt format men
      finns inte i icd10se version 2026-sample*. Focus returns to the field.
- [ ] Repeat with `I10-I15` → inline error: *koden I10-I15 är en rubrik … inte
      en tilldelningsbar kod*.
- [ ] Repeat with `NOTACODE` → inline error about the format.

## 4 · No good match → confirm no code

- [ ] Type `banan`. The card is headed **Ingen tillräcklig träff** with an
      orange left edge. **No code is shown and no confidence is shown.**
- [ ] The explanation names the rule, its version, and the actual values it
      judged, in plain Swedish.
- [ ] There is **no Godkänn button** — only *Bekräfta: ingen kod* and *Ange kod
      manuellt…*.
- [ ] The candidates are still listed under **Kandidater och underlag**.
- [ ] Press **Bekräfta: ingen kod**, confirm. Recorded as an *Avslaget*
      decision with no code.

## 5b · Choose a candidate directly

- [ ] Map `högt blodtryck`, then press **Välj** on a row that is *not* the
      suggestion. The confirm panel names that code; confirming records it as
      **Korrigerat**.
- [ ] Repeat, pressing **Välj** on the suggested code itself. It is recorded as
      **Godkänt**, not as a correction — the two mean different things.

## 5 · No good match → manual code

- [ ] Type `banan` again, press **Ange kod manuellt…**, enter `I10`, confirm.
- [ ] Recorded as *Korrigerat* with code `I10` and a validated mapping.

## 6 · Keyboard only

Unplug the mouse, or just do not touch it.

- [ ] `Tab` from the address bar: the first stop is a visible **Hoppa till
      resultatet** skip link.
- [ ] Every control is reachable, in a sensible order, with a clearly visible
      2px focus ring — including the `<details>` summaries and each **Kopiera**
      button.
- [ ] `Enter` submits the search form and the code field.
- [ ] Expand **Spårbarhet** with the keyboard; the trace renders as terminal
      lines and the values are selectable text.
- [ ] Nothing traps focus.

## 7 · Small viewport (360px)

- [ ] Narrow the window to 360px (or use device emulation).
- [ ] The page never scrolls sideways.
- [ ] The candidate table scrolls horizontally *inside its own box*, with the
      **Kod** column pinned on the left. No column is silently hidden.
- [ ] Buttons stay at least 44px tall and do not overlap.

## 8 · Traceability

- [ ] Expand **Spårbarhet**. Proposal id, trace id, code system and version,
      LLM provider/model, prompt id and full sha256, embedding provider/model,
      the gate id/version/values, and both latencies.
- [ ] Long values wrap inside the terminal block, never across the page.

---

## Why there are no automated browser tests

Playwright was installed and then removed: it does not ship Chromium builds for
macOS 12, which is the development machine here
(`Playwright does not support chromium on mac12`). Authoring browser tests that
could not be run even once locally, and wiring them into CI on that basis, would
have risked breaking CI to produce assurance nobody had checked.

What *is* automated instead, in `tests/test_ui.py`: every result state renders
labelled and wired, the test-mode banner appears and disappears with the
provider, the accept button is absent from the no-match state, decision actions
precede the evidence table, there is exactly one table, every input has a label
and description, every button declares a type, no external resource is
referenced, and **every colour pair in the stylesheet is checked against WCAG
2.1 AA** so contrast cannot regress unnoticed.
