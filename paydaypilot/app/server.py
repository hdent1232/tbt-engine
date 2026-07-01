"""Local HTTP server: serves the dashboard UI and a JSON API.

Everything runs on 127.0.0.1 only — no data ever leaves the machine.
"""

import json
import mimetypes
import os
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import db, engine, importers

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if getattr(sys, "frozen", False):  # PyInstaller bundle
    STATIC_DIR = os.path.join(sys._MEIPASS, "app", "static")

shutdown_event = threading.Event()


def _paycheck_snapshot(conn):
    return {
        "bills": db.list_bills(conn),
        "debts": db.list_debts(conn),
        "settings": db.get_settings(conn),
    }


def build_and_apply_plan(conn, amount, pay_date, source, apply_changes=True):
    snap = _paycheck_snapshot(conn)
    plan = engine.build_plan(amount, pay_date, snap["bills"], snap["debts"], snap["settings"])

    # Payoff impact of this paycheck's extra payment, shown alongside the plan.
    extra_monthly = plan["totals"]["debt_extra"] * engine.CHECKS_PER_MONTH[snap["settings"]["pay_frequency"]]
    if snap["debts"] and extra_monthly > 0:
        base = engine.simulate_payoff(snap["debts"], snap["settings"]["strategy"], 0)
        boosted = engine.simulate_payoff(snap["debts"], snap["settings"]["strategy"], extra_monthly)
        if base["months"] and boosted["months"]:
            plan["impact"] = {
                "months_saved": base["months"] - boosted["months"],
                "interest_saved": round(base["total_interest"] - boosted["total_interest"], 2),
                "debt_free_date": boosted["debt_free_date"],
            }

    if apply_changes:
        for bill_id, reserved in plan["reserve_updates"].items():
            db.set_bill_reserve(conn, bill_id, reserved)
        # Apply payments to debt balances so projections stay current.
        for item in plan["items"]:
            if item["kind"] in ("debt_min", "debt_extra") and item.get("debt_id"):
                conn.execute(
                    "UPDATE debts SET balance = MAX(0, balance - ?) WHERE id=?",
                    (item["amount"], item["debt_id"]),
                )
        # Emergency fund balance moves with the plan.
        emergency = plan["totals"]["emergency"]
        if emergency > 0:
            settings = db.get_settings(conn)
            db.set_settings(conn, {
                "emergency_balance": round(float(settings["emergency_balance"]) + emergency, 2)
            })
        conn.commit()
        plan_id = db.add_paycheck(conn, source, plan["amount"], plan["pay_date"], plan)
        plan["paycheck_id"] = plan_id
    return plan


