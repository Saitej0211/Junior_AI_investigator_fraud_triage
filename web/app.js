const TIERS = ["High", "Medium", "Low"];
const ACTIONS = ["Close as false positive", "Request documentation", "Escalate to SIU"];

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  Object.assign(node, attrs);
  if (text) node.textContent = text;
  return node;
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    // FastAPI validation errors come back as a list; show the first message.
    const detail = Array.isArray(data.detail) ? data.detail[0].msg : data.detail;
    throw new Error(detail || "Something went wrong.");
  }
  return data;
}

// ---- Who is reviewing (kept in this browser only) ----------------------

function investigator() {
  return document.getElementById("investigator").value.trim();
}

function requireInvestigator() {
  const name = investigator();
  if (!name) {
    const input = document.getElementById("investigator");
    input.focus();
    input.classList.add("invalid");
  }
  return name;
}

function setupInvestigator() {
  const input = document.getElementById("investigator");
  try { input.value = localStorage.getItem("investigator") || ""; } catch {}
  input.addEventListener("input", () => {
    input.classList.remove("invalid");
    try { localStorage.setItem("investigator", input.value.trim()); } catch {}
  });
}

// ---- Queue ---------------------------------------------------------------

const queue = { cases: [], shown: [], filter: "all", hideReviewed: false };

// On wide screens the sidebar is always there, so a case is always open; on
// phones it covers the queue, so only open one when asked.
const wide = window.matchMedia("(min-width: 901px)");

// A case needs a closer look when the AI's work raised a warning (a fact the data
// doesn't confirm, a score that doesn't match its level) or hard evidence keeps it open.
function needsLook(c) {
  return c.grounding_issues.length > 0 || c.floor_applied;
}

const FILTERS = {
  all: { label: "All", test: () => true },
  look: { label: "Needs a closer look", test: needsLook },
  High: { label: "High", test: (c) => c.tier === "High" },
  Medium: { label: "Medium", test: (c) => c.tier === "Medium" },
  Low: { label: "Low", test: (c) => c.tier === "Low" },
};

const TIER_HINTS = {
  High: "Likely to need the fraud team",
  Medium: "Need records or a closer look",
  Low: "Likely false alarms",
};

// One card per risk level (each a shortcut for its filter) and a progress card.
function renderCounts() {
  const total = queue.cases.length;
  document.getElementById("queue-sub").textContent =
    `${total} cases flagged by the fraud engine, highest AI risk first. ` +
    "The AI scores and explains each case; every fact it cites is checked against the data.";
  const box = document.getElementById("counts");
  box.replaceChildren();
  for (const tier of TIERS) {
    const n = queue.cases.filter((c) => c.tier === tier).length;
    const card = el("button", { type: "button", className: `stat stat-${tier.toLowerCase()}`,
      title: `Show ${tier} risk cases` });
    card.setAttribute("aria-pressed", String(queue.filter === tier));
    card.append(el("div", { className: "stat-label" }, `${tier} risk`),
      el("div", { className: "stat-n" }, String(n)),
      el("div", { className: "stat-hint" }, TIER_HINTS[tier]));
    card.addEventListener("click", () => {
      queue.filter = queue.filter === tier ? "all" : tier; // click again to show all
      renderQueue();
    });
    box.append(card);
  }
  const reviewed = queue.cases.filter((c) => c.decision).length;
  const card = el("div", { className: "stat stat-progress" });
  const n = el("div", { className: "stat-n" }, String(reviewed));
  n.append(el("span", { className: "stat-of" }, ` / ${total}`));
  const bar = el("div", { className: "bar" });
  const fill = el("span");
  fill.style.width = `${total ? (100 * reviewed) / total : 0}%`;
  bar.append(fill);
  // Where reviewed cases have moved to, e.g. "3 escalated to SIU · 5 closed".
  const stages = {};
  for (const c of queue.cases) if (c.decision) stages[c.decision.stage] = (stages[c.decision.stage] || 0) + 1;
  card.append(el("div", { className: "stat-label" }, "Reviewed"), n, bar, el("div", { className: "stat-hint" },
    Object.entries(stages).map(([stage, k]) => `${k} ${stage[0].toLowerCase()}${stage.slice(1)}`).join(" · ")));
  box.append(card);
}

