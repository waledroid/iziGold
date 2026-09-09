# feat/backtest-report — whole-branch review fix wave

Date: 2026-08-20. Branch: `feat/backtest-report` (no branch/merge/push performed).
Every finding in the review list is fixed. Commits:

| SHA | Title |
|---|---|
| `66999bc` | fix(backtest): stop line survives back-to-back trades; derive bar seconds |
| `3945cae` | fix(backtest): tp tracks the pyramid legs; n/a instead of 0.00% risk |
| `d1ea14b` | fix(backtest): --chart draws real trade spans; every flag documents itself |
| `4ac966e` | test(backtest): golden pin for the shipped strict window; smoke test in CI |
| `ca6ff0f` | docs(izi): re-measure the clamp table under strict; $10,000 is the floor |
| `c14eadb` | fix(backtest): stop-line separators sit on the next real bar |

## Verification performed

- Full fast suite: **479 passed, 1 deselected** (`cd service && .venv/bin/python -m pytest -q`).
  Baseline before the wave was 469 — ten new tests, no flake (the documented
  `test_pop_approved_command_concurrent_exactly_once` flake did not fire).
- `node service/tests/backtest_report_smoke.js` — PASS, 8 sections (4 new
  assertions groups added), including a `node --check` of a real generated report.
- Real regenerated reports:
  - `--source bars_max.json --days 365 --balance 10000 --json --web` → 70,707 bars,
    1,196 trades, 6.3 MB JSON / 6.4 MB HTML.
  - `--source bars_max.json --json --web` (full 516-day source) → 99,999 bars,
    1,729 trades, 8.9 MB JSON / **9,050,951 bytes** HTML.
  - `--tf M15 --days 120`, `--entry-mode fixed --days 120`, `--balance 1200 --days 120`
    each rendered through the shipped template in a Node vm to check page output.
- `--chart` PNG regenerated and inspected visually.

---

## HIGH

### H1 — the stepped stop line silently vanished for ~4% of trades

`service/app/static/backtest_report.html`. The `seen` map was keyed by raw
timestamp across ALL trades. `run()` opens a new basket in the same loop
iteration that closed the previous one, so a reversal exit and the next entry
share a bar; the incoming basket's initial stop point was skipped as a duplicate.

**Fixed** by replacing the "skip duplicates" map with a `time -> point` map that
later trades overwrite, walking trades in chronological order so "newer wins" is
well defined. The whitespace separator is emitted only where no later trade lives
(`gap < nextStart`), because a back-to-back pair has no free bar between them and
a separator one bar past the exit would cut the NEXT basket's line off at its
first step.

**Evidence** — the shipped template script was driven over the real artifact in a
Node vm (`scratchpad/h1_measure.js`), which also replays the old algorithm verbatim
for comparison. Full source, 1,729 trades:

```
SHIPPED (fixed): lost their initial stop point: 0 (0.0%) | <=1 stop point: 0 (0.0%)
OLD (buggy)    : lost their initial stop point: 124 (7.2%) | <=1 stop point: 35 (2.0%)
```

The 124 reproduces the reviewer's number exactly. My "<=1 point" count is 35 rather
than their 71 — a counting-rule difference (I count a trade's `stop_history` points
plus its exit-carry point); the headline metric matches and both go to zero.
Same measurement on the 365-day run: 98 (8.2%) → 0.

**Smoke coverage**: new section 5 builds two trades where B's entry timestamp
equals A's `exit_t` and asserts (a) the shared timestamp carries B's initial stop,
(b) no whitespace lands inside B's span, (c) every trade keeps >= 2 valued points.

---

## MEDIUM

### M2 — `tp` frozen at the first leg

`scripts/backtest.py`. Dropped the `basket.get("tp") is None` guard so `tp` is
recomputed every bar from the current legs. Also made `tp` stay `None` when the
profit target is off (`--profit-target <= 0`), where the old code still drew a
target the run would never take.

**Evidence** (full source, 1,729 trades, 315 target exits, 52% of trades multi-leg):

| | target exits landing SHORT of their drawn tp | median \|exit − tp\| | max |
|---|---|---|---|
| before | 216 / 315 | $2.89 | $34.69 |
| after | **0 / 315** | $1.35 | $41.09 |

