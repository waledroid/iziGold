# XAU Assistant

MT5 trading assistant for XAUUSD (chart timeframe, M5 default). **Your strategy
is the sole decision maker**; an AI forecaster (Chronos-Bolt) grades every
signal, everything is logged to SQLite, and Telegram is the remote control.
Manual mode proposes trades with approve/skip buttons; auto mode executes and
reports. Strict money management throughout (fixed-fractional risk, pyramiding
into winners only, breakeven ratchets, proportional profit lock, drawdown kill
switch).

Design docs: [docs/superpowers/specs/](docs/superpowers/specs/)

## Quick start

**One-shot (Windows):** run `scripts/xau-launch.bat` (copy it to your Desktop).
It bootstraps everything — checks WSL, clones the repo if missing, starts MT5,
then runs the idempotent installer below and waits for the EA heartbeat.

**Installer (WSL/Linux):**

```bash
bash scripts/setup.sh
```

Handles venv, dependencies, tests, service on `127.0.0.1:9000`, interactive
Telegram enrolment (BotFather token → auto chat-id → test message), MT5 file
copy + MetaEditor CLI compile, and prints the two remaining manual MT5 steps
(WebRequest allowlist for `http://127.0.0.1:9000`; attach the EA to a XAUUSD
M5 chart with Algo Trading on). Safe to re-run any time — completed steps skip.

Verify: `/status` in Telegram → `EA: 🟢 connected`.

## What you get

- **Strategies** — modular behind `CStrategy`
  (`mt5/Include/XauAssistant/Strategies/`). Active: `halftrend_ema_v1`
  (HalfTrend + EMA-55 dual confirmation, ATR-padded wick stop). Shadow:
  `boll_stochrsi_v1`. All strategies are evaluated and logged every bar;
  only `ActiveStrategy` trades. Per-strategy hit-rates in SQLite.
- **Telegram control** — quiet by default. Entry/exit proposals with
  🟢 Take / 🔴 Skip buttons (valid while the strategy holds its stance),
  execution photos, failure notices with the exact risk-gate reason.
  Commands: `/status` (incl. EA connection + market session), `/mode`
  (AUTO/MANUAL), `/strategy`, `/config`, `/stats`, `/history`. A pinned
  message lists all commands.
- **Dashboard** (`http://127.0.0.1:9000/ui`) — live XAUUSD candlestick chart
  with risk/reward trade boxes (red entry↔SL, green entry↔exit), strategy
  comparison, signal log with AI grades and resolved outcomes, trade history
  with chart thumbnails. First visit redirects to onboarding.
- **MT5 chart** — dark theme, the strategy's own HalfTrend/EMA lines painted
  live, trade boxes drawn per basket.
- **Rendered trade charts** (Telegram + dashboard) — candles with HalfTrend,
  EMA 9/21/55/200 overlays, entry price and SL labels.
- **Exits** — dual-confirmation trend reversal, 50%-of-peak profit lock,
  +2% basket target, ATR-padded stop. No martingale, ever.

## Key EA inputs

| Input | Default | Meaning |
|---|---|---|
| `ExecutionMode` | `EXEC_MANUAL` | runtime-switchable via Telegram `/mode` |
| `AllowLiveTrading` | `false` | AUTO on a live account refuses unless enabled |
| `RiskPerTradePct` / `MaxDrawdownPct` | `0.5` / `10` | risk per trade / kill switch |
| `ProfitTargetPct` | `2.0` | bank the basket at +2% of cycle balance (0 = off) |
| `TrailLockPct` / `TrailActivateR` | `50` / `1.0` | keep 50% of peak profit once ≥1R |
| `StopBufferATR` | `0.75` | pad the wick stop by k×ATR (0 = exact wick) |
| `TradingWindowStartHour/EndHour` | `9` / `23` | server-time entry window |
| `MaxDailyExposureMin` | `120` | max open-position minutes per day |

AI mode (`grading` vs `veto`) is service-side in `.env`. Keep `grading` until
the accuracy log proves the AI earns veto power.

## Tests

```bash
cd service && FORECASTER=fake .venv/bin/pytest -q   # fast suite
pytest -m slow                                      # real model inference
```

## Troubleshooting

- **WebRequest error 4014** — URL not in MT5's allowlist, or port mismatch.
- **"AI unavailable"** — service down; strategy still trades (fail-open).
- **No trades in AUTO** — the Telegram 🚫 notice and Experts log name the gate
  (window, spread, exposure, ADX, kill switch, margin).
- **Kill switch tripped** — deliberate manual reset: delete `XAU_KILL_<login>`
  in MT5 → Tools → Global Variables after reviewing what happened.
