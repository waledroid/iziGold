# quickflip_ny_v1 — a second, independent trading strategy

Date: 2026-08-20
Status: approved (brainstorm, this session)

## Problem

`halftrend_ema_v1` is trend-following and bleeds in zigzag markets — the M5
replay's worst chop quarter lost $4,255.64 before the M15 agreement gate, and
$1,723.72 after it. The gate reduces the damage; it does not produce profit in
chop.

The owner asked to adapt the "Quick Flip Scalper" manipulation-candle concept
(opening-range sweep, then reversal) as a counterweight. It is **mean
reversion**, so it should earn where HalfTrend suffers.

## Evidence (spike, 2026-08-20, `bars_max.json`, 17 months of M5)

The published rules do NOT transfer as written:

- **The 25%-of-daily-ATR qualifier is inapplicable to gold.** It presumes an
  overnight session gap. XAUUSD trades ~23h; its opening-range/daily-ATR ratio
  is **median 7.1%, p90 12.8%**. The 25% rule fires on 1.4–5.1% of days (2–14
  samples over 17 months) — unmeasurable.
- **London open (10:00 server) is negative**: −$0.77/oz over 251 trades.

What does hold, at the **NY open (16:30 server)**, entry = a bar closing back
inside the box after a sweep, stop = sweep extreme, target = far side:

| set | n | win% | expectancy |
|---|---|---|---|
| all days | 248 | 52.8% | +$0.41/oz |
| range ≥ 10% daily ATR | 63 | 54.0% | +$1.57/oz |
| H1 (older half), all | 124 | 50.8% | +$0.12/oz |
| H2 (newer half), all | 124 | 54.8% | +$0.70/oz |
| H1, range ≥10% | 37 | 51.4% | +$0.70/oz |
| H2, range ≥10% | 26 | 57.7% | +$2.80/oz |

**Positive in both halves and both directions** (green-open→short +$0.66/oz,
red-open→long +$0.12/oz). Nothing in the HalfTrend family achieved that.

Scale honestly: ~1 trade/week at the 10% threshold, ~$60/month at 0.1 lot.
A real edge, a small one.

## What we build

`quickflip_ny_v1`, a second strategy that trades **its own positions, at the
same time as HalfTrend, without either affecting the other**.

Rules — deliberately the rules that were MEASURED, not the ones published:

1. At `QuickFlipHour:QuickFlipMinute` (default **16:30 server**), box the first
   M15 candle: high wick to low wick.
2. Qualify if `range >= QuickFlipAtrPct × daily ATR(14)` (default **10%**, not
   25% — gold's distribution, measured above).
3. Green opening candle → wait for a sweep **above** the box; red → **below**.
4. Entry when an M5 bar **closes back inside** the box. **Not** a hammer or
   engulfing pattern: those are untested here, and the measured expectancy
   belongs to this trigger. Candle-pattern filtering is a later, separately
   measured question.
5. Stop = the sweep extreme. Target = the far side of the box.
6. `QuickFlipWindowMin` (default **90**) minutes from the box close; after that
   the setup expires unfired.
7. One trade per server day, maximum.

## Independence — the actual work

### Separate magic numbers

Today ONE `MagicNumber` identifies every position, and `CTradeManager` treats
everything carrying it as one basket. A QuickFlip position under that number
would be pyramided into, re-stopped, and **closed by HalfTrend's next reversal**
— the opposite of independence.

So: HalfTrend keeps `MagicNumber`; QuickFlip trades under
`MagicNumber + QuickFlipMagicOffset` (default **+1**), with its **own**
`CTradeManager` instance.

Consequences, all intended:
- HalfTrend trades whether or not QuickFlip traded, and vice versa.
- Both may hold positions simultaneously, including in opposite directions.
- Neither manager can see, modify or close the other's positions.

### Two explicit lanes, NOT an N-strategy generalization

The registry stays as it is (one `Active()`). We add ONE second trading lane,
gated by `QuickFlipEnabled` (default **true**). Generalizing the EA to N
trading strategies would be a far larger change to the riskiest file in the
system, for a need we do not have. YAGNI.

### The rails MUST see both magics (hard requirement)

`RiskManager.mqh:127` counts the daily loss brake from deals matching a single
`m_magic`. Left alone, **QuickFlip's losses would be invisible to the 3% brake**
— an account-level protection silently covering half the system, discovered on
precisely the day it matters.

`RiskManager` must therefore accept the SET of magics the EA trades and account
for all of them in:
- the daily realized-loss brake (`MaxDailyLossPct`),
- drawdown / kill-switch equity accounting,
- daily exposure minutes (`MaxDailyExposureMin`).

Rails stay **shared and account-level** by design. Per-strategy rails would let
two strategies lose 3% each for a 6% day. That is a bug, not independence.

### Risk sizing

Owner decision: **1% per trade for each strategy**, equal. Worst case 2% at risk
concurrently, and the 3% daily brake is reached roughly twice as fast. Recorded
as accepted.

### Attribution

Everything that currently filters `DEAL_MAGIC == MagicNumber` must handle both
and attribute to the OWNING strategy id — `XauAssistant.mq5` lines ~314, ~353,
~393, ~734, `RecoverFromPositions` (~509), and the close report at ~458, which
today posts the literal `ActiveStrategy` string. `/trade-event` and `/analyze`
already carry `strategy_id`; SQLite already tags per strategy, so per-strategy
stats come free once attribution is right.

Chart drawing (`CTradeBoxes`) is per-magic; QuickFlip needs its own instance or
per-magic keys, so boxes do not cross-label.

## Inputs

| input | default |
|---|---|
| `QuickFlipEnabled` | true |
| `QuickFlipMagicOffset` | 1 |
| `QuickFlipHour` / `QuickFlipMinute` | 16 / 30 (server) |
| `QuickFlipAtrPct` | 10.0 |
| `QuickFlipWindowMin` | 90 |
| `QuickFlipRiskPct` | 1.0 |

## Non-goals

- No hammer/engulfing detection (unmeasured — see rule 4).
- No London/Asia sessions (measured negative / thin).
- **Not added to the Python replay engine.** That engine replays HalfTrend's
  money rules; teaching it a second strategy is a separate, larger job. The
  offline evidence is `scripts/quickflip_probe.py` (this spike, promoted from
  scratch to a reproducible tool); the live evidence is the per-strategy SQLite
  log.
- No change to HalfTrend's rules, sizing, or exits.

## Testing

- MQL5 cannot be unit-tested here: verify by careful reading, then MetaEditor
  CLI compile gated at **0 errors, 0 warnings**, and state that the owner must
  re-attach or re-drag the EA for new inputs to take effect.
- `scripts/quickflip_probe.py` promoted with its H1/H2 split as a regression
  check on the numbers in this spec.
- Service-side: tests that a second `strategy_id` flows through `/trade-event`
  and `/analyze` and is tagged correctly in SQLite, and that per-strategy stats
  separate the two.
- A test that the daily-loss brake accounts for BOTH magics — the defect this
  spec exists to prevent.

## Risks

- **Biggest: the rails silently covering one lane.** Mitigated by the explicit
  requirement above plus its own test.
- Two concurrent positions double concurrent exposure (accepted).
- 63 trades at the 10% threshold is a modest sample; both halves positive is
  what earns it a live seat rather than a shadow one.
- The edge is ~$60/month at 0.1 lot. If it costs more attention than that, it
  is not worth keeping — revisit after a month of live logs.
