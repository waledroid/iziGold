# Regime-gate study — should the rulebook refuse entries when the classifier says "range"?

Date: 2026-08-17. Analysis only; nothing in `mt5/` or `service/app/` changed.
Tool: `scripts/backtest.py --regime-gate {off,range,range-strict,highvol}` (harness that
imports the module and calls `run()` per window; identical to the CLI).
Data: `bars_max.json`, 99 999 M5 bars, 2025-03-19 12:00 -> 2026-08-17 13:20 (server time).
Params: live defaults (ADX gate 10, ConfirmCloses 1, EMA 55, 1 % risk, expo 360, stop pad 0.75 ATR,
target-exit scheme, $4 000 start balance for every window).

## Verdict (short)

**Do not implement a range gate.** Range-tagged entries win exactly as often as trend-tagged ones
(17 months: 36.3 % vs 36.1 %, p = 0.94; last 30 days: 45.3 % vs 42.1 %, p = 0.80). The classifier's
"range" label carries no information about whether the next trade wins. The gate would simply throw
away 55-70 % of all entries — including the winners that pay for the whipsaws — and its P/L effect flips
sign between eras (helps 2 of 3 sub-periods, hurts the most recent one and the last 30 days badly).
The `highvol` gate is not robust either (helps 17-mo total and sub-periods 1+3, hurts sub-period 2 and
the last 30 days by -$367).

## What the replay actually tests (thresholds — read this before quoting numbers)

Two different ADX numbers are in play and they are NOT the same thing:

| Where | ADX rule | Threshold | Purpose |
|---|---|---|---|
| EA `RiskManager.TrendOK()` / replay `ADX_MIN` | Wilder ADX(14) on the chart TF, closed bar | **>= 10** (`AdxTrendThreshold` input, "near-permissive") | entry gate; blocks only dead-flat tape |
| Service `app/regime.py::classify_regime` / replay `regime_at()` | ATR(14) percentile rank in last 100 values >= 0.80 -> `high_volatility`; else Wilder ADX(14) >= **25** -> `trend`; else `range` | **25** (function default; `main.py:440` calls it with defaults) | classifies the regime the service logs / shows on Telegram |

The replay uses the classifier's live default (25) because it calls `classify_regime()` with no override
on the same 300-closed-bar window the EA posts to `/analyze` (`REGIME_WINDOW = 300`, `AiApi.mqh`) — so
the "range" tags below are byte-for-byte what the service would have logged on those signals. The 10 gate
still runs first in every run (the replay only asks the classifier once the ADX>=10 gate has passed), so
"range" here means "ADX between 10 and 25 and not in the top-20 % ATR band". Note the classifier checks
high-vol FIRST: an ADX-30 bar with spiking ATR is `high_volatility`, not `trend` — which is why "trend"
is the smallest class in every window.

Gate variants: `range` = refuse "range" bars; `range-strict` = refuse "range" and "high_volatility"
(= the only range+highvol combo the tool offers, i.e. trend-only entries); `highvol` = refuse
"high_volatility" only. Adds, stops, target, lock, reversal and flatten are untouched (code: the gate
lives only in the entry block, `backtest.py` ~L413-429, and only sets `signal = None`).

## Exit behaviour check (gate must be entries-only)

Structural: the gate code is inside `if basket is None and signal:` and only nulls the entry signal.
Empirical: for every entry that exists in BOTH the off run and a gated run (same open bar + direction),
exit reason and exit price were compared. Last 30 days, off vs range: 43 identical, 4 differing. All 4
differences are sizing-path artefacts — the gated run reaches the same bar with a different balance
(different `cycle_bal`) so lot sizes and pyramid legs differ, which moves the 2 %-of-cycle profit-target
price by a few dollars (e.g. 07-28 11:50 SELL: off target @4032.25 legs [6,4] vs range target @4031.21
legs [6,4,2]). No exit RULE fires differently. Same pattern in every window (17 mo: 727 identical /
101 sizing-path). Confirmed: entries only.

## Results per window

Columns: net P/L on $4 000, trades, win %, max open-equity valley, max closed drawdown, avg winner,
avg loser, entries the gate refused (by tag). "Baseline by entry regime" = the off run's own trades
tagged with their entry regime = first-order opportunity cost of each gate (how many skipped entries
would have won / lost). Second-order effects (a skipped basket frees the exposure budget / the basket
slot for a later entry) are why the gated-run trade counts don't equal off minus refused.

