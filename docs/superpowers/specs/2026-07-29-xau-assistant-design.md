# XAU Assistant — Design Spec

**Date:** 2026-07-29
**Status:** Approved pending user review
**Market:** XAUUSD, M15 (single symbol/timeframe for v1)

## 1. Goal

An MT5-based trading assistant where the user's own strategy is the sole
decision maker and an AI forecasting service (Chronos-Bolt by default,
swappable) acts as a confirmation/grading layer. Alerts via Telegram. Supports manual and automatic execution.
Low-cost: runs entirely on the local machine (MT5 on Windows, Python
service in WSL2), free demo broker account, no cloud services.

## 2. Non-negotiable design rules

1. The strategy is the primary decision maker; the AI only grades or
   (later, explicitly enabled) vetoes.
2. The AI is never in the trade-execution path in grading mode: in auto
   mode the EA executes first, then calls the AI.
3. Fail-open everywhere: if the AI service is down or times out, strategy
   signals still alert/execute, marked "AI unavailable", plus an urgent
   Telegram warning where possible.
4. AI accuracy is logged from the first AI call (not deferred to a later
   phase), so enabling veto mode is an evidence-based decision.
5. The AI model is swappable behind a Python `Forecaster` interface.
6. On a live account, auto mode refuses to trade unless
   `AllowLiveTrading = true` is explicitly set.

## 3. What the AI model provides (and what it doesn't)

Default model: **Chronos-Bolt** (amazon/chronos-bolt-small to start).
It is a **univariate probabilistic forecaster**: it takes a single
numeric series (last 200 M15 closes) and returns **quantile forecasts**
(e.g., q10/q25/q50/q75/q90) for the next 16 bars in a single forward
pass. It does not accept indicators and does not classify regimes.
The service derives the response fields:

- **direction** — position of the median (q50) 16-bar forecast path
  vs last close.
- **confidence** (0–1) — derived from the quantile distribution: how
  far the median moves relative to ATR, discounted by the quantile
  spread (wide q10–q90 band = uncertain, tight band clear of the
  current price = confident).
- **regime** — computed classically (not by the AI): ADX(14) plus ATR
  percentile over the last 100 bars → `trend | range | high_volatility`.
- **verdict** — `confirm | neutral | conflict`, from comparing AI
  direction/confidence against the strategy signal using configurable
  thresholds.

Alternative implementation: **Time-MoE** (point forecasts only; its
confidence is an engineered proxy — forecast slope vs ATR plus
monotonicity). Selectable via `.env`; the SQLite accuracy log (§8)
allows A/B comparison of models on real XAUUSD data.

Expectation: zero-shot directional accuracy on gold at M15 may be near
coin-flip. The system is built to measure this (see §8) before the AI is
ever allowed to block a trade.

## 4. Architecture and repo layout

    xau/
    ├── mt5/
    │   ├── Experts/XauAssistant.mq5        # event loop + wiring only
    │   └── Include/XauAssistant/
    │       ├── Strategy.mqh                # strategy interface + stub (user rules later)
    │       ├── TradeManager.mqh            # CTrade wrapper — auto-mode execution
    │       ├── AiApi.mqh                   # WebRequest client, JSON, 3s timeout
    │       ├── SignalManager.mqh           # strategy + AI → combined report, 1/bar
    │       ├── RiskManager.mqh             # MoneyWatch: fixed-% sizing + drawdown kill switch, spread check
    │       └── Alerts.mqh                  # chart arrows, MT5 popup/push
    ├── service/
    │   ├── app/
    │   │   ├── main.py                     # FastAPI: POST /analyze, GET /health
    │   │   ├── forecaster.py               # Forecaster ABC + ChronosBoltForecaster (default) + TimeMoeForecaster
    │   │   ├── analysis.py                 # forecast → direction + confidence
    │   │   ├── regime.py                   # ADX + ATR percentile classifier
    │   │   ├── verdict.py                  # grading/veto combination logic
    │   │   ├── telegram.py                 # alert formatting + send
    │   │   ├── db.py                       # SQLite: signals, AI outputs, outcomes
    │   │   └── config.py                   # .env-driven settings
    │   ├── tests/
    │   ├── requirements.txt
    │   └── .env.example
    ├── docs/
    └── README.md

## 5. Modes (independent EA inputs)

