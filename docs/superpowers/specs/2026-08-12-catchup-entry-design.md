# Guarded catch-up entry — take a missed signal after an outage if it's still valid

**Date:** 2026-08-12 · **Status:** user-approved (picker: "Guarded auto catch-up")

## Problem

On restart, `halftrend_ema_v1`'s warm-up deliberately suppresses an entry
whose flip+confirm happened during the gap (`m_fired = true` in the
`m_lastProcessed == 0` branch, `HalfTrendEma.mqh` ~line 183) — missed means
missed. The owner wants the system to take that entry **iff the trade
thesis is still intact at restart**, and skip it otherwise.

## Design (EA-only, `HalfTrendEma.mqh` + inputs; shadows unaffected)

**New EA inputs** (wired into the strategy constructor):

- `CatchupEnabled = true`
- `CatchupMaxAgeBars = 12` (signal ≤ 12 trading-TF bars old — 1 h on M5)
- `CatchupMaxChaseATR = 1.0` (price may not have run more than 1×ATR(14)
  beyond the signal bar's close in the trade direction)

**Warm-up change:** while replaying history, record where the CURRENT
trend's entry would have fired: the first bar (oldest→newest) at which the
consec-close counter reached `m_confirm` for the current trend — store its
bar time, close price, and shift. After warm-up, instead of blanket
`m_fired = true` suppression:

1. No qualifying confirm during warm-up for the current trend → behave as
   today (nothing to catch up).
2. Qualifying confirm found → evaluate the guards **on current data**:
   - `CatchupEnabled` true;
   - age: signal bar ≤ `CatchupMaxAgeBars` bars before the newest closed
     bar;
   - thesis intact **now**: for SELL, current Bid below the current
     EMA-`EmaLength` value AND trend still down (trend is current by
     construction); mirror for BUY;
   - no chasing: for SELL, `signalClose − Bid ≤ CatchupMaxChaseATR ×
     ATR(14)` (price below the signal close by more than the cap = the
     move already left); mirror for BUY. Retracement against the
     direction is fine (better entry).
3. Guards pass → do NOT suppress: leave `m_fired = false` so the first
   `Evaluate()` after warm-up returns the entry signal **through the
   normal path** — same risk gates (`CanEnter`: window, exposure, daily
   loss, news, spread, ADX), same `StopPrice()` (current HalfTrend extreme
   ± `0.75×ATR` pad), same 1% sizing over the actual stop distance, same
   `/analyze` post and Telegram/trade-event flow. Because the EA's OnTick
   bar detector starts at `g_lastBar = 0`, this fires within seconds of
   restart. Log one line: `"catch-up entry: <DIR> confirmed <N> bars ago
   during downtime — guards passed"`.
4. Guards fail → suppress as today, plus one Print stating which guard
   failed (age / thesis / chase / disabled).

**Deliberate properties:**

- MANUAL mode: the caught-up signal becomes an ordinary proposal with
  buttons — no special casing.
- The stop is the CURRENT HalfTrend extreme (may be tighter than the
  original — reflects present structure; sizing adapts).
- Scope: `halftrend_ema_v1` only. `boll_stochrsi_v1` keeps plain
  suppression (shadow-only today; pattern documented in izi for when it
  matters).
- Every re-checked condition is live — this never enters on stale data;
  it enters on today's data when today's data still satisfies the rule.

## Testing

MQL5: MetaEditor CLI compile 0/0 + adversarial read (no unit tests
possible). Service suite as regression gate (untouched). Live validation
is observational: next genuine outage-spanning signal logs either the
catch-up line or the named failing guard.

## izi.md

Same branch: the three inputs, guard list, "fires within seconds of
restart through the normal gate path", MANUAL→proposal behavior, and the
BollStochRsi non-scope note.
