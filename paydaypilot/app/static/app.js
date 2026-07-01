/* PayDay Pilot dashboard */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let STATE = null; // /api/state payload

// ------------------------------------------------------------- utilities

async function api(path, body) {
  if (window.LOCAL_API) return window.LOCAL_API.call(path, body); // serverless mode (Android/file)
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function money(n) {
  const v = Number(n) || 0;
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ------------------------------------------------------------- tabs

$$("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("#tabs button").forEach((b) => b.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "debts") loadProjection();
    if (btn.dataset.tab === "spending") loadSpending();
  });
});

$("#quit").addEventListener("click", async () => {
  if (!confirm("Quit PayDay Pilot? Your data is saved automatically.")) return;
  try { await api("/api/quit", {}); } catch (e) { /* server is going down */ }
  document.body.innerHTML = "<main><div class='panel'><h2>PayDay Pilot has stopped.</h2>" +
    "<p class='muted'>You can close this browser tab. Run the app again any time — your data is saved.</p></div></main>";
});

// ------------------------------------------------------------- state load

async function loadState() {
  STATE = await api("/api/state");
  renderDashboard();
  renderDebts();
  renderBills();
  renderPaycheckHistory();
  renderSettings();
}

// ------------------------------------------------------------- dashboard

function renderDashboard() {
  const { debts, bills, settings, budget, paychecks } = STATE;
  const totalDebt = debts.reduce((s, d) => s + d.balance, 0);
  const totalBills = bills.reduce((s, b) => s + b.amount, 0);
  const mins = debts.reduce((s, d) => s + (d.balance > 0 ? Math.min(d.min_payment, d.balance) : 0), 0);
  const ef = Number(settings.emergency_balance);
  const efT = Number(settings.emergency_target);

  $("#dash-cards").innerHTML = `
    <div class="card ${totalDebt > 0 ? "bad" : "good"}">
      <div class="label">Total debt</div><div class="value">${money(totalDebt)}</div>
      <div class="sub">${debts.filter((d) => d.balance > 0).length} active account(s)</div>
    </div>
    <div class="card">
      <div class="label">Monthly bills</div><div class="value">${money(totalBills)}</div>
      <div class="sub">+ ${money(mins)} debt minimums</div>
    </div>
    <div class="card">
      <div class="label">Est. monthly income</div><div class="value">${money(budget.monthly_income)}</div>
      <div class="sub">${budget.monthly_income ? "" : "enter a paycheck or set income in Settings"}</div>
    </div>
    <div class="card ${budget.monthly_extra > 0 ? "good" : "warn"}">
      <div class="label">Free for extra debt payoff</div><div class="value">${money(budget.monthly_extra)}</div>
      <div class="sub">per month after bills &amp; essentials</div>
    </div>
    <div class="card ${ef >= efT ? "good" : ""}">
      <div class="label">Emergency fund</div><div class="value">${money(ef)}</div>
      <div class="sub">target ${money(efT)}</div>
    </div>`;

  const latest = paychecks[0];
  if (latest && latest.plan) {
    $("#dash-plan").innerHTML =
      `<p class="muted small">${esc(latest.source)} of <b>${money(latest.amount)}</b> on ${fmtDate(latest.date)}</p>` +
      renderPlanItems(latest.plan, true);
  }
  loadDashOutlook();
}

