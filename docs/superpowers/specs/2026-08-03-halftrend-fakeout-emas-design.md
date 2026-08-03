# HalfTrend fake-out filter audit + multi-EMA chart lines

**Date:** 2026-08-03
**Goal:** confirm the halftrend_ema_v1 strategy enforces Ife's dual-confirmation
fake-out rules, and show all EMAs as colored lines on the MT5 chart.

## A. Fake-out filter — audit result: already implemented, no changes

Ife's rules (source video) mapped to the code:

| Rule | Implementation |
|---|---|
| An arrow alone is a failed confirmation | Signal fires only after `ConfirmCloses` (EA input, default 2) consecutive closes beyond the 55 EMA (`HalfTrendEma.mqh` Evaluate) |
| One entry per trend, no re-chasing | `m_fired` re-arms only on a HalfTrend flip |
| Market shakes / conflicting arrows | A flip resets the consecutive-close count; an unconfirmed flip emits no signal |
| Stay in through bull traps | Basket closes only on profit target, SL, kill switch, or a *confirmed* opposite signal — `ConditionStillTrue` only gates pyramiding, never exits |
| Accept the ~10% losing signals | Stop at the wick extreme since flip; no martingale |

`ConfirmCloses` stays an input so the user can tighten 2 → 3 without code changes.

## B. Chart EMAs (the only code change)

Extend `CHalfTrendEmaStrategy`'s existing paint path (`DrawSeg` segments,
warm-up backfill, 500-bar rolling window — all already working):

- **55 EMA**: recolor gold → **LimeGreen**, width 2 (the trading EMA, matches
  the video's green line).
- **Context EMAs**, width 1, zero effect on signals: **9 = Orange**,
  **21 = Red**, **200 = White**.
- Three new `iMA` handles created in the constructor; per-EMA object prefixes
  (`xau_ema9_`, `xau_ema21_`, `xau_ema200_`; 55 keeps `xau_ema_`) so rolling
  cleanup and `ClearPaint()` work per line.
- `DrawSeg` gains a `width` parameter (replaces the prefix-based width hack).
- `EnablePaint(true)` resets the new per-EMA previous-value anchors, same as
  the existing ones.
- Lengths/colors hardcoded in the strategy (YAGNI); only `EmaLength` (55)
  remains an EA input because it drives signals.

Scope: painted by halftrend_ema_v1 only (the active strategy);
`boll_stochrsi_v1` painting stays a no-op per the 2026-08-03 chart-visuals
spec.

## Verification

MQL5 cannot be compiled from WSL — user compiles in MetaEditor (expect
0 errors) and re-copies `Include/XauAssistant/Strategies/HalfTrendEma.mqh`
into the MT5 data folder. No Python-side changes; test suite untouched.
