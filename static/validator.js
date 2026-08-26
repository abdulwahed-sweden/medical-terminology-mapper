"use strict";
/* Validator page behaviour.
 *
 * Vanilla JS, one file, no build step. The interesting part of this project is
 * the audit trail, not the front end — this only has to be correct, keyboard
 * operable, and honest about what it is showing.
 */

const $ = (id) => document.getElementById(id);
const DEFAULT_VISIBLE = 5;

let proposal = null;      // the proposal currently on screen
let pending = null;       // a decision awaiting confirmation
let showingAll = false;

/* ------------------------------------------------------------- utilities */

function show(el, visible) { el.classList.toggle("hidden", !visible); }

function setError(el, message, input) {
  el.textContent = message || "";
  show(el, Boolean(message));
  if (input) {
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }
}

function num(value, digits = 3) {
  return (value === null || value === undefined) ? "—" : Number(value).toFixed(digits);
}

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
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

function hideAllStates() {
  ["state-suggestion", "state-nomatch", "state-failed", "state-decided",
   "correct-block", "confirm-block", "evidence"].forEach((id) => show($(id), false));
}

function focusResult(el) {
  el.focus({ preventScroll: true });
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ----------------------------------------------------------------- /map */

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
  setLoading(true);
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
    hideAllStates();
    setError($("form-error"), String(err.message || err));
  } finally {
    setLoading(false);
  }
});

function setLoading(on) {
  $("submit").disabled = on;
  show($("spinner"), on);
  $("submit-label").textContent = on ? "Söker…" : "Föreslå kod";
}

/* --------------------------------------------------------------- render */

function render(p) {
  hideAllStates();
  setError($("code-error"), "");

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

  const context = [];
  if (p.chapter_label) context.push(p.chapter_label);
  $("s-context").textContent = context.join(" · ");
  show($("s-context"), context.length > 0);

  if (p.provider_kind === "fake") {
    $("s-confidence").innerHTML =
      '<span class="badge badge--test">Testleverantör — ingen säkerhetsskattning</span>';
  } else {
    $("s-confidence").textContent =
      `Modellens säkerhet: ${num(p.model_confidence, 2)} — modellens egen ` +
      `skattning, inte en kalibrerad sannolikhet.`;
  }

  const top = (p.ranked || [])[0];
  $("s-reason").textContent = top ? top.reason : "";
  show($("s-reason"), Boolean(top && top.reason));

  show($("state-suggestion"), true);
  focusResult($("state-suggestion"));
}

function renderNoMatch(p) {
  $("n-summary").textContent =
    `Sökningen på ”${p.input_text}” gav ingen tillräckligt stark träff i ` +
    `${p.target_system} ${p.terminology_version}.`;
  const g = p.gate || {};
  const v = g.values || {};
  let why;
  if (g.fired) {
    why = `Regeln ${g.id} (version ${g.version}) stoppade förslaget: ${g.reason}. ` +
          `Bästa fulltextpoäng var ${num(v.best_ts_rank)} och bästa teckenlikhet ` +
          `${num(v.best_strict_similarity)}.`;
  } else {
    why = "Modellen bedömde att ingen av kandidaterna passar.";
  }
  $("n-why").textContent = why + " Kandidaterna finns kvar under ”Kandidater och underlag”.";
  show($("state-nomatch"), true);
  focusResult($("state-nomatch"));
}

function renderFailed(p) {
  show($("state-failed"), true);
  focusResult($("state-failed"));
}

/* -------------------------------------------------------------- evidence */

function renderEvidence(p) {
  const isFake = p.provider_kind === "fake";
  show($("th-confidence"), !isFake);

  const rankByCode = new Map((p.ranked || []).map((r, i) => [r.code, i + 1]));
  const reasonByCode = new Map((p.ranked || []).map((r) => [r.code, r.reason]));
  const confByCode = new Map((p.ranked || []).map((r) => [r.code, r.model_confidence]));

  const all = p.candidates || [];
  const rows = showingAll ? all : all.slice(0, DEFAULT_VISIBLE);
  const FIELD = { title: "Term", synonym: "Synonym", description: "Beskrivning", vector: "Vektor" };

  $("table-caption").textContent =
    `${all.length} kandidater hämtades. Visar ${rows.length}.`;

  const body = $("cand-rows");
  body.innerHTML = "";
  for (const c of rows) {
    const tr = document.createElement("tr");
    const hasLexical = (c.sources || []).includes("lexical");
    if (hasLexical) tr.classList.add("has-lexical");
    if (c.code === p.suggested_code) tr.classList.add("is-suggested");

    const sources = (c.sources || [])
      .map((s) => `<span class="badge">${s === "lexical" ? "Lexikal" : "Vektor"}</span>`)
      .join(" ");
    const modelRank = rankByCode.has(c.code) ? `#${rankByCode.get(c.code)}` : "—";
    const conf = isFake ? "" :
      `<td class="num">${num(confByCode.get(c.code), 2)}</td>`;

    tr.innerHTML =
      `<td class="code-cell">${esc(c.code)}</td>` +
      `<td>${esc(c.preferred_term)}</td>` +
      `<td>${sources}</td>` +
      `<td>${esc(FIELD[c.matched_field] || "—")}</td>` +
      `<td class="num">${num(c.lexical_score)}</td>` +
      `<td class="num">${num(c.vector_score)}</td>` +
      `<td class="num">${num(c.fused_score, 5)}</td>` +
      `<td class="num">${modelRank}</td>` + conf +
      `<td>${esc(reasonByCode.get(c.code) || "")}</td>`;
    body.appendChild(tr);
  }

  const more = all.length > DEFAULT_VISIBLE;
  show($("btn-show-all"), more);
  if (more) {
    $("btn-show-all").textContent = showingAll
      ? `Visa färre kandidater` : `Visa alla ${all.length} kandidater`;
  }
}