async function loadDashOutlook() {
  const el = $("#dash-outlook");
  const debts = STATE.debts.filter((d) => d.balance > 0);
  if (!debts.length) {
    el.innerHTML = STATE.debts.length
      ? "<div class='okbox'>🎉 All debts are paid off!</div>"
      : "Add your debts to see a payoff projection.";
    return;
  }
  try {
    const p = await api("/api/projection");
    const s = p.comparison[STATE.settings.strategy];
    const minOnly = p.comparison.minimum_only;
    if (s.stuck) {
      el.innerHTML = "<div class='warnbox'>⚠️ Current payments don't keep up with interest. Increase income or cut expenses so the balance can fall.</div>";
      return;
    }
    let html = `<div class="cards">
      <div class="card good"><div class="label">Debt-free date</div><div class="value">${fmtDate(s.debt_free_date)}</div>
        <div class="sub">${s.months} months, ${STATE.settings.strategy} + ${money(p.extra_used)}/mo extra</div></div>
      <div class="card"><div class="label">Interest you'll pay</div><div class="value">${money(s.total_interest)}</div>
        <div class="sub">${minOnly.months && !minOnly.stuck ? "vs " + money(minOnly.total_interest) + " with minimums only" : ""}</div></div>
    </div>`;
    if (s.payoff_order.length) {
      html += "<p class='muted small'>Payoff order: " +
        s.payoff_order.map((o) => `<b>${esc(o.name)}</b> (${fmtDate(o.date)})`).join(" → ") + "</p>";
    }
    el.innerHTML = html;
  } catch (e) {
    el.textContent = "Couldn't compute projection: " + e.message;
  }
}

// ------------------------------------------------------------- paycheck

$("#pc-date").value = new Date().toISOString().slice(0, 10);

$("#paycheck-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitPaycheck(false);
});
$("#pc-preview").addEventListener("click", () => submitPaycheck(true));

async function submitPaycheck(preview) {
  const amount = parseFloat($("#pc-amount").value);
  if (!amount || amount <= 0) { toast("Enter the paycheck amount."); return; }
  try {
    const { plan } = await api("/api/paycheck", {
      amount, date: $("#pc-date").value, source: $("#pc-source").value || "Paycheck", preview,
    });
    renderPlan(plan, preview);
    if (!preview) {
      toast("Plan saved. Balances and bill set-asides updated.");
      await loadState();
    }
  } catch (err) {
    toast("Error: " + err.message);
  }
}

function renderPlanItems(plan, compact) {
  let html = "";
  if (!compact) {
    const t = plan.totals;
    html += `<div class="summary-chips">
      <span class="chip">Bills: <b>${money(t.bills)}</b></span>
      <span class="chip">Debt minimums: <b>${money(t.debt_min)}</b></span>
      <span class="chip">Extra to debt: <b>${money(t.debt_extra)}</b></span>
      <span class="chip">Essentials: <b>${money(t.essentials)}</b></span>
      <span class="chip">Emergency: <b>${money(t.emergency)}</b></span>
      <span class="chip">Fun: <b>${money(t.fun)}</b></span>
      ${t.savings ? `<span class="chip">Savings: <b>${money(t.savings)}</b></span>` : ""}
    </div>`;
  }
  html += plan.items.map((i) => `
    <div class="plan-item">
      <span class="badge ${i.kind}">${{
        bill: "pay bill", reserve: "set aside", debt_min: "debt min", debt_extra: "extra debt",
        essentials: "essentials", emergency: "emergency", fun: "fun", savings: "savings",
      }[i.kind] || i.kind}</span>
      <span><span class="what">${esc(i.action)}</span><br><span class="why">${esc(i.note)}</span></span>
      <span class="amt">${money(i.amount)}</span>
    </div>`).join("");
  return html;
}

