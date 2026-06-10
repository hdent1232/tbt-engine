# TBT Engine

A desktop trading app that automatically runs two ICT futures strategies —
**KTBT** (Kaiyan's Top/Bottom Tick) and **RTBT** (RichK0J's "Rich Off Ticks")
— on NQ, MNQ, ES, MES, GC and MGC. It includes a built-in simulator, calendar
backtesting, prop-firm rule presets, and optional live trading through a
connected brokerage/prop account.

## Download & run

1. Go to **[Releases](../../releases/latest)** and download **`TBTEngine.exe`**.
2. Double-click it. A small window opens and your browser shows the dashboard.
3. To quit: click **Quit** in the dashboard, or close that window.

Windows SmartScreen may warn that it's from an unknown publisher (the app
isn't code-signed) — click **More info → Run anyway**.

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
