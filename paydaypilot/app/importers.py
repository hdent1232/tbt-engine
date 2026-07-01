"""Import + categorization: bank statement CSVs, credit report text/CSV,
keyword-based transaction categorization and spending analysis.
"""

import csv
import io
import re
from datetime import date, datetime

# ------------------------------------------------------------ categorization

DEFAULT_RULES = [
    ("rent", "Housing"), ("mortgage", "Housing"), ("apartment", "Housing"),
    ("electric", "Utilities"), ("energy", "Utilities"), ("power", "Utilities"),
    ("water", "Utilities"), ("sewer", "Utilities"), ("gas co", "Utilities"),
    ("internet", "Utilities"), ("wifi", "Utilities"), ("comcast", "Utilities"),
    ("xfinity", "Utilities"), ("spectrum", "Utilities"), ("cox ", "Utilities"),
    ("verizon", "Phone"), ("t-mobile", "Phone"), ("tmobile", "Phone"), ("at&t", "Phone"),
    ("kroger", "Groceries"), ("walmart", "Groceries"), ("aldi", "Groceries"),
    ("costco", "Groceries"), ("trader joe", "Groceries"), ("publix", "Groceries"),
    ("safeway", "Groceries"), ("heb ", "Groceries"), ("wegmans", "Groceries"),
    ("whole foods", "Groceries"), ("grocery", "Groceries"), ("food lion", "Groceries"),
    ("shell", "Gas & Fuel"), ("chevron", "Gas & Fuel"), ("exxon", "Gas & Fuel"),
    ("bp ", "Gas & Fuel"), ("speedway", "Gas & Fuel"), ("circle k", "Gas & Fuel"),
    ("marathon", "Gas & Fuel"), ("fuel", "Gas & Fuel"),
    ("uber", "Transport"), ("lyft", "Transport"), ("parking", "Transport"),
    ("toll", "Transport"), ("transit", "Transport"),
    ("netflix", "Subscriptions"), ("spotify", "Subscriptions"), ("hulu", "Subscriptions"),
    ("disney", "Subscriptions"), ("youtube", "Subscriptions"), ("apple.com", "Subscriptions"),
    ("prime video", "Subscriptions"), ("audible", "Subscriptions"), ("patreon", "Subscriptions"),
    ("onlyfans", "Subscriptions"), ("hbo", "Subscriptions"), ("paramount", "Subscriptions"),
    ("mcdonald", "Dining"), ("starbucks", "Dining"), ("chipotle", "Dining"),
    ("chick-fil-a", "Dining"), ("taco bell", "Dining"), ("wendy", "Dining"),
    ("burger", "Dining"), ("pizza", "Dining"), ("doordash", "Dining"),
    ("grubhub", "Dining"), ("ubereats", "Dining"), ("uber eats", "Dining"),
    ("restaurant", "Dining"), ("cafe", "Dining"), ("diner", "Dining"), ("bar & grill", "Dining"),
    ("amazon", "Shopping"), ("target", "Shopping"), ("best buy", "Shopping"),
    ("ebay", "Shopping"), ("etsy", "Shopping"), ("temu", "Shopping"), ("shein", "Shopping"),
    ("gym", "Health & Fitness"), ("planet fitness", "Health & Fitness"),
    ("la fitness", "Health & Fitness"), ("pharmacy", "Health & Fitness"),
    ("cvs", "Health & Fitness"), ("walgreens", "Health & Fitness"),
    ("doctor", "Health & Fitness"), ("dental", "Health & Fitness"),
    ("geico", "Insurance"), ("progressive", "Insurance"), ("state farm", "Insurance"),
    ("allstate", "Insurance"), ("insurance", "Insurance"),
    ("steam", "Entertainment"), ("playstation", "Entertainment"), ("xbox", "Entertainment"),
    ("cinema", "Entertainment"), ("theatre", "Entertainment"), ("ticketmaster", "Entertainment"),
    ("payroll", "Income"), ("direct dep", "Income"), ("paycheck", "Income"), ("salary", "Income"),
    ("car payment", "Debt Payment"), ("loan pmt", "Debt Payment"), ("loan payment", "Debt Payment"),
    ("credit card pmt", "Debt Payment"), ("card payment", "Debt Payment"), ("autopay", "Debt Payment"),
    ("transfer", "Transfers"), ("zelle", "Transfers"), ("venmo", "Transfers"),
    ("cash app", "Transfers"), ("paypal", "Transfers"), ("atm", "Cash"),
]

