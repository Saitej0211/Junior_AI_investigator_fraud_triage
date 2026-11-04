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

function renderQueue(cases) {
  const body = document.getElementById("queue");
  for (const c of cases) {
    const tierCell = el("td");
    tierCell.append(el("span", { className: `badge tier-${c.tier.toLowerCase()}` }, c.tier));
    const row = el("tr");
    row.append(
      el("td", {}, c.case_id),
      tierCell,
      el("td", { className: "num" }, c.score.toFixed(1)),
      el("td", {}, c.triggered.slice(0, 3).map((t) => t.label).join(", ") || "None"),
    );
    body.append(row);
  }
}

async function main() {
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
