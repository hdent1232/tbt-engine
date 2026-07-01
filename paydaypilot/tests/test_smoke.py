"""End-to-end smoke test: boots the real server against a temp database and
walks through the whole workflow — debts, bills, settings, a paycheck plan,
bank statement import, spending analysis and payoff projections.

Run from the paydaypilot directory:  python -m tests.test_smoke
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request

TMP = tempfile.mkdtemp(prefix="paydaypilot-test-")
os.environ["PAYDAYPILOT_DATA"] = TMP

from app.server import serve  # noqa: E402  (env var must be set first)

BANK_CSV = """Date,Description,Amount
2026-05-01,PAYROLL DIRECT DEP,1850.00
2026-05-02,RENT PAYMENT APARTMENTS LLC,-1200.00
2026-05-03,KROGER #123,-142.55
2026-05-04,NETFLIX.COM,-15.49
2026-05-05,STARBUCKS #4411,-7.85
2026-05-08,SHELL OIL 5551,-48.20
2026-05-12,DOORDASH*BURRITO,-32.40
2026-05-15,PAYROLL DIRECT DEP,1850.00
2026-05-18,AMAZON MKTPLACE,-89.99,
2026-06-02,RENT PAYMENT APARTMENTS LLC,-1200.00
2026-06-03,KROGER #123,-131.02
2026-06-04,NETFLIX.COM,-15.49
2026-06-06,STARBUCKS #4411,-9.10
2026-06-10,DOORDASH*PIZZA,-41.15
"""

DEBTS_CSV = """name,balance,apr,min payment,term,due day
Capital One Visa,2450.00,26.99,75,,15
Toyota auto loan,14800,6.4,385,48,5
"""

CREDIT_REPORT_TEXT = """
CHASE FREEDOM VISA
Balance: $3,204.55
APR: 24.99%
Minimum payment: $96

