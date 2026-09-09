# Task 3 report — the strict 3-bar entry rule becomes the default

## Summary

Flipped `scripts/backtest.py`'s `STRICT_WINDOW` default to `True`, extracted
`build_parser()` so tests can reach the CLI, replaced `--strict-window` with
`--loose-window` (`--strict-window` kept parsing as a documented no-op), and
fixed a stale regeneration instruction in the golden test's docstring. This
is the one task in the 8-task plan that deliberately changes what the
default replay run produces. Measured over the full `bars_max.json` history
(365 days), strict is worse than loose, not better — reported honestly below
alongside the entry-rate gap against the live EA.

## The default flip

`scripts/backtest.py`, constants block (~line 374):

```python
STRICT_WINDOW = True      # EA law since 2026-08-16: flip -> wait one closed
                          # bar -> enter only if the next bar OPENS beyond the
                          # EMA, else the signal is dead until the next flip.
                          # --loose-window restores the pre-2026-08-16 replay.
```

## `build_parser()` extraction

`main()` (~line 1054) split at the point that used to be `args = ap.parse_args()`:

```python
def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument(...)   # every existing add_argument call, moved verbatim
    ...
    return ap


def main():
    args = build_parser().parse_args()
    ...
```

Confirmed with `git diff`: every `ap.add_argument(...)` line moved unchanged
— no flag renamed, no type/default/choices/help string touched, no
reordering — except the two calls Step 5 explicitly asked me to replace
(`--strict-window` → `--loose-window` + suppressed `--strict-window`). The
diff hunk for that block is a clean insert-before / help-string-swap, nothing
else moved inside it.

`STRICT_WINDOW = args.strict_window` became `STRICT_WINDOW = not args.loose_window`.

## Docstring fixes

1. **Module usage docstring** (top of `scripts/backtest.py`, lines ~32 and
   ~115-129): the long-form `--strict-window` explanation said "Off
   (default, byte-identical)" — now false, since strict is the default and
   the flag is a no-op. Rewrote it under the `--loose-window` heading,
   describing strict as the default behaviour and loose as the
   compatibility flag. Not asked for explicitly in the brief, but it
   directly describes the flag whose default I flipped, so leaving it
   stale would have been the same class of defect as the golden-test
   docstring fix below.

2. **`service/tests/test_backtest_golden.py` docstring** (the assigned
   fix): it told the reader to regenerate with `pytest ... --regen-golden`,
   an option that does not exist anywhere in this repo. Replaced with a
   pointer to the actual procedure — the manual script from Task 1's brief,
   Step 4 (`.superpowers/sdd/2026-08-20-backtest-report/task-1-brief.md`):

   ```python
   Regenerate deliberately (and only deliberately) with the manual script from
   task-1-brief.md Step 4 (.superpowers/sdd/2026-08-20-backtest-report/):
       cd service && .venv/bin/python - <<'PY'
       import json
       from tests.test_backtest_golden import _replay, GOLDEN
       digest, bal, dd = _replay()
       GOLDEN.write_text(json.dumps(
           {"trades": digest, "final_balance": bal, "max_dd": dd}, indent=1))
       PY
   ```

## Golden test kept honest (Step 7)

Added the explicit pin to `_replay()` in `test_backtest_golden.py`:

```python
bt.STRICT_WINDOW = False
```

with the comment explaining the golden file was captured under loose
behaviour and must stay loose to remain a like-for-like change detector
across the default flip. `test_backtest_golden.py::test_replay_matches_golden`
passes unchanged (same 114-trade digest, same final balance/max DD) —
confirms the default flip did not silently reach into the pinned fixture.

## Test results

```
cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py -q
4 passed in 1.46s

cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
1 passed in 1.18s

cd service && .venv/bin/python -m pytest -q
448 passed, 1 deselected in 74.9s
```

Full suite is green; no other test touched `STRICT_WINDOW`, `build_parser`,
or the parser's flag set, so no incidental breakage anywhere else in the
service.

