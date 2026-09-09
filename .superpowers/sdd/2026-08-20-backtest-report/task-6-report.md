# Task 6 report: `--json PATH` — the run artifact

## Summary

Added `build_run_json(candles, trades, args, res)` to `scripts/backtest.py`
(inserted directly above `plot()`), the `--json PATH` flag in the `out`
argument group, and the write-on-exit in `main()` right after the existing
`--chart` handling. Created `service/tests/test_backtest_json.py` per the
brief verbatim. Implementation matches the brief's Step 3/4 code exactly —
no deviations were needed after confirming local names (`trades`, `bal`,
`max_dd`, `max_valley`, `CAVEATS`, `TF`, `run.sizing`) already existed as
described.

## Schema as emitted

Trimmed real sample (from the 12-month `bars_max.json` run), first 3 candle
bars, first trade only:

```json
{
  "meta": {
    "generated_at": 1787203152,
    "source": "bars_max.json",
    "tf": "M5",
    "bars": 70707,
    "start": 1755478800,
    "end": 1786972800,
    "strict_window": true,
    "entry_mode": "adr",
    "caveats": [
      "daily-loss brake NOT modelled -- a real losing day would have been cut short",
      "kill switch NOT modelled -- a real 10% drawdown would have stopped trading",
      "news blackout NOT modelled -- no offline calendar of high-impact USD events",
      "acts on bar CLOSES only; fills at close +/- half-spread, $0.20/oz round trip",
      "no margin modelling and no stop-out: a small account can go negative here"
    ]
  },
  "stats": {
    "trades": 1210, "wins": 423, "losses": 787, "win_rate": 35.0,
    "net": -2874.96, "start_balance": 4000.0, "end_balance": 1125.04,
    "max_dd": 3035.57, "max_valley": 3076.81, "best": 136.5, "worst": -72.88,
    "clamp_pct": 16.7, "risk_median": 0.89, "risk_p90": 1.27
  },
  "candles": {
    "t": [1755478800, 1755479100, 1755479400],
    "o": [3336.35, 3334.72, 3333.74],
    "h": [3339.1, 3335.43, 3334.72],
    "l": [3333.08, 3333.04, 3333.13],
    "c": [3334.67, 3333.74, 3333.47]
  },
  "ind": {
    "ema9": [null, null, null],
    "ema200": [null, null, null],
    "ht": {"v": [null, null, null], "trend": [null, null, null]}
  },
  "trades": [{
    "dir": "BUY",
    "legs": [
      {"t": 1755498900, "px": 3347.79, "oz": 16},
      {"t": 1755499500, "px": 3350.96, "oz": 11}
    ],
    "tp": 3352.99,
    "stop_history": [
      {"t": 1755498900, "stop": 3345.37},
      {"t": 1755499500, "stop": 3346.58}
    ],
    "exit": 3349.44, "exit_t": 1755499800, "why": "profit lock",
    "pl": 4.28, "bal_after": 4004.28, "regime": "trend"
  }]
}
```

`meta.args` (omitted above for brevity) is `vars(args)` filtered to non-None
values — the full CLI invocation that produced the run, for provenance.

## Indicator array length / alignment

Verified programmatically on both the golden fixture and the 12-month run:
`len(ind.ema9) == len(ind.ema21) == len(ind.ema55) == len(ind.ema200) ==
len(ind.ht.v) == len(ind.ht.trend) == len(candles.t)` in every case (70,707
on the 12-month run; matches the fixture's bar count on the golden test).
`test_indicator_series_align_with_the_candles` asserts this directly and
passes.

## `null` handling

- **EMA/HalfTrend warm-up**: `ema()` and `halftrend()` already return `None`
  for unwarmed bars; `r2()` (`None if v is None else round(v, 2)`) passes
  that straight through into JSON `null`. Confirmed: first bars of the
  12-month artifact show `ema9`/`ema200`/`ht.v`/`ht.trend` all `null`.
- **`trade.tp`**: `r2(t.get("tp"))` — `null` for `fixed` entry mode (no
  target at all) and for the measured 3-of-114 golden-fixture trades that
  stop out on their first bar before a target is computed. Re-ran the exact
  golden fixture at `--balance 10000` (the artifact test's balance) through
  `build_run_json`: 88 trades at that balance, 3 with `tp: null`, all with
  `why: "stop"` and a single leg — exactly the case the brief describes.
  No default was substituted.

## 12-month artifact (bars_max.json, --days 365 --balance 4000)

```
json       /tmp/run12m.json (6.3 MB)
bars 70707   trades 1210
stats: net -2874.96, win_rate 35.0%, clamp_pct 16.7%, risk_median 0.89%, risk_p90 1.27%
```

- **Size: 6.3 MB** — inside the plan's predicted 5–7 MB range.
- **Bars: 70,707** — a bit under the "~74,000" estimate (source has
  99,999 M5 bars total; 365 days back from the end trims some of that, and
  weekends/gaps reduce the count further). Not a concern for Task 7 sizing.
- **Trades: 1,210** — well above the brief's "a few hundred" estimate. This
  is a plain read of `run()`'s output for these params (default `--risk`,
  `--adx`, etc., at `--balance 4000`, which the CLI itself already warns is
  below the $10,000 "clean test" threshold and shows 16.7% of entries
  clamped to the minimum lot). `build_run_json` only serializes what `run()`
  produced — it does not filter, dedupe, or recompute trades — so this count
  is the same the plain-text report already showed before this task. Flagging
  it since Task 7's expected artifact size depends on trade volume, but no
  code change was needed or made.

## Test results

```
cd service && .venv/bin/python -m pytest tests/test_backtest_json.py -q
......                                                                   [100%]
6 passed in 2.41s

cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
.                                                                        [100%]
1 passed in 1.47s

cd service && .venv/bin/python -m pytest -q
1 failed, 463 passed, 1 deselected, 4 warnings in 78.19s
FAILED tests/test_db_proposals.py::test_pop_approved_command_concurrent_exactly_once
```

That single failure is the documented known flake (CLAUDE.md: "a deliberately
timing-sensitive two-thread probe ... can rarely fail under heavy machine
load (slow /mnt/c filesystem)"). Re-ran it in isolation immediately after:
passed. Unrelated to this change — it touches `app/db.py` command-queue
concurrency, nothing in `scripts/backtest.py`.

The golden test (114 pinned trades) passes unmodified, confirming Task 6
serializes only — no trading decision changed.

## Files touched

- `/mnt/c/Users/aatanda/Desktop/xau/scripts/backtest.py` — added
  `build_run_json()` (above `plot()`), `--json PATH` in the `out` arg group,
  and the write-on-exit block in `main()`.
- `/mnt/c/Users/aatanda/Desktop/xau/service/tests/test_backtest_json.py` —
  new, six tests per the brief verbatim.

## Commit

`1b7066b` — `feat(backtest): --json writes the full run artifact (candles, indicators, trades, stats)`
(2 files changed, 132 insertions(+); only the two files above staged —
`bars_max.json` and the unrelated untracked `.superpowers/*.md` study files
from prior sessions were left untouched, as instructed).
