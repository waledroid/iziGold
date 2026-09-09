# QuickFlip replay — whole-branch review, fix wave report

Date: 2026-08-20
Branch: `feat/quickflip-strategy` (no branch created, nothing merged or pushed)
Suite: **520 passed, 1 deselected** (`cd service && .venv/bin/python -m pytest -q`)
— was 505 before this wave; 15 new tests. No golden was regenerated: all three
pins (`golden_trades.json`, `golden_trades_strict.json`,
`golden_trades_both.json`) pass unchanged, so no fix moved replay behaviour.

Commits, in order:

| sha | subject |
|---|---|
| `506bbd7` | fix(backtest): the valley counts QuickFlip, and its stop still beats its target |
| `cc40b2d` | fix(quickflip-probe): pin the twin's defaults, and disclose what it drops |
| `c983942` | test(backtest): pin --strategy's CLI default, and stop over-claiming leakage |
| `f129d7a` | test(backtest): pin the open-equity valley to both lanes |
| `ef80505` | fix(backtest): say which strategy ran, and stop reporting rows of zeros |
| `e112c05` | feat(backtest-report): the HTML page tells the two lanes apart |
| `5360f7d` | docs(izi): correct the QuickFlip evidence to the numbers that ship |

Verification runs (`--source bars_max.json --days 365 --balance 10000`):

| lane | trades | win% | net | account max dd | open-equity valley |
|---|---|---|---|---|---|
| `ht` | 579 | 38.2 | +3,255.92 | 2,674.01 | 2,686.25 |
| `qf` | 118 | 50.0 | +354.56 | 250.82 | **273.78** (was `0.00`) |
| `both` | 696 (578 ht + 118 qf) | 37.9 / 50.0 | +3,551.61 | 2,892.35 | **2,902.73** |

---

## H1 — `max open-equity valley` was a fabricated 0.00 on qf runs

**Fixed.** The mark lived inside the HalfTrend section, below
`if STRATEGY == "qf": continue`, and the equity it marked
(`eq = bal + basket_pl(px)`) never included QuickFlip's floating P/L.

One helper now owns it:

- `qf_pl(px)` — unrealized P/L of the open QuickFlip position (spread charged
  the same way `basket_pl` charges it).
- `mark_equity(px)` — `eq = bal + basket_pl + qf_pl`, called at the end of the
  bar in every mode, including immediately before the `qf` short-circuit so it
  sits at the same point in the bar as the HalfTrend path's mark.

**Evidence:** a 365-day `qf` run now prints
`max open-equity valley 273.78` across 118 trades (was `0.00`). `both` moved
from 2,892.35 (which was just the closed-balance drawdown) to **2,902.73**.
HalfTrend-only runs are byte-identical — `qf_pl()` returns 0.0 with no
QuickFlip position, and the goldens confirm it.

**Pinned** (`test_qf_lane.py`), and mutation-checked:
- `test_the_open_equity_valley_is_marked_in_a_qf_only_run` — on the frozen
  fixture QuickFlip's realized drawdown is exactly 0.00, so a positive valley
  can ONLY come from marking the open position. Dropping `qf_pl()` from the
  mark fails this test (verified).
- `test_the_valley_is_never_shallower_than_the_closed_drawdown` — all three
  modes.

One pre-existing nuance left alone: the 23:50 flatten path `continue`s before
the mark. It predates this branch, affects HalfTrend equally, and no QuickFlip
position can be open at 23:50 (its window closes by ~16:00).

## H2 — the twin-drift guard did not exist

**Fixed.** `test_quickflip_probe.py::test_probe_and_engine_pin_the_same_defaults`
asserts `WINDOW_MIN == QF_WINDOW_MIN`, `ATR_DAYS == QF_ATR_DAYS`,
`SPREAD_USD == SPREAD_USD`, and the probe's parser defaults
`(hour, minute) == (QF_HOUR, QF_MINUTE)`. `build_parser()` was extracted from
the probe's `main()` purely so the test can read those defaults without running
the probe.

**Mutation evidence:**

| mutation | before | after |
|---|---|---|
| probe `WINDOW_MIN = 90 -> 45` | 505 passed (green) | `FAILED test_probe_and_engine_pin_the_same_defaults` — `assert 45 == 90` |
| probe `--hour` default `13 -> 10` (WINDOW_MIN untouched) | 505 passed (green) | `FAILED test_probe_and_engine_pin_the_same_defaults` at the parser-default assertion |
| both restored | — | 7 passed in `test_quickflip_probe.py`, 520 in the full suite |

Both mutations were confirmed green against the pre-fix tests, which is the
finding: the probe pinned nothing at all.

## H3 — same-bar stop-before-target precedence was pinned by nothing

**Fixed.** The resolution is now a pure function, `qf_resolve(pos, bar)`,
returning `(price, reason)` or `None`, carrying the explanatory comment
HalfTrend's equivalent has ("stop beats target/lock/reversal in a bar") and
stating the pessimism is deliberate.

