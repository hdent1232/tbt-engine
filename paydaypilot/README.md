# 💸 PayDay Pilot

A desktop app that tells you **exactly what to do with every paycheck**.

Enter your debts (from your credit report), your monthly bills, and how often
you're paid. Then every time you get paid, type in the amount — PayDay Pilot
instantly produces a plan: *pay this for rent, that for the car, set this much
aside for the insurance due on the 25th, keep this for groceries, and send
everything left to the credit card that's costing you the most*. Import your
bank statements and it also shows where your money actually goes and what to
cut to get debt-free faster.

Everything runs 100% locally — your financial data never leaves your computer.

## Features

**Paycheck autopilot**
- Enter a paycheck and get a dollar-by-dollar plan, in priority order:
  1. Bills and debt minimums due before your next paycheck — paid in full.
  2. Bills due later — a share is set aside each paycheck (sinking funds), so
     the money is always there when the due date arrives.
  3. A prorated essentials budget (groceries, gas, day-to-day).
  4. Emergency fund contributions until your target is reached.
  5. A small guilt-free "fun money" slice so the plan is livable.
  6. **Every remaining dollar** goes to extra principal on the smartest debt.
- Warns you when a paycheck can't cover everything, and shows exactly how many
  months sooner you'll be debt-free thanks to this paycheck's extra payment.

**Debts & payoff planning**
- Track every debt: balance, APR, minimum payment, term, due day.
- Import debts from a CSV or paste text straight from your credit report
  (Credit Karma / Experian / etc.) — found accounts are shown for review
  before anything is saved.
- Avalanche (highest APR first — saves the most interest) vs snowball
  (smallest balance first — fastest wins) vs minimums-only comparison, with
  debt-free dates, total interest, payoff order and a balance chart.

**Bills & expenses**
- All monthly bills — rent, wifi, electric, water, gas, car payment,
  insurance, phone — with due days and automatic per-paycheck set-asides.

**Bank statement analysis**
- Import CSVs exported from your bank (signed `Amount` column or separate
  `Debit`/`Credit` columns both work; duplicates are skipped automatically).
- Auto-categorization with 100+ built-in merchant rules plus your own custom
  keyword rules; categories are editable per transaction.
- Monthly breakdown of where money goes, recurring-subscription detection,
  and concrete "what to cut" suggestions — including how many months sooner
  you'd be debt-free if the savings went to debt.

## Run it

**From source** (no dependencies — just Python 3.9+):

```
cd paydaypilot
python run.py
```

A local server starts and the dashboard opens in your browser. Quit from the
dashboard's Quit button or Ctrl+C.

**Windows exe**: download `PayDayPilot.exe` from
[Releases](../../releases) (built automatically from this source by the
`build-paydaypilot` GitHub Actions workflow). Double-click it — a small
console window opens and the dashboard appears in your browser. SmartScreen
may warn because the exe isn't code-signed; click **More info → Run anyway**.

**Android app**: download `PayDayPilot.apk` from the
[android-latest release](../../releases/tag/android-latest) on your phone and
open it (allow "install unknown apps" when prompted — it's a sideload build,
not from the Play Store). The entire app runs on-device: the finance engine
is a JavaScript port (`app/static/local-api.js`) verified against the Python
engine, data lives in the app's local storage, and the APK requests **zero
permissions** — nothing ever leaves your phone. The APK is rebuilt by the
`build-apk` workflow on every change to the app or the `android/` project.

> Note: the desktop and Android apps keep separate data (each stores it
> locally on its own device). Use Settings → Export backup to move data.

## Data & privacy

All data lives in a single SQLite file at `~/.paydaypilot/data.db`
(`C:\Users\you\.paydaypilot\data.db` on Windows). The server binds to
`127.0.0.1` only; nothing is ever sent anywhere. Use **Settings → Export
backup** for a full JSON backup.

## CSV formats

*Bank statement*: a header row containing a date column (`Date`,
`Posted Date`, …), a description column (`Description`, `Memo`, `Payee`, …)
and either a signed `Amount` column or separate `Debit`/`Credit` columns —
i.e. what virtually every bank's "export CSV" produces.

*Debt import*: `name, balance, apr, min payment, term, due day` (a template
is downloadable on the Debts tab).

## How the allocator decides

Bills and minimum payments are sorted by due date and funded first. A bill
due after your next paycheck doesn't need all its money now, so each paycheck
between now and the due date contributes an equal share ("set aside"). The
essentials budget is prorated to the days this paycheck must cover. Leftover
money goes to the emergency fund (a configurable percentage until the target
is met), a small fun-money slice, and then **all of it** to one target debt —
highest APR (avalanche) or smallest balance (snowball). Payments recorded in
a plan are applied to your debt balances so projections stay current.

## Tests

```
cd paydaypilot
python -m tests.test_smoke
```

Boots the real server against a temp database and exercises the entire
workflow end-to-end.

---

*PayDay Pilot is a planning tool, not financial advice.*
