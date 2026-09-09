# Task 1 report: golden-run harness

## What was created

- `service/tests/data/bars_slice.json` — 6,000 M5 candles (449,567 bytes), compact JSON (no separators/newlines), keys `t,o,h,l,c,v` per bar, matching `bars_max.json`'s schema.
- `service/tests/data/golden_trades.json` — pinned digest of the trade list `scripts/backtest.py` currently produces over that slice, plus `final_balance` and `max_dd`.
- `service/tests/test_backtest_golden.py` — written verbatim from the brief's Step 2 code block, including `_load_bt()` (imports `scripts/backtest.py` by path as module `bt`, side-effect free since its argparse/main is guarded by `__name__ == "__main__"`), `_digest()`, `_replay()`, and `test_replay_matches_golden()`.

No other files were modified. `scripts/backtest.py` was temporarily edited for the Step 6 check and reverted; `git diff scripts/backtest.py` is empty and `git status` confirms it is not staged/changed.

## Fixture slice chosen and why

Used exactly the brief's specified slice: `bars_max.json['candles'][40000:46000]` — 6,000 bars from the middle of the ~100k-bar history (2025-03-19 → 2026-08-17 overall), giving a slice spanning roughly late Oct to mid-Nov (unix `t` 1760046600 → 1762789500), chosen by the brief to contain both trends and chop. I did not need to widen or move it — see verification below, it already satisfies every later-task need.

## Golden numbers produced

- Trade count: **114** (brief expected "roughly 100" — close enough, well above the "non-zero" bar).
- Final balance: **3906.30** (start balance 4000.0).
- Max drawdown: **468.06**.

## Verification against later-task needs (done while here, per the ruling in the brief)

- **Task 2 (pyramiding basket with >1 leg required)**: leg-count distribution across the 114 golden trades is `{1: 45, 2: 47, 3: 22}` — 69 of 114 trades (60%) have more than one leg. Plenty of multi-leg baskets available.
- **Task 3 (strict entry window must filter at least one entry the loose window takes)**: re-ran `bt.run()` over the same slice with `bt.STRICT_WINDOW` toggled False→True (the module-level flag controlling one-shot vs. continuous entry confirmation, set via `--strict-window`). Loose mode: 114 trades. Strict mode: 87 trades. Of the loose-mode entries (by `(entry_px, dir)`), 105 are not present in the strict-mode entry set — strict filtering removes a large number of entries the loose window takes, comfortably satisfying "at least one."

No change to the slice was needed; it satisfies all three constraints (non-zero trades, multi-leg baskets, strict-vs-loose divergence) simultaneously.

## Step 3 (fails for the right reason) and Step 6 (proves it can fail)

- Step 3: before the golden file existed, running the test raised `FileNotFoundError: .../golden_trades.json` (not an import error) — exactly as expected.
- Step 6: temporarily changed `RISK_PCT = 1.0` → `1.5` in `scripts/backtest.py`, re-ran the test — it **FAILED** (an `AssertionError` on the digest list — the pl/exit values shifted while trade count stayed at 114, tripping the per-trade equality check). Reverted `RISK_PCT` back to `1.0`, re-ran — **1 passed**. Confirmed `git diff scripts/backtest.py` is clean afterward.

## Full suite check

Ran the full fast suite (`cd service && .venv/bin/python -m pytest -q`) after committing: **437 passed, 1 failed, 1 deselected**. The single failure is `tests/test_db_proposals.py::test_pop_approved_command_concurrent_exactly_once`, which CLAUDE.md documents as a known, pre-existing timing flake unrelated to this change (`SystemError: error return without exception set` from concurrent SQLite access under load). Not caused by this task's changes.

## Anything a later task should know

- `bt.STRICT_WINDOW` is a module-level global on the `bt` module object returned by `_load_bt()`; later tasks can flip it directly (`bt.STRICT_WINDOW = True`) before calling `bt.run()` the way I did for verification, without needing a fresh CLI invocation.
- The fixture slice's unix timestamp range is `1760046600` (first bar) to `1762789500` (last bar) if a later task needs to reason about calendar dates for display/report purposes.
- `bars_slice.json` was written with `separators=(',', ':')` (no whitespace) to keep it compact/diffable as one line — same convention should be used if any later task regenerates it.
- `bars_max.json` at the repo root remains untracked and was not touched or added to git, per the global constraint.

## Commit

`24291f2` — `test(backtest): golden-run characterization test over a fixed M5 slice` — adds exactly the three files listed above, 978 insertions.