| Switch | Values | Default | Notes |
|---|---|---|---|
| ExecutionMode | MANUAL / AUTO | MANUAL | AUTO opens on BUY/SELL, closes on EXIT, holds while green |
| AiMode | GRADING / VETO | GRADING | VETO stays off until the accuracy log justifies it |
| AllowLiveTrading | true / false | false | Account type auto-detected; live + AUTO requires explicit opt-in |
| DebugFireTestSignal | true / false | false | Emits a fake BUY to test the full pipeline end-to-end |

Failure policy in AUTO + VETO: **fail-open** — trade executes, report
marked "AI unavailable", urgent Telegram warning sent by the EA path
that still works (MT5 alert) and by the service once it returns.

## 5a. MoneyWatch (AUTO-mode risk management)

Deliberately minimal — two rules, two inputs, implemented entirely in
`RiskManager.mqh` (works even if the AI service is dead):

1. **Grow — fixed-fractional sizing:** every trade risks
   `RiskPerTradePct` (default 0.5 %) of current equity;
   `lots = equity × risk% / (SL_distance_points × point_value)`.
   Lots compound as equity grows and shrink as it falls. Hard
   invariant: size never increases after a loss (no martingale).
2. **Protect — drawdown kill switch:** track the equity high-water
   mark (only ever rises). If equity falls `MaxDrawdownPct`
   (default 10 %) below the peak: AUTO trading disabled, urgent
   Telegram alert, manual re-enable required. This protects both the
   starting balance and profits already made.

State (high-water mark, kill-switch tripped) persists in MT5 global
variables so terminal restarts cannot reset protection. MoneyWatch
status (current risk %, distance to kill switch) is included in every
Telegram report. Deferred by choice (add only if live data shows the
need): losing-streak throttle, daily loss limit, AI-confidence-scaled
sizing.

## 5b. Pyramiding & profit target (AUTO mode, optional)

Scale into winners only — never into losers. While the strategy
condition stays green and the open basket is in profit:

- **Spacing:** add one position after each favorable move of
  `AddTriggerATR` (default 1.0 × ATR(14)) since the last entry.
  ATR-based, never fixed pips, so spacing adapts to volatility.
- **Sizing:** shrinking pyramid — adds at 1.0 / 0.7 / 0.4 × initial
  lots (`MaxPositions` default 3 total). Keeps the basket's average
  entry anchored near the first price so a normal pullback cannot
  round-trip the cycle.
- **Breakeven on add:** every add moves all stops to basket
  breakeven; from the second position onward only the market's money
  is at risk. Total cycle risk never exceeds the initial
  `RiskPerTradePct`.
- **Profit target:** balance is recorded at cycle start; when basket
  floating profit ≥ `ProfitTargetPct` (default 2.0 %) of it, close
  all positions immediately (even if the condition is still green),
  reset, await the next fresh signal.
- **Loss handling:** only the stop-loss (ATR-based, default 2 × ATR)
  or a strategy EXIT signal closes a losing trade. No exits on
  floating loss; no adds while in loss (hard invariant, see §5a).

Inputs: `EnablePyramiding`, `MaxPositions=3`, `AddTriggerATR=1.0`,
`BreakevenOnAdd=true`, `ProfitTargetPct=2.0`, `StopAtrMult=2.0`.
All defaults are starting points to be calibrated in the MT5 Strategy
Tester once the real strategy rules are implemented.

## 5c. Trading window & daily exposure budget

The strategy is fractal/interval-based: several short trades may occur
within a 30–60 minute burst. Exposure is capped, not trade count:

- `TradingWindowStart` / `TradingWindowEnd` — entries allowed only
  inside the configured daily window (chosen for gold liquidity,
  e.g., London open or London/NY overlap).
- `MaxDailyExposureMinutes` (default 60) — the EA accumulates minutes
  of open-position time per day; once spent, no new entries until the
  next trading day (open positions still manage/close normally).
- **Direction filter (deterministic, EA-side):** entries additionally
  require the classical trend check (ADX above threshold) computed
  in the EA itself — not via the AI service — so the "only trade when
  direction is clear" gate stays in the fast path and works when the
  AI is down. The AI verdict remains grading-only per §2.1.

Daily exposure state persists in MT5 global variables like §5a.

## 6. Data flow (each closed M15 bar)

1. EA detects a new bar → `Strategy.mqh` returns BUY / SELL / EXIT / NONE.
2. AUTO mode + signal: `TradeManager` executes immediately (before any
   AI call). RiskManager (MoneyWatch, §5a) gates entry: max spread,
   one position per symbol (magic number), drawdown kill switch not
   tripped.
