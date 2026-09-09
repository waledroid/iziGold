# Task 8 report — documentation reconciliation + on-page disclosure

Branch `feat/backtest-report`. Two commits:
- `52459d6` — `feat(backtest): report discloses which trades' pyramid-add markers are hidden`
- `bdf29b7` — `docs(izi): backtest defaults, balance floors, --json/--web report`

## 1. On-page disclosure (the code change)

`service/app/static/backtest_report.html`, `header()` IIFE: added a second
conditional `.warn` block (same style/class as the existing clamp-rate
warning), computed from `RUN.trades`, that fires exactly when the existing
`SHOW_ADDS = RUN.trades.length <= 300` thinning logic would hide markers:

```js
var addTrades = RUN.trades.filter(function (t) { return t.legs.length > 1; }).length;
if (RUN.trades.length > 300 && addTrades > 0) {
  document.getElementById("warnbox").innerHTML +=
    '<div class="warn">Chart is thinned: pyramid-add ("+") markers are ' +
    "hidden for " + addTrades + " of " + RUN.trades.length + " trades (runs " +
    "over 300 trades drop them to keep the chart responsive). Entry/exit " +
    "markers and the table's Legs column below are unaffected.</div>";
}
```

Also changed the pre-existing clamp-rate warning from `innerHTML =` to
`innerHTML +=` so the two boxes can coexist instead of one overwriting the
other.

**Rendered text a user actually sees**, generated from a real
`--source bars_max.json --days 365 --balance 4000` run (70,707 bars, 1,210
trades, clamp rate 16.7%) — both boxes fire on this run:

> **16.7% of entries were forced to the 0.01 minimum lot.** At this starting
> balance the 1% risk rule is often not obeyed — this run measures
> minimum-lot behaviour as much as the rulebook. $4,000 minimum for
> meaningful results; $10,000+ for a clean test.
>
> Chart is thinned: pyramid-add ("+") markers are hidden for 638 of 1210
> trades (runs over 300 trades drop them to keep the chart responsive).
> Entry/exit markers and the table's Legs column below are unaffected.

638 was computed directly from the generated `--json` artifact
(`len([t for t in trades if len(t["legs"]) > 1])`), i.e. it's the actual
count of trades carrying at least one pyramid add on that run, not just the
trade total — matching what `SHOW_ADDS` actually hides.

The trade table (Legs column) was already complete and untouched.

## 2. Doc figures corrected (old → new)

Measured baseline used for all corrections below, from a real
`--source bars_max.json --days 365 --balance 4000` run:
**70,707 bars, 1,210 trades, 6,271,824 bytes (6.3 MB) `--json`,
6,446,561 bytes (6.4 MB) `--web`.** This also reproduced the exact figures
already in commit `639417d`'s message (net −2874.96, −71.87%, final bal
1125.04, max DD 3035.57), confirming `bars_max.json` hasn't drifted since
that measurement.

`docs/superpowers/specs/2026-08-20-backtest-report-design.md`:
- Line ~98 (`--json` payload size rationale): **"~3–4 MB"** → **"~6–7 MB"**,
  with the measured 6.3/6.4 MB figures added as a sentence.
- Line ~260 (Risks → Page weight): **"~3–4 MB"** → **"~6–7 MB"** (measured
  figures added); the fallback claim **"a timeframe switcher, not a change
  of default"** → rewritten to describe what actually shipped: pyramid-add
  marker thinning above 300 trades (candles/indicators/stop/entry-exit
  markers/trade table always draw in full), with the on-page count
  disclosure from part 1, and a note that a timeframe switcher was
  considered and rejected because it would change what the replay tests,
  not just how it renders.

`docs/superpowers/plans/2026-08-20-backtest-report.md`:
- Line ~1075 (Task 7 expectation): **"a file in the 5–7 MB range"** →
  **"a file in the 6–7 MB range"**, with the measured 6.3/6.4 MB figures
  and trade/bar counts appended.
- Line ~1563 (Later phases, Mini App tab, GZip note): **"the artifact is
  5–7 MB over the wire"** → **"the artifact is 6.3–6.4 MB over the wire
  (measured for a 365-day, 70,707-bar, 1,210-trade run)"**.