Zero short exits is the decisive number: after the fix the fill is always at or
beyond the drawn level, and the residual gap is honest bar-close overshoot (the
replay closes at the first close that crossed the target, not at the target price).
The max grows because a frozen tp could sit on either side; the remaining maximum
is a single large overshoot bar.

`tp` feeds only the artifact and the page — no decision reads it. **The golden
characterization test passes unchanged**, confirming replay behaviour did not move.

New test: `test_tp_tracks_the_legs_so_a_target_exit_never_lands_short_of_it`
(`service/tests/test_backtest_records.py`).

### M3 — fixed mode reported risk of `0.00%`

`run.sizing` now emits `None` for `clamp_pct` / `risk_median` / `risk_p90` when
`entries == 0`, `build_run_json` passes the nulls through, and the page renders
`n/a`. A null clamp rate cannot trip the >10% banner either.

**Evidence**: `--entry-mode fixed --days 120` renders
`... n/a / p90 n/a  Risk taken`. Two new tests in `test_backtest_web.py`
(null in fixed mode; real floats on a risk-sized run) and smoke section 7
(asserts `n/a` present and `0.00%` absent).

### M4 — `--chart` fabricated trade spans

`plot()` drew `axvspan(exit_i - 12, exit_i)` with a comment claiming the entry bar
was "unknown". It has been recorded since `bf3b7e4`. Now uses
`idx[t["legs"][0]["t"]]` for both the span and the entry marker; the false comment
is deleted. `--chart` help text now names it the legacy PNG and points at `--web`.
The flag was kept, per the ruling.

**Evidence**: regenerated PNG over a 10-day slice — spans now vary in width with
real trade duration and entry markers sit on the opening bar.

### M5 — balance guidance contradicted the tool's own distortion threshold

**Re-measured 2026-08-20 on the SHIPPED DEFAULT (strict entry window):**

```
scripts/backtest.py --source bars_max.json --days 365 --balance N
```

| Starting balance | entries clamped to min lot | risk actually taken (median / p90) |
|---|---|---|
| $500 | 94.7% | 1.72% / 34.71% |
| $800 | 68.5% | 1.43% / 3.89% |
| $1,200 | 47.0% | 0.98% / 2.28% |
| $2,000 | 32.3% | 0.90% / 1.77% |
| $4,000 | **16.7%** | 0.89% / 1.27% |
| $10,000 | 1.3% | 0.93% / 0.99% |
| $25,000 | 0.0% | 0.97% / 0.99% |

Over the **full source** (516 days, no `--days`): **20.5%** at $4,000, **3.7%** at
$10,000. The old table (88.7 / 50.8 / 10.2 / 0.4%) was a LOOSE-window measurement.

Guidance moved (the >10% "results distorted" threshold is untouched):
**"$10,000+ for a clean test of the risk rules; $4,000 still clamps roughly one
entry in six."** Updated in:

- `scripts/backtest.py` — constants comment (full table, dated, labelled strict),
  `validate_balance()` refuse text and warn text, the `--help` epilog;
- `service/tests/test_backtest_balance.py` — docstring;
- `docs/superpowers/specs/2026-08-20-backtest-report-design.md` §5 — table replaced,
  with a blockquote keeping the superseded loose figures and explaining *why*
  strict clamps more (it refuses late-drift entries, which are disproportionately
  the wide-stop ones that size fine);
- `docs/superpowers/plans/2026-08-20-backtest-report.md` — a "Superseded" banner on
  Task 4 (the plan is a historical record; its code snippets are left as written);
- `.claude/agents/izi.md`.

### M6 — the shipped default had no golden pin

Added `service/tests/data/golden_trades_strict.json` (87 trades, final balance
$4,239.44, max DD $339.14) over the SAME frozen fixture, plus
`test_strict_replay_matches_golden` and `test_the_two_pins_really_pin_different_runs`
(guards a copy-paste that made both goldens the same file). The docstring now
explains the two-pin arrangement.

**The existing loose golden and `bars_slice.json` were not touched** — confirmed
via `git status`, which showed only `golden_trades_strict.json` as new.

---

## LOW

