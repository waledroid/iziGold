# Task 3 report — run both lanes against one balance

Commit: `02eaf3a` — `feat(backtest): QuickFlip lane trades alongside HalfTrend on one balance`

## What was added, and exactly where

All in `scripts/backtest.py`, found by surrounding code (the plan's line numbers
were stale — the file had grown with `BIAS_BUFFER_ATR`/`--bias-buffer-atr` and
Task 2's `QF_*` constants + `qf_signals()`/`qf_daily_atr()` since the brief was
written):

- **Module constant**: `STRATEGY = "both"` added next to the other
  strategy-shaped constants, right after `FLATTEN_HM = (23, 50)` and before the
  "trading timeframe" comment block (around what is now line ~322).
- **`close_basket()`'s `trades.append({...})`**: added `"lane": "ht"` as the
  first key, every existing key kept.
- **`run()`'s preamble**: added the `qf_by_i` precomputation (guarded by
  `STRATEGY in ("qf", "both")`) and `qf_pos = None`, placed between
  `trades = []` and the `sizing = {...}` dict, verbatim per the brief.
- **Main loop** (`for i in range(EMA_LEN + AMPLITUDE + 2, len(candles)):`):
  inserted the QuickFlip lane block as the very first statement in the loop
  body — before `x = candles[i]` / `when, h, m = hhmm(x["t"])` and before any
  HalfTrend `continue` guard (the first one, `if e is None or a is None or
  ht[i] is None: continue`, is several lines further down). This is the exact
  block from the brief: intrabar stop/target/expiry check on the resident
  `qf_pos`, closing it into `trades` with `"lane": "qf"` and updating
  `bal`/`peak_bal`/`max_dd`; then opening a new `qf_pos` from `qf_by_i[i]` if
  none is resident, sized off the *current* shared balance
  (`bal * QF_RISK_PCT / 100 / dist`). Immediately after: `if STRATEGY == "qf":
  continue` so `--strategy qf` runs QuickFlip alone.
- **`build_parser()`**: `--strategy` added to the `rules` argument group,
  right after `--profit-target`, choices `("ht", "qf", "both")`, default
  `"both"`.
- **`main()`**: `global STRATEGY; STRATEGY = args.strategy` added beside the
  other global assignments, right after the `BIAS_BUFFER_ATR` wiring.

**Exposure was deliberately left unshared** — the QuickFlip block never
touches `expo[day]`; that stays HalfTrend's alone. The account-level state
(`bal`, `peak_bal`, `max_dd`) is shared, exactly as the brief specifies.

`run()`'s return arity is unchanged: `(trades, bal, max_dd, valley)`.

## The two replaced tests, and why

The brief's `test_ht_lane_is_identical_whether_or_not_qf_runs` asserts
HalfTrend's entry AND exit prices are byte-identical whether or not QuickFlip
runs. That's false by construction: HalfTrend's profit target is `cycle_bal *
PROFIT_TARGET_PCT / 100` — a dollar amount off the balance at basket entry.
Any QuickFlip P/L that lands before a HalfTrend basket closes moves the
balance, therefore the target, therefore the exit price and therefore the
timing of the next basket's open. That coupling is the intended cost of one
shared account; it is not the cross-lane interference the design forbids
(one strategy closing/modifying the other's positions).

Replaced (in `service/tests/test_qf_lane.py`) with the two tests from the
task instructions, verbatim:

- **`test_lanes_close_only_their_own_positions`** — the real independence
  property. Every trade with `lane == "qf"` must close for a QF-only reason
  (`"qf target"`/`"qf stop"`/`"qf expired"`); every trade with `lane == "ht"`
  must never close for one of those reasons.
- **`test_ht_decisions_match_until_the_first_qf_close`** — before any
  QuickFlip trade has resolved, the shared balance is untouched, so
  HalfTrend's decisions (dir, entry price, exit price, why) must be
  bit-identical to running HalfTrend alone, for every HT trade whose
  `exit_t` is at or before the first QF close. After that point divergence is
  expected and not checked.

Every other test in the brief (`test_ht_only_produces_no_qf_trades`,
`test_qf_only_produces_no_ht_trades`, `test_both_runs_both_lanes`,
`test_qf_trades_carry_the_full_record_shape`,
`test_balances_chain_across_both_lanes`) was kept exactly as written.

## Per-lane trade counts on the fixture (`bars_slice.json`, $10,000 start)

| `--strategy` | total trades | ht | qf | final balance |
|---|---|---|---|---|
| `ht`   | 43 | 43 | 0 | 9,903.29 |
| `qf`   | 2  | 0  | 2 | 10,036.90 |
| `both` | 45 | 43 | 2 | 10,045.27 |

QuickFlip produces exactly the 2 setups the brief said to expect at
`QF_ATR_PCT = 5.0` on this fixture.

## Golden pins

Both `_replay()` and `_replay_strict()` in
`service/tests/test_backtest_golden.py` now set `bt.STRATEGY = "ht"` (with a
comment explaining why, matching the existing style). `pytest
tests/test_backtest_golden.py -q` → **3 passed**, numbers unchanged — the
golden JSON files were not touched or regenerated.

## Unplanned fallout, fixed

`tests/test_backtest_web.py::test_fixed_entry_mode_serializes_tp_as_null_for_every_trade`
asserts every trade in a fixed-entry-mode run has `tp is None`. It calls
`bt.run()` directly without pinning `STRATEGY`, so under the new default
(`"both"`) a QuickFlip trade (which always carries a `tp`) leaked into its
fixture window and broke the assertion — `ENTRY_MODE` is a HalfTrend-only
concept and was never meant to govern QuickFlip. Fixed by adding `bt.STRATEGY
= "ht"` to that test, with a comment, the same pattern used in the golden
tests. This is the only test outside the brief's file list that needed a
change.

## Full suite

`cd service && .venv/bin/python -m pytest -q` → **500 passed, 1 deselected**
(the `-m slow` Chronos test). One run hit the documented known flake,
`test_pop_approved_command_concurrent_exactly_once` (CLAUDE.md: "a
deliberately timing-sensitive two-thread probe ... a failure there alone →
re-run before treating it as a regression"); it passed in isolation and on
the immediate re-run of the full suite. No other failures, on any run.

## Files touched

- `/mnt/c/Users/aatanda/Desktop/xau/scripts/backtest.py`
- `/mnt/c/Users/aatanda/Desktop/xau/service/tests/test_qf_lane.py` (new)
- `/mnt/c/Users/aatanda/Desktop/xau/service/tests/test_backtest_golden.py`
- `/mnt/c/Users/aatanda/Desktop/xau/service/tests/test_backtest_web.py`

## Concerns

- None blocking. The one thing worth flagging to the owner: QuickFlip sizing
  (`oz = max(MIN_OZ, int(bal * QF_RISK_PCT / 100 / dist))`) reads `bal` at
  the moment its setup fires, which is now whatever HalfTrend has done to the
  account so far that run — this is the intended coupling, not a bug, but it
  means QF position size is not reproducible in isolation from a `both` run
  (only `--strategy qf`'s own path is).
