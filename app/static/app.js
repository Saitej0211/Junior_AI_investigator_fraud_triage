const TIERS = ["High", "Medium", "Low"];

function el(tag, attrs = {}, text = "") {
  const node = document.createElement(tag);
  Object.assign(node, attrs);
  if (text) node.textContent = text;
  return node;
}

function renderCounts(cases) {
  const box = document.getElementById("counts");
  for (const tier of TIERS) {
    const n = cases.filter((c) => c.tier === tier).length;
    const chip = el("span", { className: `chip tier-${tier.toLowerCase()}` });
    chip.append(el("strong", {}, String(n)), ` ${tier}`);
    box.append(chip);
  }
}

function renderAlerts(cases) {
  const box = document.getElementById("alerts");
  // Each case lists the others, so only report a group from its lowest case_id.
  for (const c of cases) {
    const others = c.shares_claim_number_with;
    if (others.length && others.every((o) => c.case_id < o)) {
      box.append(el("div", { className: "alert" },
        `Same claim number on ${[c.case_id, ...others].join(" and ")}`));
    }
  }
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
  cell.append(el("div", {}, a.summary));
  if (c.grounding_issues.length) {
    cell.append(el("div", { className: "warn", title: c.grounding_issues.join("\n") },
      `⚠ ${c.grounding_issues.length} check${c.grounding_issues.length > 1 ? "s" : ""} failed`));
  }
  return cell;
}

function renderQueue(cases) {
  const body = document.getElementById("queue");
  for (const c of cases) {
    const tierCell = el("td");
    tierCell.append(el("span", { className: `badge tier-${c.tier.toLowerCase()}` }, c.tier));
    const row = el("tr", { className: "clickable", tabIndex: 0 });
    row.addEventListener("click", () => openCase(c.case_id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openCase(c.case_id); });
    row.append(
      el("td", {}, c.case_id),
      tierCell,
      el("td", { className: "num" }, c.score.toFixed(1)),
      assessmentCell(c),
      el("td", {}, c.triggered.slice(0, 3).map((t) => t.label).join(", ") || "None"),
    );
    body.append(row);
  }
}

// ---- Case panel -------------------------------------------------------

const SUGGESTED = [
  "Why this tier?",
  "How does this compare with normal cases of this care type?",
  "What should I request first?",
];

let current = null; // { id, history: [{question, answer}] }

function citationList(items) {
  const list = el("ul", { className: "cites" });
  for (const i of items) {
    const li = el("li", { className: i.verified ? "" : "unverified" });
    li.append(el("span", { className: "mark", title: i.verified
      ? "Checked against the case data" : "Not found in the case data" },
      i.verified ? "✓" : "⚠"));
    li.append(el("code", {}, `${i.field} = ${i.value}`));
    if (i.why) li.append(` ${i.why}`);
    list.append(li);
  }
  return list;
}

function bulletList(items) {
  const list = el("ul");
  for (const text of items) list.append(el("li", {}, text));
  return list;
}

function renderCase(c) {
  document.getElementById("case-title").textContent = `${c.case_id}`;
  const body = document.getElementById("case-body");
  body.replaceChildren();

  const meta = el("p", { className: "meta" });
  meta.append(el("span", { className: `badge tier-${c.tier.toLowerCase()}` }, c.tier),
    ` Rules score ${c.score.toFixed(1)} · ${c.claim.care_type} · $${c.claim.claim_amount_usd.toLocaleString()}`);
  body.append(meta);
  if (c.floor_reason) {
    body.append(el("div", { className: "alert" }, `Held at ${c.tier}: ${c.floor_reason}`));
  }

  const a = c.assessment;
  if (!a) {
    body.append(el("p", { className: "muted" }, "No AI assessment for this case."));
  } else {
    body.append(el("div", { className: "action" },
      `${a.recommended_action} · AI rates ${a.risk_level}, confidence ${a.confidence}`));
    body.append(el("p", {}, a.summary));
    for (const issue of c.grounding_issues) body.append(el("div", { className: "warn" }, `⚠ ${issue}`));
    body.append(el("h3", {}, "Evidence"), citationList(a.key_indicators));
    body.append(el("h3", {}, "Next steps"), bulletList(a.next_steps));
    body.append(el("h3", {}, "Open questions"), bulletList(a.open_questions));
  }

  const raw = el("details");
  raw.append(el("summary", {}, "Claim data"));
  const table = el("table", { className: "facts" });
  for (const [k, v] of Object.entries(c.claim)) {
    const tr = el("tr");
    tr.append(el("th", {}, k), el("td", {}, String(v)));
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
  msg.scrollIntoView({ block: "nearest" });
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
    if (current.id !== id) return; // investigator moved to another case
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

async function openCase(id) {
  const res = await fetch(`/api/cases/${id}`);
  if (!res.ok) return;
  current = { id, history: [] };
  renderCase(await res.json());
  document.getElementById("thread").replaceChildren();
  renderSuggestions();
  document.getElementById("case").showModal();
}

function setupPanel() {
  const dialog = document.getElementById("case");
  document.getElementById("case-close").addEventListener("click", () => dialog.close());
  // Clicking the dimmed backdrop closes the panel.
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  document.getElementById("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = document.getElementById("ask-input").value.trim();
    if (q) ask(q);
  });
}

async function main() {
  setupPanel();
  const res = await fetch("/api/cases");
  if (!res.ok) {
    const err = document.getElementById("error");
    err.textContent = (await res.json()).detail;
    err.hidden = false;
    return;
  }
  const cases = await res.json();
  renderCounts(cases);
  renderAlerts(cases);
  renderQueue(cases);
}

main();
