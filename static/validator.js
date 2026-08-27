"use strict";
/* Validator page behaviour — Al-Noor v1.1 layout.
 *
 * Vanilla JS, one file, no build step. The state machine and every DOM id are
 * unchanged from the previous revision; what changed is how each state is
 * drawn.
 *
 * One thing deliberately NOT implemented from the design: the per-candidate
 * match percentage. The prototype's figures were hand-authored constants, and
 * no function of the real scores reproduces them. A bold percentage beside a
 * diagnosis code claims a certainty this pipeline cannot support -- the same
 * failure the retrieval gate exists to prevent. The slot carries the evidence
 * that is real instead: source badges, the matched field, and the score bars.
 */

const $ = (id) => document.getElementById(id);
const DEFAULT_VISIBLE = 5;

let proposal = null;   // the proposal on screen
let pending = null;    // a decision awaiting confirmation
let showingAll = false;

const FIELD_LABEL = {
  title: "Term", synonym: "Synonym", description: "Beskrivning", vector: "Vektor",
};

/* ------------------------------------------------------------- utilities */

const show = (el, on) => el.classList.toggle("hidden", !on);

function setError(el, message, input) {
  el.textContent = message || "";
  show(el, Boolean(message));
  if (input) {
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }
}

const num = (v, d = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

function esc(v) {
  return String(v === null || v === undefined ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stockholm(iso) {
  try {
    return new Intl.DateTimeFormat("sv-SE", {
      dateStyle: "long", timeStyle: "short", timeZone: "Europe/Stockholm",
    }).format(new Date(iso));
  } catch { return iso; }
}

function hideAll() {
  ["state-suggestion", "state-nomatch", "state-failed", "state-decided",
   "correct-block", "confirm-block", "evidence"].forEach((id) => show($(id), false));
}

function focusState(el) {
  el.focus({ preventScroll: true });
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ------------------------------------------------------------------ /map */

$("examples").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-example]");
  if (!chip) return;
  $("text").value = chip.dataset.example;
  $("text").focus();
});

$("map-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  const validator = $("validator_id").value.trim();
  setError($("form-error"), "");
  setError($("text-error"), text ? "" : "Skriv en klinisk term.", $("text"));
  setError($("validator-error"), validator ? "" : "Ange vem som validerar.", $("validator_id"));
  if (!text) { $("text").focus(); return; }
  if (!validator) { $("validator_id").focus(); return; }

  const [system, version] = $("target").value.split("|");
  loading(true);
  try {
    const response = await fetch("/map", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, target_system: system, version }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(detailOf(body) || response.statusText);
    proposal = body;
    showingAll = false;
    render(body);
  } catch (err) {
    hideAll();
    setError($("form-error"), String(err.message || err));
  } finally {
    loading(false);
  }
});

function loading(on) {
  $("submit").disabled = on;
  show($("spinner"), on);
  $("submit-label").textContent = on ? "Söker…" : "Föreslå kod";
}

/* --------------------------------------------------------------- render */

function render(p) {
  hideAll();
  setError($("code-error"), "");
  const where = `${p.target_system} · ${p.terminology_version}`;
  $("s-system").textContent = where;
  $("n-system").textContent = where;

  if (p.status === "no_good_match") renderNoMatch(p);
  else if (p.status === "rerank_failed") renderFailed(p);
  else renderSuggestion(p);

  renderEvidence(p);
  renderTrace(p);
  show($("evidence"), true);
}

function renderSuggestion(p) {
  $("s-code").textContent = p.suggested_code;
  $("s-term").textContent = p.suggested_term || "";
  const top = (p.ranked || [])[0];
  $("s-reason").textContent = top ? top.reason : "";

  if (p.provider_kind === "fake") {
    $("s-confidence").innerHTML =
      '<span class="tag tag--test">Testleverantör — ingen säkerhetsskattning</span>';
    $("s-note").textContent = "Ingen säkerhetsskattning i testläge";
  } else {
    $("s-confidence").textContent =
      `Modellens säkerhet: ${num(p.model_confidence, 2)} — modellens egen ` +
      `skattning, inte en kalibrerad sannolikhet.`;
    $("s-note").textContent = "";
  }
  show($("state-suggestion"), true);
  focusState($("state-suggestion"));
}

function renderNoMatch(p) {
  $("n-summary").textContent =
    `Ingen kod föreslås för ”${p.input_text}”.`;
  const g = p.gate || {}, v = g.values || {};
  $("n-why").textContent = g.fired
    ? `Sökningen gav inget tillräckligt starkt underlag i ${p.target_system} ` +
      `${p.terminology_version}. Regeln ${g.id} (v${g.version}) stoppade förslaget: ` +
      `${g.reason}. Bästa fulltextpoäng ${num(v.best_ts_rank)}, bästa teckenlikhet ` +
      `${num(v.best_strict_similarity)}. Modellen anropades inte.`
    : "Modellen bedömde att ingen av kandidaterna passar.";
  show($("state-nomatch"), true);
  focusState($("state-nomatch"));
}