function renderPlan(plan, preview) {
  const out = $("#plan-output");
  out.style.display = "block";
  let html = `<h2>${preview ? "Preview — " : ""}Your plan for the ${money(plan.amount)} paycheck (${fmtDate(plan.pay_date)})</h2>`;
  html += `<p class="muted small">This plan covers you until your next expected paycheck around <b>${fmtDate(plan.next_paycheck_expected)}</b>.</p>`;
  plan.warnings.forEach((w) => { html += `<div class="warnbox">⚠️ ${esc(w)}</div>`; });
  html += renderPlanItems(plan, false);
  if (plan.impact && plan.impact.months_saved > 0) {
    html += `<div class="okbox">🚀 Keeping this up, the extra payments make you debt-free
      <b>${plan.impact.months_saved} month(s) sooner</b> (by ${fmtDate(plan.impact.debt_free_date)})
      and save <b>${money(plan.impact.interest_saved)}</b> in interest.</div>`;
  }
  if (preview) {
    html += `<p class="muted small">This is a preview — nothing was saved. Click <b>Create plan</b> to commit it.</p>`;
  }
  out.innerHTML = html;
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderPaycheckHistory() {
  const el = $("#paycheck-history");
  if (!STATE.paychecks.length) { el.textContent = "Nothing yet."; return; }
  el.innerHTML = `<table class="table"><thead><tr>
      <th>Date</th><th>Source</th><th class="num">Amount</th><th class="num">To bills</th>
      <th class="num">To debt</th><th></th></tr></thead><tbody>` +
    STATE.paychecks.map((p) => {
      const t = p.plan ? p.plan.totals : null;
      return `<tr>
        <td>${fmtDate(p.date)}</td><td>${esc(p.source)}</td>
        <td class="num">${money(p.amount)}</td>
        <td class="num">${t ? money(t.bills) : "—"}</td>
        <td class="num">${t ? money(t.debt_min + t.debt_extra) : "—"}</td>
        <td><button class="mini ghost" data-show="${p.id}">view</button>
            <button class="mini danger ghost" data-del="${p.id}">✕</button></td></tr>`;
    }).join("") + "</tbody></table>";
  el.querySelectorAll("[data-show]").forEach((b) => b.addEventListener("click", () => {
    const p = STATE.paychecks.find((x) => x.id == b.dataset.show);
    if (p && p.plan) renderPlan(p.plan, false);
  }));
  el.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this paycheck record? (Debt balances and set-asides are not reverted.)")) return;
    await api("/api/paycheck/delete", { id: Number(b.dataset.del) });
    await loadState();
  }));
}

// ------------------------------------------------------------- debts

const KIND_LABELS = {
  credit_card: "Credit card", auto_loan: "Auto loan", student_loan: "Student loan",
  personal: "Personal loan", medical: "Medical", mortgage: "Mortgage", other: "Other",
};

function renderDebts() {
  const tbody = $("#debt-table tbody");
  if (!STATE.debts.length) {
    tbody.innerHTML = "<tr><td colspan='8' class='muted'>No debts yet — add them below or import from your credit report.</td></tr>";
    return;
  }
  tbody.innerHTML = STATE.debts.map((d) => `<tr>
      <td>${esc(d.name)}</td><td>${KIND_LABELS[d.kind] || esc(d.kind)}</td>
      <td class="num">${money(d.balance)}</td><td class="num">${d.apr.toFixed(2)}%</td>
      <td class="num">${money(d.min_payment)}</td><td class="num">${d.term_months ?? "—"}</td>
      <td class="num">${d.due_day}</td>
      <td><button class="mini ghost" data-edit="${d.id}">edit</button>
          <button class="mini danger ghost" data-del="${d.id}">✕</button></td></tr>`).join("");
  tbody.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const d = STATE.debts.find((x) => x.id == b.dataset.edit);
    $("#d-id").value = d.id; $("#d-name").value = d.name; $("#d-kind").value = d.kind;
    $("#d-balance").value = d.balance; $("#d-apr").value = d.apr; $("#d-min").value = d.min_payment;
    $("#d-term").value = d.term_months ?? ""; $("#d-due").value = d.due_day;
    $("#d-submit").textContent = "Save debt"; $("#d-cancel").style.display = "";
  }));
  tbody.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this debt?")) return;
    await api("/api/debts/delete", { id: Number(b.dataset.del) });
    await loadState(); loadProjection();
  }));
}

