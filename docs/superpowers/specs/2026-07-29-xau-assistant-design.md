# XAU Assistant — Design Spec

**Date:** 2026-07-29
**Status:** Approved pending user review
**Market:** XAUUSD, M15 (single symbol/timeframe for v1)

## 1. Goal

An MT5-based trading assistant where the user's own strategy is the sole
decision maker and a Time-MoE AI service acts as a confirmation/grading
layer. Alerts via Telegram. Supports manual and automatic execution.
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

## 3. What Time-MoE actually provides (and what it doesn't)

Time-MoE is a **univariate point-forecasting** model. It takes a single
numeric series (last 200 M15 closes) and predicts future values. It does
not accept indicators, does not output probabilities, and does not
classify regimes. Therefore the service derives the response fields:

- **direction** — sign of the 16-bar-ahead forecast path vs last close.
- **confidence** (0–1) — engineered proxy: forecast slope magnitude
  relative to current ATR, combined with forecast monotonicity
  (how consistently the forecast path points one way).
- **regime** — computed classically (not by the AI): ADX(14) plus ATR
  percentile over the last 100 bars → `trend | range | high_volatility`.
- **verdict** — `confirm | neutral | conflict`, from comparing AI
  direction/confidence against the strategy signal using configurable
  thresholds.

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
    │       ├── TimeMoeApi.mqh              # WebRequest client, JSON, 3s timeout
    │       ├── SignalManager.mqh           # strategy + AI → combined report, 1/bar
    │       ├── RiskManager.mqh             # ATR lot sizing, spread check, daily-loss breaker
    │       └── Alerts.mqh                  # chart arrows, MT5 popup/push
    ├── service/
    │   ├── app/
    │   │   ├── main.py                     # FastAPI: POST /analyze, GET /health
    │   │   ├── forecaster.py               # Forecaster ABC + TimeMoeForecaster (50M, CPU)
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

## 6. Data flow (each closed M15 bar)

1. EA detects a new bar → `Strategy.mqh` returns BUY / SELL / EXIT / NONE.
2. AUTO mode + signal: `TradeManager` executes immediately (before any
   AI call). RiskManager gates entry: max spread, one position per
   symbol (magic number), daily-loss circuit breaker (drops EA to
   MANUAL for the rest of the day when hit).
3. EA POSTs to `/analyze` on every bar — **including NONE** — with:
   symbol, timeframe, signal, last 200 closed OHLCV candles. The
   fresh candles let the service lazily resolve outcomes of past
   signals (price change 16 bars later) with no separate data feed.
4. Service: Time-MoE forecast → direction/confidence → regime →
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

This contract is the seam for rule §2.5: swapping Time-MoE for Chronos,
TimesFM, etc. touches only `forecaster.py`.

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
| Time-MoE 50M CPU inference | ~200–500 ms | no (GRADING) |
| Full AI round trip | ~0.5–1.5 s | only in VETO, capped 3 s |

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
  without loading the model; one slow marked test with real Time-MoE;
  contract tests on request/response schemas.
- **EA:** compiles clean in MetaEditor; `DebugFireTestSignal` input
  exercises EA → API → Telegram end-to-end before any real strategy
  exists; strategy stub is Strategy-Tester-compatible for later
  backtesting of the real rules.

## 12. Phases

1. **Scaffold + pipeline** — both sides, stub strategy, debug signal
   end-to-end, Telegram, SQLite logging live.
2. **Real AI** — Time-MoE inference, confidence derivation, regime
   detection, verdict logic.
3. **Strategy implementation** — user supplies documented rules
   (currently in a video, to be extracted as text); implemented in
   `Strategy.mqh`; live grading on demo; AUTO mode available.
4. **Calibration** — accuracy analysis from SQLite, threshold tuning,
   optional VETO enablement, position-sizing suggestions.

## 13. Open items

- Strategy rules: pending user extraction from video (entries, exits,
  filters, session limits). Blocked on user; does not block Phases 1–2.
- Telegram bot token + chat ID: user must create the bot via BotFather
  and provide credentials in `.env`.
- Confidence/verdict thresholds: initial defaults are placeholders by
  design; real values come from Phase 4 calibration.