$("btn-show-all").addEventListener("click", () => {
  showingAll = !showingAll;
  renderEvidence(proposal);
});

/* ----------------------------------------------------------- traceability */

function renderTrace(p) {
  const g = p.gate || {};
  const rows = [
    ["Förslag-id", p.id, true],
    ["Spårnings-id", p.trace_id, true],
    ["Kodverk och version", `${p.target_system} ${p.terminology_version}`, false],
    ["LLM-leverantör och modell", `${p.llm_provider} / ${p.llm_model}`, false],
    ["Prompt", `${p.prompt_id} · ${String(p.prompt_hash).slice(0, 12)}…`, true, p.prompt_hash],
    ["Inbäddning", `${p.embedding_provider} / ${p.embedding_model}`, false],
    ["Regel (gate)", `${g.id} v${g.version} · ${g.fired ? "stoppade" : "släppte igenom"}`, false],
    ["Regelns värden", JSON.stringify(g.values || {}), true],
    ["Hämtningstid", `${p.latency_ms_retrieval} ms`, false],
    ["Omrankningstid", `${p.latency_ms_rerank} ms`, false],
  ];

  const box = $("trace-body");
  box.innerHTML = "";
  for (const [label, value, copyable, fullValue] of rows) {
    const row = document.createElement("div");
    row.className = "trace-row";
    row.innerHTML = `<span class="label">${esc(label)}</span>` +
                    `<span class="value">${esc(value)}</span>`;
    if (copyable) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn--small";
      btn.textContent = "Kopiera";
      btn.setAttribute("aria-label", `Kopiera ${label}`);
      const confirmation = document.createElement("span");
      confirmation.className = "copied hidden";
      confirmation.textContent = "kopierat";
      confirmation.setAttribute("role", "status");
      btn.addEventListener("click", async () => {
        const payload = String(fullValue !== undefined ? fullValue : value);
        try { await navigator.clipboard.writeText(payload); }
        catch { /* clipboard unavailable; the value is on screen regardless */ }
        show(confirmation, true);
        setTimeout(() => show(confirmation, false), 2000);
      });
      row.appendChild(btn);
      row.appendChild(confirmation);
    }
    box.appendChild(row);
  }
}

/* -------------------------------------------------------------- decisions */

function codeHint() {
  const system = proposal ? proposal.target_system : "";
  return system === "kva"
    ? "Fem tecken, till exempel AF015 eller AAA00."
    : "Bokstav och två siffror, eventuellt med punkt, till exempel I15.9.";
}

function openCorrect() {
  $("code-hint").textContent = codeHint();
  show($("correct-block"), true);
  setError($("code-error"), "", $("final_code"));
  $("final_code").focus();
}

$("btn-correct").addEventListener("click", openCorrect);
$("btn-nomatch-correct").addEventListener("click", openCorrect);
$("btn-failed-correct").addEventListener("click", openCorrect);
$("btn-correct-cancel").addEventListener("click", () => {
  show($("correct-block"), false);
  $("btn-correct").focus();
});

$("btn-accept").addEventListener("click", () => submitDecision("accept", null));
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

$("final_code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("btn-correct-submit").click(); }
});

function askConfirm(kind, code) {
  pending = { kind, code };
  const validator = $("validator_id").value.trim();
  const where = `${proposal.target_system} ${proposal.terminology_version}`;
  $("confirm-text").textContent = kind === "reject"
    ? `Ingen kod registreras för ”${proposal.input_text}”. Beslutet sparas som ` +
      `avslag av ${validator}.`
    : `Koden ${code} registreras för ”${proposal.input_text}” i ${where}, av ${validator}.`;
  show($("confirm-block"), true);
  $("btn-confirm-yes").focus();
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
  const fields = [
    ["Beslut", words[d.decision] || d.decision],
    ["Validerare", d.validator_id],
    ["Kod", d.final_code || "Ingen kod registrerad"],
    ["Kodverk och version", `${p.target_system} ${p.terminology_version}`],
    ["Tidpunkt", stockholm(d.created_at)],
  ];
  if (d.validator_note) fields.push(["Kommentar", d.validator_note]);

  const dl = $("d-fields");
  dl.innerHTML = "";
  for (const [label, value] of fields) {
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = value;
    dl.appendChild(dt); dl.appendChild(dd);
  }

  show($("d-json-wrap"), Boolean(p.validated_mapping));
  if (p.validated_mapping) {
    $("d-json").textContent = JSON.stringify(p.validated_mapping, null, 2);
  }

  ["state-suggestion", "state-nomatch", "state-failed",
   "correct-block", "confirm-block"].forEach((id) => show($(id), false));
  show($("state-decided"), true);
  focusResult($("state-decided"));
}

function detailOf(body) {
  if (!body) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  return null;
}