$("#debt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/debts", {
    id: $("#d-id").value ? Number($("#d-id").value) : null,
    name: $("#d-name").value, kind: $("#d-kind").value,
    balance: $("#d-balance").value, apr: $("#d-apr").value,
    min_payment: $("#d-min").value, term_months: $("#d-term").value || null,
    due_day: $("#d-due").value,
  });
  resetDebtForm();
  toast("Debt saved.");
  await loadState(); loadProjection();
});
$("#d-cancel").addEventListener("click", resetDebtForm);

function resetDebtForm() {
  $("#debt-form").reset();
  $("#d-id").value = "";
  $("#d-submit").textContent = "Add debt";
  $("#d-cancel").style.display = "none";
}

// ---- credit report import

$("#debt-import-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (f) $("#debt-import-text").value = await f.text();
});

$("#debt-csv-template").addEventListener("click", (e) => {
  e.preventDefault();
  const csv = "name,balance,apr,min payment,term,due day\nCapital One Visa,2450.00,26.99,75,,15\nToyota auto loan,14800,6.4,385,48,5\n";
  download("debts-template.csv", csv, "text/csv");
});

$("#debt-import-parse").addEventListener("click", async () => {
  const text = $("#debt-import-text").value.trim();
  if (!text) { toast("Paste your credit report text or CSV first."); return; }
  const { debts, source } = await api("/api/debts/import", { text });
  const el = $("#debt-import-review");
  if (!debts.length) {
    el.innerHTML = "<div class='warnbox'>Couldn't find any accounts in that text. Try the CSV template instead — " +
      "columns: name, balance, apr, min payment, term, due day.</div>";
    return;
  }
  el.innerHTML = `<p class="muted small">Found ${debts.length} account(s) (${source === "csv" ? "CSV" : "text scan"}).
      Review, fix anything that's off, then confirm:</p>
    <table class="table"><thead><tr><th></th><th>Name</th><th class="num">Balance</th>
      <th class="num">APR %</th><th class="num">Min payment</th></tr></thead><tbody>` +
    debts.map((d, i) => `<tr>
      <td><input type="checkbox" checked data-i="${i}"></td>
      <td><input value="${esc(d.name)}" data-f="name" data-i="${i}"></td>
      <td class="num"><input type="number" step="0.01" value="${d.balance}" data-f="balance" data-i="${i}" style="width:110px"></td>
      <td class="num"><input type="number" step="0.01" value="${d.apr}" data-f="apr" data-i="${i}" style="width:80px"></td>
      <td class="num"><input type="number" step="0.01" value="${d.min_payment}" data-f="min_payment" data-i="${i}" style="width:100px"></td>
    </tr>`).join("") +
    `</tbody></table><button class="primary" id="debt-import-confirm">Add selected debts</button>`;
  el._debts = debts;
  $("#debt-import-confirm").addEventListener("click", async () => {
    const rows = el._debts.map((d, i) => ({ ...d }));
    el.querySelectorAll("input[data-f]").forEach((inp) => {
      rows[Number(inp.dataset.i)][inp.dataset.f] = inp.type === "number" ? Number(inp.value) : inp.value;
    });
    const selected = [];
    el.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      if (cb.checked) selected.push(rows[Number(cb.dataset.i)]);
    });
    if (!selected.length) { toast("Nothing selected."); return; }
    await api("/api/debts", selected);
    el.innerHTML = ""; $("#debt-import-text").value = "";
    toast(`Added ${selected.length} debt(s).`);
    await loadState(); loadProjection();
  });
});

// ---- payoff projection

$("#proj-refresh").addEventListener("click", loadProjection);

