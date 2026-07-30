# Second Strategy: boll_stochrsi_v1 (Bollinger Bands + Stochastic RSI) — Design Spec

**Date:** 2026-07-31
**Status:** Approved in conversation; pending user review of this document
**Builds on:** [2026-07-30-strategy-framework-design.md](2026-07-30-strategy-framework-design.md)

## 1. Goal

Second registry strategy, adapted from a Binance-Futures Bollinger Band +
Stochastic RSI strategy (source video) to XAUUSD M15 in the existing
framework: one new file `mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh`
plus one registration line in the EA. Shadow-evaluated head-to-head against
`halftrend_ema_v1` from its first bar; the UI comparison view includes it
with zero UI changes. Platform-specific source steps (Binance interface,
TradingView tab, chart cleanup) do not carry over.

## 2. Indicators

- **Bollinger Bands (20, 2)** on M15 closes (`iBands`): upper, middle
  (SMA 20), lower.
- **Bandwidth** = (upper − lower) / middle, computed per closed bar.
- **Stochastic RSI (14, 14, 3, 3)**, computed in the strategy from RSI(14):
  StochRSI = (RSI − min(RSI, 14)) / (max(RSI, 14) − min(RSI, 14)) × 100;
  %K = SMA(StochRSI, 3) (the source's "blue line"),
  %D = SMA(%K, 3) (the "red line"). Cross detection on closed bars.
- The source's overbought (100) / oversold (0) commentary is logged but is
  **not** an entry/exit filter in v1 (the source states no rule for it).

## 3. Rules (all on closed M15 bars)

Inputs (defaults calibratable in the Strategy Tester):
`BbPeriod=20`, `BbDev=2.0`, `TrendCloses=2`, `SqueezeLookback=100`,
`SqueezePctile=25`, `ExpansionBars=2`, `RsiPeriod=14`, `StochPeriod=14`,
`KSmooth=3`, `DSmooth=3`.

- **Trend zone (long):** last `TrendCloses` consecutive closes between the
  middle line and the upper band. Short: between middle and lower band.
- **Squeeze:** bandwidth in the bottom `SqueezePctile` % of the last
  `SqueezeLookback` bars.
- **Expansion:** bandwidth has risen for `ExpansionBars` consecutive bars,
  starting from a bar that qualified as a squeeze. The expansion state
  stays active until bandwidth stops rising for `ExpansionBars`
  consecutive bars (symmetric hysteresis).
- **BUY:** trend zone (long) AND expansion active AND %K crossed above %D
  on this closed bar (fresh cross — the trigger, so no once-per-phase
  latch is needed; re-entry after an exit requires a new fresh cross).
- **SELL:** exact inverse.
- **EXIT:** a close crossing the middle band against the position
  (long: close < middle; short: close > middle) → `SIGNAL_EXIT`.
  Unlike `halftrend_ema_v1`, this strategy uses `SIGNAL_EXIT` — its exit
  condition is not the opposite entry.
- **StopPrice:** returns 0 → framework's ATR default (2 × ATR via
  `StopAtrMult`). User-chosen: middle-band exit + ATR stop.
- **ConditionStillTrue** (pyramiding gate): close still in the trend zone
  for the basket direction.
- `Id()` = exact string `"boll_stochrsi_v1"`.

## 4. Integration

- Registered in EA `OnInit` after `halftrend_ema_v1`; `ActiveStrategy`
  input selects which trades. Shadow logging, per-strategy stats, remote
  switching (once the UI ships) all work unchanged.
- No service or db changes required.
- The source's profit claims (2.62 % / 5 % examples, "trader's paradise")
  are treated as unverified marketing; the shadow log measures the real
  hit-rate on XAU M15.

## 5. Testing

- Compiles clean in MetaEditor (manual, Windows).
- StochRSI faithfulness: spot-check %K/%D values against TradingView's
  Stoch RSI (14,14,3,3) on the same XAUUSD M15 bars.
- Strategy Tester backtest before any AUTO use; squeeze/expansion inputs
  calibrated there.
- Warm-up replay on first Evaluate (same pattern as HalfTrendEma,
  including stale-entry suppression at warm-up end: if entry conditions
  already hold after replay, do not fire — wait for a fresh cross).
