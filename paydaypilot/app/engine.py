"""The money engine: paycheck allocation and debt payoff math.

Allocation strategy (per paycheck, in priority order):
  1. Bills and debt minimum payments, funded as sinking funds — each paycheck
     contributes its share so the full amount is set aside by the due date.
     Bills due before the next paycheck are funded in full.
  2. Day-to-day essentials budget (groceries, gas, ...), prorated to the
     number of days this paycheck has to cover.
  3. Emergency fund contribution until the target is reached.
  4. A small guilt-free "fun money" slice, so the plan is livable.
  5. Everything left goes to extra principal on one target debt, chosen by
     the avalanche (highest APR) or snowball (smallest balance) strategy.

Payoff projection: month-by-month amortization of all debts under a given
strategy and monthly extra-payment amount.
"""

from datetime import date, timedelta

PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 30}
AVG_DAYS_PER_MONTH = 30.44
CHECKS_PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "semimonthly": 2.0, "monthly": 1.0}


def parse_date(s):
    return date.fromisoformat(str(s)[:10])


def next_due_date(due_day, from_date):
    """First occurrence of a monthly due day on or after from_date (due_day capped at 28)."""
    due_day = max(1, min(28, int(due_day)))
    if from_date.day <= due_day:
        return from_date.replace(day=due_day)
    year, month = (from_date.year + 1, 1) if from_date.month == 12 else (from_date.year, from_date.month + 1)
    return date(year, month, due_day)