function renderAlerts() {
  const box = document.getElementById("alerts");
  box.replaceChildren();
  // Each case lists the others, so only report a group from its lowest case_id.
  for (const c of queue.cases) {
    const others = c.shares_claim_number_with;
    if (others.length && others.every((o) => c.case_id < o)) {
      box.append(el("div", { className: "alert" },
        `Same claim number on ${[c.case_id, ...others].join(" and ")}`));
    }
  }
}

function renderFilters() {
  const box = document.getElementById("filters");
  box.replaceChildren();
  for (const [key, f] of Object.entries(FILTERS)) {
    const n = queue.cases.filter(f.test).length;
    const btn = el("button", { type: "button", className: "filter" }, `${f.label} (${n})`);
    btn.setAttribute("aria-pressed", String(queue.filter === key));
    btn.addEventListener("click", () => { queue.filter = key; renderQueue(); });
    box.append(btn);
  }
}

function cleanLowPending() {
  return queue.cases.filter((c) => c.clean_low && !c.decision);
}

function renderBulk() {
  const btn = document.getElementById("bulk-accept");
  const n = cleanLowPending().length;
  btn.textContent = `Accept ${n} clean Low case${n === 1 ? "" : "s"}`;
  btn.disabled = n === 0;
}

function statusCell(c) {
  const cell = el("td");
  const d = c.decision;
  if (!d) {
    cell.append(el("span", { className: "status todo" }, "To review"));
  } else {
    // The stage the decision moved the case to; overrides are marked as such.
    cell.append(el("span", { className: `status ${d.decision === "accept" ? "done" : "override"}` }, `→ ${d.stage}`));
    if (d.decision === "override") cell.append(el("div", { className: "muted" }, "AI overridden"));
  }
  return cell;
}

// The AI's 0-100 score and its risk level.
function riskCell(c) {
  const cell = el("td", { className: "risk" });
  cell.append(el("span", { className: "score-num" }, String(c.score)), " ",
    el("span", { className: `badge tier-${c.tier.toLowerCase()}` }, c.tier));
  return cell;
}

function assessmentCell(c) {
  const cell = el("td", { className: "assessment" });
  const a = c.assessment;
  if (!a) {
    cell.append(el("span", { className: "muted" },
      c.ai_status === "skipped" ? "Rules only" : "AI unavailable"));
    return cell;
  }
  cell.append(el("div", { className: "action" }, a.recommended_action));
  cell.append(el("div", { className: "clamp" }, a.summary));
  if (c.grounding_issues.length) {
    cell.append(el("div", { className: "warn", title: c.grounding_issues.join("\n") },
      `⚠ ${c.grounding_issues.length} thing${c.grounding_issues.length > 1 ? "s" : ""} to double-check`));
  }
  return cell;
}

// The top three concerns; the rest are in the case panel.
function concernsCell(c) {
  const cell = el("td", { className: "concerns" });
  if (!c.concerns.length) {
    cell.append(el("span", { className: "muted" }, "No warning signs"));
    return cell;
  }
  const text = el("div", { className: "clamp" }, c.concerns.slice(0, 3).join(", "));
  const more = c.concerns.length - 3;
  if (more > 0) text.append(el("span", { className: "muted" }, ` +${more} more`));
  cell.append(text);
  return cell;
}