**Pinned** (`test_qf_signals.py`):
`test_stop_beats_target_when_one_bar_covers_both_short` /
`..._long` construct a bar whose high ≥ stop AND low ≤ tp and assert the STOP
wins; `test_the_stop_does_not_win_when_it_was_never_touched` stops a
"always answer stop" resolver from passing them;
`test_expiry_never_pre_empts_a_stop_on_the_same_bar` and
`test_an_untouched_position_before_expiry_resolves_to_nothing` cover the rest
of the order.

**Mutation evidence:** reversing the precedence to target-first inside
`qf_resolve()`:
- `2 failed, 11 passed` — exactly the two same-bar tests
  (`AssertionError` at `qf_resolve(...) == (2005.0, "qf stop")`).
- 365-day `qf` net moves **+354.56 → +427.66 (+20.6%)**, reproducing the
  reviewer's figure exactly.
- Restored: `13 passed`, net back to +354.56.

Root cause worth remembering, recorded in izi.md: the frozen fixture's only two
QuickFlip trades exit `qf expired` and `qf target`. **No `qf stop` trade is in
any golden**, though 48 of 118 real trades exit that way — a golden pin only
guards the paths its fixture happens to take.

## M1 — the probe overstates the lane ~1.9x

**Fixed, both halves.**

(a) The probe now discloses it. Its docstring carries a "WHAT THIS PROBE LEAVES
OUT" block, and `EXCLUSION_NOTE` prints under every table (single-session and
`--sweep`), naming the engine as the thing to quote for a decision.

(b) The evidence in `.claude/agents/izi.md` and the spec now carries the ENGINE
table. **I re-measured it rather than copying it**, and it reproduces exactly:

| gate | n | exp/oz | older half | newer half |
|---|---|---|---|---|
| none | 257 | +0.229 | −0.015 | +0.472 |
| 5% (shipped) | 177 | +0.246 | +0.189 | +0.302 |
| 10% | 43 | +0.979 | −0.583 | +2.470 |
| 15% | 9 | −2.532 | +2.675 | −6.698 |

Probe side, also re-measured: 5% gate = 165 rows, +$0.458/oz, +75.55 total;
1.9x the engine's +$0.246/oz on 177 trades; the 12 expired trades net −$52.27.

Both documents now state that the **10% → 5% decision gets stronger** on engine
numbers (10%'s older half flips negative; 5% is the only both-halves-positive
gate). izi.md's "a divergence between them is a bug" is removed and replaced
with "one divergence is BY DESIGN — do not fix it by making the probe trade
expiries."

Side effect worth noting: the spec's rules and input table still said
`QuickFlipAtrPct = 10.0` while the code shipped 5.0. Corrected to 5.0 with a
pointer at the engine evidence.

## M2 — `--strategy` invisible in the run header

**Fixed.** The header is now assembled as a parts list. It always names the
lane (`strategy qf (quickflip only)`); HalfTrend's parameters print only when
HalfTrend runs; QuickFlip's own line (`quickflip 13:30 server, box >= 5% daily
ATR, 90min window, 0.25% risk`) prints when QuickFlip runs. A `qf` header is
now three fields, not fifteen fields describing a lane that never traded.
`--strategy`'s CLI default is pinned by L1 below.

## M3 — rows of zeros from HalfTrend-only blocks

**Fixed.** Every HalfTrend-only block (entry-regime, ATR-spike, gate refusals,
chop, strict-window, min-stop floor, bias, hour table, S/R headroom,
confirm-mode) is guarded by `ht_trades`. A `qf` run's output is now the trade
list, the lane line, the account lines, sizing and the QuickFlip exposure line
— nothing that reads as a claim about a lane that did not run.

## M4 — two spec requirements that shipped short

(a) **Fixed.** `_lane_drawdown()` walks each lane's OWN realized P/L curve and
`_lane_stats` carries `max_dd`, printed per lane and in the JSON. Documented
(in code, in output, and in izi.md) as deliberately NOT summing to the
account's joint drawdown. 365-day figures: ht 2,674.01 alone / 2,990.58 inside
`both`; qf 250.82 alone / 283.53 inside `both`.

(b) **Fixed.** `scripts/backtest_report.py` contained the string "lane" zero
times. Now: the page title names the split; each trade row carries a Lane
column (QuickFlip coloured); QuickFlip entry markers are drawn in the lane
colour `#00e5ff` and labelled `QF#n`; QuickFlip exits keep their win/loss
colour (what the eye reads first) but change shape; QuickFlip trade boxes are
outlined in the lane colour; the legend carries the swatch; and the header
gains a per-lane breakdown (trades, win%, net, max dd). The headless Node smoke
test gains a lane scenario asserting all of it.

## M5 — `--tf M15 --strategy both` silently dropped the lane

**Fixed.** `qf_timeframe_warning(tf, strategy)` returns the warning; `main()`
prints it directly under the header:

```
WARNING: --strategy both asked for the QuickFlip lane, but QuickFlip boxes its
opening range from THREE M5 bars and this run is on M15. It will produce ZERO
QuickFlip setups -- this run measures HalfTrend alone.
```