DISCRETIONARY = {"Dining", "Subscriptions", "Shopping", "Entertainment", "Other", "Cash"}

# Recurring-but-essential categories that shouldn't be flagged as "likely
# subscriptions" (rent recurs every month; that's not a subscription to cancel).
ESSENTIAL_RECURRING = {"Housing", "Utilities", "Phone", "Insurance", "Groceries",
                       "Gas & Fuel", "Debt Payment", "Transfers", "Income"}


def categorize(description, rules):
    desc = description.lower()
    for r in rules:  # user rules first (caller puts them first)
        if r["keyword"] in desc:
            return r["category"]
    return "Other"


def merge_rules(user_rules):
    merged = [{"keyword": r["keyword"].lower(), "category": r["category"]} for r in user_rules]
    merged += [{"keyword": k, "category": c} for k, c in DEFAULT_RULES]
    return merged


# ------------------------------------------------------------ bank CSV import

DATE_COLS = ("date", "transaction date", "trans date", "posted date", "posting date", "post date")
DESC_COLS = ("description", "memo", "payee", "name", "details", "merchant", "transaction")
AMOUNT_COLS = ("amount", "transaction amount", "amt")
DEBIT_COLS = ("debit", "withdrawal", "withdrawals", "money out", "outflow")
CREDIT_COLS = ("credit", "deposit", "deposits", "money in", "inflow")

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y")


def _parse_date(value):
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_money(value):
    value = value.strip().replace("$", "").replace(",", "")
    if not value:
        return None
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()")
    try:
        num = float(value)
    except ValueError:
        return None
    return -num if negative else num


def _find_col(header, candidates):
    lowered = [h.strip().lower() for h in header]
    for cand in candidates:
        for i, h in enumerate(lowered):
            if h == cand:
                return i
    for cand in candidates:
        for i, h in enumerate(lowered):
            if cand in h:
                return i
    return None


def parse_bank_csv(text, rules):
    """Parse a bank/credit-card statement CSV into normalized transactions.

    Handles single signed 'Amount' columns as well as separate Debit/Credit
    columns. Amounts are stored with money-out negative.
    """
    reader = csv.reader(io.StringIO(text))
    lines = [row for row in reader if any(cell.strip() for cell in row)]
    if not lines:
        return [], "File is empty."

    header = lines[0]
    di = _find_col(header, DATE_COLS)
    if di is None:
        return [], "Couldn't find a date column. Expected a header row with a 'Date' column."
    desc_i = _find_col(header, DESC_COLS)
    amt_i = _find_col(header, AMOUNT_COLS)
    debit_i = _find_col(header, DEBIT_COLS)
    credit_i = _find_col(header, CREDIT_COLS)
    if amt_i is None and debit_i is None and credit_i is None:
        return [], "Couldn't find an Amount (or Debit/Credit) column."

    txns = []
    skipped = 0
    for row in lines[1:]:
        if len(row) <= di:
            skipped += 1
            continue
        d = _parse_date(row[di])
        if not d:
            skipped += 1
            continue
        desc = row[desc_i].strip() if desc_i is not None and len(row) > desc_i else "Transaction"
        amount = None
        if amt_i is not None and len(row) > amt_i:
            amount = _parse_money(row[amt_i])
        if amount is None:
            debit = _parse_money(row[debit_i]) if debit_i is not None and len(row) > debit_i else None
            credit = _parse_money(row[credit_i]) if credit_i is not None and len(row) > credit_i else None
            if debit:
                amount = -abs(debit)
            elif credit:
                amount = abs(credit)
        if amount is None:
            skipped += 1
            continue
        category = categorize(desc, rules)
        if amount > 0 and category == "Other":
            category = "Income"
        txns.append({"date": d, "description": desc, "amount": round(amount, 2),
                     "category": category})
    note = f"Skipped {skipped} unparseable row(s)." if skipped else ""
    return txns, note