async function loadProjection() {
  const el = $("#projection");
  if (!STATE || !STATE.debts.some((d) => d.balance > 0)) {
    el.innerHTML = "<p class='muted'>Add debts with balances to compare payoff strategies.</p>";
    return;
  }
  const extraInput = $("#proj-extra").value;
  const q = extraInput !== "" ? `?extra=${encodeURIComponent(extraInput)}` : "";
  const p = await api("/api/projection" + q);
  $("#proj-extra").placeholder = `auto: ${p.budget.monthly_extra}`;
  const c = p.comparison;
  const fmt = (r, label) => {
    if (r.stuck || !r.months) {
      return `<div class="card bad"><div class="label">${label}</div>
        <div class="value">never</div><div class="sub">payments don't cover interest</div></div>`;
    }
    return `<div class="card ${label.toLowerCase().includes(STATE.settings.strategy) ? "winner" : ""}">
      <div class="label">${label}</div>
      <div class="value">${r.months} mo</div>
      <div class="sub">debt-free ${fmtDate(r.debt_free_date)}<br>${money(r.total_interest)} total interest</div></div>`;
  };
  let html = `<div class="compare">
    ${fmt(c.minimum_only, "Minimums only")}
    ${fmt(c.snowball, "Snowball")}
    ${fmt(c.avalanche, "Avalanche")}
  </div>
  <p class="muted small">Using ${money(p.extra_used)}/month extra toward debt. Your selected strategy:
    <b>${STATE.settings.strategy}</b> (change in Settings).</p>`;
  const active = c[STATE.settings.strategy];
  if (active.timeline && active.timeline.length > 1) {
    html += chartSVG(active.timeline.map((t) => t.total_balance));
  }
  el.innerHTML = html;
}

