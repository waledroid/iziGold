# Task 7 report — `--web PATH` self-contained report page

## What was built

- `service/app/static/backtest_report.html` — the page template, following the
  brief's template verbatim with one deliberate deviation: pyramid-add ("+")
  markers are only drawn when `RUN.trades.length <= 300` (see "Marker
  thinning" below).
- `scripts/backtest_report.py` — the writer, verbatim from the brief:
  `write_report(artifact, out_path)` inlines the vendored chart library, the
  template and `json.dumps(artifact)` into one HTML file (data substituted
  last, so the arbitrary run JSON is never re-scanned for placeholder
  tokens).
- `scripts/backtest.py` — added `--web PATH` to the Output arg group and
  wired it into `main()`. `art` is now built once and reused by both `--json`
  and `--web` if both flags are passed in the same run.
- `service/tests/test_backtest_web.py` — the four tests from the brief
  (non-empty, no external assets, library+run embedded, no unsubstituted
  placeholders), plus one more (see "Extra regression test" below).
- `service/tests/backtest_report_smoke.js` — the headless Node smoke test
  (see below). Placed under `service/tests/` alongside the Python tests for
  this feature (not `scripts/`) because it tests the same artifact
  (`backtest_report.html`) that `test_backtest_web.py` tests, just from the
  angle Python can't reach — the actual JS the browser runs. Run with:
  `node service/tests/backtest_report_smoke.js`.

## Extra regression test (as requested)

`test_fixed_entry_mode_serializes_tp_as_null_for_every_trade` in
`test_backtest_web.py` runs `bt.run()` in `--entry-mode fixed` over the
golden fixture slice and asserts every trade's serialized `tp` is `None`.
Note: `bt.run()` reads `ENTRY_MODE`/`FIXED_LOTS` as module globals that are
normally only set from `args` inside `main()`; since the test bypasses
`main()` (same pattern as `test_backtest_golden.py`'s `STRICT_WINDOW`), it
sets `bt.ENTRY_MODE = "fixed"` and `bt.FIXED_LOTS = 0.05` directly before
calling `bt.run()`. This is the client-side half of the invariant the
box-drawing code depends on (`if (tr.tp != null)`); the headless smoke test
independently proves the *drawing* code honours a null `tp` correctly (see
below), so the two together cover the full path from serialization to pixel.

## Headless smoke test — design and exact assertions

Node 22 has no DOM, no `<canvas>`, and no `lightweight-charts`, and the task
forbids new npm/node dependencies (no jsdom, no headless browser). The smoke
test hand-rolls the minimal stubs the template's inline script actually
touches (`document`, `window`, `ResizeObserver`, `LightweightCharts`) and
runs the **real, unmodified template script** (extracted from
`backtest_report.html`, not reimplemented) against it inside Node's `vm`
module, in a fresh V8 context per scenario.

**1. Syntax check (`node --check`).** The template's two `<script>` blocks
are extracted by regex; the first must be exactly the `__LIB__` placeholder
(a structural assertion — if this fails, the template changed shape and the
rest of the script's assumptions are stale). The second (main) block is
written to a temp file and checked with `node --check` via
`execFileSync(process.execPath, ['--check', tmp])` — real `node --check`,
not `vm.Script` parsing, per the brief's literal ask. If a real generated
report exists on disk (`/tmp/backtest12m.html` by default, overridable via
`BTR_SMOKE_REAL_REPORT`), its script is extracted and `node --check`'d too —
this is what actually caught nothing wrong, but importantly proves the
writer's JSON substitution of a real 6.4 MB / 1,210-trade payload never
produces invalid JS syntax (unicode, `</script>`-adjacent characters, size,
etc.), which the raw-template check alone can't prove.

**2. Small hand-built "known artifact"** (2 candles-array of 20 M5 bars, 2
trades — one BUY with an add leg and a `tp`, one SELL with `tp: null` to
mirror a fixed-mode/no-target trade) is fed through the real script inside a
stubbed `LightweightCharts`/`document`/canvas-2d context that **records**
every call. Assertions:
- **candle count**: `candlestick.setData()` receives exactly the artifact's
  20 candles.
- **indicator series lengths**: all 4 EMA `setData()` arrays have length 20
  (one point per candle), and each series' `color` option matches the
  Mini-App palette (`#e0e0e0`/`#ffb74d`/`#42a5f5`/`#ab47bc`).
- **HalfTrend per-point colour**: the HalfTrend series' base `color` is
  `HT_UP`; its data points carry a per-point `color`, and both `HT_UP`
  (`#1e90ff`) and `HT_DOWN` (`#ff4500`) actually appear; null HalfTrend bars
  carry no `value`/`color` key at all.
- **stepped-stop whitespace gaps**: hand-counted expected length (7: 2+1
  stop-history points for trade 1 + 1 exit-carry point, 1+1 for trade 2 +
  1 exit-carry point, + 1 gap point per trade = 7), sorted ascending, and the
  two whitespace-only points (no `value` key) land exactly at
  `exit_t + 300` for each trade — proving two trades' stop lines can never
  visually join.
- **marker count and shape sequence**: 5 markers (entry+add+exit for trade 1,
  entry+exit for trade 2), sorted ascending by time, shapes
  `[arrowUp, circle, square, arrowDown, square]`.
- **marker sort has teeth, not just already-sorted input** (section 4 below)
  — see the negative-test note.
- **trade-box geometry**: `sizeCanvas()`'s effect (`cv.width/height`,
  `cv.style.width/height`, the `ctx.setTransform(dpr,0,0,dpr,0,0)` call) is
  checked against a stubbed 800×500 wrap rect and `devicePixelRatio: 2`.
  `drawBoxes()`'s `fillRect`/`strokeRect` calls are hand-computed against
  linear stub `timeToCoordinate`/`priceToCoordinate` functions: trade 1
  (has a `tp`) draws risk-fill + reward-fill + stroke (3 ops) at exact
  `(x, y, w, h)` coordinates worked out by hand from the stub formulas;
  trade 2 (`tp: null`) draws only risk-fill + stroke (2 ops) — **no green
  reward rect**, which is the client-side proof that the `tp: null`
  invariant (pinned server-side by the new regression test above) is
  correctly honoured all the way to pixels.

**3. Marker thinning at the 300-trade cutoff.** Two scenarios — 300 identical
cheap trades and 301 — assert marker count is `n*3` at 300 (adds still
shown) and `n*2` at 301 (adds thinned), matching the `SHOW_ADDS =
RUN.trades.length <= 300` guard added to the template. Both check the
resulting (thinned or not) marker array is still sorted ascending.

**4. Marker sort actually has teeth.** Section 2's fixture happens to list
its two trades in chronological order, so its "sorted ascending" assertion
would still pass even with the template's `marks.sort(...)` deleted — that
alone doesn't prove the sort matters. A dedicated fixture lists the
chronologically-**later** trade first in `RUN.trades` (plausible in
practice — e.g. after a filter/dedupe pass reorders trades) and asserts the
final marker array is nonetheless ascending. **I verified this negative
case concretely**: I temporarily commented out `marks.sort(...)` in the
committed template, reran the smoke test, and section 4's assertion failed
with a clear diff (`[1700003000, 1700003600, 1700000600, ...]` vs sorted);
I then restored the template from a backup and diffed it byte-identical to
the original before rerunning to confirm the restore was clean and the
suite passes again. This is the one property here that is a hard runtime
crash if wrong (Lightweight Charts throws on an unsorted markers array), not
just a cosmetic bug, so I wanted concrete proof the check bites.

Cross-realm gotcha hit and fixed while writing this: objects created inside
the `vm` sandbox belong to a different V8 realm, so `Array.prototype.filter`/
`.map` calls on them return realm-tagged arrays that fail
`assert.deepStrictEqual` against plain literals as "same structure but not
reference-equal" even when content is identical. Fixed by re-materializing
through the outer `Array.from(...)` before any `deepStrictEqual` comparison
(two call sites: the stepped-stop whitespace check and the marker-shape
sequence check).

## Real 12-month artifact: numbers and marker thinning

Ran `scripts/backtest.py --source bars_max.json --days 365 --balance 4000
--web /tmp/backtest12m.html`:

- **1,210 trades**, 70,707+ bars (matches the brief's stated real numbers
  exactly — confirmed via `grep -o '"trades":[0-9]*'` on the output and via
  the printed `report ... (6.4 MB)` line).
- **Report file size: 6,446,354 bytes (6.4 MB)**, generated in ~4.7s.
- Because 1,210 > 300, the template's `SHOW_ADDS` guard is active: **pyramid-
  add markers are omitted, entry and exit markers are always drawn**. This
  is the thinning applied, and the reasoning is: with ~1,210 trades the
  marker candidate count is ~3,600 (entry+add+exit each); dropping the add
  markers cuts that to ~2,420 while keeping the two markers a viewer needs
  to make sense of what a table-row click zoomed to (entry arrow, exit
  square with P/L). The trade **table** is unaffected — it lists all 1,210
  trades regardless of the chart-marker thinning, per the brief's
  instruction to keep it complete.
- I did not further thin candles, the stepped-stop line, or the canvas box
  loop — the box loop already skips off-screen trades early (`if (t1 <
  vr.from || t0 > vr.to) return`, present in the brief's template and left
  unchanged), so it stays O(visible trades) per redraw regardless of total
  trade count.
- I could not measure in-browser frame timing/responsiveness (no browser
  available here); the `--web` command itself completed in ~4.7s end to end
  (backtest replay + artifact build + template substitution + file write),
  which is not a proxy for render smoothness.

## Test results

- `cd service && .venv/bin/python -m pytest -q` → **469 passed, 1 deselected**
  (the pre-existing `slow` marker), including the golden test
  (`test_backtest_golden.py`, 114-trade pin, untouched) and all 5 tests in
  the new `test_backtest_web.py`.
- `node service/tests/backtest_report_smoke.js` → **PASS**, all assertions
  above, run both against the raw template and against the real generated
  12-month report (`node --check` step 1).

## What I could NOT verify without a browser

Per the brief's ruling, the following are explicitly out of scope for me and
need a human (owner) look at the real file
(`/mnt/c/Users/aatanda/Desktop/backtest12m.html`, also at
`/tmp/backtest12m.html`):

1. Whether the chart actually renders and is visually usable (pan/zoom
   responsiveness) with 70k+ candles and 1,210 trades loaded in a real
   browser — the smoke test proves the data handed to the library is
   correctly shaped, not that Lightweight Charts renders it acceptably at
   this scale.
2. Whether the HalfTrend line visually reads as ONE line that changes colour
   blue↔orange-red (vs. looking like two overlapping lines) — confirmed
   structurally (one series, two colours present, per-point colour field)
   but not visually.
3. Whether the trade boxes, stepped stop line, and markers look right
   overlaid on real candles at real screen coordinates (the smoke test uses
   synthetic linear stub coordinate functions, not the real chart's
   time/price scale).
4. Whether clicking a table row visually zooms to the right trade (the
   click handler and `setVisibleRange` call are simple and match the
   brief's template unchanged, but not exercised end-to-end here).
5. General "does this look like MT5" aesthetic judgement.

## Files touched

- `service/app/static/backtest_report.html` (new)
- `scripts/backtest_report.py` (new)
- `scripts/backtest.py` (modified: `--web` flag + `main()` wiring)
- `service/tests/test_backtest_web.py` (new)
- `service/tests/backtest_report_smoke.js` (new)
