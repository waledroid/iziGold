# TradeTimeframe — pin trading to M5 regardless of chart timeframe

**Date:** 2026-08-12 · **Status:** user-approved ("we should be trading on m5
not m15 ..the ea support for m15 is just for visual only")

## Problem

Every EA decision path uses `PERIOD_CURRENT`, so switching the chart to M15
on 08-11 silently moved trading to M15 (uncalibrated — produced the 08-12
−$41.97 M15 stop-out). The chart TF must become **visual only**.

## Design

New EA input: `input ENUM_TIMEFRAMES TradeTimeframe = PERIOD_M5;` — every
trading decision reads this, never the chart TF. On hot-reload the new input
takes its default (M5), which is exactly the desired state.

**Pinned to `TradeTimeframe`** (replace `PERIOD_CURRENT` / add a `m_tf`
member set via Init):

- `XauAssistant.mq5`: `g_atrHandle = iATR(...)` (stop pad), the OnTick
  bar-close detector `iTime(_Symbol, tf, 0)`.
- `RiskManager.mqh`: `iADX` handle, exposure accounting
  `PeriodSeconds(tf)`, per-bar daily-loss cache `iTime(tf, 0)`. `Init`
  gains a `ENUM_TIMEFRAMES tf` parameter.
- `AiApi.mqh`: the 300-candle `CopyRates` export AND the `timeframe`
  string sent in `/analyze` (derive from `tf`, not `_Period`) — signals
  must be tagged M5 even on an M15 chart.
- `UiApi.mqh`: `PostHeartbeat`'s forming-bar `CopyRates` (bar 0 must match
  the /chart accumulator's TF). Method gains a tf param or member.
- `Strategies/HalfTrendEma.mqh` + `Strategies/BollStochRsi.mqh`: ALL
  indicator handles (`iMA/iATR/iBands/iRSI`), bar data
  (`CopyHigh/Low/Close`, `iTime/iLow/iHigh/iClose`, `Bars`),
  `PeriodSeconds` — via an `m_tf` member set in `Init`. `CStrategy::Init`
  signature gains the tf parameter (base class + both strategies +
  registration in the EA's OnInit).

**Stays `PERIOD_CURRENT` (visual):** `TradeBoxes.mqh` — box/arrow painting
geometry follows the chart the user is looking at; objects are time/price
anchored so M5-signal anchors render fine on any chart TF.

**Verification gate:** after the change,
`grep -n PERIOD_CURRENT mt5/Experts mt5/Include -r` must show hits ONLY in
`TradeBoxes.mqh`. MetaEditor CLI compile 0 errors / 0 warnings.

## Guards

- `OnInit` prints one line: `"trading TF: M5 (chart: M15 — visual only)"`
  whenever chart TF ≠ TradeTimeframe, so the log always states the split.
- No service changes: `/analyze` carries the TF string as today; stats are
  already split per timeframe. The M15-tagged rows from 08-11/12 stay in
  the db as historical record.

## Testing

MQL5: CLI compile 0/0 + the grep gate + careful review. Service suite runs
as regression (no service code touched). Live check after deploy: next
`/analyze` row tagged `M5` while the chart sits on M15.

## izi.md

Same branch: TradeTimeframe input (default M5, chart TF is visual-only),
the TradeBoxes exception, the grep invariant, and the 08-11 M15 incident in
history-worth-knowing.
