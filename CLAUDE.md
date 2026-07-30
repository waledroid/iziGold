# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MT5 trading assistant for XAUUSD M15 with two halves that talk over HTTP:

- `mt5/` — MQL5 Expert Advisor (runs in MetaTrader 5 on Windows). The user's strategy is the **sole decision maker**.
- `service/` — Python FastAPI service (runs in WSL2/Linux) that grades strategy signals with an AI forecaster (Chronos-Bolt), sends Telegram alerts, and logs everything to SQLite.

Authoritative design spec: `docs/superpowers/specs/2026-07-29-xau-assistant-design.md`. Implementation plans live in `docs/superpowers/plans/`.

## Commands

All Python work happens in `service/`:

```bash
cd service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # core deps
pip install -r requirements-model.txt    # torch + chronos (only needed for real inference)

pytest                                   # fast suite (excludes slow by default via pyproject addopts)
pytest tests/test_analysis.py            # one file
pytest tests/test_api.py::test_name      # one test
pytest -m slow                           # real Chronos-Bolt inference (downloads model ~1 min first time)

uvicorn app.main:app --host 0.0.0.0 --port 8000   # run the service (cp .env.example .env first)
```

Set `FORECASTER=fake` in `.env` to develop/run without torch or the model — `FakeForecaster` is a deterministic linear extrapolation used by the fast tests.

The MQL5 side (`mt5/`) **cannot be compiled or tested from this environment** — it compiles in MetaEditor on Windows. Verify MQL5 changes by careful reading; flag that the user must compile in MetaEditor (expect 0 errors) and re-copy files into the MT5 data folder.

## Non-negotiable design rules (from the spec)

1. The strategy decides; the AI only grades (or, later and explicitly enabled, vetoes).
2. In AUTO mode the EA **executes first, then** calls the AI — the AI is never in the trade path in grading mode.
3. **Fail-open everywhere**: AI service down/timeout → strategy signals still alert/execute, marked "AI unavailable". `/analyze` catches forecaster exceptions and returns `neutral/0.0/ai_available=false` rather than erroring.
4. AI accuracy is logged from the first call so enabling veto mode is evidence-based.
5. The AI model is swappable behind the `Forecaster` ABC in `app/forecaster.py` — that file is the only place a model swap touches.
6. Live account + AUTO refuses to trade unless `AllowLiveTrading=true` (checked in `OnInit`, returns `INIT_FAILED`).
7. No martingale: position size never increases after a loss; pyramiding adds only into winners.

## Architecture / data flow

Each closed M15 bar: EA evaluates `Strategy.mqh` → (AUTO) `TradeManager` executes immediately → EA POSTs to `/analyze` with the signal (**including NONE**) and the last 200 OHLCV candles → service runs forecast → `analysis.py` derives direction/confidence → `regime.py` classifies trend/range/high_volatility classically (ADX + ATR percentile, not the AI) → `verdict.py` combines with the strategy signal → service logs to SQLite and sends Telegram directly (no second EA round trip) → EA draws the chart signal with the AI grade.

Key subtlety: the every-bar NONE posts are what let `db.py` **lazily resolve outcomes** of past signals (`resolve_outcomes` runs on every `/analyze` call using the fresh candles) — there is no separate data feed. Don't "optimize away" the NONE calls.

Service module responsibilities:
- `main.py` — FastAPI wiring only (`/analyze`, `/health`); forecaster and db live on `app.state` via lifespan.
- `forecaster.py` — `Forecaster` ABC returning `QuantileForecast` (q10/q50/q90); `FakeForecaster`, `ChronosBoltForecaster` (lazy model load on first call), `timemoe` planned.
- `config.py` — pydantic-settings singleton `settings` read from `.env`. AI mode (`grading`/`veto`) is configured **service-side** here, not in the EA.

EA structure: `Experts/XauAssistant.mq5` is event loop + wiring only; all logic lives in `Include/XauAssistant/` (`Strategy.mqh` stub — real rules slot in there without touching anything else; `TradeManager.mqh` pyramiding/profit-target; `RiskManager.mqh` "MoneyWatch" sizing + drawdown kill switch; `AiApi.mqh` WebRequest client; `SignalManager.mqh`; `Alerts.mqh`). Risk/kill-switch/exposure state persists in MT5 global variables so terminal restarts can't reset protection.

## Testing conventions

Tests use synthetic candle fixtures (`tests/fixtures.py`: known trend, known range, known vol spike) so analysis/regime/verdict logic is validated without loading the model. The API contract (spec §7) is covered by contract tests — keep request/response schema changes in sync with `models.py`, the tests, and `AiApi.mqh` on the EA side.

## Current status

Strategy layer is modular: strategies live in `mt5/Include/XauAssistant/Strategies/` behind `CStrategy` (with `Id()` and `StopPrice()`), registered in the EA's `OnInit`, all shadow-evaluated per bar (only `ActiveStrategy` trades/alerts). First real strategy: `halftrend_ema_v1`. The `/analyze` request carries `strategy_id` + `shadows`; SQLite tags every row and `stats()` reports per-strategy hit-rates. Confidence/verdict thresholds are placeholder defaults awaiting Phase 4 calibration against the SQLite accuracy log.