class Handler(BaseHTTPRequestHandler):
    server_version = "PayDayPilot/1.0"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw.decode("utf-8", "replace")}

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        fname = os.path.normpath(path.lstrip("/"))
        full = os.path.join(STATIC_DIR, fname)
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ routing
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        conn = db.connect()
        try:
            if route == "/api/state":
                settings = db.get_settings(conn)
                debts = db.list_debts(conn)
                bills = db.list_bills(conn)
                paychecks = db.list_paychecks(conn, limit=12)
                budget = engine.estimate_monthly_extra(bills, debts, settings, paychecks)
                self._json({
                    "settings": settings, "debts": debts, "bills": bills,
                    "paychecks": paychecks, "budget": budget,
                    "today": date.today().isoformat(),
                })
            elif route == "/api/projection":
                settings = db.get_settings(conn)
                debts = db.list_debts(conn)
                bills = db.list_bills(conn)
                paychecks = db.list_paychecks(conn)
                budget = engine.estimate_monthly_extra(bills, debts, settings, paychecks)
                extra = budget["monthly_extra"]
                if "extra" in query:
                    try:
                        extra = float(query["extra"][0])
                    except ValueError:
                        pass
                self._json({
                    "budget": budget,
                    "comparison": engine.compare_strategies(debts, extra),
                    "extra_used": extra,
                })
            elif route == "/api/transactions":
                self._json({"transactions": db.list_transactions(conn)})
            elif route == "/api/spending":
                months = int(query.get("months", ["6"])[0])
                txns = db.list_transactions(conn, limit=10000)
                summary = importers.spending_summary(txns, months)
                # How much sooner debt-free if suggested cuts go to debt?
                settings = db.get_settings(conn)
                debts = db.list_debts(conn)
                bills = db.list_bills(conn)
                paychecks = db.list_paychecks(conn)
                budget = engine.estimate_monthly_extra(bills, debts, settings, paychecks)
                cut = summary["potential_monthly_savings"]
                if cut > 0 and any(d["balance"] > 0.01 for d in debts):
                    base = engine.simulate_payoff(debts, settings["strategy"], budget["monthly_extra"])
                    boosted = engine.simulate_payoff(debts, settings["strategy"],
                                                     budget["monthly_extra"] + cut)
                    if base["months"] and boosted["months"]:
                        summary["cut_impact"] = {
                            "months_saved": base["months"] - boosted["months"],
                            "interest_saved": round(base["total_interest"] - boosted["total_interest"], 2),
                        }
                self._json(summary)
            elif route == "/api/rules":
                self._json({"rules": db.list_rules(conn)})
            elif route == "/api/export":
                self._json({
                    "settings": db.get_settings(conn),
                    "debts": db.list_debts(conn),
                    "bills": db.list_bills(conn),
                    "paychecks": db.list_paychecks(conn, limit=100000),
                    "transactions": db.list_transactions(conn, limit=100000),
                    "rules": db.list_rules(conn),
                })
            elif route.startswith("/api/"):
                self._json({"error": "not found"}, 404)
            else:
                self._static(route)
        finally:
            conn.close()

    def do_POST(self):
        route = urlparse(self.path).path
        body = self._read_body()
        conn = db.connect()
        try:
            if route == "/api/debts":
                upserted = body if isinstance(body, list) else [body]
                for d in upserted:
                    db.upsert_debt(conn, d)
                self._json({"ok": True, "debts": db.list_debts(conn)})
            elif route == "/api/debts/delete":
                db.delete_debt(conn, body["id"])
                self._json({"ok": True})
            elif route == "/api/debts/import":
                text = body.get("text", "")
                debts = importers.parse_debts_csv(text)
                source = "csv"
                if not debts:
                    debts = importers.parse_debts_text(text)
                    source = "text"
                self._json({"debts": debts, "source": source})
            elif route == "/api/bills":
                for b in (body if isinstance(body, list) else [body]):
                    db.upsert_bill(conn, b)
                self._json({"ok": True, "bills": db.list_bills(conn)})
            elif route == "/api/bills/delete":
                db.delete_bill(conn, body["id"])
                self._json({"ok": True})
            elif route == "/api/paycheck":
                amount = float(body["amount"])
                pay_date = engine.parse_date(body.get("date") or date.today().isoformat())
                source = body.get("source") or "Paycheck"
                preview = bool(body.get("preview"))
                plan = build_and_apply_plan(conn, amount, pay_date, source,
                                            apply_changes=not preview)
                self._json({"plan": plan})
            elif route == "/api/paycheck/delete":
                db.delete_paycheck(conn, body["id"])
                self._json({"ok": True})
            elif route == "/api/transactions/import":
                rules = importers.merge_rules(db.list_rules(conn))
                txns, note = importers.parse_bank_csv(body.get("csv", ""), rules)
                added = db.add_transactions(conn, txns)
                self._json({"parsed": len(txns), "added": added,
                            "duplicates": len(txns) - added, "note": note})
            elif route == "/api/transactions/clear":
                db.delete_transactions(conn)
                self._json({"ok": True})
            elif route == "/api/transactions/category":
                db.update_transaction_category(conn, body["id"], body["category"])
                self._json({"ok": True})
            elif route == "/api/rules":
                db.add_rule(conn, body["keyword"], body["category"])
                self._json({"ok": True, "rules": db.list_rules(conn)})
            elif route == "/api/rules/delete":
                db.delete_rule(conn, body["id"])
                self._json({"ok": True})
            elif route == "/api/settings":
                db.set_settings(conn, body)
                self._json({"ok": True, "settings": db.get_settings(conn)})
            elif route == "/api/quit":
                self._json({"ok": True, "bye": True})
                shutdown_event.set()
            else:
                self._json({"error": "not found"}, 404)
        except (KeyError, ValueError, TypeError) as exc:
            self._json({"error": f"Bad request: {exc}"}, 400)
        finally:
            conn.close()


def serve(port=0):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return httpd