def checks_until(due, pay_date, freq):
    """How many paychecks (counting this one) arrive on or before the due date."""
    days = (due - pay_date).days
    if days <= 0:
        return 1
    return max(1, days // PERIOD_DAYS[freq] + 1)


def pick_target_debt(debts, strategy):
    active = [d for d in debts if d["balance"] > 0.01]
    if not active:
        return None
    if strategy == "snowball":
        return min(active, key=lambda d: d["balance"])
    return max(active, key=lambda d: (d["apr"], d["balance"]))


def build_plan(amount, pay_date, bills, debts, settings):
    """Return an allocation plan: exactly where every dollar of this paycheck goes.

    Also returns per-bill reserve updates so bill sinking funds carry over
    between paychecks. Does not mutate anything.
    """
    freq = settings["pay_frequency"]
    strategy = settings["strategy"]
    period = PERIOD_DAYS[freq]
    window_end = pay_date + timedelta(days=period)

    remaining = round(float(amount), 2)
    items = []
    warnings = []
    reserve_updates = {}  # bill_id -> new reserved amount

    # ---- 1. obligations: bills + debt minimums, earliest due first -------
    obligations = []
    for b in bills:
        due = next_due_date(b["due_day"], pay_date)
        needed = max(0.0, b["amount"] - b["reserved"])
        n = checks_until(due, pay_date, freq)
        share = needed if due < window_end else round(needed / n, 2)
        obligations.append({
            "kind": "bill", "ref": b, "due": due, "share": share,
            "due_now": due < window_end,
        })
    for d in debts:
        if d["balance"] <= 0.01 or d["min_payment"] <= 0:
            continue
        due = next_due_date(d["due_day"], pay_date)
        payment = min(d["min_payment"], d["balance"])
        n = checks_until(due, pay_date, freq)
        share = payment if due < window_end else round(payment / n, 2)
        obligations.append({
            "kind": "debt_min", "ref": d, "due": due, "share": share,
            "due_now": due < window_end,
        })
    obligations.sort(key=lambda o: (o["due"], -o["share"]))

    for ob in obligations:
        if ob["share"] < 0.01:
            continue
        alloc = round(min(ob["share"], remaining), 2)
        ref = ob["ref"]
        if alloc < ob["share"] - 0.01:
            warnings.append(
                f"Not enough left to fully cover {ref['name']} "
                f"(needed ${ob['share']:.2f}, allocated ${alloc:.2f})."
            )
        remaining = round(remaining - alloc, 2)
        if ob["kind"] == "bill":
            if ob["due_now"]:
                pay_amount = round(min(ref["amount"], ref["reserved"] + alloc), 2)
                action = f"Pay {ref['name']}"
                note = f"due {ob['due'].isoformat()}"
                if ref["reserved"] > 0.01:
                    note += f" (${ref['reserved']:.2f} already set aside)"
                reserve_updates[ref["id"]] = 0.0
                items.append({
                    "action": action, "amount": pay_amount, "from_paycheck": alloc,
                    "kind": "bill", "category": ref["category"],
                    "due": ob["due"].isoformat(), "note": note,
                })
            else:
                reserve_updates[ref["id"]] = round(ref["reserved"] + alloc, 2)
                if alloc >= 0.01:
                    items.append({
                        "action": f"Set aside for {ref['name']}", "amount": alloc,
                        "from_paycheck": alloc, "kind": "reserve",
                        "category": ref["category"], "due": ob["due"].isoformat(),
                        "note": f"${reserve_updates[ref['id']]:.2f} of ${ref['amount']:.2f} "
                                f"saved for the {ob['due'].isoformat()} bill",
                    })
        else:  # debt minimum
            if alloc >= 0.01:
                verb = "Pay" if ob["due_now"] else "Set aside for"
                items.append({
                    "action": f"{verb} {ref['name']} (minimum)", "amount": alloc,
                    "from_paycheck": alloc, "kind": "debt_min", "category": "Debt",
                    "due": ob["due"].isoformat(),
                    "note": f"minimum payment, due {ob['due'].isoformat()}",
                    "debt_id": ref["id"],
                })
        if remaining <= 0:
            remaining = 0.0

    # ---- 2. essentials budget --------------------------------------------
    variable_budget = float(settings["variable_budget"])
    essentials = round(variable_budget * period / AVG_DAYS_PER_MONTH, 2)
    if essentials > 0.01:
        alloc = round(min(essentials, remaining), 2)
        if alloc < essentials - 0.01:
            warnings.append(
                f"Essentials budget is short: ${alloc:.2f} of ${essentials:.2f} "
                f"for groceries/gas until the next paycheck."
            )
        if alloc >= 0.01:
            items.append({
                "action": "Keep for essentials", "amount": alloc, "from_paycheck": alloc,
                "kind": "essentials", "category": "Essentials", "due": "",
                "note": f"groceries, gas & day-to-day spending for the next {period} days",
            })
        remaining = round(remaining - alloc, 2)

    # ---- 3. emergency fund -----------------------------------------------
    target = float(settings["emergency_target"])
    balance = float(settings["emergency_balance"])
    emergency_alloc = 0.0
    if remaining > 0.01 and balance < target:
        pct = float(settings["emergency_pct"]) / 100.0
        emergency_alloc = round(min(target - balance, remaining * pct), 2)
        if emergency_alloc >= 0.01:
            items.append({
                "action": "Move to emergency fund", "amount": emergency_alloc,
                "from_paycheck": emergency_alloc, "kind": "emergency",
                "category": "Savings", "due": "",
                "note": f"fund at ${balance + emergency_alloc:.2f} of ${target:.2f} target",
            })
            remaining = round(remaining - emergency_alloc, 2)

    # ---- 4. fun money ------------------------------------------------------
    fun = 0.0
    if remaining > 0.01:
        fun = round(remaining * float(settings["fun_pct"]) / 100.0, 2)
        if fun >= 0.01:
            items.append({
                "action": "Fun money", "amount": fun, "from_paycheck": fun,
                "kind": "fun", "category": "Fun", "due": "",
                "note": "guilt-free spending so the plan is sustainable",
            })
            remaining = round(remaining - fun, 2)

    # ---- 5. extra debt payment ---------------------------------------------
    extra = 0.0
    target_debt = pick_target_debt(debts, strategy)
    if remaining > 0.01:
        if target_debt:
            extra = remaining
            items.append({
                "action": f"EXTRA payment to {target_debt['name']}", "amount": extra,
                "from_paycheck": extra, "kind": "debt_extra", "category": "Debt",
                "due": "", "debt_id": target_debt["id"],
                "note": f"{strategy} target — {target_debt['apr']:.2f}% APR, "
                        f"${target_debt['balance']:.2f} balance",
            })
        else:
            items.append({
                "action": "Move to savings", "amount": remaining, "from_paycheck": remaining,
                "kind": "savings", "category": "Savings", "due": "",
                "note": "no active debts — build savings or invest",
            })
        remaining = 0.0

    total_allocated = round(sum(i["from_paycheck"] for i in items), 2)
    return {
        "pay_date": pay_date.isoformat(),
        "amount": round(float(amount), 2),
        "window_days": period,
        "next_paycheck_expected": window_end.isoformat(),
        "items": items,
        "warnings": warnings,
        "totals": {
            "bills": round(sum(i["from_paycheck"] for i in items if i["kind"] in ("bill", "reserve")), 2),
            "debt_min": round(sum(i["from_paycheck"] for i in items if i["kind"] == "debt_min"), 2),
            "debt_extra": extra,
            "essentials": round(sum(i["from_paycheck"] for i in items if i["kind"] == "essentials"), 2),
            "emergency": emergency_alloc,
            "fun": fun,
            "savings": round(sum(i["from_paycheck"] for i in items if i["kind"] == "savings"), 2),
            "allocated": total_allocated,
            "unallocated": round(float(amount) - total_allocated, 2),
        },
        "reserve_updates": reserve_updates,
        "target_debt": target_debt["name"] if target_debt else None,
        "strategy": strategy,
    }


# -------------------------------------------------------------- projections

def simulate_payoff(debts, strategy, monthly_extra, start=None):
    """Amortize all debts month by month. Returns payoff timeline and interest."""
    start = start or date.today()
    balances = {d["id"]: float(d["balance"]) for d in debts if d["balance"] > 0.01}
    info = {d["id"]: d for d in debts}
    if not balances:
        return {"months": 0, "total_interest": 0.0, "debt_free_date": start.isoformat(),
                "payoff_order": [], "timeline": []}

    total_interest = 0.0
    payoff_order = []
    timeline = []
    month = 0
    freed_minimums = 0.0  # rolled-over minimums from paid-off debts

    while balances and month < 720:
        month += 1
        # interest accrual
        for did in list(balances):
            interest = balances[did] * info[did]["apr"] / 100.0 / 12.0
            balances[did] += interest
            total_interest += interest
        # minimum payments
        for did in list(balances):
            pay = min(info[did]["min_payment"], balances[did])
            balances[did] -= pay
        # extra + freed-up minimums to the strategy target
        budget = monthly_extra + freed_minimums
        while budget > 0.005 and balances:
            active = [info[did] | {"balance": balances[did]} for did in balances]
            target = pick_target_debt(active, strategy)
            if not target:
                break
            pay = min(budget, balances[target["id"]])
            balances[target["id"]] -= pay
            budget -= pay
        # retire finished debts, roll their minimums forward
        for did in list(balances):
            if balances[did] <= 0.005:
                freed_minimums += info[did]["min_payment"]
                payoff_order.append({
                    "name": info[did]["name"], "month": month,
                    "date": _add_months(start, month).isoformat(),
                })
                del balances[did]
        timeline.append({
            "month": month,
            "date": _add_months(start, month).isoformat(),
            "total_balance": round(sum(balances.values()), 2),
        })

    return {
        "months": month if not balances else None,
        "total_interest": round(total_interest, 2),
        "debt_free_date": _add_months(start, month).isoformat() if not balances else None,
        "payoff_order": payoff_order,
        "timeline": timeline,
        "stuck": bool(balances),  # payments don't cover interest
    }


def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, 28)
    return date(year, month, day)