function renderQueue() {
  renderCounts();
  renderFilters();
  renderBulk();
  const test = FILTERS[queue.filter].test;
  const shown = queue.cases.filter((c) => test(c) && !(queue.hideReviewed && c.decision));
  queue.shown = shown;
  const body = document.getElementById("queue");
  body.replaceChildren();
  for (const c of shown) {
    const row = el("tr", { className: "clickable", tabIndex: 0 });
    row.dataset.caseId = c.case_id;
    row.classList.toggle("selected", c.case_id === current?.id);
    row.classList.toggle("reviewed", Boolean(c.decision));
    row.addEventListener("click", () => openCase(c.case_id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openCase(c.case_id); });
    row.append(
      el("td", { className: "num muted" }, String(c.serial)),
      el("td", { className: "case-id" }, c.case_id),
      riskCell(c),
      statusCell(c),
      assessmentCell(c),
      concernsCell(c),
    );
    body.append(row);
  }
  if (!shown.length) {
    const td = el("td", { colSpan: 6, className: "muted empty-row" }, "No cases match this view.");
    const tr = el("tr");
    tr.append(td);
    body.append(tr);
  }
  updatePosition();
  // Keep the open case in step with the view: if a filter hid it, open the
  // first visible case instead.
  if (wide.matches && shown.length && !shown.some((c) => c.case_id === current?.id)) {
    openCase(shown[0].case_id);
  }
}

async function bulkAccept() {
  const name = requireInvestigator();
  if (!name) return;
  const ids = cleanLowPending().map((c) => c.case_id);
  const ok = confirm(`Accept the AI's "Close as false positive" on ${ids.length} cases?\n\n` +
    "Only cases the AI rates Low, with no hard evidence and nothing to double-check, " +
    "are included. Each one is recorded under your name and moved to Closed.");
  if (!ok) return;
  try {
    await postJSON("/api/bulk-accept", { case_ids: ids, investigator: name });
    await loadQueue();
  } catch (e) {
    alert(e.message);
  }
}

async function loadQueue() {
  const res = await fetch("/api/cases");
  if (!res.ok) {
    const err = document.getElementById("error");
    err.textContent = (await res.json()).detail;
    err.hidden = false;
    return;
  }
  queue.cases = await res.json();
  // Position in the risk-sorted queue; it stays with the case when filtering.
  queue.cases.forEach((c, i) => { c.serial = i + 1; });
  renderAlerts();
  renderQueue();
}

function setupQueue() {
  document.getElementById("bulk-accept").addEventListener("click", bulkAccept);
  document.getElementById("hide-reviewed").addEventListener("change", (e) => {
    queue.hideReviewed = e.target.checked;
    renderQueue();
  });
}

// ---- Case panel -------------------------------------------------------

const SUGGESTED = [
  "Why this risk level?",
  "How does this compare with similar cases?",
  "What should I request first?",
];

let current = null; // { id, history: [{question, answer}] }

function citationList(items) {
  const list = el("ul", { className: "cites" });
  for (const i of items) {
    const li = el("li", { className: i.verified ? "" : "unverified" });
    li.append(el("span", { className: "mark", title: i.verified
      ? "Checked against the case data" : "Couldn't be confirmed in the case data" },
      i.verified ? "✓" : "⚠"));
    const fact = el("div");
    fact.append(el("strong", {}, `${i.label}: `), i.shown);
    li.append(fact);
    if (i.why) li.append(el("div", { className: "why" }, i.why));
    list.append(li);
  }
  return list;
}

function bulletList(items) {
  const list = el("ul");
  for (const text of items) list.append(el("li", {}, text));
  return list;
}

