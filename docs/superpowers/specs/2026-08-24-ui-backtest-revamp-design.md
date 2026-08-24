# UI + Backtest Revamp — Design

**Date:** 2026-08-24
**Status:** Implemented (plan `docs/superpowers/plans/2026-08-24-ui-backtest-revamp.md`)

That plan's "Deliberate deviations from the spec" section records eight
implementation deviations from this design — all simplifications, none of
which change the user-visible result; read it alongside this document.

## Goal

Bring the dashboard and onboarding UI up to date with everything the system
now does, replace the hand-drawn dashboard chart with TradingView's
Lightweight Charts showing past trades, and add a Backtest page that runs the
existing replay engine from the browser with filters (strategy, date range,
starting equity, risk, and the engine's rule toggles).

User decisions (2026-08-24):
- Backtest data source: **persist candles in SQLite** + one-time MT5 backfill.
- Chart: **Lightweight Charts** (vendored TradingView library), not the
  tradingview.com widget.
- Backtest strategy coverage at launch: **both HalfTrend lanes**
  (`halftrend_ema_v1` M5 and `halftrend_m15_v1` M15); `boll_stochrsi` shown
  greyed out as "not yet supported".

## Current state (verified 2026-08-24)

- `service/app/static/dashboard.html` (~950 lines): hand-rolled Canvas-2D
  chart (`_drawChart()` ~:638), stat strip, controls card (AUTO/MANUAL,
  close-all, proposals), strategy comparison table, trade history, signal
  log. Polls `/ui/state` (5 s) and `/ui/candles`, `/ui/overlays`,
  `/ui/trades`, `/ui/stats`, `/ui/signals`, `/ui/profile` (30 s).
- `service/app/static/onboarding.html` (~285 lines): five fieldsets saving
  via `POST /ui/profile`; reachable only by typing the URL once a profile
  exists.
- **Candles are not persisted.** `/analyze` merges the EA's rolling window
  into `app.state.recent_candles` (cap 2000 bars ≈ one week of M5), lost on
  restart. No candles table.
- **Trades are stored as an event log** (`trades`: one row per
  open/add/close; `profit` on close rows; `htf_agree`, `ema200_agree`,
  `entry_mode`, `tp` columns exist). `db.recent_trades()` does not SELECT
  `htf_agree`/`ema200_agree`, so the UI cannot show them today.
- **Backtest engine already exists**: `scripts/backtest.py` (~2,100 lines) —
  lane plug-in contract (`Account`, `Lane`, `LANES`, `run()`), ~45 CLI flags
  (data source/range, `--entry-mode`, `--exit-scheme`, `--ema200-confirm`,
  `--bias-tf/--bias-mode`, `--json`, `--web`). It replays the **M5 HalfTrend
  lane only**; no M15 lane, no BollStochRsi port. Guarded by frozen golden
  tests (`service/tests/test_backtest_golden.py`, LOOSE + STRICT pins over
  `tests/data/bars_slice.json`) that must not be regenerated.
- `scripts/backtest_report.py` + `service/app/static/backtest_report.html`
  produce a standalone HTML report (stats row, Lightweight Charts with
  risk/reward zones, zoom presets, row-click trade zoom).
- `scripts/dump_bars.py` pulls M5 bars from a running MT5 terminal into
  `/ui/candles`-shaped JSON.
- `service/app/static/vendor/lightweight-charts.standalone.production.js`
  is vendored and already used by `miniapp.html` and the backtest report.
  The dashboard does not use it yet.
- UI gaps vs. current behavior: no M15 lane tab/overlay, no
  `htf_agree`/`ema200_agree` display, no dashboard control for
  `htf_enforce`/`ema200_enforce`/`entry_mode` (Telegram-only today), stats
  are per `strategy @timeframe` but rendered as an opaque string, shadow
  signals have no filter/comparison view, backtest reports are unreachable
  from the UI.

## Design

### 1. Candle persistence

New SQLite table (additive migration in `db.py`):

```sql
CREATE TABLE IF NOT EXISTS candles (
  symbol    TEXT NOT NULL,
  timeframe TEXT NOT NULL,     -- 'M5', 'M15', ...
  bar_time  INTEGER NOT NULL,  -- epoch seconds, bar open
  o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
  v REAL,
  PRIMARY KEY (symbol, timeframe, bar_time)
);
```

- `/analyze`'s existing `_merge_candle_window` additionally upserts the
  incoming bars (INSERT OR REPLACE — last write wins; the EA re-sends the
  forming bar until it closes, so upsert keeps the final close).
- On service startup, seed `app.state.recent_candles` with the most recent
  2000 bars for the chart symbol/timeframe so the dashboard chart survives
  restarts. In-memory accumulator behavior otherwise unchanged.
- Backfill: `scripts/backfill_candles.py` — reuses the `dump_bars.py`
  MT5 route to pull up to ~12 months of M5 (and optionally M15) bars and
  bulk-insert them. Idempotent; safe to re-run. Run once from `setup.sh` or
  manually; document in izi.md.
- M15 for backtests is **resampled from M5** in the engine (3 × M5 → 1 M15,
  aligned to 15-minute boundaries; drop incomplete leading/trailing groups),
  so one backfill covers both lanes. If M15 rows exist in the table they are
  preferred over resampling.

### 2. Dashboard revamp (`dashboard.html`)

- **Chart**: replace the canvas chart with Lightweight Charts
  (`/static/vendor/lightweight-charts.standalone.production.js`, same as the
  mini-app). Candlestick series from `/ui/candles`; overlays from
  `/ui/overlays?strategy=` as line series (HalfTrend, EMA 9/21/55/200,
  Bollinger). Keep the expand/lightbox affordance.
- **Trades on the chart**: markers from `/ui/trades` — up/down arrows at
  open/add events, close markers labelled with profit; hover tooltip with
  direction, lots, price, P/L. Clicking a trade-history row scrolls/zooms
  the chart to that trade (same pattern as the backtest report).
- **Strategy tabs**: generated dynamically from `/ui/stats` keys
  (`strategy_id @timeframe`) instead of the two hardcoded tabs, so
  `halftrend_m15_v1 @M15` gets a tab. `/ui/overlays` gains an M15-aware
  builder for `halftrend_m15_v1` (HalfTrend + EMA lines computed on M15
  bars, resampled or read from the candles table).
- **Trade table**: `db.recent_trades()` SELECTs and returns `htf_agree`,
  `ema200_agree`, `tp`, `entry_mode`; the table shows M15-agree ✅/⚠️ and
  EMA200-agree ✅/⚠️ columns (blank where NULL).
- **Rule toggles**: controls card gains three controls — `htf_enforce`
  (on/off), `ema200_enforce` (on/off), `entry_mode` (strict/loose) — backed
  by a new `POST /ui/rules` endpoint writing the same kv keys the Telegram
  commands use (`db.htf_enforce()`, `db.ema200_enforce()`, entry-mode kv).
  Current values displayed from `GET /ui/state` (extended to include them).
  Telegram commands keep working; last writer wins.
- **Strategy comparison table**: split the `strategy @timeframe` key into
  Strategy and TF columns; mark the active strategy; shadow rows visually
  distinct.
- **Signal log**: add an All / Active-only / Shadows-only filter (client
  side; the data already carries `is_active`).
- **Menu bar**: shared header across pages — **Dashboard · Backtest ·
  Settings** — implemented as a small shared include (duplicated markup kept
  in sync is acceptable given three static pages; no build step).

### 3. Backtest page and API

**Page** `GET /ui/backtest` → `app/static/backtest.html`:

- Filter form:
  - Strategy: `halftrend_ema_v1 (M5)`, `halftrend_m15_v1 (M15)`,
    `boll_stochrsi` (disabled, "not yet supported").
  - Date range: start / end date pickers, pre-filled with the available
    candle range (shown on the page, e.g. "data available 2025-09-01 →
    today").
  - Starting equity (default 10 000) and risk % per trade (default from
    profile's `risk_per_trade_pct`).
  - Rule toggles mirroring the engine flags: entry mode (adr/fixed —
    engine's `--entry-mode`), exit scheme, EMA-200 confirm, M15 bias
    (`--bias-mode`), chop filter. Defaults match live EA behavior.
- Run button → POST, then live status ("running… N% / done / failed").
- Recent runs list (params summary, date, net P/L, link to report; delete
  button).

**API** (all service-side, no EA involvement):

- `POST /ui/backtest` — validate params (range within available candles,
  equity > 0, supported strategy), insert a `backtest_runs` row
  (`status='running'`), launch a background thread. Returns `{run_id}`.
  One run at a time; a second POST while running returns 409.
- `GET /ui/backtest/runs` — recent runs with status + headline stats.
- `GET /ui/backtest/{run_id}` — status/progress/error for polling.
- `GET /ui/backtest/{run_id}/report` — serves the generated standalone HTML
  report.
- New table `backtest_runs(id, created_ts, params_json, status, progress,
  error, stats_json, report_path)`. Artifacts under
  `service/data/backtests/{run_id}/` (result JSON + report HTML).

**Engine integration** (`service/app/backtest_runner.py`, a thin adapter):

- Imports `scripts/backtest.py` by path; the engine file stays where it is
  and its CLI behavior is untouched (golden pins stay green).
- New candle **source**: read from the SQLite candles table for the
  requested range (the engine already accepts a bars list / JSON source; the
  adapter feeds it rows in the same shape).
- **M15 lane (additive)**: a new lane entry replaying the HalfTrend rules on
  M15 bars with `halftrend_m15_v1`'s parameters (taken from the EA's
  registration defaults in `mt5/Experts/XauAssistant.mq5`). Implemented as a
  new lane in `LANES` — no change to the existing `ht` lane's code path.
- Report generation reuses `scripts/backtest_report.write_report()`.
- Progress: the adapter updates `backtest_runs.progress` periodically;
  failures write `status='failed'` + `error`.

### 4. Onboarding (`onboarding.html`)

- Restyled to match the revamped dashboard (shared header/menu, same
  styles); all existing fields and the `POST /ui/profile` contract stay.
- Reachable as **Settings** in the menu; gets an explicit "Back to
  dashboard" action. First-run redirect behavior unchanged.

## Error handling

- Backtest with a range outside stored candles → 400 with the available
  range in the message; the form prevents it up front by showing the range.
- Engine exception mid-run → run marked `failed`, error surfaced on the
  page; never crashes the service (background thread, guarded).
- Candle upsert failures on `/analyze` are logged and swallowed — the
  fail-open rule holds; grading must never break because persistence did.
- Lightweight Charts missing/failed to load → chart panel shows a plain
  error, rest of the dashboard still works.

## Testing

- `pytest` additions: candle upsert + startup seeding + M5→M15 resampling;
  `recent_trades()` new fields; `/ui/rules` contract; backtest API contract
  (run lifecycle over the existing `tests/data/bars_slice.json`, small range
  so it stays in the fast suite); available-range validation.
- **Golden pins untouched**: `test_backtest_golden.py` LOOSE/STRICT must
  pass unmodified — M15 lane and SQLite source are additive.
- `backtest_report_smoke.js` still passes (template unchanged or extended
  compatibly).
- Manual: dashboard chart + markers verified against MT5 chart; toggle
  round-trip vs Telegram `/agree`.

## Out of scope (explicit)

- BollStochRsi Python port (filter shows it greyed out).
- Any MQL5/EA change — this is entirely service-side.
- Multi-user auth on the dashboard (stays 127.0.0.1-only).
- Backtest queueing/parallel runs (one at a time).

## Ops / documentation

- izi.md updated in the same commits (new endpoints, candles table,
  backfill procedure, backtest page runbook) — per the CLAUDE.md law.
- `setup.sh`: optionally invoke the backfill when MT5 is reachable and the
  candles table is empty.
