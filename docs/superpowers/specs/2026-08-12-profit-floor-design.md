# Profit floor — lock the target as a stop instead of exiting (backtest first)

**Date:** 2026-08-12 · **Status:** phase 1 (backtest) approved; EA
implementation follows the numbers. Owner preference going in: variant A.

## Concept (user-approved direction)

Today `ProfitTargetPct=2.0` CLOSES the basket at +2% of cycle balance.
Proposed: at target, convert the target into a **floor** — move the
basket's shared stop to the price where the basket is worth ~the target,
and let the trade run under the unchanged exit rules (reversal flip,
ladder, profit lock 50%-of-peak). Floor = max(locked target, profit lock)
as price advances.

Two arming variants:

- **A — lock a bit less, immediately** (owner's lean): at target, stop →
  price where basket P/L = target − 0.25×ATR(14)-worth. Guaranteed floor
  from second one, slightly under full target.
- **B — full target, slightly later**: at target + 0.25×ATR-worth of
  profit, stop → price where basket P/L = full target. Locks 100% but may
  never arm if price stalls at target.

**Adds freeze after lock (both variants)** — owner-identified erosion: an
add entered beyond the locked stop price closes at a loss when the floor
stop is hit, paying that loss out of the banked floor. Once armed: no new
pyramid adds, and the floor stop only ever ratchets tighter (ladder may
tighten it further, never loosen).

## Phase 1 — backtest (this phase)

Extend `scripts/backtest.py` (already replays entries, ladder stops,
shrinking adds, profit lock, profit target, reversal exits on real broker
bars from `scripts/dump_bars.py`) with an `--exit-scheme` option:

- `target-exit` (baseline, current behavior)
- `floor-a` (variant A: immediate lock at target − 0.25×ATR, adds frozen)
- `floor-b` (variant B: lock full target at target + 0.25×ATR, adds frozen)
- `floor-a-adds` (variant A with adds LEFT ON — quantifies the erosion)

Floor mechanics in the replay: solve the basket price level where
Σ lots_i × directional(entry_i − P) × contract = floor amount; shared stop
= max(existing ladder/stop, floor level) directionally (pure ratchet);
profit lock unchanged and evaluated alongside (higher floor wins).

Run all four schemes over the maximum available M5 history from the
terminal (dump fresh bars; report the actual window), same params as live
(risk 1%, ADX 10, exposure 360, window 4–23). Report per scheme: net P/L,
trade count, win rate, max drawdown/valley, average and max winner, and
the per-trade delta table for trades where schemes diverge.

Backtester caveats stay as documented: no daily-loss brake, no news
blackout, no kill switch modeled.

## Phase 2 — EA implementation (after the numbers; separate plan)

Winning variant lands in `TradeManager.mqh`: the profit-target branch arms
the floor instead of `CloseAll`; an armed flag (per-symbol MT5 global, so
restarts keep the frozen-adds state) blocks the add path; `RatchetBasketStop`
respects the floor as a minimum. Full pipeline with compile gate + izi.md.

## Decision rule

Owner picks from the numbers. If floor variants do not beat `target-exit`
on net P/L without materially worsening the valley, keep the current exit.