3. EA POSTs to `/analyze` on every bar — **including NONE** — with:
   symbol, timeframe, signal, last 200 closed OHLCV candles. The
   fresh candles let the service lazily resolve outcomes of past
   signals (price change 16 bars later) with no separate data feed.
4. Service: model forecast → direction/confidence → regime →
   verdict. If signal ≠ NONE: log to SQLite and send the Telegram
   report directly (no second EA round trip).
5. EA parses the JSON response, draws the chart signal with the AI
   grade, raises the MT5 alert.
6. WebRequest timeout is 3 s; any failure → fail-open path (§2.3).

## 7. API contract

    POST /analyze
    Request:  { symbol, timeframe, signal, candles: [{t,o,h,l,c,v} × 200] }
    Response: { direction: "bullish|bearish|neutral",
                confidence: 0.0–1.0,
                regime: "trend|range|high_volatility",
                verdict: "confirm|neutral|conflict",
                mode: "grading|veto",
                ai_available: true }

    GET /health → { status, model_loaded, db_ok }

This contract is the seam for rule §2.5: swapping Chronos-Bolt for
Time-MoE, TimesFM, etc. touches only `forecaster.py`.

## 8. Accuracy tracking (from day one)

SQLite schema (managed in `db.py`):

- `signals` — every non-NONE strategy signal: timestamp, bar time,
  signal, price, AI direction/confidence/regime/verdict, mode,
  ai_available.
- Outcome columns filled lazily: when later `/analyze` calls deliver
  candles covering signal_time + 16 bars, record the realized move and
  whether the AI direction was correct.

This dataset is the sole basis for calibrating confidence thresholds
and deciding whether VETO mode ever turns on.

## 9. Performance budget

| Component | Latency | In trade path? |
|---|---|---|
| Strategy evaluation (in-terminal) | µs | yes |
| Order execution (EA → broker) | 50–300 ms | yes (AUTO) |
| Chronos-Bolt-small CPU inference (single pass) | ~20–100 ms | no (GRADING) |
| Full AI round trip | ~0.3–1 s | only in VETO, capped 3 s |

Footprint: ~1.5–2 GB RAM (PyTorch CPU + model), one inference per
15 min. No GPU, no Docker required.

## 10. Environment

- MT5 on Windows; EA calls `http://127.0.0.1:8000/analyze`.
- URL must be added to MT5 allowed list (Tools → Options → Expert
  Advisors → WebRequest URLs).
- Python 3.11+ service in WSL2, uvicorn on 0.0.0.0:8000 (Windows
  reaches WSL2 via localhost forwarding). Fallback: run the service
  natively on Windows — no code change.

## 11. Testing

- **Python:** pytest with synthetic candle fixtures (known trend,
  known range, known vol spike) validating analysis/regime/verdict
  without loading the model; one slow marked test with real Chronos-Bolt;
  contract tests on request/response schemas.
- **EA:** compiles clean in MetaEditor; `DebugFireTestSignal` input
  exercises EA → API → Telegram end-to-end before any real strategy
  exists; strategy stub is Strategy-Tester-compatible for later
  backtesting of the real rules.

## 12. Phases

1. **Scaffold + pipeline** — both sides, stub strategy, debug signal
   end-to-end, Telegram, SQLite logging live.
2. **Real AI** — Chronos-Bolt inference, quantile-based confidence,
   regime detection, verdict logic; Time-MoE as optional alternative.
3. **Strategy implementation** — user supplies documented rules
   (currently in a video, to be extracted as text); implemented in
   `Strategy.mqh`; live grading on demo; AUTO mode available.
4. **Calibration** — accuracy analysis from SQLite, threshold tuning,
   optional VETO enablement, position-sizing suggestions.

## 13. Open items

- Strategy rules: pending user extraction from video (entries, exits,
  filters, session limits). Blocked on user; does not block Phases 1–2.
- Fractal interval length: if the strategy's decision intervals are
  shorter than 15 minutes, the EA's evaluation timeframe moves to
  M5/M1 bar close (architecture unchanged — same event loop, smaller
  bars). Confirm when rules are extracted.
- Trading window hours: user to choose the daily session window
  (recommendation: London/NY overlap for gold liquidity).
- Telegram bot token + chat ID: user must create the bot via BotFather
  and provide credentials in `.env`.
- Confidence/verdict thresholds: initial defaults are placeholders by
  design; real values come from Phase 4 calibration.