function renderFailed() {
  show($("state-failed"), true);
  focusState($("state-failed"));
}

/* -------------------------------------------------------------- evidence */

function bar(value, kind) {
  if (value === null || value === undefined) {
    return `<span class="bar bar--absent"><span class="bar__track"></span>` +
           `<span class="bar__val">—</span></span>`;
  }
  const pctWidth = Math.max(0, Math.min(1, Number(value))) * 100;
  return `<span class="bar"><span class="bar__track">` +
         `<span class="bar__fill bar__fill--${kind}" style="width:${pctWidth.toFixed(1)}%"></span>` +
         `</span><span class="bar__val">${num(value)}</span></span>`;
}

function renderEvidence(p) {
  const isFake = p.provider_kind === "fake";
  show($("th-confidence"), !isFake);

  const rankBy = new Map((p.ranked || []).map((r, i) => [r.code, i + 1]));
  const reasonBy = new Map((p.ranked || []).map((r) => [r.code, r.reason]));
  const confBy = new Map((p.ranked || []).map((r) => [r.code, r.model_confidence]));

  const all = p.candidates || [];
  const rows = showingAll ? all : all.slice(0, DEFAULT_VISIBLE);
  $("table-caption").textContent = `${all.length} kandidater hämtades. Visar ${rows.length}.`;

  const body = $("cand-rows");
  body.innerHTML = "";
  rows.forEach((c, index) => {
    const tr = document.createElement("tr");
    const isBest = index === 0;
    if (isBest) tr.classList.add("is-best");

    const badges = (c.sources || [])
      .map((s) => s === "lexical"
        ? '<span class="tag tag--lex">Lexikal</span>'
        : '<span class="tag">Vektor</span>').join("");
    const bestPill = isBest ? '<span class="tag tag--best">Bästa träff</span>' : "";
    const reason = reasonBy.get(c.code);
    const conf = isFake ? "" : `<td class="num">${num(confBy.get(c.code), 2)}</td>`;
    const modelRank = rankBy.has(c.code) ? `#${rankBy.get(c.code)}` : "—";

    tr.innerHTML =
      `<td><span class="rank">${index + 1}</span></td>` +
      `<td class="cell-code">${esc(c.code)}</td>` +
      `<td><span class="cell-term">${esc(c.preferred_term)}</span>` +
        `<span class="cell-sub">${bestPill}${badges}` +
        (reason ? `<span class="cell-reason">${esc(reason)}</span>` : "") +
        `</span></td>` +
      `<td><span class="tag tag--field">${esc(FIELD_LABEL[c.matched_field] || "—")}</span></td>` +
      `<td>${bar(c.lexical_score, "lex")}</td>` +
      `<td>${bar(c.vector_score, "vec")}</td>` +
      `<td class="num">${num(c.fused_score, 5)}<br>` +
        `<span class="bar__val">${modelRank}</span></td>` + conf +
      `<td><button type="button" class="btn btn--sm" data-choose="${esc(c.code)}">Välj</button></td>`;
    body.appendChild(tr);
  });

  const more = all.length > DEFAULT_VISIBLE;
  show($("btn-show-all"), more);
  if (more) {
    $("btn-show-all").textContent = showingAll
      ? "Visa färre kandidater" : `Visa alla ${all.length} kandidater`;
  }
}

$("btn-show-all").addEventListener("click", () => {
  showingAll = !showingAll;
  renderEvidence(proposal);
});

/* Choosing a candidate records it as the decision. If it is the code the
 * system already suggested, that is an `accept`; anything else is a
 * `correct`. The two mean different things in the audit trail. */
$("cand-rows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-choose]");
  if (!button || !proposal) return;
  const code = button.dataset.choose;
  if (proposal.suggested_code && code === proposal.suggested_code) askConfirm("accept", null);
  else askConfirm("correct", code);
});

/* ---------------------------------------------------------- traceability */

function termRow(key, value, isCode) {
  return `<div class="terminal__row"><span class="terminal__key">${esc(key)}</span>` +
         `<span class="terminal__val${isCode ? " terminal__val--code" : ""}">${esc(value)}</span></div>`;
}

function renderTrace(p) {
  const g = p.gate || {};
  $("trace-body").innerHTML = [
    termRow("förslag-id", p.id),
    termRow("spårnings-id", p.trace_id),
    termRow("kodverk", `${p.target_system} ${p.terminology_version}`),
    termRow("modell", `${p.llm_provider} / ${p.llm_model}`),
    termRow("prompt", `${p.prompt_id} · sha256 ${p.prompt_hash}`),
    termRow("inbäddning", `${p.embedding_provider} / ${p.embedding_model}`),
    termRow("regel", `${g.id} v${g.version} · ${g.fired ? "stoppade" : "släppte igenom"}`),
    termRow("regelvärden", JSON.stringify(g.values || {})),
    termRow("tider", `hämtning ${p.latency_ms_retrieval} ms · omrankning ${p.latency_ms_rerank} ms`),
  ].join("");
}

