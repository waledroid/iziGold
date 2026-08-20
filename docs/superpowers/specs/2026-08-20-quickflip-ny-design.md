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

## Evidence (spike, 2026-08-20) — CORRECTED, and weaker than first reported

**A time-convention bug invalidated the first two spikes.** `backtest.py`'s
`hhmm()` reads candle `t` as SERVER wall-clock directly (proof: server hour 00
contains zero bars — it is the daily market break). Spikes 1-2 added a further
+3h shift, so every session label was wrong by three hours: what was reported
as "the NY open" was really server 13:30. The measurements were real; the
labels were not.

Corrected, sweeping **every half-hour** rather than guessing sessions
(`bars_max.json`, 17 months of M5, entry = close back inside the box after a
sweep, stop = sweep extreme, target = far side, 90-minute window):

| server | n | win% | expectancy | older half | newer half |
|---|---|---|---|---|---|
| 10:00 (true London open) | 224 | 44.6% | +$0.63/oz | **−0.68** | +1.93 |
| 02:30 | 253 | 49.4% | +$0.49/oz | +0.03 | +0.94 |
| 18:30 | 189 | 46.0% | +$0.40/oz | +0.13 | +0.67 |
| 16:30 (true NY open) | 182 | 46.7% | +$0.37/oz | +0.93 | **−0.20** |
| **13:30** | **246** | **52.4%** | **+$0.36/oz** | +0.06 | +0.67 |
| 17:00 (worst) | 167 | 34.7% | −$1.74/oz | | |

Also true, and stated plainly because it bounds what this can claim:

- **The published 25%-of-daily-ATR qualifier is inapplicable to gold.** It
  presumes an overnight session gap; XAUUSD's opening-range/daily-ATR ratio is
  median ~7%, so the rule fires on 1–5% of days. Adapted threshold: **10%**.
- **46 half-hours were searched.** Some looking good is what noise produces.
  The slots that pass a split test pass it by +$0.03–$0.13/oz in the older
  half — statistically indistinguishable from zero.
- The pattern is the one seen across every study this week: **the newer half is
  good, the older half is not.** That is consistent with a market regime rather
  than a durable edge.

**Status: this is a paid experiment, not a validated edge**, entered knowingly
by the owner after the correction above was presented. Size is reduced
accordingly (see Risk sizing). Review after ~2 months of live logs; if the
live log does not reproduce a positive expectancy, remove it.

Default session: **13:30 server** — the best combination of win rate (52.4%),
sample size (246) and both-halves-positive. `02:30` and `18:30` also passed;
`10:00` had the best headline expectancy but LOSES in the older half.

## What we build

`quickflip_ny_v1`, a second strategy that trades **its own positions, at the
same time as HalfTrend, without either affecting the other**.

Rules — deliberately the rules that were MEASURED, not the ones published:

1. At `QuickFlipHour:QuickFlipMinute` (default **13:30 server**), box the first
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

Owner decision, taken AFTER the corrected evidence above: **0.25% per trade**
for QuickFlip while HalfTrend keeps 1%. Worst case 1.25% at risk concurrently.
The reduced size is the point — it buys real fills on a weak hypothesis for a
quarter of the exposure, rather than betting full size on a 246-trade sample
whose older half is flat.

### Attribution

Everything that currently filters `DEAL_MAGIC == MagicNumber` must handle both
and attribute to the OWNING strategy id — `XauAssistant.mq5` lines ~314, ~353,
~393, ~734, `RecoverFromPositions` (~509), and the close report at ~458, which
today posts the literal `ActiveStrategy` string. `/trade-event` and `/analyze`
already carry `strategy_id`; SQLite already tags per strategy, so per-strategy
stats come free once attribution is right.

Chart drawing (`CTradeBoxes`) is per-magic; QuickFlip needs its own instance or
per-magic keys, so boxes do not cross-label.

## The replay must model BOTH lanes (owner: "go for it in both live and backtest")

A replay of QuickFlip alone would answer the wrong question. The point of this
work is whether the two strategies **coexist profitably** — sharing one
balance, one set of rails, and one exposure budget — so the replay gains a
second lane mirroring the EA:

- `--strategy ht|qf|both` (default **both**). `ht` reproduces every study
  published before today; `qf` isolates the new strategy; `both` is what now
  runs live.
- Both lanes trade against **one balance**, in chronological order, so their
  P/L compounds together exactly as the account experiences it.
- Each lane owns its own positions and cannot close the other's — the replay
  mirror of the magic-number split.
- The rails are evaluated **across both lanes**: the daily-loss brake, the
  drawdown/kill-switch accounting and the exposure budget see combined
  realized and open P/L. (The brake is still not modelled — see the standing
  caveats — but exposure and sizing are, and both must be lane-aware.)
- Reporting gains a per-lane breakdown (trades, win%, net, max DD) **and** a
  combined equity curve, plus a correlation read: how often both lanes were in
  the market at once, and what combined exposure peaked at.
- `--web`/`--json` carry a `lane` on every trade so the report can colour and
  filter them.

The golden pins must be extended: the existing pins keep `--strategy ht` so
they stay like-for-like change detectors, and a new pin captures `both`.

## Inputs

| input | default |
|---|---|
| `QuickFlipEnabled` | true |
| `QuickFlipMagicOffset` | 1 |
| `QuickFlipHour` / `QuickFlipMinute` | 13 / 30 (server) |
| `QuickFlipAtrPct` | 10.0 |
| `QuickFlipWindowMin` | 90 |
| `QuickFlipRiskPct` | 0.25 |

## Non-goals

- No hammer/engulfing detection (unmeasured — see rule 4).
- No London/Asia sessions (measured negative / thin).
- No third strategy, and no N-lane generalization of either the EA or the
  replay. Two explicit lanes only.
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