def estimate_monthly_extra(bills, debts, settings, recent_paychecks):
    """Best estimate of how much can go to extra debt payments each month."""
    income = float(settings["monthly_net_income"])
    if income <= 0 and recent_paychecks:
        checks = recent_paychecks[:8]
        avg = sum(p["amount"] for p in checks) / len(checks)
        income = avg * CHECKS_PER_MONTH[settings["pay_frequency"]]
    bills_total = sum(b["amount"] for b in bills)
    mins_total = sum(min(d["min_payment"], d["balance"]) for d in debts if d["balance"] > 0.01)
    variable = float(settings["variable_budget"])
    leftover = income - bills_total - mins_total - variable
    fun = max(0.0, leftover) * float(settings["fun_pct"]) / 100.0
    return {
        "monthly_income": round(income, 2),
        "monthly_bills": round(bills_total, 2),
        "monthly_debt_minimums": round(mins_total, 2),
        "monthly_essentials": round(variable, 2),
        "monthly_fun": round(fun, 2),
        "monthly_extra": round(max(0.0, leftover - fun), 2),
    }


def compare_strategies(debts, monthly_extra):
    """Min-only vs snowball vs avalanche, so the app can show why its plan wins."""
    return {
        "minimum_only": simulate_payoff(debts, "avalanche", 0),
        "snowball": simulate_payoff(debts, "snowball", monthly_extra),
        "avalanche": simulate_payoff(debts, "avalanche", monthly_extra),
    }
