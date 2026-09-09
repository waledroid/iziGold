# Task 2 report: `qf_signals()` — QuickFlip setups as pure price geometry

## What the previous implementer left (uncommitted, process died mid-task)

- `scripts/backtest.py` — modified: added `QF_HOUR`, `QF_MINUTE`, `QF_ATR_PCT`,
  `QF_WINDOW_MIN`, `QF_RISK_PCT`, `QF_ATR_DAYS` constants plus `qf_daily_atr()`
  and `qf_signals()`, inserted above `def run(`. Matches the brief's Step 3
  implementation, **including** the future-data fix: `qf_daily_atr()` uses
  `if i <= QF_ATR_DAYS: continue` (not `<`), with a code comment explaining
  that `i < QF_ATR_DAYS` would let `keys[j - 1]` wrap to `keys[-1]` — the last
  day in the whole dataset — on the first eligible day, leaking months of
  future close into the first computed ATR. This mirrors the wraparound fix
  from commit 63defbc in `quickflip_probe.py`.
- `service/tests/test_qf_signals.py` — new file, 8 tests (the brief's 7 plus
  one extra: `test_atr_boundary_does_not_leak_the_last_days_close`, a
  regression test specifically for the wraparound bug above — it mutates only
  the last candle's close by +10,000 and asserts the first computed ATR is
  unchanged).

Nothing was committed; no report existed.

## What I verified myself (not just trusted)

- Read the full diff of `scripts/backtest.py` and the full content of
  `test_qf_signals.py` directly — confirmed the wraparound guard
  (`i <= QF_ATR_DAYS`) is actually present in the code, not just claimed.
- Ran `tests/test_qf_signals.py` alone: **8 passed**.
- Ran `tests/test_backtest_golden.py` alone (the two HalfTrend golden pins +
  the "pins really pin different runs" check): **3 passed**, confirming this
  task only *added* a function and did not disturb the existing replay engine.
- Confirmed `scripts/quickflip_probe.py`'s `daily_atr()` has the analogous
  fix (commit 63defbc), so the pattern being mirrored here is real, not
  invented.

## Required change: `QF_ATR_PCT` 10.0 → 5.0 (pinned default changed)

Per controller ruling, `QF_ATR_PCT` was lowered from the brief's `10.0` to
`5.0`. The brief's `10.0` was adapted from a published "25% of daily ATR"
opening-range rule scaled down to gold's ~7% median opening range — a
general adaptation never measured at the specific session this strategy
trades (13:30 server). Measured over 17 months with
`scripts/quickflip_probe.py`:

| gate | trades | win% | $/oz | total | older half |
|---|---|---|---|---|---|
| none | 245 | 52.7% | +0.37 | +91.01 | +0.07 |
| >=5% | 165 | 49.7% | +0.46 | +75.55 | +0.23 |
| >=10% (old default) | 35 | 45.7% | +1.64 | +57.37 | +0.06 |
| >=15% | 7 | 28.6% | -3.55 | -24.86 | -1.76 |

At 10% the strategy fires ~2x/month on a 35-trade sample whose older half is
indistinguishable from zero, and earns less in total than the looser 5% gate.
5% is the best-performing cut on this evidence (more trades, higher total,
and — unlike 10% — a positive older-half result, meaning the edge isn't
recency-only).

Changed two places:

1. **`scripts/backtest.py`** — `QF_ATR_PCT = 5.0`, with its comment replaced
   to state the reasoning above in short form, explicitly warning a future
   reader not to "restore" 10 or 25 believing them authoritative:

   ```python
   QF_ATR_PCT = 5.0       # box range as % of daily ATR(14). Measured at 13:30
                           # server over 17mo with quickflip_probe.py: >=10%
                           # leaves only 35 trades whose older half is
                           # indistinguishable from zero and earns LESS total
                           # than a looser gate; >=5% is the best-performing cut
                           # (165 trades, +75.55 total, older half +0.23/oz).
                           # The published "25% of daily ATR" opening-range rule
                           # does not transfer to gold and was never authoritative
                           # here -- do not "restore" 10 or 25 believing otherwise.
   ```

2. **`service/tests/test_qf_signals.py::test_defaults_match_the_spec`** — the
   pinned assertion `assert bt.QF_ATR_PCT == 10.0` was changed to
   `assert bt.QF_ATR_PCT == 5.0`. **This is a pinned default I changed on
   purpose**, per the controller ruling above, not a slip — the old pin
   would otherwise silently fail after the constant changed, which is exactly
   the point of a pin, but the brief's 10.0 value itself is what's being
   overridden here.

## Setup counts at QF_ATR_PCT = 5.0

- **Frozen fixture** (`service/tests/data/bars_slice.json`, untouched):
  **2 setups**. Non-zero, as required — this unblocks the next task, which
  needs at least one QuickFlip setup present in the fixture (10.0 yielded
  only 1; 5.0 yields 2).
- **Full history** (`bars_max.json`, repo root, untracked, 99,999 M5 bars via
  `resample()` at the default M5 TF — not committed, not regenerated):
  **177 setups**. (Higher than the probe study's 165 because `qf_signals()`
  is the geometry-only lane feeding the next task's execution loop, not a
  byte-identical reimplementation of `quickflip_probe.py`'s own qualifier
  logic — the two are related studies, not required to match exactly.)

## Test results

- `tests/test_qf_signals.py` (8 tests, including the updated pin): **8
  passed**.
- `tests/test_backtest_golden.py` (HalfTrend golden pins, untouched by this
  task): **3 passed**.
- Full fast suite (`cd service && .venv/bin/python -m pytest -q`): **493
  passed, 1 deselected** (the slow-marked Chronos test, excluded by default),
  in ~91s. Baseline before this task's work was 485 passing; 485 + 8 new
  tests in `test_qf_signals.py` = 493, which matches exactly — no other test
  count moved.

## Commit

Staged and committed `scripts/backtest.py` and `service/tests/test_qf_signals.py`
together as `feat(backtest): ...` (see final report message for the SHA).

## Constraints honored

- No new Python dependencies.
- `service/tests/data/bars_slice.json` not modified.
- Neither golden file (`golden_trades.json`, `golden_trades_strict.json`)
  regenerated.
- `bars_max.json` not committed (confirmed untracked, still ignored/absent
  from `git status`).
- Candle `t` read with no offset (the geometry code and the count script
  above both use raw `x["t"]` / `hhmm(x["t"])` with no adjustment).
- No subagents dispatched.
