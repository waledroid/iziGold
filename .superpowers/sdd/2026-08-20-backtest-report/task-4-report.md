# Task 4 report — balance validation and the sizing-clamp measurement

## Summary

`scripts/backtest.py`'s sizing is `oz = max(MIN_OZ, int(risk / dist))`: when
1% of balance can't cover one ounce at the stop distance, the engine takes
the 0.01 minimum lot instead of skipping the trade, silently over-risking.
This task adds no new rail — it only refuses balances too small to be
meaningful, warns on marginal ones, and measures + reports the clamp rate
and realized risk on every run, so a small-balance backtest can no longer
print a confident P/L without disclosing that it mostly tested "always
trade the minimum lot."

## What was added, and where

`scripts/backtest.py`:

- **Constants + validator** (after `MIN_OZ`, ~line 320): `MIN_BALANCE = 500.0`,
  `WARN_BALANCE = 2000.0`, and `validate_balance(value) -> str | None`
  (`None` = fine, `str` = warn-and-run, raises `SystemExit` below the floor).
  Text and thresholds copied verbatim from the brief.
- **`run()` preamble** (~line 689, next to `trades = []`): added
  `sizing = {"entries": 0, "clamped": 0, "risk_pct": []}`.
- **Sizing site** (~line 976-982, the risk-sized non-`fixed` entry branch):
  records an entry attempt, whether it clamped (`want < MIN_OZ`), and the
  realized risk % actually taken at the clamped `oz`.
- **End of `run()`** (~line 1038, right before `return trades, bal, max_dd,
  max_valley`): publishes `run.sizing = {"entries", "clamped", "clamp_pct",
  "risk_median", "risk_p90"}` as a function attribute, same pattern as the
  existing `run.bias_flips`. `run()`'s signature and return tuple are
  unchanged.
- **`main()`** (~line 1223, right after `args = build_parser().parse_args()`):
  calls `validate_balance(args.balance)`, prints the warning if any (refusal
  raises `SystemExit` and exits before any replay work).
- **Report tail** (~line 1503, next to the `net P/L` line): prints the clamp
  rate (flagged `<-- results distorted` above 10%) and realized risk
  median/p90 vs. the `RISK_PCT` target, only when `run.sizing["entries"] > 0`
  (so `fixed` entry mode, which never risk-sizes, prints nothing — correct
  per the brief, not a bug).

New file `service/tests/test_backtest_balance.py` — the 5 tests from the
brief, copied verbatim.

## Fixture measurements (`service/tests/data/bars_slice.json`, 6,000 M5 bars)

| balance | entries | clamped | clamp % | risk median % | risk p90 % |
|--------:|--------:|--------:|--------:|---------------:|-----------:|
| $500    | 94      | 69      | 73.4    | 1.55           | 3.83       |
| $800    | 93      | 51      | 54.8    | 1.04           | 2.50       |
| $1,200  | 85      | 24      | 28.2    | 0.84           | 1.67       |
| $4,000  | 87      | 2       | 2.3     | 0.91           | 0.98       |
| $10,000 | 88      | 0       | 0.0     | 0.96           | 0.99       |
| $25,000 | 90      | 0       | 0.0     | 0.98           | 1.00       |

(target risk is `RISK_PCT = 1.0%`; medians/p90s sit near or above it because
clamped trades take *more* than 1%, dragging the distribution up even at
$4,000 where clamping is rare.)

## The 20pp threshold

Held comfortably — no fixture edit needed. `test_a_small_account_clamps_far_
more_than_a_large_one` compares $800 vs. $25,000: measured gap is
**54.8pp** (54.8% vs 0.0%), well above the 20pp the brief's test asserts.
Left the test and its threshold exactly as given in the brief; no comment
edit was required since the measured margin is not marginal.

## Step 7 — real 12-month CLI checks (`bars_max.json`, `--days 365`)

```
$ service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 --balance 1200
WARNING: at $1200, 1% risk often cannot cover one ounce at the stop distance, ...
...
net P/L       -341.79  (-28.48%)
final bal      858.21   max drawdown 582.92   max open-equity valley 598.56
sizing     47.0% of entries clamped to the 0.01 minimum lot  <-- results distorted
           risk actually taken: median 0.98% p90 2.28%  (target 1.00%)
```

Warning printed and clamp rate landed at 47.0% — near the brief's "~51%"
(the brief's number came from the pre-existing 12-month measurement window
cited in the constants' comment; the live `bars_max.json` window/strict-mode
default drift a few points, as expected, not a bug).

```
$ service/.venv/bin/python scripts/backtest.py --source bars_max.json --balance 300 ; echo "exit=$?"
--balance 300 is below the $500 floor.
At that size nearly every entry clamps to the 0.01 minimum lot, so the replay
measures minimum-lot behaviour, not the rulebook -- and margin stop-out is
not modelled, so the account can go negative. Use $4,000+ for meaningful
results, $10,000+ for a clean test of the risk rules.
exit=1
```

Refused as expected, exit code 1.

## Test results

- `service/tests/test_backtest_balance.py`: 5 passed.
- `service/tests/test_backtest_golden.py`: 1 passed (114-trade pin unchanged
  — Task 4 only counts, it does not decide).
- Full fast suite (`cd service && .venv/bin/python -m pytest -q`): 454
  passed, 1 deselected (the `slow` Chronos marker), no regressions.

## Concerns

None. The `fixed` entry-mode branch was deliberately left untouched (no
`sizing[...]` increments there), matching the brief's note that it correctly
reports `0.0` with zero recorded entries.