Discover it Card   Balance $890.10   18.5% APR   min payment $35
"""


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, path, body=None, expect=200):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, expect)
            return json.loads(res.read().decode())

    def test_full_workflow(self):
        # -- static UI is served
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn("PayDay Pilot", res.read().decode())

        # -- settings
        self.call("/api/settings", {
            "pay_frequency": "biweekly", "strategy": "avalanche",
            "variable_budget": 500, "emergency_target": 1000,
            "emergency_balance": 200, "emergency_pct": 20, "fun_pct": 5,
        })

        # -- bills
        for bill in [
            {"name": "Rent", "category": "Housing", "amount": 1200, "due_day": 1},
            {"name": "Electric", "category": "Utilities", "amount": 90, "due_day": 12},
            {"name": "Wifi", "category": "Utilities", "amount": 65, "due_day": 20},
            {"name": "Car insurance", "category": "Insurance", "amount": 140, "due_day": 25},
        ]:
            self.call("/api/bills", bill)

        # -- debts via CSV import then confirm
        parsed = self.call("/api/debts/import", {"text": DEBTS_CSV})
        self.assertEqual(parsed["source"], "csv")
        self.assertEqual(len(parsed["debts"]), 2)
        self.call("/api/debts", parsed["debts"])

        # -- debts via pasted credit-report text
        scanned = self.call("/api/debts/import", {"text": CREDIT_REPORT_TEXT})
        self.assertEqual(scanned["source"], "text")
        self.assertGreaterEqual(len(scanned["debts"]), 2)
        balances = sorted(d["balance"] for d in scanned["debts"])
        self.assertIn(3204.55, balances)
        self.assertIn(890.10, balances)

        state = self.call("/api/state")
        self.assertEqual(len(state["bills"]), 4)
        self.assertEqual(len(state["debts"]), 2)

        # -- paycheck preview does not persist
        preview = self.call("/api/paycheck", {
            "amount": 1850, "date": "2026-07-01", "source": "Job", "preview": True,
        })["plan"]
        self.assertEqual(preview["amount"], 1850)
        self.assertAlmostEqual(preview["totals"]["allocated"], 1850, places=2)
        self.assertEqual(len(self.call("/api/state")["paychecks"]), 0)

        # -- real paycheck: allocations add up and state mutates
        # (2600 is enough to cover rent + obligations and reach the
        # emergency/extra-debt tiers of the allocator)
        plan = self.call("/api/paycheck", {
            "amount": 2600, "date": "2026-07-01", "source": "Job",
        })["plan"]
        self.assertAlmostEqual(plan["totals"]["allocated"], 2600, places=2)
        self.assertGreater(plan["totals"]["emergency"], 0)
        self.assertGreater(plan["totals"]["debt_extra"], 0)
        self.assertEqual(plan["target_debt"], "Capital One Visa")  # highest APR
        kinds = {i["kind"] for i in plan["items"]}
        self.assertIn("essentials", kinds)
        self.assertTrue({"bill", "reserve"} & kinds)
        state = self.call("/api/state")
        self.assertEqual(len(state["paychecks"]), 1)
        # emergency contribution applied
        self.assertGreater(float(state["settings"]["emergency_balance"]), 200)
        # debt balances reduced by payments in the plan
        paid = sum(i["amount"] for i in plan["items"] if i["kind"] in ("debt_min", "debt_extra"))
        total_debt = sum(d["balance"] for d in state["debts"])
        self.assertAlmostEqual(total_debt, 2450 + 14800 - paid, places=1)

        # -- projection compares strategies
        proj = self.call("/api/projection?extra=400")
        for key in ("minimum_only", "snowball", "avalanche"):
            self.assertIn(key, proj["comparison"])
        av, mo = proj["comparison"]["avalanche"], proj["comparison"]["minimum_only"]
        self.assertLess(av["months"], mo["months"])
        self.assertLess(av["total_interest"], mo["total_interest"])

        # -- bank statement import + dedupe
        imp = self.call("/api/transactions/import", {"csv": BANK_CSV})
        self.assertGreaterEqual(imp["added"], 13)
        again = self.call("/api/transactions/import", {"csv": BANK_CSV})
        self.assertEqual(again["added"], 0)

        # -- spending analysis
        spend = self.call("/api/spending?months=6")
        cats = {c["category"] for c in spend["categories"]}
        self.assertIn("Housing", cats)
        self.assertIn("Groceries", cats)
        self.assertIn("Dining", cats)
        self.assertTrue(any(r["monthly_avg"] > 0 for r in spend["recurring"]))
        self.assertTrue(spend["suggestions"])  # dining etc. should trigger cut suggestions
        self.assertIn("2026-05", spend["income_by_month"])

        # -- export contains everything
        export = self.call("/api/export")
        for key in ("settings", "debts", "bills", "paychecks", "transactions", "rules"):
            self.assertIn(key, export)


class EngineTest(unittest.TestCase):
    def test_payoff_math(self):
        from app.engine import simulate_payoff
        debts = [
            {"id": 1, "name": "Card A", "balance": 1000, "apr": 24.0, "min_payment": 30, "due_day": 1},
            {"id": 2, "name": "Card B", "balance": 5000, "apr": 12.0, "min_payment": 100, "due_day": 1},
        ]
        base = simulate_payoff(debts, "avalanche", 0)
        boosted = simulate_payoff(debts, "avalanche", 300)
        self.assertLess(boosted["months"], base["months"])
        self.assertLess(boosted["total_interest"], base["total_interest"])
        self.assertEqual(boosted["payoff_order"][0]["name"], "Card A")  # highest APR first
        snow = simulate_payoff(debts, "snowball", 300)
        self.assertEqual(snow["payoff_order"][0]["name"], "Card A")  # also smallest here
        # avalanche never pays more interest than snowball
        self.assertLessEqual(boosted["total_interest"], snow["total_interest"] + 0.01)

    def test_stuck_detection(self):
        from app.engine import simulate_payoff
        debts = [{"id": 1, "name": "Bad", "balance": 10000, "apr": 30.0, "min_payment": 10, "due_day": 1}]
        result = simulate_payoff(debts, "avalanche", 0)
        self.assertTrue(result["stuck"])

    def test_next_due_date(self):
        from datetime import date
        from app.engine import next_due_date
        self.assertEqual(next_due_date(15, date(2026, 7, 1)), date(2026, 7, 15))
        self.assertEqual(next_due_date(15, date(2026, 7, 15)), date(2026, 7, 15))
        self.assertEqual(next_due_date(5, date(2026, 7, 20)), date(2026, 8, 5))
        self.assertEqual(next_due_date(10, date(2026, 12, 20)), date(2027, 1, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