# ------------------------------------------------------------ credit report import

DEBT_NAME_COLS = ("name", "account", "account name", "creditor", "lender")
DEBT_BALANCE_COLS = ("balance", "amount owed", "current balance", "owed")
DEBT_APR_COLS = ("apr", "interest rate", "rate", "interest")
DEBT_MIN_COLS = ("min payment", "minimum payment", "monthly payment", "payment", "min_payment")
DEBT_TERM_COLS = ("term", "term months", "months", "term_months")
DEBT_DUE_COLS = ("due day", "due", "due_day")

KNOWN_CREDITORS = (
    "capital one", "chase", "discover", "amex", "american express", "citi", "citibank",
    "bank of america", "wells fargo", "synchrony", "credit one", "usaa", "navy federal",
    "us bank", "barclays", "goldman", "apple card", "affirm", "klarna", "afterpay",
    "upstart", "sofi", "lending club", "avant", "onemain", "ally", "santander",
    "toyota financial", "honda financial", "gm financial", "ford credit", "carmax",
    "nelnet", "navient", "mohela", "great lakes", "fedloan", "sallie mae", "earnest",
    "aidvantage", "student loan", "auto loan", "car loan", "personal loan", "medical",
    "credit card", "visa", "mastercard",
)


def parse_debts_csv(text):
    reader = csv.reader(io.StringIO(text))
    lines = [row for row in reader if any(cell.strip() for cell in row)]
    if len(lines) < 2:
        return []
    header = lines[0]
    ni = _find_col(header, DEBT_NAME_COLS)
    bi = _find_col(header, DEBT_BALANCE_COLS)
    if ni is None or bi is None:
        return []
    ai = _find_col(header, DEBT_APR_COLS)
    mi = _find_col(header, DEBT_MIN_COLS)
    ti = _find_col(header, DEBT_TERM_COLS)
    dui = _find_col(header, DEBT_DUE_COLS)

    def cell(row, i):
        return row[i].strip() if i is not None and len(row) > i else ""

    debts = []
    for row in lines[1:]:
        name = cell(row, ni)
        balance = _parse_money(cell(row, bi))
        if not name or balance is None:
            continue
        apr = _parse_money(cell(row, ai).replace("%", "")) or 0
        minp = _parse_money(cell(row, mi)) or 0
        term = cell(row, ti)
        due = cell(row, dui)
        debts.append({
            "name": name, "balance": abs(balance), "apr": apr, "min_payment": abs(minp),
            "term_months": int(float(term)) if term else None,
            "due_day": int(float(due)) if due else 1,
            "kind": "other",
        })
    return debts


MONEY_RE = r"\$?\s?([\d,]+(?:\.\d{1,2})?)"


def parse_debts_text(text):
    """Best-effort extraction of accounts + balances from pasted credit report text.

    Looks for known creditor names and grabs the balance / rate / payment
    figures near them. Anything found is presented for review before saving.
    """
    found = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        low = line.lower()
        creditor = next((c for c in KNOWN_CREDITORS if c in low), None)
        # Detail lines ("Balance: $X", "APR: Y%") describe the account named
        # above them — they are context, not accounts of their own.
        if not creditor and re.match(r"^(balance|amount|owed|apr|interest|min(imum)?|monthly|payment)\b", low):
            continue
        balance_m = re.search(r"(?:balance|owed|amount)\D{0,12}" + MONEY_RE, low)
        if not creditor and not balance_m:
            continue
        context = " ".join(lines[i:i + 4]).lower()
        balance_m = balance_m or re.search(r"(?:balance|owed|amount)\D{0,12}" + MONEY_RE, context)
        if not balance_m:
            money = re.search(MONEY_RE, context)
            balance_m = money if creditor and money else None
        if not balance_m:
            continue
        balance = float(balance_m.group(1).replace(",", ""))
        apr_m = re.search(r"([\d.]+)\s?%", context)
        min_m = re.search(r"(?:min(?:imum)?|monthly)\s?(?:payment|pmt)\D{0,12}" + MONEY_RE, context)
        name = line if not creditor else line[:60]
        entry = {
            "name": re.sub(r"\s+", " ", name)[:60],
            "balance": balance,
            "apr": float(apr_m.group(1)) if apr_m else 0,
            "min_payment": float(min_m.group(1).replace(",", "")) if min_m else 0,
            "term_months": None, "due_day": 1, "kind": "other",
        }
        if all(abs(f["balance"] - balance) > 0.01 or f["name"] != entry["name"] for f in found):
            found.append(entry)
    return found


