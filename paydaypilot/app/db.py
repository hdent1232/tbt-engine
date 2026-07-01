"""SQLite storage layer for PayDay Pilot.

All data lives in a single SQLite file in the user's home directory
(~/.paydaypilot/data.db) so the app works the same whether it is run from
source or from a packaged executable. Override with the PAYDAYPILOT_DATA
environment variable (useful for tests).
"""

import json
import os
import sqlite3

DEFAULT_SETTINGS = {
    "pay_frequency": "biweekly",   # weekly | biweekly | semimonthly | monthly
    "strategy": "avalanche",       # avalanche | snowball
    "emergency_target": "1000",
    "emergency_balance": "0",
    "emergency_pct": "20",         # % of leftover routed to emergency fund until target met
    "fun_pct": "5",                # % of leftover kept as guilt-free spending money
    "variable_budget": "600",      # monthly budget for groceries/gas/day-to-day essentials
    "monthly_net_income": "0",     # 0 = estimate from recent paychecks
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS debts(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'other',
    balance     REAL NOT NULL DEFAULT 0,
    apr         REAL NOT NULL DEFAULT 0,
    min_payment REAL NOT NULL DEFAULT 0,
    term_months INTEGER,
    due_day     INTEGER NOT NULL DEFAULT 1,
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bills(
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Housing',
    amount   REAL NOT NULL DEFAULT 0,
    due_day  INTEGER NOT NULL DEFAULT 1,
    reserved REAL NOT NULL DEFAULT 0,
    notes    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS paychecks(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL DEFAULT 'Paycheck',
    amount    REAL NOT NULL,
    date      TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS transactions(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    category    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rules(
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword  TEXT NOT NULL,
    category TEXT NOT NULL
);
"""


def data_dir():
    path = os.environ.get("PAYDAYPILOT_DATA")
    if not path:
        path = os.path.join(os.path.expanduser("~"), ".paydaypilot")
    os.makedirs(path, exist_ok=True)
    return path


def connect():
    conn = sqlite3.connect(os.path.join(data_dir(), "data.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- settings

def get_settings(conn):
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


def set_settings(conn, values):
    for key, value in values.items():
        if key in DEFAULT_SETTINGS:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
    conn.commit()


# ---------------------------------------------------------------- debts

def list_debts(conn):
    return rows_to_dicts(conn.execute("SELECT * FROM debts ORDER BY apr DESC, balance DESC"))


def upsert_debt(conn, d):
    fields = {
        "name": (d.get("name") or "Debt").strip(),
        "kind": d.get("kind") or "other",
        "balance": float(d.get("balance") or 0),
        "apr": float(d.get("apr") or 0),
        "min_payment": float(d.get("min_payment") or 0),
        "term_months": int(d["term_months"]) if d.get("term_months") else None,
        "due_day": max(1, min(28, int(d.get("due_day") or 1))),
        "notes": d.get("notes") or "",
    }
    if d.get("id"):
        conn.execute(
            "UPDATE debts SET name=?, kind=?, balance=?, apr=?, min_payment=?, "
            "term_months=?, due_day=?, notes=? WHERE id=?",
            (*fields.values(), d["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO debts(name, kind, balance, apr, min_payment, term_months, due_day, notes) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(fields.values()),
        )
    conn.commit()


def delete_debt(conn, debt_id):
    conn.execute("DELETE FROM debts WHERE id=?", (debt_id,))
    conn.commit()


# ---------------------------------------------------------------- bills

def list_bills(conn):
    return rows_to_dicts(conn.execute("SELECT * FROM bills ORDER BY due_day, name"))


def upsert_bill(conn, b):
    fields = {
        "name": (b.get("name") or "Bill").strip(),
        "category": b.get("category") or "Other",
        "amount": float(b.get("amount") or 0),
        "due_day": max(1, min(28, int(b.get("due_day") or 1))),
        "notes": b.get("notes") or "",
    }
    if b.get("id"):
        conn.execute(
            "UPDATE bills SET name=?, category=?, amount=?, due_day=?, notes=? WHERE id=?",
            (*fields.values(), b["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO bills(name, category, amount, due_day, notes) VALUES(?, ?, ?, ?, ?)",
            tuple(fields.values()),
        )
    conn.commit()


def set_bill_reserve(conn, bill_id, reserved):
    conn.execute("UPDATE bills SET reserved=? WHERE id=?", (round(reserved, 2), bill_id))


def delete_bill(conn, bill_id):
    conn.execute("DELETE FROM bills WHERE id=?", (bill_id,))
    conn.commit()


# ---------------------------------------------------------------- paychecks

def list_paychecks(conn, limit=50):
    rows = rows_to_dicts(
        conn.execute("SELECT * FROM paychecks ORDER BY date DESC, id DESC LIMIT ?", (limit,))
    )
    for r in rows:
        r["plan"] = json.loads(r.pop("plan_json")) if r.get("plan_json") else None
    return rows


def add_paycheck(conn, source, amount, date, plan):
    cur = conn.execute(
        "INSERT INTO paychecks(source, amount, date, plan_json) VALUES(?, ?, ?, ?)",
        (source, amount, date, json.dumps(plan)),
    )
    conn.commit()
    return cur.lastrowid


def delete_paycheck(conn, paycheck_id):
    conn.execute("DELETE FROM paychecks WHERE id=?", (paycheck_id,))
    conn.commit()


# ---------------------------------------------------------------- transactions

def list_transactions(conn, limit=1000):
    return rows_to_dicts(
        conn.execute("SELECT * FROM transactions ORDER BY date DESC, id DESC LIMIT ?", (limit,))
    )


def add_transactions(conn, txns):
    """Insert transactions, skipping exact (date, description, amount) duplicates."""
    added = 0
    for t in txns:
        exists = conn.execute(
            "SELECT 1 FROM transactions WHERE date=? AND description=? AND amount=?",
            (t["date"], t["description"], t["amount"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO transactions(date, description, amount, category) VALUES(?, ?, ?, ?)",
            (t["date"], t["description"], t["amount"], t.get("category", "")),
        )
        added += 1
    conn.commit()
    return added


def update_transaction_category(conn, txn_id, category):
    conn.execute("UPDATE transactions SET category=? WHERE id=?", (category, txn_id))
    conn.commit()


def delete_transactions(conn):
    conn.execute("DELETE FROM transactions")
    conn.commit()


# ---------------------------------------------------------------- rules

def list_rules(conn):
    return rows_to_dicts(conn.execute("SELECT * FROM rules ORDER BY keyword"))


def add_rule(conn, keyword, category):
    conn.execute("INSERT INTO rules(keyword, category) VALUES(?, ?)", (keyword.lower(), category))
    conn.commit()


def delete_rule(conn, rule_id):
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    conn.commit()