function chartSVG(values) {
  const w = 800, h = 160, pad = 8;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => {
    const x = pad + (i / Math.max(1, values.length - 1)) * (w - 2 * pad);
    const y = pad + (1 - v / max) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="#4f8cff" stroke-width="2.5"/>
  </svg><p class="muted small">Total debt balance over time until payoff.</p>`;
}

// ------------------------------------------------------------- bills

function renderBills() {
  const tbody = $("#bill-table tbody");
  if (!STATE.bills.length) {
    tbody.innerHTML = "<tr><td colspan='6' class='muted'>No bills yet — add rent, utilities, insurance, car payment…</td></tr>";
  } else {
    tbody.innerHTML = STATE.bills.map((b) => `<tr>
      <td>${esc(b.name)}</td><td>${esc(b.category)}</td>
      <td class="num">${money(b.amount)}</td><td class="num">${b.due_day}</td>
      <td class="num">${money(b.reserved)}</td>
      <td><button class="mini ghost" data-edit="${b.id}">edit</button>
          <button class="mini danger ghost" data-del="${b.id}">✕</button></td></tr>`).join("");
  }
  const total = STATE.bills.reduce((s, b) => s + b.amount, 0);
  $("#bill-total").textContent = STATE.bills.length ? `Total fixed bills: ${money(total)} / month` : "";
  tbody.querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () => {
    const b = STATE.bills.find((x) => x.id == btn.dataset.edit);
    $("#b-id").value = b.id; $("#b-name").value = b.name; $("#b-category").value = b.category;
    $("#b-amount").value = b.amount; $("#b-due").value = b.due_day;
    $("#b-submit").textContent = "Save bill"; $("#b-cancel").style.display = "";
  }));
  tbody.querySelectorAll("[data-del]").forEach((btn) => btn.addEventListener("click", async () => {
    if (!confirm("Delete this bill?")) return;
    await api("/api/bills/delete", { id: Number(btn.dataset.del) });
    await loadState();
  }));
}

$("#bill-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/bills", {
    id: $("#b-id").value ? Number($("#b-id").value) : null,
    name: $("#b-name").value, category: $("#b-category").value,
    amount: $("#b-amount").value, due_day: $("#b-due").value,
  });
  $("#bill-form").reset(); $("#b-id").value = "";
  $("#b-submit").textContent = "Add bill"; $("#b-cancel").style.display = "none";
  toast("Bill saved.");
  await loadState();
});
$("#b-cancel").addEventListener("click", () => {
  $("#bill-form").reset(); $("#b-id").value = "";
  $("#b-submit").textContent = "Add bill"; $("#b-cancel").style.display = "none";
});

// ------------------------------------------------------------- spending

$("#bank-import").addEventListener("click", async () => {
  const files = $("#bank-file").files;
  if (!files.length) { toast("Choose one or more CSV files first."); return; }
  let added = 0, dupes = 0, notes = [];
  for (const f of files) {
    const text = await f.text();
    const r = await api("/api/transactions/import", { csv: text });
    added += r.added; dupes += r.duplicates;
    if (r.note) notes.push(`${f.name}: ${r.note}`);
    if (!r.parsed) notes.push(`${f.name}: no transactions found — check that it has Date and Amount columns.`);
  }
  $("#bank-import-result").innerHTML =
    `Imported <b>${added}</b> transaction(s)` + (dupes ? `, skipped ${dupes} duplicate(s)` : "") + "." +
    (notes.length ? `<br><span class="small">${notes.map(esc).join("<br>")}</span>` : "");
  $("#bank-file").value = "";
  loadSpending();
});

$("#bank-clear").addEventListener("click", async () => {
  if (!confirm("Delete ALL imported transactions?")) return;
  await api("/api/transactions/clear", {});
  toast("Transactions cleared.");
  loadSpending();
});

async function loadSpending() {
  const s = await api("/api/spending?months=6");
  $("#spend-range").textContent = s.months.length ? `(${s.months[0]} → ${s.months[s.months.length - 1]})` : "";

  // category bars
  const catEl = $("#spend-categories");
  if (!s.categories.length) {
    catEl.textContent = "Import a statement to see your breakdown.";
  } else {
    const max = Math.max(...s.categories.map((c) => c.monthly_avg));
    catEl.innerHTML = s.categories.map((c) => `
      <div class="bar-row">
        <span class="name">${esc(c.category)}${c.discretionary ? " ✂️" : ""}</span>
        <div class="bar-track"><div class="bar-fill ${c.discretionary ? "disc" : ""}"
             style="width:${(100 * c.monthly_avg / max).toFixed(1)}%"></div></div>
        <span class="val">${money(c.monthly_avg)}/mo</span>
      </div>`).join("") +
      `<p class="muted small">Average total spend: <b>${money(s.total_monthly_spend)}/mo</b>.
       ✂️ = discretionary. Transfers, debt payments and income are excluded.</p>`;
  }

  // suggestions
  const sugEl = $("#spend-suggestions");
  if (!s.suggestions.length) {
    sugEl.textContent = "No obvious cuts found yet — import more statements for better analysis.";
  } else {
    let html = s.suggestions.map((x) => `<div class="plan-item">
        <span class="badge fun">cut</span>
        <span><span class="what">${esc(x.category)}</span><br><span class="why">${esc(x.message)}</span></span>
        <span class="amt">−${money(x.suggested_cut)}/mo</span></div>`).join("");
    html += `<div class="okbox">Total potential: <b>${money(s.potential_monthly_savings)}/mo</b>` +
      (s.cut_impact ? ` — put toward debt, that's <b>${s.cut_impact.months_saved} month(s) sooner</b> debt-free and <b>${money(s.cut_impact.interest_saved)}</b> less interest.` : ".") +
      `</div>`;
    sugEl.innerHTML = html;
  }

  // recurring
  const recEl = $("#spend-recurring");
  recEl.innerHTML = s.recurring.length
    ? s.recurring.map((r) => `<div class="plan-item">
        <span><span class="what">${esc(r.merchant)}</span><br><span class="why">seen in ${r.months_seen} month(s)</span></span>
        <span class="amt">${money(r.monthly_avg)}/mo</span></div>`).join("")
    : "None detected yet.";

  // monthly trend bars
  const trendEl = $("#spend-trend");
  if (s.months.length) {
    const totals = s.months.map((m) => Object.values(s.by_month[m]).reduce((a, b) => a + b, 0));
    const max = Math.max(...totals, 1);
    trendEl.innerHTML = s.months.map((m, i) => `
      <div class="bar-row">
        <span class="name">${m}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(100 * totals[i] / max).toFixed(1)}%"></div></div>
        <span class="val">${money(totals[i])}</span>
      </div>`).join("");
  } else {
    trendEl.textContent = "";
  }

  loadTransactions();
}