# ------------------------------------------------------------ spending analysis

def spending_summary(transactions, months=6):
    """Monthly totals per category, recurring-charge detection and cut suggestions."""
    by_month = {}
    income_by_month = {}
    merchants = {}
    for t in transactions:
        month = t["date"][:7]
        if t["amount"] >= 0 or t["category"] in ("Transfers", "Debt Payment", "Income"):
            if t["amount"] > 0 and t["category"] == "Income":
                income_by_month[month] = income_by_month.get(month, 0) + t["amount"]
            continue
        spent = -t["amount"]
        cat = t["category"] or "Other"
        by_month.setdefault(month, {})
        by_month[month][cat] = by_month[month].get(cat, 0) + spent
        key = _normalize_merchant(t["description"])
        merchants.setdefault(key, []).append((month, spent, t["description"], cat))

    month_keys = sorted(by_month.keys())[-months:]
    by_month = {m: by_month[m] for m in month_keys}
    n_months = max(1, len(month_keys))

    cat_totals = {}
    for m in by_month.values():
        for cat, amt in m.items():
            cat_totals[cat] = cat_totals.get(cat, 0) + amt
    categories = [
        {
            "category": cat,
            "total": round(total, 2),
            "monthly_avg": round(total / n_months, 2),
            "discretionary": cat in DISCRETIONARY,
        }
        for cat, total in sorted(cat_totals.items(), key=lambda kv: -kv[1])
    ]

    recurring = []
    for key, hits in merchants.items():
        if hits[0][3] in ESSENTIAL_RECURRING:
            continue
        hit_months = {m for m, _, _, _ in hits}
        if len(hit_months) >= 2:
            amounts = [a for _, a, _, _ in hits]
            avg = sum(amounts) / len(amounts)
            spread = max(amounts) - min(amounts)
            if avg > 0 and spread <= max(2.0, avg * 0.25):
                recurring.append({
                    "merchant": hits[0][2][:48],
                    "monthly_avg": round(avg, 2),
                    "months_seen": len(hit_months),
                })
    recurring.sort(key=lambda r: -r["monthly_avg"])

    suggestions = []
    for c in categories:
        if c["discretionary"] and c["monthly_avg"] >= 20:
            cut = round(c["monthly_avg"] * 0.5, 2)
            suggestions.append({
                "category": c["category"],
                "monthly_avg": c["monthly_avg"],
                "suggested_cut": cut,
                "message": f"You average ${c['monthly_avg']:.0f}/mo on {c['category']}. "
                           f"Cutting half frees ${cut:.0f}/mo for debt payoff.",
            })

    return {
        "months": month_keys,
        "by_month": {m: {k: round(v, 2) for k, v in cats.items()} for m, cats in by_month.items()},
        "categories": categories,
        "income_by_month": {m: round(v, 2) for m, v in sorted(income_by_month.items())[-months:]},
        "recurring": recurring[:20],
        "suggestions": suggestions,
        "total_monthly_spend": round(sum(c["monthly_avg"] for c in categories), 2),
        "potential_monthly_savings": round(sum(s["suggested_cut"] for s in suggestions), 2),
    }


def _normalize_merchant(desc):
    d = re.sub(r"[#*\d]", "", desc.lower())
    d = re.sub(r"\s+", " ", d).strip()
    return d[:32]
