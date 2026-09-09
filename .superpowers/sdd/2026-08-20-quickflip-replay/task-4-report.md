# Task 4 report: per-lane reporting, and a combined golden

## Status

Complete. Commit `dfc5472` — "feat(backtest): per-lane reporting and a golden pin for the combined run".

## Per-lane stdout block (real run, `--strategy both`)

```
$ service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 --balance 10000 --strategy both
...
lane halftrend  trades   578  win%  37.9  net    3246.37
lane quickflip  trades   118  win%  50.0  net     305.24
           quickflip trades overlapping a halftrend position: 17

net P/L      +3551.61  (+35.52%)
final bal    13551.61   max drawdown 2892.35   max open-equity valley 2902.73
```

## Full per-strategy comparison (Step 9)

```
--- ht ---
trades: 579  |  wins 221  losses 358
net P/L      +3255.92  (+32.56%)

--- qf ---
trades: 118  |  wins 59  losses 59
net P/L       +354.56  (+3.55%)

--- both ---
trades: 696  |  wins 278  losses 418
lane halftrend  trades   578  win%  37.9  net    3246.37
lane quickflip  trades   118  win%  50.0  net     305.24
           quickflip trades overlapping a halftrend position: 17
net P/L      +3551.61  (+35.52%)
```

**Does `both` beat `ht`?** Yes: +3551.61 vs +3255.92, a +295.69 improvement
(+35.52% vs +32.56% on the same $10,000 start). Both lanes individually earn
slightly less inside `both` than run alone (ht: 3246.37 vs 3255.92 on one
fewer trade — 578 vs 579, the expected dollar-profit-target interaction
sharing one balance; qf: 305.24 vs 354.56, same shared-balance sizing
effect), but the combined account still comes out ahead because QuickFlip's
net is additive on top of HalfTrend's, not a reallocation away from it.

## `both_open_bars` (concurrency)

On the full-history run: **17**. That is 17 of QuickFlip's 118 trades (14%)
that opened while a HalfTrend position was also open — i.e. exposure was
briefly doubled on the shared account for those 17 trades. It's a small
fraction, consistent with QuickFlip being a much lower-frequency lane (118
trades/year vs HalfTrend's ~579), so the two lanes mostly don't collide, but
it is not zero — the report exists precisely so this isn't hidden by a single
blended equity curve.

## Golden regeneration — before/after (the risk this task called out)

Recorded before regenerating, then re-checked after `_digest()` gained
`lane` and all three goldens were rewritten:

| Golden | Before (count / balance / max_dd) | After (count / balance / max_dd) | Moved? |
|---|---|---|---|
| `golden_trades.json` (loose) | 114 / 3906.3 / 468.06 | 114 / 3906.3 / 468.06 | No |
| `golden_trades_strict.json` (strict) | 43 / 3879.97 / 236.27 | 43 / 3879.97 / 236.27 | No |
| `golden_trades_both.json` (new) | n/a | 45 / 3895.85 / 236.27 (43 ht + 2 qf) | n/a (new file) |

HalfTrend's behaviour on the frozen fixture is unchanged — the regeneration
only added the `lane` key to each trade's digest, exactly as expected. The
new combined golden's 43 ht + 2 qf = 45 matches the counts already recorded
for this fixture in the task context.

## Guard verification (Step 5)

```
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 10000 --strategy both --hour-table --min-stop-atr 0.5
```

No traceback (exit code 0). Cross-checked that the HalfTrend-only report
blocks are correctly scoped to `ht_trades` (578 trades), not the full
696-trade list:
- entry regime breakdown: 68 + 356 + 154 = 578
- entry-hour breakdown: sums to 578
- bias breakdown: 573 (with) + 0 (counter) + 5 (untagged) = 578

## Full suite

`cd service && .venv/bin/python -m pytest -q` → **505 passed, 1 deselected**
(500 baseline + 4 new `test_qf_report.py` tests + 1 new
`test_both_lane_replay_matches_golden`).

## Concerns

None. No new dependencies added; `bars_max.json` was not touched/committed;
`service/tests/data/bars_slice.json` untouched; daily-loss brake / kill
switch / news blackout remain unmodelled and are unaffected by this task.

## Fix round 1/5

Two review findings addressed:

**Finding 1 (Medium) — `test_concurrency_is_reported` didn't discriminate.**
Changed the assertion from `"both_open_bars" in art["stats"]["lanes"]` to
pinning the actual value against the frozen fixture:
`art["stats"]["lanes"]["qf_trades_overlapping_ht"] == 1` (with `--strategy
both`, default `_artifact()` balance 10000.0). Confirmed the value myself
before pinning it (`_lane_stats()` on the fixture returns
`{'ht': {...43 trades...}, 'qf': {...2 trades...}, 'qf_trades_overlapping_ht': 1}`)
— matches the reviewer's number.

Mutation-check evidence:
1. Replaced the interval intersection `a0 < b1 and b0 < a1` with `a0 == b0`
   in `scripts/backtest.py`.
2. Ran `pytest tests/test_qf_report.py::test_concurrency_is_reported -q`
   → **FAILED**: `assert 0 == 1`.
3. Restored the original line from a pre-edit backup.
4. Re-ran the same test → **4 passed** (whole file).
5. `git diff scripts/backtest.py` after restore showed only the three
   intended rename edits (docstring + two key renames) — the mutation left
   no trace.

**Finding 2 (Low) — `both_open_bars` didn't count bars.**
Renamed the field to `qf_trades_overlapping_ht` everywhere and corrected
the `_lane_stats()` docstring to say "how many QuickFlip trades overlapped
a HalfTrend position" instead of "how many bars both lanes were open
together". Updated every consumer:
- `scripts/backtest.py` `_lane_stats()` (the dict key)
- `scripts/backtest.py` `main()`'s stdout block (`ls['qf_trades_overlapping_ht']`)
- `service/tests/test_qf_report.py` (`test_concurrency_is_reported`)

`grep -rn "both_open_bars" --include="*.py" .` after the change returns
nothing. (The original planning doc,
`docs/superpowers/plans/2026-08-20-quickflip-replay.md`, still shows the old
name in its Step-1/Step-3 code blocks — left untouched since it's a
historical record of the plan as written, not code that runs or consumes
the field.)

### Re-verification

```
cd service && .venv/bin/python -m pytest tests/test_qf_report.py tests/test_backtest_golden.py -q
........                                                                 [100%]
8 passed in 3.41s

cd service && .venv/bin/python -m pytest -q
505 passed, 1 deselected, 3 warnings in 108.52s
```

Commit: `fix(backtest): pin the overlap count and name it for what it counts`
