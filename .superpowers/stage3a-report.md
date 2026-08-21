# Stage 3a — widen RiskManager's daily-loss brake to a magic set

Branch: `feat/multi-magic-rails`. File touched: `mt5/Include/XauAssistant/RiskManager.mqh` only.

## What changed

- `long m_magic` → `long m_magics[4]` + `int m_magicCount` (a fixed-size slot
  array — MQL5 has no set type; 4 slots is ample headroom for a second lane).
- `Init(...)` keeps its existing single-`magic` parameter (call site in
  `XauAssistant.mq5` unchanged) but now seeds the set via `AddMagic(magic)`
  instead of a direct assignment.
- New `void AddMagic(long m)`: ignores an already-registered magic
  (duplicate → no-op) and ignores a call once all 4 slots are used (no-op,
  never writes past the array). Adding a second lane later is exactly
  `g_risk.AddMagic(QuickFlipMagic);` after `Init()` — nothing else changes.
- New `bool HasMagic(long m)`: linear scan of the registered set.
- `TodayRealized()`'s deal filter changed from
  `if(HistoryDealGetInteger(tk, DEAL_MAGIC) != m_magic) continue;`
  to
  `if(!HasMagic(HistoryDealGetInteger(tk, DEAL_MAGIC))) continue;`
- Two comments updated to describe "symbol + any registered magic" instead
  of "symbol+magic", and a comment added above the array documenting the
  bit-identical intent for the single-magic case.

No other line in the file changed. No input default changed. `TradeManager.mqh`
and `TradeBoxes.mqh` untouched, as instructed.

## Equivalence walkthrough (single magic in the set)

Old predicate, deal `tk` is skipped (does not count toward `realized`) when:

    HistoryDealGetInteger(tk, DEAL_MAGIC) != m_magic

New predicate, deal `tk` is skipped when:

    !HasMagic(HistoryDealGetInteger(tk, DEAL_MAGIC))

With exactly one magic registered (`Init(..., magic, ...)` calls
`AddMagic(magic)` once, `m_magicCount == 1`, `m_magics[0] == magic`):

`HasMagic(x)` iterates `i = 0` only, tests `m_magics[0] == x`, i.e.
`magic == x`, and returns that boolean (loop has one iteration, no early
exit needed before the natural end). So:

    HasMagic(x)  ==  (x == magic)
    !HasMagic(x) ==  (x != magic)

Substituting `x = HistoryDealGetInteger(tk, DEAL_MAGIC)`:

    !HasMagic(HistoryDealGetInteger(tk, DEAL_MAGIC))
      == (HistoryDealGetInteger(tk, DEAL_MAGIC) != magic)

which is exactly the old skip condition (`m_magic` and `magic` are the same
value — `Init`'s parameter, previously stored directly in `m_magic`, is now
stored as `m_magics[0]` via `AddMagic`). The two predicates are pointwise
identical over every possible `DEAL_MAGIC` value, so every deal that counted
before still counts, every deal that was skipped before is still skipped,
`realized` accumulates identically, and every function built on
`TodayRealized()` (`DailyLossBreached`, `DailyLossUsedPct`,
`DailyLossUsedUsd`, `DailyLossThresholdUsd`, `ResetDailyBrake`'s base
snapshot, the awareness/latch messages derived from them) is unchanged.
**Verdict: provably equivalent, not merely asserted equivalent.**

The only added behavior is `AddMagic` for a *second* value, which is never
called unless something explicitly calls it — nothing in this commit calls
it with a second value, so live behavior today is unaffected either way.

## Audit of other single-magic assumptions in the file

Went through every read of `AccountInfo*`/`GlobalVariable*`/`Position*` in
the file, not just line 127:

- **High-water mark / drawdown (`OnBarUpdate`, `DrawdownPct`,
  `HighWaterMark`)** — computed from `AccountInfoDouble(ACCOUNT_EQUITY)`,
  the whole-account equity number the broker reports. This is **not**
  magic-filtered at all, by construction — equity is an account-level
  number. It already covers every lane trading the account (QuickFlip's
  losses show up in equity the instant they're realized/floating), so no
  widening was needed here — it was never magic-scoped in the first place.
- **Exposure minutes (`OnBarUpdate`'s `ExpoKey()` accumulator,
  `ExposureMinutesUsed`)** — gated on `PositionsTotal() > 0`.
  `PositionsTotal()` is MT5's whole-account open-position count, not
  filtered by symbol or magic either. So this, too, already counts minutes
  with *any* position open on the account, including a future QuickFlip
  position — it was already "both lanes" before this change, just not for
  the reason one might guess (it's not filtered at all, rather than being
  correctly filtered).
- **Kill switch (`KillSwitchTripped`, `OnBarUpdate`'s trip check)** — driven
  off the same account-equity HWM above; same conclusion.
- **News blackout, ADX/trend gate, spread gate, trading-window gate** — none
  reference `m_magic`; these are account/symbol/market conditions, not
  per-deal, so they were never lane-scoped and need no change.
- **`ResetDailyBrake()` / `BrakeBase()`** — `BrakeBase()` stores a snapshot
  of `TodayRealized()` at reset time; since `TodayRealized()` now unions all
  registered magics, a brake reset taken after a second lane is registered
  correctly snapshots both lanes' realized P/L together. No separate change
  needed — it inherits the fix by calling `TodayRealized()`.

Net finding: **line 127's `TodayRealized()` deal scan was the only place in
this file with a real single-magic blind spot.** Everything else in
`RiskManager.mqh` is already account-wide (equity- or position-count based)
and was already correctly covering every lane, for a reason unrelated to
magic numbers — it simply never looked at magic to begin with.

Not fixed here (out of scope — file discipline for this task is
`RiskManager.mqh` only, and `TradeManager.mqh`/`TradeBoxes.mqh` are
explicitly excluded): `TradeManager.mqh`, `Reconciler.mqh`, and
`UiApi.mqh` all have their own single-`m_magic` fields used to filter
`PositionGetInteger(POSITION_MAGIC)`/`HistoryDealGetInteger(...DEAL_MAGIC)`.
Those are the "one INSTANCE per lane" pieces the task description already
flags as a later, different change — noting them here only so Stage 3b
doesn't have to rediscover them.

## Compile

Copied `mt5/Include/XauAssistant/RiskManager.mqh` to the MT5 data folder
(`.../MQL5/Include/XauAssistant/RiskManager.mqh`, verified identical via
`diff`), then compiled `Experts/XauAssistant.mq5`:

    Result: 0 errors, 0 warnings, 3501 ms elapsed, cpu='X64 Regular'

## Python suite

`cd service && .venv/bin/python -m pytest -q` → `555 passed, 1 deselected,
3 warnings` — matches the stated baseline exactly, unaffected by an
`mt5/`-only change as expected.