### (a) 08-17 morning session (indicators warmed from 08-11 01:00; 1 253 bars, 25 baseline trades)
| gate | net P/L | trades | win% | max valley | max DD | avg win | avg loss | refused |
|---|---|---|---|---|---|---|---|---|
| off | +162.02 | 25 | 52.0 | 287.21 | 233.17 | +44.85 | -35.08 | 0 |
| range | +3.59 | 9 | 55.6 | 197.86 | 118.36 | +29.50 | -35.98 | 18 (range 18) |
| range-strict | -2.66 | 6 | 50.0 | 181.51 | 114.11 | +37.15 | -38.04 | 22 (range 19, highvol 3) |
| highvol | +169.55 | 25 | 52.0 | 293.78 | 246.29 | +45.43 | -35.08 | 3 (highvol 3) |

Baseline by entry regime: trend 5 (2W/3L, +30.28, 40 %), range 18 (9W/9L, +95.69, 50 %),
high_volatility 2 (2W/0L, +36.05).

The 08-17 day itself in the baseline (all five entries tagged **range**):
05:50 SELL -> reversal -20.79 | 06:55 BUY -> stop -37.08 | 07:55 SELL -> stop -35.49 |
08:45 BUY -> profit target +84.54 | 11:40 SELL -> open at data end -1.96. Day net **-10.78**.
The range gate skips ALL five: it saves the two whipsaws (~-$73 in replay, -$78 live) but also skips
the +84.54 winner three bars later. Net effect of the gate on 08-17: **+$10.78**, not +$78.

### (b) last 30 days (07-20 -> 08-17, 5 669 bars, 111 baseline trades)
| gate | net P/L | trades | win% | max valley | max DD | avg win | avg loss | refused |
|---|---|---|---|---|---|---|---|---|
| off | **+780.08** | 111 | 46.8 | 343.10 | 289.29 | +56.68 | -36.74 | 0 |
| range | +437.13 | 53 | 47.2 | 309.22 | 277.51 | +56.19 | -34.55 | 75 (range) |
| range-strict | -49.73 | 21 | 42.9 | 265.02 | 206.76 | +40.10 | -34.22 | 108 (range 76, highvol 32) |
| highvol | +413.56 | 93 | 44.1 | 401.69 | 359.79 | +54.61 | -35.10 | 31 (highvol) |

Baseline by entry regime:
| regime | trades | wins | losses | net | win% | avg win | avg loss |
|---|---|---|---|---|---|---|---|
| trend | 19 | 8 | 11 | -20.91 | 42.1 | +49.18 | -37.67 |
| range | 64 | 29 | 35 | **+449.91** | 45.3 | +61.07 | -37.75 |
| high_volatility | 28 | 15 | 13 | +351.08 | 53.6 | +52.19 | -33.22 |

The range gate would have skipped 29 winners and 35 losers worth **+$450 net** — more than half of the
month's profit — and lowered the valley by only $34. Trend-tagged entries were the WORST class this month.

### (c) full 17 months (99 999 bars, 2 157 baseline trades)
| gate | net P/L | trades | win% | max valley | max DD | avg win | avg loss | refused |
|---|---|---|---|---|---|---|---|---|
| off | -2296.56 | 2157 | 36.4 | 3827.11 | 3787.90 | +36.59 | -22.61 | 0 |
| range | -2376.25 | 892 | 35.4 | 3296.31 | 3251.75 | +32.17 | -21.77 | 1517 (range) |
| range-strict | -772.85 | 279 | 33.3 | 1596.64 | 1566.93 | +48.23 | -28.27 | 2148 (range 1531, highvol 617) |
| highvol | -1230.82 | 1747 | 35.0 | 2938.98 | 2875.60 | +47.88 | -26.90 | 600 (highvol) |

Baseline by entry regime:
| regime | trades | wins | losses | net | win% | avg win | avg loss |
|---|---|---|---|---|---|---|---|
| trend | 255 | 92 | 163 | -290.81 | 36.1 | +32.54 | -20.15 |
| range | 1329 | 483 | 846 | -597.51 | **36.3** | +37.87 | -22.33 |
| high_volatility | 573 | 210 | 363 | -1408.25 | 36.6 | +35.41 | -24.37 |