## 12-month strict-vs-loose comparison (`bars_max.json`, 365 days, $4,000 start)

```
STRICT (new default):
net P/L      -2874.96  (-71.87%)
final bal     1125.04   max drawdown 3035.57   max open-equity valley 3076.81
trading days 258  trades: 1210  trades/day 4.69  win% 35.0

LOOSE (--loose-window, old behaviour):
net P/L      -1998.59  (-49.96%)
final bal     2001.41   max drawdown 2576.96   max open-equity valley 2622.03
trading days 258  trades: 1504  trades/day 5.83  win% 34.6
```

The loose figure matches the brief's reference value exactly (−$1,998,
−50%), confirming the harness is wired correctly. **Strict is not the
improvement the rule's introduction implied** — over this 365-day window
strict loses more ($2,875 vs $1,999) and draws down harder ($3,036 vs
$2,577) than loose. Both are steep losers over the full year; strict simply
loses faster per dollar risked because it takes fewer, apparently no
better-selected, trades. This is reported as measured, per the brief's
instruction not to explain it away — see the hypothesis below.

## Entry-rate numbers requested

**Fixture slice (`service/tests/data/bars_slice.json`, 6,000 M5 bars, the
golden test's slice) — settling the Task 1 report vs. reviewer disagreement:**

```
LOOSE:  114 trades
STRICT:  87 trades
filtered: 27 trades (23.7% of loose's entries)
strict retains 76.3% of loose's entries
```

**The reviewer's figure (114 → 87) is correct; the Task 1 report's "105-of-114"
was wrong.** Verified directly with `bt.run()` called twice on the same
fixture (`STRICT_WINDOW = False` then `True`), same balance/verbose args as
the golden test uses.

**365-day run, strict vs loose trades/day:**

| | trades | trading days | trades/day |
|---|---|---|---|
| strict | 1210 | 258 | 4.69 |
| loose | 1504 | 258 | 5.83 |

**Strict vs live (~3 baskets/day, observed 2026-08-19: 15 signals, 3
baskets):**

```
4.69 / 3 = 1.56x
```

Strict trades about **1.6x as often** as the live EA did on its one
observed day — a real gap, not a rounding difference, but not an
order-of-magnitude discrepancy either.

**Hypothesis (labelled as such — I did not chase this further per the
brief's instruction that the entry rule itself is verified correct):**

1. **n=1 comparison.** The live figure is a single day. 4.69/day is a
   365-day average that includes both quiet and volatile stretches; one
   live day landing below the yearly average is unremarkable sampling
   noise, not necessarily a discrepancy.
2. **Unmodeled live suppressors.** Per this task's own constraints, the
   replay does not model the daily-loss brake, the kill switch, or the
   news blackout. All three exist specifically to stop the live EA from
   taking entries the strategy would otherwise fire on. Any day the live
   account tripped the brake, hit a blackout window, or the kill switch
   engaged would show fewer live baskets than an unconstrained strict
   replay of the same day, independent of any bug in the entry rule.
3. **Possible regime mismatch.** `bars_max.json`'s 365-day window
   (2025-08-18 → 2026-08-17) may simply contain more HalfTrend flip
   activity than the single day of live data available for comparison —
   without a longer live sample there's no way to separate "the replay
   over-trades" from "that one live day was calm."

I did not go looking for a transcription bug between the replay's rule and
the EA's rule — that's independently verified (`scripts/backtest.py:747` vs
`mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh:181`) and out of scope
for this task.

## Concerns for review

- The 12-month strict result is a net loser and worse than loose by both
  P/L and drawdown on this data. That's a legitimate finding to surface to
  the plan owner before later tasks (report generation, defaults docs)
  build on "strict is now the default" without flagging that the measured
  effect of this task, on this dataset, is negative.
- The entry-rate gap vs. live (1.56x) is plausible but not fully explained;
  hypothesis 2 (unmodeled brake/blackout/kill-switch) is the most likely
  candidate given the plan's explicit choice not to model those — but that's
  a hypothesis, not a settled explanation.

## Fix round 1 — untested wiring + attribution correction

### Attribution correction (not a code fix — recording per coordinator instruction)

The "105-of-114" framing in this report's "Entry-rate numbers requested"
section and in the commit message is **not** something Task 1's report
claimed. Task 1's report correctly stated "Loose mode: 114 trades. Strict
mode: 87 trades" — the same figures I independently re-measured. The
"105-of-114" number came from the controller's ledger (passed to me in the
Task 3 dispatch) and is itself a real, different statistic: loose entries
with no `(entry_px, dir)` match in the strict set, which also counts flips
that merely re-priced onto a different bar rather than being refused
outright. So: my final 114/87 numbers stand unchanged, but crediting the
"105-of-114" framing to Task 1's report was wrong — that framing was the
controller's, not Task 1's. Left uncorrected in the existing commit message
per instruction (history stays as written); recorded here for the next
reader.

### Untested wiring — fixed

`STRICT_WINDOW = not args.loose_window` (`scripts/backtest.py`, inside
`main()`) was the only line making `--loose-window` actually control
runtime behaviour, and nothing exercised it: the four Step-1 tests either
read the module constant before parsing, inspected the parsed `Namespace`
without reaching `main()`, or set `bt.STRICT_WINDOW` directly, bypassing the
CLI-to-global path entirely. Dropping the `not` would have passed the whole
suite silently.

Extracted the assignment into a small function so a test can drive the real
path without running a whole backtest:

```python
def apply_window_args(args):
    """Wire --loose-window (and the suppressed --strict-window no-op) into
    the STRICT_WINDOW runtime flag. Extracted out of main() so a test can
    drive the real CLI-to-global path without running a whole backtest."""
    global STRICT_WINDOW
    STRICT_WINDOW = not args.loose_window
```

`main()` now calls `apply_window_args(args)` in place of the inline
assignment; nothing else in `main()` was restructured (the `global
STRICT_WINDOW, MIN_STOP_ATR` line was split so `MIN_STOP_ATR` keeps its own
`global` and `STRICT_WINDOW` is no longer touched directly in `main()`).

Added `test_apply_window_args_wires_the_cli_flag_to_the_runtime_flag` to
`service/tests/test_backtest_strict_window.py`: parses real argv with
`build_parser()`, calls `apply_window_args()` on the result (the same call
`main()` makes), and asserts against a module global seeded to the *wrong*
value each time — no flag -> True, `--loose-window` -> False,
`--strict-window` -> True (no-op).

### Mutation-check evidence

```
$ sed -i 's/STRICT_WINDOW = not args.loose_window/STRICT_WINDOW = args.loose_window/' scripts/backtest.py
$ cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py -q
...F.                                                                    [100%]
FAILED tests/test_backtest_strict_window.py::test_apply_window_args_wires_the_cli_flag_to_the_runtime_flag
AssertionError: no flag must leave STRICT_WINDOW True
assert False is True
1 failed, 4 passed in 2.99s

$ sed -i 's/STRICT_WINDOW = args.loose_window/STRICT_WINDOW = not args.loose_window/' scripts/backtest.py
$ cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py -q
.....                                                                    [100%]
5 passed in 1.39s

$ git diff scripts/backtest.py
# clean: only the intended apply_window_args() extraction, no mutation leftover
```

The new test is the only one of the five that fails under the mutation; the
other four (default-at-import, parsed-Namespace checks, direct-global entry
count) are unaffected by it, confirming they were never covering this line.

### Test results (fix round 1)

```
cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py tests/test_backtest_golden.py -q
6 passed in 1.67s

cd service && .venv/bin/python -m pytest -q
449 passed, 1 deselected in 75.0s
```