- Task 8's own 4 checkboxes (Steps 1–4) ticked `[x]`. I did **not**
  retroactively tick Tasks 1–7's boxes — the brief's "(tick the boxes)"
  note sits inside Task 8's own Files list, next to Task 8's own 4 steps,
  and I have no first-hand verification of the earlier tasks' step-by-step
  execution to responsibly tick on their behalf.

## 3. izi.md reconciliation with Task 7's f3b2cdf section

Task 7's commit (`f3b2cdf`, izi.md:535-570 pre-edit) already correctly
covered `--source`/`--days`/`--help` grouping and the full `--json`/`--web`
report description (page contents, thinning mechanism, shared artifact
build, test coverage) — none of that was touched except two factual
corrections (the MB figures above) and one addition (the on-page disclosure
now existing). What was genuinely missing, because it came from *earlier*
commits (`639417d`, `26627c5`) that were never folded into izi.md at all:
strict-window-is-now-default, `--loose-window`, and the `--balance`
floor/warn/clamp-rate behaviour. I inserted that material into the same
paragraph (no new bullet, no duplicate "Backtesting:" section) and added one
new dated entry to §7 (History) with the strict-vs-loose headline number.

Final state of the reconciled paragraph (`.claude/agents/izi.md:535-590`):

```
- **Backtesting**: `service/.venv/bin/python scripts/backtest.py --balance 4000
  [--verbose]` replays halftrend + the full current money rulebook over the
  accumulated candles (cap 2000 bars ≈ one trading week; memory-only, resets
  on service restart). Validated against reality (reproduced the +$94.81
  live basket within $0.35). Simplifications: bar-close granularity, own
  Wilder ATR/ADX, flat spread charge, no margin model. Un-modeled gates:
  the daily loss brake (MaxDailyLossPct) is not simulated, and neither is
  the news blackout (NewsGuard) — replay results are slightly optimistic vs
  the live rulebook around losing days and high-impact events; both
  omissions are now named in `--help` and on every report's caveats line
  (2026-08-20), so the limitation travels with the output instead of living
  only here. **Strict 3-bar entry window is the default** (2026-08-20): flip
  → wait one closed bar → enter only if the next bar opens beyond EMA-55,
  else the signal is dead until the next flip — this has been the live EA's
  law since 2026-08-16 (`767497a`). `--loose-window` restores the pre-2026-08-20
  replay behaviour (enter on the flip bar itself) for reproducing old
  studies — **every dated report under `.superpowers/` from before
  2026-08-20 is a LOOSE run**; re-running one with today's default gets
  different trades and different P/L. `--balance` refuses below $500 (the
  result would be fiction) and warns below $2,000; the binding constraint is
  the 0.01-minimum-lot floor, not spread — measured over 12 months of M5
  (`bars_max.json`, 365 days): entries clamp to that floor 88.7% of the time
  at $500, 50.8% at $1,200, 10.2% at $4,000, 0.4% at $10,000. Every run now
  reports its own clamp rate and the realized risk actually taken (median /
  p90 vs the 1% target), in stdout, `--json`, and the `--web` page (a
  `>10%` clamp rate renders an on-page warning). `--source
  PATH` replays a saved JSON dump instead of the live 2000-bar cap (e.g.
  `bars_max.json`, ~12 months); `--days N` slices the tail. `--help` groups
  every knob into Data/Rules/Experiments/Output; Rules mirror live EA inputs,
  Experiments default OFF and are study-only. **`--json PATH`** writes the
  full run artifact (meta/stats/candles/indicators/every trade, parallel
  arrays not per-bar objects — 12mo of M5 is ~74k bars). **`--web PATH`**
  (2026-08-20) writes ONE self-contained HTML report from that same artifact
  via `scripts/backtest_report.py:write_report()` — inlines the vendored
  Lightweight Charts lib (`service/app/static/vendor/`), the page template
  (`service/app/static/backtest_report.html` — same file the Mini App tab is
  planned to reuse later so the drawing code isn't duplicated), and the JSON,
  so it opens from disk with no server/network. Shows candles + EMA9/21/55/200
  + HalfTrend (one series, per-point colour, matching the Mini App's MT5-style
  render) + a stepped stop-loss line + a canvas overlay drawing each trade's
  red risk / green reward zone (green zone omitted when `tp` is null, e.g.
  `--entry-mode fixed`) + a complete trade table (row click zooms the chart).
  A real 365-day M5 run measures 70,707 bars / 1,210 trades — 6.3 MB `--json`,
  6.4 MB `--web`; above 300 trades the page thins pyramid-add chart markers
  (entry/exit markers always draw; the trade table's Legs column is never
  thinned) to keep Lightweight Charts responsive — this is a report-rendering
  thinning only, it does not touch replay logic or the artifact JSON. The page
  now discloses this on-page (a `.warn` line naming how many trades' add
  markers it hid, e.g. "638 of 1,210" for that run) so the limit travels with
  the report instead of silently vanishing markers. `--web` and `--json` share one artifact build when both are
  passed together. `--json`/`--web` output is unvalidated by eye beyond the
  automated tests (`service/tests/test_backtest_web.py`,
  `service/tests/backtest_report_smoke.js` — a headless Node smoke test with
  hand-rolled DOM/canvas/LightweightCharts stubs since no npm/browser deps
  were added) — a human should open a generated report at least once after
  any further change to the template or writer.
```