- **L7** — page now raises a banner when `start_balance` is in `[500, 2000)`,
  independent of the clamp rate. Smoke section 8 checks the band is inclusive at
  $500, exclusive at $2,000, silent at $10,000, and that the separate >10% clamp
  banner still fires on its own and still uses `>10`, not `>=10`.
  Verified on a real `--balance 1200` run.
- **L8** — bar seconds derived from `RUN.candles.t[1] - RUN.candles.t[0]`
  (`BAR_SEC`), used for row-click zoom padding. The separator placement went
  further in `c14eadb`: it is the **next real bar** from the candle series, not
  `exit_t + BAR_SEC`, because a session break or weekend can leave that timestamp
  unoccupied. Measured on `--tf M15 --days 120`: 20 off-grid separator points
  before, **0** after; 0 off-grid on the 12-month M5 run (6,134 stop points) and
  on the $1,200 run.
- **L9** — `--balance`, `--source`, `--verbose`, `--fixed-lots` documented
  (`--balance` states its default and that it changes the result, not just the
  scale). `test_every_flag_documents_itself` now fails if any flag ships helpless.
- **L10** — Experiments blurb rewritten: the filters default OFF because the live
  EA has none, but `--window-start` / `--window-end` are the EA's real window and
  default to its live 4-23. Flags stay where they are, per the ruling.
- **L11** — one line at the end of `main()`, built from the same `CAVEATS` list so
  it cannot drift, plus an end-to-end subprocess test asserting stdout names the
  brake, the kill switch and the news blackout.
- **L12** — `service/tests/test_backtest_report_smoke.py` runs the Node smoke test
  as a subprocess and skips cleanly when `node` is missing. Verified both paths
  (`PATH=/tmp` → 1 skipped with reason; normal → passes). No new dependency.
- **`--strict-window`** — un-SUPPRESSed, with help text saying it is accepted and
  now a no-op because strict is the default. Test asserts it is visible and says
  "no-op".

---

## NITS

- **Break-even trades**: `losses` is now `pl < 0` in both `run()`'s stdout stats and
  `build_run_json`; a `flat` count is carried in the artifact and printed when
  non-zero.
- **Drawdown labels**: header shows `Max drawdown (closed)` and
  `Max valley (open equity)` — `max_valley` was in the artifact but never shown.
- **`.gitignore`**: `bars_max.json` added; `git check-ignore -v` confirms, and it no
  longer appears in `git status`. It was never committed.
- **Artifact sizes qualified**: 6.3 MB / 6.4 MB are `--days 365` figures; the plain
  default over the current source (516 days, 99,999 bars, 1,729 trades) writes
  ~8.9 MB JSON / ~9.0 MB HTML (measured: 9,050,951 bytes). Corrected in the spec
  (twice), the plan (twice) and izi.md.
- **Spec §1.3**: `--exit-scheme` moved to Rules (where it shipped, correctly — its
  default is live behaviour), and a "Shipped deviations" note records both it and
  the mirror-image case of the window flags.
- **Spec §3**: the forced Lightweight Charts v4 deviation recorded — v4 offers only
  `arrowUp`/`arrowDown`/`circle`/`square`, so the requested triangle-at-add and
  X-at-exit shipped as a yellow circle and a P/L-coloured square.
- **izi.md**: "both omissions" → **three** unmodelled rails (daily-loss brake, kill
  switch, news blackout), and they are now named in `--help`, `--json`
  `meta.caveats`, the report page *and* stdout. `test_backtest_golden.py` is now
  documented as the branch's safety net, with the two-pin arrangement and the
  "never regenerate the loose golden" rule, alongside the smoke test and its new
  pytest wrapper. Report-layer facts (stop-line collision rule, `tp` tracking,
  derived bar seconds, both drawdowns) recorded there too.
- **`test_backtest_records.py:4`**: unused `import pathlib` removed. Also removed an
  equally unused `import pytest` from `test_backtest_golden.py` while editing it.

---

## Constraints honoured

- No new Python or Node dependencies; the smoke test still hand-stubs its own
  DOM / canvas / LightweightCharts.
- The page is still self-contained — `test_report_references_no_external_assets`
  passes.
- The daily-loss brake, kill switch and news blackout remain **UNMODELLED**; only
  their disclosure changed.
- `bars_max.json` was never committed (now git-ignored).
- No branch created, nothing merged, nothing pushed.

## Nothing was left unfixed.
