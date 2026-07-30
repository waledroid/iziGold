# Strategy Framework + First Strategy (halftrend_ema_v1) — Design Spec

**Date:** 2026-07-30
**Status:** Approved in conversation; pending user review of this document
**Builds on:** [2026-07-29-xau-assistant-design.md](2026-07-29-xau-assistant-design.md)

## 1. Goal

Make the strategy layer modular: multiple strategies compiled into the EA
behind one interface, switchable at runtime, with every strategy
shadow-evaluated and logged each bar so live head-to-head performance data
drives the decision of which strategy to trade. Implement the first real
strategy (Half Trend + EMA 55 dual confirmation) as registry entry
`halftrend_ema_v1`.

A separate follow-up spec covers the UI (hybrid Telegram + local web
dashboard) that consumes this data. Decisions already made for it: EA
heartbeat every 5–10 s, real MT5 chart screenshots **and** service-rendered
charts, content = live trades, trade history with entry/exit/reason,
MoneyWatch status, AI accuracy stats, equity curve, signal log.

## 2. Constraints and key decisions

1. **MQL5 compiles statically** — no hot-loading new strategies. Chosen
   model: all strategies compile into the EA; adding one requires
   recompile; *switching* among compiled ones is runtime.
2. **Shadow evaluation** — every registered strategy is evaluated every
   closed bar. Only the active strategy trades and alerts; shadow signals
   are logged and graded silently.
3. **Shadow performance is signal-level, not basket-level.** Each shadow
   signal is graded by what price did over the 16-bar horizon (the same
   machinery that grades the AI). No per-shadow simulation of
   pyramiding/MoneyWatch — that is a backtest engine and out of scope.
4. **Strategies never move into the Python service** — that would put the
   service in the trade path and break the fail-open rule (base spec §2).
5. All original design rules stand: strategy is sole decision maker, AI
   grades only, fail-open everywhere, no martingale.

## 3. EA architecture

### 3.1 CStrategy interface (Strategy.mqh)

```
class CStrategy {
public:
   virtual string      Id();                 // stable id, flows into API + SQLite, e.g. "halftrend_ema_v1"
   virtual ENUM_SIGNAL Evaluate();           // once per closed bar
   virtual bool        ConditionStillTrue(ENUM_SIGNAL dir);  // pyramiding gate
   virtual double      StopPrice(ENUM_SIGNAL dir);           // preferred SL; 0 = use ATR default
};
```

`StopPrice()` is new: strategies that define their own stop placement
return it; `TradeManager` uses it when non-zero, otherwise falls back to
the existing `StopAtrMult × ATR` stop. Stop logic lives with the strategy
that owns it.

### 3.2 StrategyRegistry.mqh (new)

Owns an array of all compiled-in strategies and the active index.
Registration is one line per strategy in the registry's setup. Adding a
strategy touches exactly two things: its own new file under
`Include/XauAssistant/Strategies/`, and one registration line.

Each closed bar the registry evaluates every strategy. The active one's
signal drives everything that exists today (alerts, AUTO execution,
Telegram). The rest are shadows: signals forwarded to the service for
logging, no trades, no alerts. The AI forecast runs once per bar and
grades all of them.

### 3.3 Switching (v1)

`input string ActiveStrategy` selects from the registry (unknown id →
`INIT_FAILED` with a clear message). Changing an input re-inits the EA;
this is safe because protective state (kill switch, high-water mark,
exposure minutes) already persists in MT5 global variables. A switch while
a basket is open is allowed: `TradeManager` manages the basket
independently of which strategy opened it (stops, target, and EXIT
handling are strategy-independent once open).

Remote switching (Telegram command / web button, delivered via the
heartbeat response) belongs to the UI spec.

## 4. API contract changes

`AnalyzeRequest` gains two backward-compatible fields:

    { symbol, timeframe, signal, candles: [...],
      strategy_id: "halftrend_ema_v1",          // producer of the active signal; default "unknown"
      shadows: [ {strategy_id, signal}, ... ]   // non-NONE shadow signals only; default []
    }