New §7 (History) entry, inserted immediately before `# 7b. Watchdog`:

```
- **2026-08-20 first measured cost of the strict-entry fix** — the backtester
  flipped its default to the strict 3-bar entry window (§3 above), matching
  what the live EA has done since 2026-08-16. Run head-to-head over the same
  12 months (`bars_max.json`, 365 days, $4,000 start): **loose (old replay
  behaviour) net −$1,998.59 (−49.96%), final balance $2,001.41, max drawdown
  $2,576.96** vs **strict (live EA's actual rule) net −$2,874.96 (−71.87%),
  final balance $1,125.04, max drawdown $3,035.57**. Strict is *worse* on
  this window — both deeper loss and deeper drawdown — and it stays the
  default anyway because it is what the EA actually does; a backtester that
  quietly modelled the easier rule would be lying about live risk. This is
  not a verdict on the strategy, it is the first real evidence of what the
  08-16 entry-window fix costs over a year. Strict enters more often than the
  live EA is observed to (4.69 trades/day over the 365-day run vs ~3/day
  observed live on 2026-08-19) — most likely the daily-loss brake, kill
  switch and news blackout this replay still does not model (hypothesis, not
  confirmed; see §3 above and re-check once more live days accumulate).
```

Numbers in that entry are the exact figures from commit `639417d`'s message
(git log, not memory), and I independently reproduced them by re-running
`scripts/backtest.py --source bars_max.json --days 365 --balance 4000`
(strict, default) and `--loose-window` (loose) — both matched to the cent.
No duplication was created: Task 7's `--json`/`--web` prose was edited in
place (two factual corrections + one addition), not appended alongside a
second copy.

## 4. Test results

- `node service/tests/backtest_report_smoke.js` → **PASS**, all 10
  assertions, unchanged (no edits needed — it doesn't assert on `#warnbox`
  content, only on chart/marker/geometry behaviour, and the thinning
  threshold logic itself wasn't touched).
- `cd service && .venv/bin/python -m pytest tests/test_backtest_web.py -q`
  → **5 passed**.
- `cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q`
  → **1 passed** (replay behaviour unaffected — this task never touched
  `scripts/backtest.py`).
- `cd service && FORECASTER=fake .venv/bin/pytest -q` (full fast suite) →
  **469 passed, 1 deselected** (the pre-existing `slow` marker, unrelated to
  this task).

## Notes / judgment calls

- The brief's own strict-vs-loose figures (−50.0%, −71.9%) are the same
  numbers as `639417d`'s exact −49.96%/−71.87%, just rounded to one more
  digit; I recorded the unrounded commit-message figures in izi.md as the
  more precise source of truth.
- Kept the code change (`52459d6`) and the docs change (`bdf29b7`) as two
  separate commits — `feat(backtest):` for the on-page behaviour change,
  `docs(izi):` per the brief's required style for the documentation commit
  — with the docs commit immediately following so the CLAUDE.md izi-sync
  rule ("same commit or an immediate follow-up before pushing") is
  satisfied; nothing else was committed in between.
