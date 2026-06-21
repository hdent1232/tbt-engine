# TBT Engine

A desktop trading app that automatically runs two ICT futures strategies —
**KTBT** (Kaiyan's Top/Bottom Tick) and **RTBT** (RichK0J's "Rich Off Ticks")
— on NQ, MNQ, ES, MES, GC and MGC. It pairs a paper-trading simulator and deep
calendar backtesting with a full analytics suite (Monte Carlo, walk-forward,
MAE/MFE), prop-firm rule presets, and optional live trading through Interactive
Brokers, a desktop bridge (NinjaTrader/Quantower), Rithmic, or Tradovate.

## Download & run

1. Go to **[Releases](../../releases/latest)** and download **`TBTEngine.exe`**.
2. Double-click it. A small window opens and your browser shows the dashboard.
3. To quit: click **Quit** in the dashboard, or close that window.

Windows SmartScreen may warn that it's from an unknown publisher (the app
isn't code-signed) — click **More info → Run anyway**.

## Features

**Strategies & engine**
- Two ICT strategies (KTBT, RTBT) across six instruments — NQ, MNQ, ES, MES, GC, MGC.
- Strategy Lab: tune parameters and save named profiles, with built-in eval-safe / lower-risk / high-win-rate presets.
- Export any strategy as a TradingView Pine Script.

**Simulator & live trading**
- Paper-trading simulator on live data, with a "why no trade?" diagnostic and a feed-stall watchdog.
- Optional live/demo trading via Interactive Brokers, the NinjaTrader or Quantower desktop bridges, Rithmic, or Tradovate.
- Native bracket orders (entry + stop + target), auto-reconnect, crash/restart recovery, and naked-position alerts.

**Backtesting & analysis**
- Calendar backtesting against years of local data (the free feed tops up recent dates automatically).
- Performance analytics — profit factor, expectancy, R-multiple distribution, monthly breakdown, daily P&L calendar, MAE/MFE.
- Monte Carlo robustness + risk-of-ruin, walk-forward optimization, and parameter-sensitivity sweeps.
- Day chart with trade overlays and bar-by-bar replay; side-by-side profile comparison; multi-symbol batch backtests.
- Filter, sort, search and export trades (CSV / JSON).

**Risk & prop-firm tools**
- Prop-firm presets (Lucid, Apex) with trailing-drawdown and consistency rules.
- Prop Eval Tracker — live progress toward the profit target, drawdown room, and consistency limit.
- Account-blow kill-switch, break-even-after-TP1, and round-trip commission modeling.

**Automation & quality of life**
- Schedule the bot to start/stop — and even launch itself — on a daily, timezone-aware schedule.
- Phone push notifications (with quiet hours), a first-run setup wizard, and a per-trade journal.
- Black-box recorder + diagnostics (searchable and downloadable), and settings/profiles backup & restore.
- Keyboard shortcuts, light/dark theme, and one-click self-update from GitHub.

## Updating

You only download the exe once. After that, open the dashboard, scroll to
**App Updates**, and click **Check for updates → Install & restart**. The app
replaces itself with the newest release here and restarts — no re-downloading.

## Notes

- This repository only hosts the compiled app. Nothing about your accounts,
  credentials, or settings is stored here — those live only on your own PC.
- Trading futures carries substantial risk of loss. The app ships in safe
  simulation mode; live trading is gated behind explicit confirmations.
  Nothing here is financial advice.