`AnalyzeResponse` is unchanged — it describes the active signal.

Service behavior per call: one forecast as today. Active signal logged and
alerted as now, tagged with `strategy_id` and `is_active=1`. Each shadow
signal gets its own row — same verdict logic reusing the already-computed
forecast, `is_active=0`, **no Telegram alert**.

## 5. Database changes (db.py)

- `signals` gains `strategy_id TEXT` and `is_active INTEGER DEFAULT 1`.
  Migration: guarded `ALTER TABLE ... ADD COLUMN` in `SignalDb.__init__`.
  Existing rows keep `strategy_id = NULL` (displayed as "pre-framework").
- Outcome resolution is unchanged — it already grades every unresolved
  row, so shadows are graded for free.
- `stats()` grows a per-strategy breakdown keyed by `strategy_id`:
  signal count, resolved count, direction hit-rate (% of BUY/SELL where
  the 16-bar move went the signal's way), average move in the signal's
  direction. This grades the *strategy*, alongside the existing
  `ai_correct` scorecard which grades the AI. It is the query the UI's
  comparison view will expose.

## 6. First strategy: halftrend_ema_v1

File: `Include/XauAssistant/Strategies/HalfTrendEma.mqh`. Source: Half
Trend + EMA 55 dual-confirmation strategy (Crypto9ite video), adapted for
XAUUSD M15. All evaluation on closed bars — this enforces "wait for the
close" and kills intra-bar fake-outs by construction.

- **Indicators:** Half Trend (Everget algorithm) reimplemented in MQL5,
  amplitude 4; EMA 55 via `iMA`. The TradingView "hide channel" step is
  visual-only and does not apply.
- **Inputs:** `HtAmplitude=4`, `EmaLength=55`, `ConfirmCloses=2`
  (the video's "multiple closes"; user chose 2 as default, calibratable
  in the Strategy Tester).
- **BUY:** Half Trend blue AND last `ConfirmCloses` consecutive closes
  above the EMA. Fires **once per Half Trend flip**; the close-count
  restarts at the flip, so confirmation closes are counted from the flip bar
  onward. No re-entry signals within the same trend.
- **SELL:** exact inverse.
- **EXIT:** opposite Half Trend color AND `ConfirmCloses` consecutive
  closes on the opposite side of the EMA. Whichever of EXIT or stop-loss
  comes first closes the position.
- **StopPrice:** lowest wick since the Half Trend flipped blue (BUY);
  highest wick since it flipped red (SELL). Wide stops are handled by
  fixed-fractional sizing (lots shrink), never by widening risk.
- **ConditionStillTrue** (pyramiding gate): trend color unchanged AND
  close on the correct side of the EMA.
- **Unchanged layers on top:** ADX filter, trading window, daily exposure,
  spread cap, kill switch, pyramiding/profit-target. The strategy says
  *what* and *where*; MoneyWatch says *whether* and *how much*.
- The video's claimed ~90% win rate (on crypto) is treated as unverified
  marketing; the shadow log measures the real hit-rate on XAU M15 before
  any trust is extended.

## 7. Testing

**Service (pytest):**
- Contract tests for `strategy_id` + `shadows` fields, including defaults
  (old-style requests still valid).
- Shadow logging: one `/analyze` call with shadows → active row
  (`is_active=1`, alerted) + shadow rows (`is_active=0`, not alerted).
- Migration: opening a pre-framework db adds the columns and keeps rows.
- Per-strategy `stats()` breakdown on synthetic outcomes.

**EA (manual, Windows):**
- Compiles clean in MetaEditor.
- Half Trend faithfulness: values spot-checked against TradingView on the
  same XAUUSD M15 bars (acceptance check for the reimplementation).
- Strategy Tester backtest of `halftrend_ema_v1` before any AUTO use.
- `DebugFireTestSignal` still exercises the full pipeline.

## 8. Out of scope (deferred)

- UI spec (next): dashboards, heartbeat, screenshots, comparison views,
  remote strategy switching.
- Per-shadow basket simulation (backtest engine).
- Hot-loading strategies without recompile (impossible in MQL5).