function when(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function describeDecision(d) {
  const verb = d.decision === "accept" ? "Accepted the AI's call" : "Overrode the AI";
  return `${verb}: moved to ${d.stage} (${d.final_risk_level} risk)`;
}

// The investigator's decision: accept the AI's call, or override it with a reason.
function decisionBox(c) {
  const box = el("section", { className: "decision" });
  box.append(el("h3", {}, "Your decision"));
  const d = c.decision;
  const controls = el("div", { className: "decision-controls" });

  if (d) {
    box.append(el("p", { className: "decided" }, describeDecision(d)));
    box.append(el("div", { className: "muted" }, `${d.investigator} · ${when(d.created_at)}`));
    if (d.reason) box.append(el("p", { className: "why" }, `Reason: ${d.reason}`));
    const change = el("button", { type: "button", className: "link-btn" }, "Change decision");
    change.addEventListener("click", () => { controls.hidden = false; change.hidden = true; });
    box.append(change);
    controls.hidden = true;
  }

  const a = c.assessment;
  const accept = el("button", { type: "button", className: "primary" },
    a ? `Accept: ${a.recommended_action}` : "Accept");
  accept.disabled = !a;
  const override = el("button", { type: "button" }, "Override…");
  const buttons = el("div", { className: "btn-row" });
  buttons.append(accept, override);

  const form = el("form", { className: "override-form" });
  form.hidden = true;
  const level = el("select", { name: "risk_level", required: true });
  for (const t of TIERS) level.append(el("option", { value: t }, `${t} risk`));
  const action = el("select", { name: "action", required: true });
  for (const x of ACTIONS) action.append(el("option", { value: x }, x));
  if (a) { level.value = a.risk_level; action.value = a.recommended_action; }
  const reason = el("textarea", { name: "reason", rows: 3, required: true,
    placeholder: "Why are you deciding differently from the AI?" });
  const save = el("button", { type: "submit", className: "primary" }, "Save override");
  const cancel = el("button", { type: "button" }, "Cancel");
  const formButtons = el("div", { className: "btn-row" });
  formButtons.append(save, cancel);
  form.append(level, action, reason, formButtons);

  const error = el("div", { className: "error-text" });
  error.hidden = true;

  async function submit(body) {
    const name = requireInvestigator();
    if (!name) {
      error.textContent = "Enter your name at the top first.";
      error.hidden = false;
      return;
    }
    try {
      await postJSON(`/api/cases/${c.case_id}/decision`, { ...body, investigator: name });
      await loadQueue();
      // Move on to the next case still to review, as an investigator would.
      const next = nextToReview(c.case_id);
      if (next) await openCase(next.case_id); else await refreshCase();
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  }

  accept.addEventListener("click", () => submit({ decision: "accept" }));
  override.addEventListener("click", () => { form.hidden = false; buttons.hidden = true; reason.focus(); });
  cancel.addEventListener("click", () => { form.hidden = true; buttons.hidden = false; error.hidden = true; });
  reason.addEventListener("input", () => { error.hidden = true; });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!reason.value.trim()) {
      error.textContent = "Add a reason for the override.";
      error.hidden = false;
      return;
    }
    submit({ decision: "override", risk_level: level.value, action: action.value,
             reason: reason.value.trim() });
  });

  controls.append(buttons, form, error);
  box.append(controls);
  return box;
}