/* ------------------------------------------------------------- decisions */

const codeHint = () => (proposal && proposal.target_system === "kva")
  ? "Fem tecken, till exempel AF015 eller AAA00."
  : "Bokstav och två siffror, eventuellt med punkt, till exempel I15.9.";

function openCorrect(prefill) {
  $("code-hint").textContent = codeHint();
  if (prefill) $("final_code").value = prefill;
  show($("correct-block"), true);
  setError($("code-error"), "", $("final_code"));
  $("final_code").focus();
}

$("btn-correct").addEventListener("click", () => openCorrect());
$("btn-nomatch-correct").addEventListener("click", () => openCorrect());
$("btn-failed-correct").addEventListener("click", () => openCorrect());
$("btn-correct-cancel").addEventListener("click", () => {
  show($("correct-block"), false);
  $("btn-correct").focus();
});

$("btn-accept").addEventListener("click", () => askConfirm("accept", null));
$("btn-reject").addEventListener("click", () => askConfirm("reject", null));
$("btn-nomatch-confirm").addEventListener("click", () => askConfirm("reject", null));
$("btn-failed-reject").addEventListener("click", () => askConfirm("reject", null));

$("btn-correct-submit").addEventListener("click", () => {
  const code = $("final_code").value.trim();
  if (!code) {
    setError($("code-error"), "Ange en kod.", $("final_code"));
    $("final_code").focus();
    return;
  }
  askConfirm("correct", code);
});

$("final_code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); $("btn-correct-submit").click(); }
});

function askConfirm(kind, code) {
  pending = { kind, code };
  const validator = $("validator_id").value.trim();
  const where = `${proposal.target_system} ${proposal.terminology_version}`;
  const term = proposal.input_text;
  $("confirm-text").textContent =
    kind === "reject"
      ? `Ingen kod registreras för ”${term}”. Beslutet sparas som avslag av ${validator}.`
      : kind === "accept"
        ? `Koden ${proposal.suggested_code} registreras för ”${term}” i ${where}, av ${validator}.`
        : `Koden ${code} registreras för ”${term}” i ${where}, av ${validator}.`;
  show($("confirm-block"), true);
  $("btn-confirm-yes").focus();
  $("confirm-block").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("btn-confirm-no").addEventListener("click", () => {
  show($("confirm-block"), false);
  pending = null;
});

$("btn-confirm-yes").addEventListener("click", () => {
  if (pending) submitDecision(pending.kind, pending.code);
});

async function submitDecision(kind, finalCode) {
  if (!proposal) return;
  const validator = $("validator_id").value.trim();
  if (!validator) {
    setError($("validator-error"), "Ange vem som validerar.", $("validator_id"));
    $("validator_id").focus();
    return;
  }
  setError($("code-error"), "");
  setError($("form-error"), "");
  try {
    const response = await fetch("/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal_id: proposal.id,
        decision: kind,
        final_code: finalCode || null,
        validator_note: $("note").value.trim() || null,
        validator_id: validator,
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      const message = detailOf(body) || response.statusText;
      show($("confirm-block"), false);
      if (kind === "correct") {
        openCorrect(finalCode);
        setError($("code-error"), message, $("final_code"));
        $("final_code").focus();
      } else {
        setError($("form-error"), message);
      }
      return;
    }
    proposal = body;
    renderDecision(body);
  } catch (err) {
    setError($("form-error"), String(err.message || err));
  }
}

function renderDecision(p) {
  const d = p.decision;
  const words = { accept: "Godkänt", reject: "Avslaget", correct: "Korrigerat" };
  $("d-when").textContent = stockholm(d.created_at);

  const rows = [
    ["system", p.target_system, false],
    ["version", p.terminology_version, false],
    ["kod", d.final_code || "Ingen kod registrerad", Boolean(d.final_code)],
    ["beslut", words[d.decision] || d.decision, false],
    ["validerare", d.validator_id, false],
    ["beslut-id", d.id, false],
  ];
  if (d.validator_note) rows.push(["kommentar", d.validator_note, false]);
  $("d-fields").innerHTML = rows.map(([k, v, c]) => termRow(k, v, c)).join("");

  ["state-suggestion", "state-nomatch", "state-failed",
   "correct-block", "confirm-block"].forEach((id) => show($(id), false));
  show($("state-decided"), true);
  focusState($("state-decided"));
}

function detailOf(body) {
  if (!body) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return null;
}