Two-proportion z-test, range vs trend win rate: z = 0.08, **p = 0.94**; range vs non-range: p = 0.95.
The label does not separate winners from losers. (Reminder: the whole 17-month baseline is negative
because the first 11 months pre-date the current rulebook tuning; the sub-periods below are the
robustness view.)

### (d) three equal sub-periods (33 334 bars each)
| period | gate | net P/L | trades | win% | max valley | refused |
|---|---|---|---|---|---|---|
| 1: 2025-03-19 -> 2025-09-05 | off | -2011.97 | 732 | 34.6 | 2917 | 0 |
| | range | -1743.92 (+268) | 319 | 33.9 | 2349 | 498 |
| | range-strict | -547.30 (+1465) | 82 | 32.9 | 1019 | 740 |
| | highvol | -819.83 (+1192) | 568 | 35.0 | 1597 | 235 |
| 2: 2025-09-05 -> 2026-02-26 | off | -1198.56 | 723 | 32.8 | 3329 | 0 |
| | range | -274.76 (+924) | 278 | 32.4 | 838 | 533 |
| | range-strict | -384.36 (+814) | 94 | 31.9 | 554 | 722 |
| | highvol | -1700.73 (**-502**) | 611 | 30.6 | 2786 | 180 |
| 3: 2026-02-26 -> 2026-08-17 | off | +1481.16 | 673 | 38.2 | 1506 | 0 |
| | range | -93.51 (**-1575**) | 292 | 36.0 | 1411 | 476 |
| | range-strict | +634.80 (-846) | 103 | 38.8 | 434 | 686 |
| | highvol | +2370.67 (+890) | 566 | 39.9 | 918 | 188 |

Baseline win % by entry regime per sub-period (trend / range / highvol):
P1 33.3 / 34.8 / 34.5 — P2 25.3 / 33.2 / 35.3 — P3 39.6 / 38.8 / 36.0.
Range-tagged net per sub-period: -481, -1068, **+1254**. In the one era where the strategy actually
makes money (P3, the current rulebook era), range entries supplied 85 % of the profit.

Robustness read: `range` gate: 2 of 3 sub-periods better, the most recent and most relevant one
-$1 575 worse. `highvol`: 2 of 3 better, sub-period 2 -$502 and last-30d -$367 worse. `range-strict`:
2 of 3 better, but only by amputating 90 % of trades; the last-30d result is a loss (-$50 vs +$780).
None of the three variants improves both halves of the recent data (P3 and last 30 d) — this is the
signature of a regime-tuned filter, not a structural edge.

## The classifier itself (last 30 days, per bar, 300-bar windows)

| set | trend | range | high_volatility |
|---|---|---|---|
| all bars (5 369 classified) | 32.1 % | 43.5 % | 24.4 % |
| 04-23 trading window | 30.4 % | 41.4 % | 28.1 % |
| window AND replay ADX(14) >= 10 (i.e. bars where the EA may enter) | 30.8 % | 40.7 % | 28.5 % |

So "range" is simply the plurality state of the tape (~41 % of enterable bars, 58 % of baseline entries)
— and its trades win 45 % vs 42 % for "trend". The 08-17 whipsaws being range-tagged is what you'd
expect from a label that covers most bars, not evidence the label predicts whipsaws.

## Caveats

- Replay simplifications apply (close-only fills, no daily-loss brake, no news blackout, flat spread);
  gate comparisons are all within the same replay so they cancel, but absolute dollars are approximate.
- Sizing-path divergence means gated and off runs are not trade-for-trade comparable after the first
  skip; the "baseline by entry regime" tables are the clean first-order opportunity-cost measure.
- The classifier's ADX 25 is a fixed default; a different threshold might separate better, but the
  17-month win rates are so flat across all three classes (36.1 / 36.3 / 36.6) that no threshold on
  these two inputs looks promising without a much stronger prior.

## If it were implemented anyway (for the record)

Not recommended. If the owner still wants an experiment, the least-bad variant on the recent data is
`highvol` (P3 +$890, but last 30 d -$367) — and it should be an EA-side port
(`RiskManager.CanEnter` refusing with literal "regime: high_volatility" using an MQL5 port of the ATR
percentile rank over 100 bars), NOT a service-side veto via `/analyze`: in AUTO the EA executes before
the AI call (design rule 2), and a service veto would violate fail-open (rule 3) — service down means the
gate silently disappears. Any port would need the classifier's 300-bar window semantics reproduced
exactly or the tags will drift from what the service logs.