function notesBox(c) {
  const box = el("section", { className: "notes" });
  box.append(el("h3", {}, "Notes"));
  for (const n of c.history.notes) {
    const item = el("div", { className: "note" });
    item.append(el("div", {}, n.text),
      el("div", { className: "muted" }, `${n.investigator} · ${when(n.created_at)}`));
    box.append(item);
  }
  const form = el("form", { className: "note-form" });
  const text = el("textarea", { rows: 2, placeholder: "Add a note for the file…" });
  const add = el("button", { type: "submit" }, "Add note");
  const error = el("div", { className: "error-text" });
  error.hidden = true;
  form.append(text, add, error);
  text.addEventListener("input", () => { error.hidden = true; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = requireInvestigator();
    if (!text.value.trim() || !name) {
      error.textContent = name ? "Write a note first." : "Enter your name at the top first.";
      error.hidden = false;
      return;
    }
    try {
      await postJSON(`/api/cases/${c.case_id}/notes`, { text: text.value.trim(), investigator: name });
      await refreshCase();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
  box.append(form);
  return box;
}

function auditTrail(c) {
  const box = el("details", { className: "audit" });
  box.append(el("summary", {}, `Decision history (${c.history.decisions.length})`));
  if (!c.history.decisions.length) box.append(el("p", { className: "muted" }, "No decisions yet."));
  for (const d of [...c.history.decisions].reverse()) {
    const item = el("div", { className: "note" });
    item.append(el("div", {}, describeDecision(d)));
    if (d.reason) item.append(el("div", { className: "why" }, `Reason: ${d.reason}`));
    item.append(el("div", { className: "muted" },
      `${d.investigator} · ${when(d.created_at)} · AI had said ${d.ai_action ?? "nothing"}`));
    box.append(item);
  }
  return box;
}

// The AI's score as a card, with a bar on the 0-100 scale.
function scoreCard(title, score, level, note) {
  const card = el("div", { className: `score-card card-${level.toLowerCase()}` });
  const n = el("div", { className: "stat-n" }, score == null ? "–" : String(Math.round(score)));
  n.append(el("span", { className: "stat-of" }, " / 100"));
  const meter = el("div", { className: "meter" });
  const fill = el("span");
  fill.style.width = `${score ?? 0}%`;
  meter.append(fill);
  card.append(el("div", { className: "stat-label" }, title), n, meter,
    el("span", { className: `badge tier-${level.toLowerCase()}` }, `${level} risk`),
    el("div", { className: "stat-hint" }, note));
  return card;
}

function comparison(c, a) {
  const box = el("section", { className: "compare" });
  box.append(scoreCard("AI risk score", a.risk_score, a.risk_level, `${a.confidence} confidence`));
  return box;
}

function renderCase(c) {
  const serial = queue.cases.find((q) => q.case_id === c.case_id)?.serial;
  document.getElementById("case-title").textContent = serial ? `#${serial} · ${c.case_id}` : c.case_id;
  const body = document.getElementById("case-body");
  body.replaceChildren();

  body.append(el("p", { className: "meta muted" },
    `${c.claim.care_type} · $${c.claim.claim_amount_usd.toLocaleString()} · ${c.claim.state}`));
  const a = c.assessment;
  body.append(comparison(c, a));
  if (c.floor_reason) {
    body.append(el("div", { className: "alert" },
      `Can't be closed automatically: ${c.floor_reason}.`));
  }

  if (!a) {
    body.append(el("p", { className: "muted" }, "No AI assessment for this case."));
  } else {
    body.append(el("div", { className: "action" }, `Suggested: ${a.recommended_action}`));
    body.append(el("p", {}, a.summary));
    for (const issue of c.grounding_issues) body.append(el("div", { className: "warn" }, `⚠ ${issue}`));
  }

  body.append(decisionBox(c));

  if (a) {
    body.append(el("h3", {}, "What the AI noticed"), citationList(a.key_indicators));
    body.append(el("h3", {}, "What to do next"), bulletList(a.next_steps));
    body.append(el("h3", {}, "What this data can't tell us"), bulletList(a.open_questions));
  }

  body.append(notesBox(c), auditTrail(c));

  const raw = el("details");
  raw.append(el("summary", {}, "All claim details"));
  const table = el("table", { className: "facts" });
  for (const { label, value } of c.claim_display) {
    const tr = el("tr");
    tr.append(el("th", {}, label), el("td", {}, value));
    table.append(tr);
  }
  raw.append(table);
  body.append(raw);
}

function renderSuggestions() {
  const box = document.getElementById("suggestions");
  box.replaceChildren();
  for (const q of SUGGESTED) {
    const btn = el("button", { type: "button", className: "suggestion" }, q);
    btn.addEventListener("click", () => ask(q));
    box.append(btn);
  }
}

function addMessage(kind, content) {
  const msg = el("div", { className: `msg msg-${kind}` });
  if (typeof content === "string") msg.textContent = content;
  else msg.append(...content);
  const thread = document.getElementById("thread");
  thread.append(msg);
  thread.scrollTop = thread.scrollHeight;
  return msg;
}

async function ask(question) {
  const id = current.id;
  const input = document.getElementById("ask-input");
  const submit = document.querySelector("#ask-form button");
  input.value = "";
  addMessage("q", question);
  const pending = addMessage("a", "Thinking…");
  pending.classList.add("muted");
  submit.disabled = true;

  try {
    const res = await fetch(`/api/cases/${id}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: current.history }),
    });
    const data = await res.json();
    if (current?.id !== id) return; // investigator moved to another case
    if (!res.ok) {
      pending.textContent = data.detail || "Something went wrong.";
      pending.className = "msg msg-a error";
      return;
    }
    const parts = [el("p", {}, data.answer)];
    if (!data.answerable) parts.push(el("div", { className: "warn" }, "The case data can't fully answer this."));
    if (data.citations.length) parts.push(citationList(data.citations));
    pending.className = "msg msg-a";
    pending.replaceChildren(...parts);
    current.history.push({ question, answer: data.answer });
  } catch {
    pending.textContent = "Couldn't reach the server.";
    pending.className = "msg msg-a error";
  } finally {
    submit.disabled = false;
  }
}

function showSidebar(open) {
  const side = document.getElementById("case");
  side.querySelector("#case-empty").hidden = open;
  for (const part of side.querySelectorAll(".side-head, .side-body, .ask")) part.hidden = !open;
  // On phones the sidebar covers the queue while a case is open.
  document.body.classList.toggle("case-open", open);
  for (const row of document.querySelectorAll("#queue tr")) {
    row.classList.toggle("selected", open && row.dataset.caseId === current?.id);
  }
}

// Re-render the open case (after a decision or note) without clearing the chat.
async function refreshCase() {
  if (!current) return;
  const body = document.getElementById("case-body");
  const scroll = body.scrollTop;
  const res = await fetch(`/api/cases/${current.id}`);
  if (res.ok) renderCase(await res.json());
  body.scrollTop = scroll;
}

function step(offset) {
  const i = queue.shown.findIndex((c) => c.case_id === current?.id);
  const target = queue.shown[i + offset];
  if (target) openCase(target.case_id);
}

function nextToReview(fromId) {
  const i = queue.shown.findIndex((c) => c.case_id === fromId);
  const after = [...queue.shown.slice(i + 1), ...queue.shown.slice(0, Math.max(i, 0))];
  return after.find((c) => !c.decision && c.case_id !== fromId);
}

function updatePosition() {
  const i = queue.shown.findIndex((c) => c.case_id === current?.id);
  document.getElementById("case-position").textContent =
    i >= 0 ? `${i + 1} of ${queue.shown.length}` : "";
  document.getElementById("case-prev").disabled = i <= 0;
  document.getElementById("case-next").disabled = i < 0 || i >= queue.shown.length - 1;
}

let openRequest = 0; // only the latest openCase call may render

async function openCase(id) {
  const request = ++openRequest;
  const res = await fetch(`/api/cases/${id}`);
  if (!res.ok || request !== openRequest) return;
  current = { id, history: [] };
  renderCase(await res.json());
  document.getElementById("thread").replaceChildren();
  renderSuggestions();
  showSidebar(true);
  updatePosition();
  document.getElementById("case-body").scrollTop = 0;
  document.querySelector(`#queue tr[data-case-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
}

function setupPanel() {
  document.getElementById("case-close").addEventListener("click", () => {
    current = null;
    showSidebar(false);
  });
  document.getElementById("case-prev").addEventListener("click", () => step(-1));
  document.getElementById("case-next").addEventListener("click", () => step(1));
  // ↑/↓ move through the queue, unless the investigator is typing.
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey) return;
    if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
    if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
  });
  // The chat collapses to its header; the choice is remembered in this browser.
  const toggle = document.getElementById("ask-toggle");
  const setAskOpen = (open) => {
    toggle.setAttribute("aria-expanded", String(open));
    document.getElementById("ask-body").hidden = !open;
    try { localStorage.setItem("askOpen", open ? "1" : "0"); } catch {}
  };
  let saved = null;
  try { saved = localStorage.getItem("askOpen"); } catch {}
  setAskOpen(saved !== "0");
  toggle.addEventListener("click", () => setAskOpen(toggle.getAttribute("aria-expanded") !== "true"));

  document.getElementById("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = document.getElementById("ask-input").value.trim();
    if (q) ask(q);
  });
}

document.getElementById("today").textContent =
  new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
setupInvestigator();
setupQueue();
setupPanel();
loadQueue();