async function loadTransactions() {
  const { transactions } = await api("/api/transactions");
  const el = $("#txn-list");
  if (!transactions.length) { el.textContent = "None imported yet."; return; }
  el.innerHTML = `<table class="table"><thead><tr>
      <th>Date</th><th>Description</th><th class="num">Amount</th><th>Category</th></tr></thead><tbody>` +
    transactions.slice(0, 300).map((t) => `<tr>
      <td>${t.date}</td><td>${esc(t.description)}</td>
      <td class="num" style="color:${t.amount < 0 ? "var(--red)" : "var(--green)"}">${money(t.amount)}</td>
      <td><input value="${esc(t.category)}" data-txn="${t.id}" style="width:130px"></td></tr>`).join("") +
    `</tbody></table>` +
    (transactions.length > 300 ? `<p class="muted small">Showing newest 300 of ${transactions.length}.</p>` : "");
  el.querySelectorAll("input[data-txn]").forEach((inp) => inp.addEventListener("change", async () => {
    await api("/api/transactions/category", { id: Number(inp.dataset.txn), category: inp.value });
    toast("Category updated.");
  }));
}

// ------------------------------------------------------------- settings

function renderSettings() {
  const s = STATE.settings;
  $("#s-freq").value = s.pay_frequency;
  $("#s-strategy").value = s.strategy;
  $("#s-variable").value = s.variable_budget;
  $("#s-etarget").value = s.emergency_target;
  $("#s-ebalance").value = s.emergency_balance;
  $("#s-epct").value = s.emergency_pct;
  $("#s-fun").value = s.fun_pct;
  $("#s-income").value = s.monthly_net_income;
  loadRules();
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    pay_frequency: $("#s-freq").value,
    strategy: $("#s-strategy").value,
    variable_budget: $("#s-variable").value,
    emergency_target: $("#s-etarget").value,
    emergency_balance: $("#s-ebalance").value,
    emergency_pct: $("#s-epct").value,
    fun_pct: $("#s-fun").value,
    monthly_net_income: $("#s-income").value,
  });
  toast("Settings saved.");
  await loadState();
});

async function loadRules() {
  const { rules } = await api("/api/rules");
  const el = $("#rules-list");
  el.innerHTML = rules.length
    ? rules.map((r) => `<span class="chip">"${esc(r.keyword)}" → ${esc(r.category)}
        <button class="mini danger ghost" data-rule="${r.id}">✕</button></span>`).join(" ")
    : "<p class='muted small'>No custom rules yet.</p>";
  el.querySelectorAll("[data-rule]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/rules/delete", { id: Number(b.dataset.rule) });
    loadRules();
  }));
}

$("#rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/rules", { keyword: $("#r-keyword").value, category: $("#r-category").value });
  $("#rule-form").reset();
  loadRules();
});

$("#export-json").addEventListener("click", async () => {
  const data = await api("/api/export");
  download("paydaypilot-backup.json", JSON.stringify(data, null, 2), "application/json");
});

function download(name, content, type) {
  if (window.AndroidBridge && window.AndroidBridge.saveFile) {
    // Android WebView can't download blob: URLs — hand the file to the app.
    window.AndroidBridge.saveFile(name, type, btoa(unescape(encodeURIComponent(content))));
    toast(`Saved ${name} to your Downloads.`);
    return;
  }
  const blob = new Blob([content], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------- boot

if (window.LOCAL_API) $("#quit").style.display = "none"; // nothing to quit in app mode
loadState().catch((e) => toast("Failed to load: " + e.message));
