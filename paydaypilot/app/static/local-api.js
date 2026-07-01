/* PayDay Pilot local engine — a full JavaScript port of the Python backend
 * (db + engine + importers + API routes) so the app can run with no server:
 * inside the Android WebView, or by opening index.html straight from disk.
 *
 * Activates only when the page is NOT served over http(s); the desktop app
 * keeps using the Python server. Data lives in localStorage.
 */
"use strict";

(function () {
  if (location.protocol.startsWith("http")) return; // server mode: stay dormant

  // ================================================================ storage

  const STORE_KEY = "paydaypilot";

  const DEFAULT_SETTINGS = {
    pay_frequency: "biweekly",
    strategy: "avalanche",
    emergency_target: "1000",
    emergency_balance: "0",
    emergency_pct: "20",
    fun_pct: "5",
    variable_budget: "600",
    monthly_net_income: "0",
  };

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* corrupted store: start fresh */ }
    return { settings: {}, debts: [], bills: [], paychecks: [], transactions: [], rules: [], seq: 1 };
  }

  function save(db) {
    localStorage.setItem(STORE_KEY, JSON.stringify(db));
  }

  function nextId(db) {
    return db.seq++;
  }

  function getSettings(db) {
    return Object.assign({}, DEFAULT_SETTINGS, db.settings);
  }

  const r2 = (x) => Math.round((Number(x) + Number.EPSILON) * 100) / 100;

  // ================================================================ dates
  // ISO date strings ("YYYY-MM-DD") everywhere; arithmetic via UTC to dodge
  // timezone/DST surprises.

  function toUTC(iso) {
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function fromUTC(ms) {
    const d = new Date(ms);
    return d.toISOString().slice(0, 10);
  }

  function todayISO() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function addDays(iso, days) {
    return fromUTC(toUTC(iso) + days * 86400000);
  }

  function daysBetween(a, b) {
    return Math.round((toUTC(b) - toUTC(a)) / 86400000);
  }

  function addMonths(iso, months) {
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    const idx = m - 1 + months;
    const year = y + Math.floor(idx / 12);
    const month = ((idx % 12) + 12) % 12 + 1;
    const day = Math.min(d, 28);
    return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }

  function nextDueDate(dueDay, fromIso) {
    dueDay = Math.max(1, Math.min(28, Number(dueDay) || 1));
    const [y, m, d] = fromIso.slice(0, 10).split("-").map(Number);
    if (d <= dueDay) return `${y}-${String(m).padStart(2, "0")}-${String(dueDay).padStart(2, "0")}`;
    const [yy, mm] = m === 12 ? [y + 1, 1] : [y, m + 1];
    return `${yy}-${String(mm).padStart(2, "0")}-${String(dueDay).padStart(2, "0")}`;
  }

  // ================================================================ engine

  const PERIOD_DAYS = { weekly: 7, biweekly: 14, semimonthly: 15, monthly: 30 };
  const AVG_DAYS_PER_MONTH = 30.44;
  const CHECKS_PER_MONTH = { weekly: 52 / 12, biweekly: 26 / 12, semimonthly: 2.0, monthly: 1.0 };

  function checksUntil(due, payDate, freq) {
    const days = daysBetween(payDate, due);
    if (days <= 0) return 1;
    return Math.max(1, Math.floor(days / PERIOD_DAYS[freq]) + 1);
  }

  function pickTargetDebt(debts, strategy) {
    const active = debts.filter((d) => d.balance > 0.01);
    if (!active.length) return null;
    if (strategy === "snowball") {
      return active.reduce((a, b) => (b.balance < a.balance ? b : a));
    }
    return active.reduce((a, b) =>
      (b.apr > a.apr || (b.apr === a.apr && b.balance > a.balance)) ? b : a);
  }

  function buildPlan(amount, payDate, bills, debts, settings) {
    const freq = settings.pay_frequency;
    const strategy = settings.strategy;
    const period = PERIOD_DAYS[freq];
    const windowEnd = addDays(payDate, period);

    let remaining = r2(amount);
    const items = [];
    const warnings = [];
    const reserveUpdates = {};

    const obligations = [];
    for (const b of bills) {
      const due = nextDueDate(b.due_day, payDate);
      const needed = Math.max(0, b.amount - b.reserved);
      const n = checksUntil(due, payDate, freq);
      const dueNow = due < windowEnd;
      obligations.push({ kind: "bill", ref: b, due, share: dueNow ? needed : r2(needed / n), dueNow });
    }
    for (const d of debts) {
      if (d.balance <= 0.01 || d.min_payment <= 0) continue;
      const due = nextDueDate(d.due_day, payDate);
      const payment = Math.min(d.min_payment, d.balance);
      const n = checksUntil(due, payDate, freq);
      const dueNow = due < windowEnd;
      obligations.push({ kind: "debt_min", ref: d, due, share: dueNow ? payment : r2(payment / n), dueNow });
    }
    obligations.sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : b.share - a.share));

    for (const ob of obligations) {
      if (ob.share < 0.01) continue;
      const alloc = r2(Math.min(ob.share, remaining));
      const ref = ob.ref;
      if (alloc < ob.share - 0.01) {
        warnings.push(`Not enough left to fully cover ${ref.name} ` +
          `(needed $${ob.share.toFixed(2)}, allocated $${alloc.toFixed(2)}).`);
      }
      remaining = r2(remaining - alloc);
      if (ob.kind === "bill") {
        if (ob.dueNow) {
          const payAmount = r2(Math.min(ref.amount, ref.reserved + alloc));
          let note = `due ${ob.due}`;
          if (ref.reserved > 0.01) note += ` ($${ref.reserved.toFixed(2)} already set aside)`;
          reserveUpdates[ref.id] = 0;
          items.push({ action: `Pay ${ref.name}`, amount: payAmount, from_paycheck: alloc,
            kind: "bill", category: ref.category, due: ob.due, note });
        } else {
          reserveUpdates[ref.id] = r2(ref.reserved + alloc);
          if (alloc >= 0.01) {
            items.push({ action: `Set aside for ${ref.name}`, amount: alloc, from_paycheck: alloc,
              kind: "reserve", category: ref.category, due: ob.due,
              note: `$${reserveUpdates[ref.id].toFixed(2)} of $${ref.amount.toFixed(2)} ` +
                    `saved for the ${ob.due} bill` });
          }
        }
      } else if (alloc >= 0.01) {
        const verb = ob.dueNow ? "Pay" : "Set aside for";
        items.push({ action: `${verb} ${ref.name} (minimum)`, amount: alloc, from_paycheck: alloc,
          kind: "debt_min", category: "Debt", due: ob.due,
          note: `minimum payment, due ${ob.due}`, debt_id: ref.id });
      }
      if (remaining <= 0) remaining = 0;
    }

    const variableBudget = Number(settings.variable_budget);
    const essentials = r2(variableBudget * period / AVG_DAYS_PER_MONTH);
    if (essentials > 0.01) {
      const alloc = r2(Math.min(essentials, remaining));
      if (alloc < essentials - 0.01) {
        warnings.push(`Essentials budget is short: $${alloc.toFixed(2)} of $${essentials.toFixed(2)} ` +
          `for groceries/gas until the next paycheck.`);
      }
      if (alloc >= 0.01) {
        items.push({ action: "Keep for essentials", amount: alloc, from_paycheck: alloc,
          kind: "essentials", category: "Essentials", due: "",
          note: `groceries, gas & day-to-day spending for the next ${period} days` });
      }
      remaining = r2(remaining - alloc);
    }

    const target = Number(settings.emergency_target);
    const balance = Number(settings.emergency_balance);
    let emergencyAlloc = 0;
    if (remaining > 0.01 && balance < target) {
      const pct = Number(settings.emergency_pct) / 100;
      emergencyAlloc = r2(Math.min(target - balance, remaining * pct));
      if (emergencyAlloc >= 0.01) {
        items.push({ action: "Move to emergency fund", amount: emergencyAlloc,
          from_paycheck: emergencyAlloc, kind: "emergency", category: "Savings", due: "",
          note: `fund at $${(balance + emergencyAlloc).toFixed(2)} of $${target.toFixed(2)} target` });
        remaining = r2(remaining - emergencyAlloc);
      } else {
        emergencyAlloc = 0;
      }
    }

    let fun = 0;
    if (remaining > 0.01) {
      fun = r2(remaining * Number(settings.fun_pct) / 100);
      if (fun >= 0.01) {
        items.push({ action: "Fun money", amount: fun, from_paycheck: fun,
          kind: "fun", category: "Fun", due: "",
          note: "guilt-free spending so the plan is sustainable" });
        remaining = r2(remaining - fun);
      } else {
        fun = 0;
      }
    }

    let extra = 0;
    const targetDebt = pickTargetDebt(debts, strategy);
    if (remaining > 0.01) {
      if (targetDebt) {
        extra = remaining;
        items.push({ action: `EXTRA payment to ${targetDebt.name}`, amount: extra,
          from_paycheck: extra, kind: "debt_extra", category: "Debt", due: "",
          debt_id: targetDebt.id,
          note: `${strategy} target — ${targetDebt.apr.toFixed(2)}% APR, ` +
                `$${targetDebt.balance.toFixed(2)} balance` });
      } else {
        items.push({ action: "Move to savings", amount: remaining, from_paycheck: remaining,
          kind: "savings", category: "Savings", due: "",
          note: "no active debts — build savings or invest" });
      }
      remaining = 0;
    }

    const sum = (kinds) => r2(items.filter((i) => kinds.includes(i.kind))
      .reduce((s, i) => s + i.from_paycheck, 0));
    const totalAllocated = sum(["bill", "reserve", "debt_min", "debt_extra", "essentials", "emergency", "fun", "savings"]);
    return {
      pay_date: payDate,
      amount: r2(amount),
      window_days: period,
      next_paycheck_expected: windowEnd,
      items,
      warnings,
      totals: {
        bills: sum(["bill", "reserve"]),
        debt_min: sum(["debt_min"]),
        debt_extra: extra,
        essentials: sum(["essentials"]),
        emergency: emergencyAlloc,
        fun,
        savings: sum(["savings"]),
        allocated: totalAllocated,
        unallocated: r2(amount - totalAllocated),
      },
      reserve_updates: reserveUpdates,
      target_debt: targetDebt ? targetDebt.name : null,
      strategy,
    };
  }

  function simulatePayoff(debts, strategy, monthlyExtra, start) {
    start = start || todayISO();
    const balances = {};
    const info = {};
    for (const d of debts) {
      if (d.balance > 0.01) { balances[d.id] = d.balance; info[d.id] = d; }
    }
    if (!Object.keys(balances).length) {
      return { months: 0, total_interest: 0, debt_free_date: start, payoff_order: [], timeline: [], stuck: false };
    }
    let totalInterest = 0;
    const payoffOrder = [];
    const timeline = [];
    let month = 0;
    let freedMinimums = 0;

    while (Object.keys(balances).length && month < 720) {
      month += 1;
      for (const id of Object.keys(balances)) {
        const interest = balances[id] * info[id].apr / 100 / 12;
        balances[id] += interest;
        totalInterest += interest;
      }
      for (const id of Object.keys(balances)) {
        balances[id] -= Math.min(info[id].min_payment, balances[id]);
      }
      let budget = monthlyExtra + freedMinimums;
      while (budget > 0.005 && Object.keys(balances).length) {
        const active = Object.keys(balances).map((id) =>
          Object.assign({}, info[id], { balance: balances[id] }));
        const target = pickTargetDebt(active, strategy);
        if (!target) break;
        const pay = Math.min(budget, balances[target.id]);
        balances[target.id] -= pay;
        budget -= pay;
      }
      for (const id of Object.keys(balances)) {
        if (balances[id] <= 0.005) {
          freedMinimums += info[id].min_payment;
          payoffOrder.push({ name: info[id].name, month, date: addMonths(start, month) });
          delete balances[id];
        }
      }
      timeline.push({ month, date: addMonths(start, month),
        total_balance: r2(Object.values(balances).reduce((a, b) => a + b, 0)) });
    }
    const stuck = Object.keys(balances).length > 0;
    return {
      months: stuck ? null : month,
      total_interest: r2(totalInterest),
      debt_free_date: stuck ? null : addMonths(start, month),
      payoff_order: payoffOrder,
      timeline,
      stuck,
    };
  }

  function estimateMonthlyExtra(bills, debts, settings, recentPaychecks) {
    let income = Number(settings.monthly_net_income);
    if (income <= 0 && recentPaychecks.length) {
      const checks = recentPaychecks.slice(0, 8);
      const avg = checks.reduce((s, p) => s + p.amount, 0) / checks.length;
      income = avg * CHECKS_PER_MONTH[settings.pay_frequency];
    }
    const billsTotal = bills.reduce((s, b) => s + b.amount, 0);
    const minsTotal = debts.reduce((s, d) =>
      s + (d.balance > 0.01 ? Math.min(d.min_payment, d.balance) : 0), 0);
    const variable = Number(settings.variable_budget);
    const leftover = income - billsTotal - minsTotal - variable;
    const fun = Math.max(0, leftover) * Number(settings.fun_pct) / 100;
    return {
      monthly_income: r2(income),
      monthly_bills: r2(billsTotal),
      monthly_debt_minimums: r2(minsTotal),
      monthly_essentials: r2(variable),
      monthly_fun: r2(fun),
      monthly_extra: r2(Math.max(0, leftover - fun)),
    };
  }

  function compareStrategies(debts, monthlyExtra) {
    return {
      minimum_only: simulatePayoff(debts, "avalanche", 0),
      snowball: simulatePayoff(debts, "snowball", monthlyExtra),
      avalanche: simulatePayoff(debts, "avalanche", monthlyExtra),
    };
  }

  // ================================================================ importers

  const DEFAULT_RULES = [
    ["rent", "Housing"], ["mortgage", "Housing"], ["apartment", "Housing"],
    ["electric", "Utilities"], ["energy", "Utilities"], ["power", "Utilities"],
    ["water", "Utilities"], ["sewer", "Utilities"], ["gas co", "Utilities"],
    ["internet", "Utilities"], ["wifi", "Utilities"], ["comcast", "Utilities"],
    ["xfinity", "Utilities"], ["spectrum", "Utilities"], ["cox ", "Utilities"],
    ["verizon", "Phone"], ["t-mobile", "Phone"], ["tmobile", "Phone"], ["at&t", "Phone"],
    ["kroger", "Groceries"], ["walmart", "Groceries"], ["aldi", "Groceries"],
    ["costco", "Groceries"], ["trader joe", "Groceries"], ["publix", "Groceries"],
    ["safeway", "Groceries"], ["heb ", "Groceries"], ["wegmans", "Groceries"],
    ["whole foods", "Groceries"], ["grocery", "Groceries"], ["food lion", "Groceries"],
    ["shell", "Gas & Fuel"], ["chevron", "Gas & Fuel"], ["exxon", "Gas & Fuel"],
    ["bp ", "Gas & Fuel"], ["speedway", "Gas & Fuel"], ["circle k", "Gas & Fuel"],
    ["marathon", "Gas & Fuel"], ["fuel", "Gas & Fuel"],
    ["uber", "Transport"], ["lyft", "Transport"], ["parking", "Transport"],
    ["toll", "Transport"], ["transit", "Transport"],
    ["netflix", "Subscriptions"], ["spotify", "Subscriptions"], ["hulu", "Subscriptions"],
    ["disney", "Subscriptions"], ["youtube", "Subscriptions"], ["apple.com", "Subscriptions"],
    ["prime video", "Subscriptions"], ["audible", "Subscriptions"], ["patreon", "Subscriptions"],
    ["onlyfans", "Subscriptions"], ["hbo", "Subscriptions"], ["paramount", "Subscriptions"],
    ["mcdonald", "Dining"], ["starbucks", "Dining"], ["chipotle", "Dining"],
    ["chick-fil-a", "Dining"], ["taco bell", "Dining"], ["wendy", "Dining"],
    ["burger", "Dining"], ["pizza", "Dining"], ["doordash", "Dining"],
    ["grubhub", "Dining"], ["ubereats", "Dining"], ["uber eats", "Dining"],
    ["restaurant", "Dining"], ["cafe", "Dining"], ["diner", "Dining"], ["bar & grill", "Dining"],
    ["amazon", "Shopping"], ["target", "Shopping"], ["best buy", "Shopping"],
    ["ebay", "Shopping"], ["etsy", "Shopping"], ["temu", "Shopping"], ["shein", "Shopping"],
    ["gym", "Health & Fitness"], ["planet fitness", "Health & Fitness"],
    ["la fitness", "Health & Fitness"], ["pharmacy", "Health & Fitness"],
    ["cvs", "Health & Fitness"], ["walgreens", "Health & Fitness"],
    ["doctor", "Health & Fitness"], ["dental", "Health & Fitness"],
    ["geico", "Insurance"], ["progressive", "Insurance"], ["state farm", "Insurance"],
    ["allstate", "Insurance"], ["insurance", "Insurance"],
    ["steam", "Entertainment"], ["playstation", "Entertainment"], ["xbox", "Entertainment"],
    ["cinema", "Entertainment"], ["theatre", "Entertainment"], ["ticketmaster", "Entertainment"],
    ["payroll", "Income"], ["direct dep", "Income"], ["paycheck", "Income"], ["salary", "Income"],
    ["car payment", "Debt Payment"], ["loan pmt", "Debt Payment"], ["loan payment", "Debt Payment"],
    ["credit card pmt", "Debt Payment"], ["card payment", "Debt Payment"], ["autopay", "Debt Payment"],
    ["transfer", "Transfers"], ["zelle", "Transfers"], ["venmo", "Transfers"],
    ["cash app", "Transfers"], ["paypal", "Transfers"], ["atm", "Cash"],
  ];

  const DISCRETIONARY = new Set(["Dining", "Subscriptions", "Shopping", "Entertainment", "Other", "Cash"]);
  const ESSENTIAL_RECURRING = new Set(["Housing", "Utilities", "Phone", "Insurance", "Groceries",
    "Gas & Fuel", "Debt Payment", "Transfers", "Income"]);

  function mergeRules(userRules) {
    return userRules.map((r) => ({ keyword: r.keyword.toLowerCase(), category: r.category }))
      .concat(DEFAULT_RULES.map(([k, c]) => ({ keyword: k, category: c })));
  }

  function categorize(description, rules) {
    const desc = description.toLowerCase();
    for (const r of rules) if (desc.includes(r.keyword)) return r.category;
    return "Other";
  }

  function parseCSV(text) {
    // Small RFC-4180-ish parser: quoted fields, embedded commas/newlines.
    const rows = [];
    let row = [], field = "", inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; }
          else inQuotes = false;
        } else field += c;
      } else if (c === '"') {
        inQuotes = true;
      } else if (c === ",") {
        row.push(field); field = "";
      } else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        rows.push(row); row = [];
      } else {
        field += c;
      }
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    return rows.filter((r) => r.some((cell) => cell.trim() !== ""));
  }

  const DATE_COLS = ["date", "transaction date", "trans date", "posted date", "posting date", "post date"];
  const DESC_COLS = ["description", "memo", "payee", "name", "details", "merchant", "transaction"];
  const AMOUNT_COLS = ["amount", "transaction amount", "amt"];
  const DEBIT_COLS = ["debit", "withdrawal", "withdrawals", "money out", "outflow"];
  const CREDIT_COLS = ["credit", "deposit", "deposits", "money in", "inflow"];

  function parseAnyDate(value) {
    value = value.trim();
    let m = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
    m = value.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/);
    if (m) {
      let [, mo, d, y] = m;
      if (y.length === 2) y = (Number(y) > 70 ? "19" : "20") + y;
      if (Number(mo) > 12 && Number(d) <= 12) [mo, d] = [d, mo]; // d/m/y statements
      if (Number(mo) < 1 || Number(mo) > 12 || Number(d) < 1 || Number(d) > 31) return null;
      return `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
    }
    const parsed = new Date(value);
    if (!isNaN(parsed) && /[a-zA-Z]/.test(value)) {
      return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
    }
    return null;
  }

  function parseMoney(value) {
    value = String(value ?? "").trim().replace(/\$/g, "").replace(/,/g, "");
    if (!value) return null;
    const negative = value.startsWith("(") && value.endsWith(")");
    value = value.replace(/^\(|\)$/g, "");
    const num = Number(value);
    if (isNaN(num)) return null;
    return negative ? -num : num;
  }

  function findCol(header, candidates) {
    const lowered = header.map((h) => h.trim().toLowerCase());
    for (const cand of candidates) {
      const i = lowered.indexOf(cand);
      if (i !== -1) return i;
    }
    for (const cand of candidates) {
      const i = lowered.findIndex((h) => h.includes(cand));
      if (i !== -1) return i;
    }
    return null;
  }

  function parseBankCsv(text, rules) {
    const lines = parseCSV(text);
    if (!lines.length) return [[], "File is empty."];
    const header = lines[0];
    const di = findCol(header, DATE_COLS);
    if (di === null) return [[], "Couldn't find a date column. Expected a header row with a 'Date' column."];
    const descI = findCol(header, DESC_COLS);
    const amtI = findCol(header, AMOUNT_COLS);
    const debitI = findCol(header, DEBIT_COLS);
    const creditI = findCol(header, CREDIT_COLS);
    if (amtI === null && debitI === null && creditI === null) {
      return [[], "Couldn't find an Amount (or Debit/Credit) column."];
    }
    const txns = [];
    let skipped = 0;
    for (const row of lines.slice(1)) {
      if (row.length <= di) { skipped++; continue; }
      const d = parseAnyDate(row[di]);
      if (!d) { skipped++; continue; }
      const desc = descI !== null && row.length > descI ? row[descI].trim() : "Transaction";
      let amount = null;
      if (amtI !== null && row.length > amtI) amount = parseMoney(row[amtI]);
      if (amount === null) {
        const debit = debitI !== null && row.length > debitI ? parseMoney(row[debitI]) : null;
        const credit = creditI !== null && row.length > creditI ? parseMoney(row[creditI]) : null;
        if (debit) amount = -Math.abs(debit);
        else if (credit) amount = Math.abs(credit);
      }
      if (amount === null) { skipped++; continue; }
      let category = categorize(desc, rules);
      if (amount > 0 && category === "Other") category = "Income";
      txns.push({ date: d, description: desc, amount: r2(amount), category });
    }
    return [txns, skipped ? `Skipped ${skipped} unparseable row(s).` : ""];
  }

  const DEBT_NAME_COLS = ["name", "account", "account name", "creditor", "lender"];
  const DEBT_BALANCE_COLS = ["balance", "amount owed", "current balance", "owed"];
  const DEBT_APR_COLS = ["apr", "interest rate", "rate", "interest"];
  const DEBT_MIN_COLS = ["min payment", "minimum payment", "monthly payment", "payment", "min_payment"];
  const DEBT_TERM_COLS = ["term", "term months", "months", "term_months"];
  const DEBT_DUE_COLS = ["due day", "due", "due_day"];

  const KNOWN_CREDITORS = [
    "capital one", "chase", "discover", "amex", "american express", "citi", "citibank",
    "bank of america", "wells fargo", "synchrony", "credit one", "usaa", "navy federal",
    "us bank", "barclays", "goldman", "apple card", "affirm", "klarna", "afterpay",
    "upstart", "sofi", "lending club", "avant", "onemain", "ally", "santander",
    "toyota financial", "honda financial", "gm financial", "ford credit", "carmax",
    "nelnet", "navient", "mohela", "great lakes", "fedloan", "sallie mae", "earnest",
    "aidvantage", "student loan", "auto loan", "car loan", "personal loan", "medical",
    "credit card", "visa", "mastercard",
  ];

  function parseDebtsCsv(text) {
    const lines = parseCSV(text);
    if (lines.length < 2) return [];
    const header = lines[0];
    const ni = findCol(header, DEBT_NAME_COLS);
    const bi = findCol(header, DEBT_BALANCE_COLS);
    if (ni === null || bi === null) return [];
    const ai = findCol(header, DEBT_APR_COLS);
    const mi = findCol(header, DEBT_MIN_COLS);
    const ti = findCol(header, DEBT_TERM_COLS);
    const dui = findCol(header, DEBT_DUE_COLS);
    const cell = (row, i) => (i !== null && row.length > i ? row[i].trim() : "");

    const debts = [];
    for (const row of lines.slice(1)) {
      const name = cell(row, ni);
      const balance = parseMoney(cell(row, bi));
      if (!name || balance === null) continue;
      const term = cell(row, ti);
      const due = cell(row, dui);
      debts.push({
        name,
        balance: Math.abs(balance),
        apr: parseMoney(cell(row, ai).replace("%", "")) || 0,
        min_payment: Math.abs(parseMoney(cell(row, mi)) || 0),
        term_months: term ? Math.trunc(Number(term)) : null,
        due_day: due ? Math.trunc(Number(due)) : 1,
        kind: "other",
      });
    }
    return debts;
  }

  const MONEY_RE = "\\$?\\s?([\\d,]+(?:\\.\\d{1,2})?)";

  function parseDebtsText(text) {
    const found = [];
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const low = line.toLowerCase();
      const creditor = KNOWN_CREDITORS.find((c) => low.includes(c)) || null;
      // Detail lines ("Balance: $X", "APR: Y%") describe the account named
      // above them — they are context, not accounts of their own.
      if (!creditor && /^(balance|amount|owed|apr|interest|min(imum)?|monthly|payment)\b/.test(low)) continue;
      let balanceM = low.match(new RegExp("(?:balance|owed|amount)\\D{0,12}" + MONEY_RE));
      if (!creditor && !balanceM) continue;
      const context = lines.slice(i, i + 4).join(" ").toLowerCase();
      balanceM = balanceM || context.match(new RegExp("(?:balance|owed|amount)\\D{0,12}" + MONEY_RE));
      if (!balanceM) {
        const money = context.match(new RegExp(MONEY_RE));
        balanceM = creditor && money ? money : null;
      }
      if (!balanceM) continue;
      const balance = Number(balanceM[1].replace(/,/g, ""));
      const aprM = context.match(/([\d.]+)\s?%/);
      const minM = context.match(new RegExp("(?:min(?:imum)?|monthly)\\s?(?:payment|pmt)\\D{0,12}" + MONEY_RE));
      const entry = {
        name: line.replace(/\s+/g, " ").slice(0, 60),
        balance,
        apr: aprM ? Number(aprM[1]) : 0,
        min_payment: minM ? Number(minM[1].replace(/,/g, "")) : 0,
        term_months: null, due_day: 1, kind: "other",
      };
      if (found.every((f) => Math.abs(f.balance - balance) > 0.01 || f.name !== entry.name)) {
        found.push(entry);
      }
    }
    return found;
  }

  function normalizeMerchant(desc) {
    return desc.toLowerCase().replace(/[#*\d]/g, "").replace(/\s+/g, " ").trim().slice(0, 32);
  }

  function spendingSummary(transactions, months) {
    months = months || 6;
    const byMonth = {};
    const incomeByMonth = {};
    const merchants = {};
    for (const t of transactions) {
      const month = t.date.slice(0, 7);
      if (t.amount >= 0 || ["Transfers", "Debt Payment", "Income"].includes(t.category)) {
        if (t.amount > 0 && t.category === "Income") {
          incomeByMonth[month] = (incomeByMonth[month] || 0) + t.amount;
        }
        continue;
      }
      const spent = -t.amount;
      const cat = t.category || "Other";
      (byMonth[month] = byMonth[month] || {})[cat] = (byMonth[month][cat] || 0) + spent;
      const key = normalizeMerchant(t.description);
      (merchants[key] = merchants[key] || []).push([month, spent, t.description, cat]);
    }

    const monthKeys = Object.keys(byMonth).sort().slice(-months);
    const byMonthKept = {};
    for (const m of monthKeys) {
      byMonthKept[m] = {};
      for (const [k, v] of Object.entries(byMonth[m])) byMonthKept[m][k] = r2(v);
    }
    const nMonths = Math.max(1, monthKeys.length);

    const catTotals = {};
    for (const m of monthKeys) {
      for (const [cat, amt] of Object.entries(byMonth[m])) {
        catTotals[cat] = (catTotals[cat] || 0) + amt;
      }
    }
    const categories = Object.entries(catTotals)
      .sort((a, b) => b[1] - a[1])
      .map(([category, total]) => ({
        category,
        total: r2(total),
        monthly_avg: r2(total / nMonths),
        discretionary: DISCRETIONARY.has(category),
      }));

    const recurring = [];
    for (const hits of Object.values(merchants)) {
      if (ESSENTIAL_RECURRING.has(hits[0][3])) continue;
      const hitMonths = new Set(hits.map((h) => h[0]));
      if (hitMonths.size >= 2) {
        const amounts = hits.map((h) => h[1]);
        const avg = amounts.reduce((a, b) => a + b, 0) / amounts.length;
        const spread = Math.max(...amounts) - Math.min(...amounts);
        if (avg > 0 && spread <= Math.max(2.0, avg * 0.25)) {
          recurring.push({ merchant: hits[0][2].slice(0, 48), monthly_avg: r2(avg),
            months_seen: hitMonths.size });
        }
      }
    }
    recurring.sort((a, b) => b.monthly_avg - a.monthly_avg);

    const suggestions = [];
    for (const c of categories) {
      if (c.discretionary && c.monthly_avg >= 20) {
        const cut = r2(c.monthly_avg * 0.5);
        suggestions.push({
          category: c.category, monthly_avg: c.monthly_avg, suggested_cut: cut,
          message: `You average $${Math.round(c.monthly_avg)}/mo on ${c.category}. ` +
                   `Cutting half frees $${Math.round(cut)}/mo for debt payoff.`,
        });
      }
    }

    const incomeKept = {};
    for (const m of Object.keys(incomeByMonth).sort().slice(-months)) {
      incomeKept[m] = r2(incomeByMonth[m]);
    }
    return {
      months: monthKeys,
      by_month: byMonthKept,
      categories,
      income_by_month: incomeKept,
      recurring: recurring.slice(0, 20),
      suggestions,
      total_monthly_spend: r2(categories.reduce((s, c) => s + c.monthly_avg, 0)),
      potential_monthly_savings: r2(suggestions.reduce((s, x) => s + x.suggested_cut, 0)),
    };
  }

  // ================================================================ routes
  // Mirrors app/server.py so the frontend works unchanged.

  function listDebts(db) {
    return db.debts.slice().sort((a, b) => b.apr - a.apr || b.balance - a.balance);
  }

  function listBills(db) {
    return db.bills.slice().sort((a, b) => a.due_day - b.due_day || a.name.localeCompare(b.name));
  }

  function listPaychecks(db, limit) {
    return db.paychecks.slice().sort((a, b) =>
      b.date.localeCompare(a.date) || b.id - a.id).slice(0, limit || 50);
  }

  function listTransactions(db, limit) {
    return db.transactions.slice().sort((a, b) =>
      b.date.localeCompare(a.date) || b.id - a.id).slice(0, limit || 1000);
  }

  function upsertDebt(db, d) {
    const fields = {
      name: (d.name || "Debt").trim(),
      kind: d.kind || "other",
      balance: Number(d.balance) || 0,
      apr: Number(d.apr) || 0,
      min_payment: Number(d.min_payment) || 0,
      term_months: d.term_months ? Math.trunc(Number(d.term_months)) : null,
      due_day: Math.max(1, Math.min(28, Math.trunc(Number(d.due_day)) || 1)),
      notes: d.notes || "",
    };
    if (d.id) {
      const existing = db.debts.find((x) => x.id === Number(d.id));
      if (existing) Object.assign(existing, fields);
    } else {
      db.debts.push(Object.assign({ id: nextId(db) }, fields));
    }
  }

  function upsertBill(db, b) {
    const fields = {
      name: (b.name || "Bill").trim(),
      category: b.category || "Other",
      amount: Number(b.amount) || 0,
      due_day: Math.max(1, Math.min(28, Math.trunc(Number(b.due_day)) || 1)),
      notes: b.notes || "",
    };
    if (b.id) {
      const existing = db.bills.find((x) => x.id === Number(b.id));
      if (existing) Object.assign(existing, fields);
    } else {
      db.bills.push(Object.assign({ id: nextId(db), reserved: 0 }, fields));
    }
  }

  function buildAndApplyPlan(db, amount, payDate, source, apply) {
    const settings = getSettings(db);
    const bills = listBills(db);
    const debts = listDebts(db);
    const plan = buildPlan(amount, payDate, bills, debts, settings);

    const extraMonthly = plan.totals.debt_extra * CHECKS_PER_MONTH[settings.pay_frequency];
    if (debts.length && extraMonthly > 0) {
      const base = simulatePayoff(debts, settings.strategy, 0);
      const boosted = simulatePayoff(debts, settings.strategy, extraMonthly);
      if (base.months && boosted.months) {
        plan.impact = {
          months_saved: base.months - boosted.months,
          interest_saved: r2(base.total_interest - boosted.total_interest),
          debt_free_date: boosted.debt_free_date,
        };
      }
    }

    if (apply) {
      for (const [billId, reserved] of Object.entries(plan.reserve_updates)) {
        const bill = db.bills.find((x) => x.id === Number(billId));
        if (bill) bill.reserved = reserved;
      }
      for (const item of plan.items) {
        if ((item.kind === "debt_min" || item.kind === "debt_extra") && item.debt_id) {
          const debt = db.debts.find((x) => x.id === item.debt_id);
          if (debt) debt.balance = r2(Math.max(0, debt.balance - item.amount));
        }
      }
      if (plan.totals.emergency > 0) {
        db.settings.emergency_balance =
          String(r2(Number(getSettings(db).emergency_balance) + plan.totals.emergency));
      }
      const id = nextId(db);
      db.paychecks.push({ id, source, amount: plan.amount, date: plan.pay_date, plan });
      plan.paycheck_id = id;
      save(db);
    }
    return plan;
  }

  function addTransactions(db, txns) {
    let added = 0;
    for (const t of txns) {
      const dup = db.transactions.some((x) =>
        x.date === t.date && x.description === t.description && x.amount === t.amount);
      if (dup) continue;
      db.transactions.push({ id: nextId(db), date: t.date, description: t.description,
        amount: t.amount, category: t.category || "" });
      added++;
    }
    return added;
  }

  function stateResponse(db) {
    const settings = getSettings(db);
    const debts = listDebts(db);
    const bills = listBills(db);
    const paychecks = listPaychecks(db, 12);
    return {
      settings, debts, bills, paychecks,
      budget: estimateMonthlyExtra(bills, debts, settings, paychecks),
      today: todayISO(),
    };
  }

  function handle(path, body) {
    const [route, queryStr] = path.split("?");
    const query = {};
    for (const pair of (queryStr || "").split("&")) {
      if (!pair) continue;
      const [k, v] = pair.split("=");
      query[decodeURIComponent(k)] = decodeURIComponent(v || "");
    }
    const db = load();
    const settings = getSettings(db);

    switch (route) {
      case "/api/state":
        return stateResponse(db);

      case "/api/projection": {
        const debts = listDebts(db);
        const budget = estimateMonthlyExtra(listBills(db), debts, settings, listPaychecks(db));
        let extra = budget.monthly_extra;
        if (query.extra !== undefined && query.extra !== "" && !isNaN(Number(query.extra))) {
          extra = Number(query.extra);
        }
        return { budget, comparison: compareStrategies(debts, extra), extra_used: extra };
      }

      case "/api/transactions":
        return { transactions: listTransactions(db) };

      case "/api/spending": {
        const summary = spendingSummary(listTransactions(db, 10000), Number(query.months) || 6);
        const debts = listDebts(db);
        const budget = estimateMonthlyExtra(listBills(db), debts, settings, listPaychecks(db));
        const cut = summary.potential_monthly_savings;
        if (cut > 0 && debts.some((d) => d.balance > 0.01)) {
          const base = simulatePayoff(debts, settings.strategy, budget.monthly_extra);
          const boosted = simulatePayoff(debts, settings.strategy, budget.monthly_extra + cut);
          if (base.months && boosted.months) {
            summary.cut_impact = {
              months_saved: base.months - boosted.months,
              interest_saved: r2(base.total_interest - boosted.total_interest),
            };
          }
        }
        return summary;
      }

      case "/api/rules":
        if (body) {
          db.rules.push({ id: nextId(db), keyword: body.keyword.toLowerCase(), category: body.category });
          save(db);
        }
        return { ok: true, rules: db.rules.slice().sort((a, b) => a.keyword.localeCompare(b.keyword)) };

      case "/api/rules/delete":
        db.rules = db.rules.filter((r) => r.id !== Number(body.id));
        save(db);
        return { ok: true };

      case "/api/export":
        return {
          settings, debts: listDebts(db), bills: listBills(db),
          paychecks: listPaychecks(db, 100000),
          transactions: listTransactions(db, 100000),
          rules: db.rules,
        };

      case "/api/debts":
        for (const d of Array.isArray(body) ? body : [body]) upsertDebt(db, d);
        save(db);
        return { ok: true, debts: listDebts(db) };

      case "/api/debts/delete":
        db.debts = db.debts.filter((d) => d.id !== Number(body.id));
        save(db);
        return { ok: true };

      case "/api/debts/import": {
        const text = body.text || "";
        let debts = parseDebtsCsv(text);
        let source = "csv";
        if (!debts.length) { debts = parseDebtsText(text); source = "text"; }
        return { debts, source };
      }

      case "/api/bills":
        for (const b of Array.isArray(body) ? body : [body]) upsertBill(db, b);
        save(db);
        return { ok: true, bills: listBills(db) };

      case "/api/bills/delete":
        db.bills = db.bills.filter((b) => b.id !== Number(body.id));
        save(db);
        return { ok: true };

      case "/api/paycheck": {
        const amount = Number(body.amount);
        if (!amount || isNaN(amount)) throw new Error("Bad request: amount");
        const payDate = (body.date || todayISO()).slice(0, 10);
        return { plan: buildAndApplyPlan(db, amount, payDate, body.source || "Paycheck", !body.preview) };
      }

      case "/api/paycheck/delete":
        db.paychecks = db.paychecks.filter((p) => p.id !== Number(body.id));
        save(db);
        return { ok: true };

      case "/api/transactions/import": {
        const [txns, note] = parseBankCsv(body.csv || "", mergeRules(db.rules));
        const added = addTransactions(db, txns);
        save(db);
        return { parsed: txns.length, added, duplicates: txns.length - added, note };
      }

      case "/api/transactions/clear":
        db.transactions = [];
        save(db);
        return { ok: true };

      case "/api/transactions/category": {
        const t = db.transactions.find((x) => x.id === Number(body.id));
        if (t) t.category = body.category;
        save(db);
        return { ok: true };
      }

      case "/api/settings": {
        for (const [key, value] of Object.entries(body || {})) {
          if (key in DEFAULT_SETTINGS) db.settings[key] = String(value);
        }
        save(db);
        return { ok: true, settings: getSettings(db) };
      }

      case "/api/quit":
        return { ok: true, bye: true };

      default:
        throw new Error("not found: " + route);
    }
  }

  window.LOCAL_API = {
    call(path, body) {
      // Async to match fetch-based api(); serialize to strip prototypes,
      // exactly like a JSON round-trip through the server would.
      return Promise.resolve().then(() => JSON.parse(JSON.stringify(handle(path, body))));
    },
    // exposed for tests
    _internals: { buildPlan, simulatePayoff, spendingSummary, parseBankCsv, parseDebtsCsv,
      parseDebtsText, nextDueDate, estimateMonthlyExtra },
  };
})();