For `--strategy qf` the tail reads "this run measures NOTHING."
`test_the_m15_warning_is_telling_the_truth` proves the claim rather than
assuming it: `qf_signals(resample(candles, 900)) == []` while the same fixture
on M5 is non-empty.

## M8 — QuickFlip's clamped entries invisible in the sizing report

**Fixed, reported per lane** rather than blended: a 0.25%-risk entry and a
1%-risk entry are not the same measurement, and folding QuickFlip into a
"target 1.00%" median would replace one distortion with another.

Two numbers print, because "clamped" and "ended at the minimum lot" are
different readings and the reviewer's 13.6% is the second:

```
sizing     3.4% of 118 entries clamped to the 0.01 minimum lot (quickflip lane)
           quickflip risk actually taken: median 0.23% p90 0.25%  (target 0.25%);
           16 of 118 entries (13.6%) ended at the 0.01 minimum lot
```

**3.4%** = risk sizing asked for LESS than one ounce and was overruled upward
(HalfTrend's existing definition of "clamped" — the over-risk case).
**13.6% (16 of 118)** = ended at the broker floor, which is the reviewer's
figure and includes entries where one ounce is genuinely what 0.25% bought.
Both are visible; neither can be quoted alone to mean the other.

## M9 — factual errors in izi.md

**All three fixed.**

(a) `ht` alone is **579** trades, not 578 (578 is the HalfTrend count inside
the `both` run). Stated explicitly so the next reader does not "correct" it
back.

(b) The cost of coexistence is **$58.87** and **bidirectional**: sum-of-parts
3,255.92 + 354.56 = 3,610.48 vs `both` 3,551.61. HalfTrend loses $9.55
(3,255.92 → 3,246.37); QuickFlip loses **$49.32** (354.56 → 305.24). Roughly
6x the claimed figure, and the lane that pays most is QuickFlip.

(c) `MaxDailyExposureMin` does not "stay deliberately per-lane" in the replay.
`EXPO_MIN` charges held-bar minutes and refuses entries **only in the HalfTrend
block**; QuickFlip is neither charged nor gated. izi.md now says what is
implemented: one budget on one lane, and that the replay cannot say what giving
QuickFlip a budget would cost.

## L1 / L2 / L3 / Task-3

- **L1 fixed.** `assert args.strategy == bt.STRATEGY` added to
  `test_cli_defaults_match_the_module_defaults`. Verified: setting the argparse
  default to `"ht"` now fails that test (it previously left all 505 green).
- **L2 fixed.** Dead `"legs_count": 1` removed.
- **L3 fixed.** `qf_signals()` now carries the reciprocal twin warning, names
  the pinning test, and states which divergence is intentional.
- **Task-3 fixed.** `test_ht_decisions_match_until_the_first_qf_close`'s
  docstring now makes the honest weaker claim. Measured on the fixture and
  written into it: the two QuickFlip setups open 2025-10-30 14:55 and
  2025-11-06 14:00, while the last HalfTrend trade compared exits 2025-10-30
  06:10 — **8.75 hours** before QuickFlip opens anything. So the test exercises
  leakage BEFORE QuickFlip trades, never a HalfTrend basket held across a live
  QuickFlip position. The docstring says not to stretch the fixture.

---

## Reported, deliberately not fixed

### M7 — QuickFlip has no minimum stop distance

**Not fixed, by instruction — made visible instead.** A floor would change
every measured result above and the value is an owner decision.

Largest QuickFlip position over 365 days at $10,000:

> **44 oz (0.44 lots) on 11-11 13:50 @ 4,143.94 = $182,333 notional, 18.2x the
> starting balance, on a stop distance of $0.57.**

(In the `both` run the same setup sizes 42 oz = $174,045, 17.4x — the shared
balance was lower at that moment.) Tightest stop this run **$0.57**; median
**$5.10**. Every run now prints the largest position, its notional, its
leverage against the starting balance, and the tightest/median stop distance,
followed by a plain statement that HalfTrend is protected by `MIN_STOP_ATR`
and its ATR-buffered stop while QuickFlip has neither. Recorded in izi.md and
the spec's Risks as an **open owner decision**.

### M6 — `--entry-mode fixed` and `--risk` do not reach QuickFlip

**Not fixed, by instruction** (it needs an owner decision on whether QuickFlip
should honour those flags). Confirmed directly: with `--entry-mode fixed` and
`--risk 5.0`, HalfTrend risk-sizes zero entries (all fixed lots) while
QuickFlip still risk-sizes its own at `QF_RISK_PCT = 0.25` (sizes 2 oz and 5 oz
on the fixture) — so a "pure fixed-lots study" silently contains one risk-sized
lane, and `--risk` moves only one of the two lanes. Recorded in izi.md under
"Known and NOT fixed".

### Left unmodelled, as instructed

The daily-loss brake, the kill switch and the news blackout remain unmodelled;
this wave only changed how things are disclosed. `bars_max.json` was used for
measurement and is not committed. `bars_slice.json` is untouched.
