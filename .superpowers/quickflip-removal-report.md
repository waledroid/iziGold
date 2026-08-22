# QuickFlip removal report

Branch `refactor/halftrend-lane`. Two commits:

- `6dd05c1` `refactor(backtest): drop the QuickFlip lane`
- `1a147f7` `docs: record the QuickFlip drop, with the number`

## Why

QuickFlip was a second backtest lane, evaluated as a paid experiment. Its
MARGINAL contribution to the shared account measured **+$118 over 17
months — about $7/month** on a $10,000 account: standalone profit largely
evaporated once it shared a balance with HalfTrend. It never traded live —
only in the replay engine and its tests. The owner decided to drop it.

## What was removed

- `scripts/quickflip_probe.py` (standalone evidence tool) — deleted.
- From `scripts/backtest.py`: `qf_signals`, `qf_daily_atr`, `qf_resolve`,
  `QuickFlipLane`, the `QF_*` constants, the `--strategy` CLI flag (removed
  entirely, not just its `qf`/`both` choices — `ht` is the only registered
  lane, so there was nothing left to select), the `lane_desc`/header/report
  branches that only fired for `qf`/`both`, `_lane_stats`'s qf loop and the
  `qf_trades_overlapping_ht` figure, and every qf-specific sizing/report
  block in `main()`.
- Test files deleted: `service/tests/test_qf_signals.py`,
  `test_qf_lane.py`, `test_qf_report.py`, `test_quickflip_probe.py`.
- `service/tests/data/golden_trades_both.json` deleted, along with
  `_replay_both()` / `test_both_lane_replay_matches_golden` in
  `test_backtest_golden.py`.
- QuickFlip/lane references cleaned from `test_backtest_web.py` (stale
  comment), `test_basket_twins.py` (docstring pointer to the deleted probe
  test), and `test_backtest_strict_window.py` (the `args.strategy ==
  bt.STRATEGY` pin, since both are gone).
- `service/app/static/backtest_report.html`: removed the `QF_L` colour, the
  `lane()` helper, the "QuickFlip trade" legend entry, the Lane table
  column, the per-lane header comparison block, and all qf-conditional
  marker/box styling — the report no longer distinguishes lanes since only
  one exists.
- `service/tests/backtest_report_smoke.js`: removed the `QF_L` constant and
  the entire "2b. Lane labelling" test section that asserted QuickFlip's
  visual distinctness.
- `scripts/backtest_report.py`: only a stale comment referencing
  `--strategy both` — the lane-breakdown-in-title logic itself already
  degraded gracefully to a no-op with one lane, so it needed no functional
  change.

## What was deliberately kept

- **The `Lane`/`Account` plug-in contract and the `LANES` registry**
  (`scripts/backtest.py`), now `LANES = {"ht": None}`. This is the plug-in
  seam the owner asked for — it costs nothing to keep and is how a future
  strategy gets added without surgery on `run()`, the CLI, `_lane_stats`,
  or the HTML report. `lanes_for()` is kept as a function (accepting and
  ignoring a `strategy` argument for backward compatibility with existing
  call sites like `bt.lanes_for("ht")` in the golden/characterization
  tests) rather than being inlined, for the same reason.
- `docs/superpowers/specs/2026-08-20-quickflip-ny-design.md` and
  `docs/superpowers/plans/2026-08-20-quickflip-replay.md` — not deleted.
  Both now carry a `**STATUS: DROPPED 2026-08-22**` note at the top stating
  the measured marginal contribution and that the code was removed.
- `.claude/agents/izi.md`'s QuickFlip section — kept as history (mechanics,
  the honesty-pass fixes, the equity-valley-must-sum-every-lane lesson that
  is why `Lane`/`Account` still exist), with a `DROPPED 2026-08-22` note at
  its top carrying the same number and a pointer to the spec/plan. Also
  corrected the RiskManager multi-magic-set note, which previously implied
  QuickFlip was still coming to `mt5/`.

## Verification

- Full suite: **529 passed** (baseline was 569; the -40 drop is exactly the
  39 tests collected from the four deleted `test_qf_*`/`test_quickflip_probe`
  files plus the one deleted `test_both_lane_replay_matches_golden`).
- `service/.venv/bin/python scripts/backtest.py --source bars_max.json
  --balance 10000`: exit 0, no traceback, header carries the `[dataset ...]`
  fingerprint and `strategy halftrend_ema_v1`, trade log and summary print
  normally (1,189 trades, net +$9,008.50), no qf/quickflip line anywhere.
- `node service/tests/backtest_report_smoke.js`: **PASS — all headless
  assertions passed** (both against the raw template and, separately,
  against a real `--web` report generated from `bars_max.json`).
- Generated `--web` report (60-day slice, `bars_max.json`, $10,000):
  0 case-insensitive matches for `quickflip|qf_|QF_L|"qf"|'qf'`.
- The `loose` and `strict` HalfTrend goldens (`test_replay_matches_golden`,
  `test_strict_replay_matches_golden`) and the 21 HalfTrend characterization
  pins (`test_halftrend_characterization.py`) pass unchanged — proof the
  removal did not disturb the first lane. Neither golden file was
  regenerated.

## Final grep

`git grep -ni "quickflip\|qf_\|QF_\|"qf"\|'qf'"` over tracked files, excluding
the two `.superpowers/stage3*-report.md` session artifacts (pre-existing
records of unrelated past work) and the three documents intentionally kept
as historical record (`izi.md`, the spec, the plan), returns only five hits,
all in `scripts/backtest.py`, all explanatory comments naming QuickFlip as
the (removed) reference implementation the `Lane`/`Account` contract was
built for and pointing at the dropped spec — no live identifiers, no
dangling references to code that no longer exists:

```
scripts/backtest.py:42:   ...QuickFlip, the second lane this replay...
scripts/backtest.py:811:  ...QuickFlip was the reference implementation...
scripts/backtest.py:861:  ...caught with QuickFlip, the plug-in lane this contract was...
scripts/backtest.py:1000: ...caught with QuickFlip, the...
scripts/backtest.py:1385: ...like the QuickFlip-vs-...
```

## Concerns

None outstanding. `bars_max.json` was used only for manual verification
runs and was not committed (it is untracked/gitignored, as required).
`service/tests/data/bars_slice.json` was not touched. `mt5/` was not
touched. No new dependencies were added.
