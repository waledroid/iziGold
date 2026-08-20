#!/usr/bin/env node
'use strict';
/*
 * Headless smoke test for service/app/static/backtest_report.html.
 *
 * Node has no DOM, no <canvas>, and no lightweight-charts, and Task 7's
 * constraints forbid adding npm/node dependencies (no jsdom, no headless
 * browser) -- so this hand-rolls minimal stubs for exactly what the
 * template's inline script touches: document, window, ResizeObserver,
 * LightweightCharts. It proves the DATA WIRING is correct -- the shapes and
 * values the template computes and hands to the chart library -- not that
 * the chart looks right on screen. Whether it visually resembles the MT5
 * display is a human call on the real generated file; this script cannot
 * make that call and does not try to.
 *
 * What it checks, in order:
 *   1. `node --check` on the template's inline script (raw, and -- if a real
 *      generated report is present -- with real embedded JSON substituted
 *      in), so a syntax error can never ship silently.
 *   2. Against a small hand-built "known artifact" (2 trades, one with a
 *      pyramid add, one with tp:null to mirror fixed-entry-mode trades):
 *        - candle count fed to the chart matches the artifact
 *        - each EMA series receives one point per candle
 *        - the HalfTrend series carries per-point colours, both HT_UP and
 *          HT_DOWN present, and null HalfTrend bars carry no value/color
 *        - the stepped-stop series contains whitespace gap points between
 *          trades (so two trades' stop lines never visually join)
 *        - markers are sorted ascending by time (Lightweight Charts throws
 *          at runtime on an unsorted array -- this is the one property that
 *          would be a hard runtime crash, not just a cosmetic bug)
 *        - trade-box canvas geometry matches hand-computed coordinates
 *          given stubbed timeToCoordinate/priceToCoordinate functions, and
 *          the tp:null trade draws no green reward-zone rect
 *   3. Against a 301-trade artifact: pyramid-add ("+") markers are thinned
 *      out (entry/exit only) once trade count exceeds the 300 cutoff added
 *      to the template for the real 1,210-trade run; a fixture below the
 *      cutoff keeps its add markers.
 *   4. Markers really are re-sorted, not merely already in order.
 *   5. Back-to-back trades that SHARE a timestamp (a reversal exit and the
 *      next entry land on the same bar) each keep a drawable stop line, with
 *      the NEWER basket owning the shared timestamp.
 *   6. Bar length is derived from the candle series, so --tf M15 runs put
 *      their stop-line gap points on the M15 grid instead of 300 s off it.
 *   7. Header honesty: null risk stats render "n/a" rather than "0.00%",
 *      and both drawdown figures (closed balance, open equity) are shown.
 *   8. A $500-$2,000 starting balance raises its own on-page banner (spec
 *      section 5), independent of the unchanged >10% clamp-rate banner.
 *
 * Run: node service/tests/backtest_report_smoke.js
 * Exit code 0 = every assertion passed. Non-zero + message on stderr = fail.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE_PATH = path.join(ROOT, 'service', 'app', 'static', 'backtest_report.html');

function extractScriptBlocks(html) {
  const re = /<script>([\s\S]*?)<\/script>/g;
  const out = [];
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

function nodeCheck(src, label) {
  const tmp = path.join(os.tmpdir(),
    `btr-check-${Date.now()}-${Math.random().toString(36).slice(2)}.js`);
  fs.writeFileSync(tmp, src);
  try {
    execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
  } catch (e) {
    console.error(`SYNTAX ERROR in ${label}:\n${e.stderr}`);
    process.exit(1);
  } finally {
    fs.unlinkSync(tmp);
  }
  console.log(`ok    node --check: ${label}`);
}

// ---------------------------------------------------------------------------
// 1. Syntax check
// ---------------------------------------------------------------------------
const templateHtml = fs.readFileSync(TEMPLATE_PATH, 'utf8');
const blocks = extractScriptBlocks(templateHtml);
assert.strictEqual(blocks.length, 2,
  `expected 2 <script> blocks in the template, found ${blocks.length} -- ` +
  'template structure changed, update this smoke test');
const [libPlaceholderBlock, mainScriptRaw] = blocks;
assert.strictEqual(libPlaceholderBlock.trim(), '__LIB__',
  'first <script> block is no longer the __LIB__ placeholder -- template changed');

nodeCheck(mainScriptRaw, 'template inline script (raw, __DATA__ unresolved)');

const REAL_REPORT = process.env.BTR_SMOKE_REAL_REPORT ||
  path.join(os.tmpdir(), 'backtest12m.html');
if (fs.existsSync(REAL_REPORT)) {
  const realBlocks = extractScriptBlocks(fs.readFileSync(REAL_REPORT, 'utf8'));
  assert.strictEqual(realBlocks.length, 2, 'real report does not have 2 <script> blocks');
  nodeCheck(realBlocks[1], `generated report inline script (${REAL_REPORT})`);
} else {
  console.log('skip  no generated report on disk (set BTR_SMOKE_REAL_REPORT or ' +
    `run backtest.py --web first; looked at ${REAL_REPORT}) -- the raw-template ` +
    'check above still covers syntax');
}

// ---------------------------------------------------------------------------
// Stub environment
// ---------------------------------------------------------------------------
const HT_UP = '#1e90ff', HT_DOWN = '#ff4500';
const RED = 'rgba(239,83,80,0.18)', GREEN = 'rgba(38,166,154,0.18)';
const GREEN_L = '#26a69a', RED_L = '#ef5350';

function makeCtxStub() {
  const ops = [];
  const ctx = {
    fillStyle: null, strokeStyle: null, lineWidth: null,
    setTransform(...args) { ops.push({ op: 'setTransform', args }); },
    clearRect(x, y, w, h) { ops.push({ op: 'clearRect', x, y, w, h }); },
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h, style: ctx.fillStyle }); },
    strokeRect(x, y, w, h) {
      ops.push({ op: 'strokeRect', x, y, w, h, style: ctx.strokeStyle, lineWidth: ctx.lineWidth });
    },
  };
  ctx.__ops = ops;
  return ctx;
}

/** Runs the template's inline script against `runFixture` in a fresh vm
 * context with recording stubs, and returns everything the assertions need
 * to inspect: the series stubs (with their .data payloads), the candlestick
 * stub (.data / .markers), and the canvas op log. */
function runScenario(runFixture, opts) {
  opts = opts || {};
  const T0 = runFixture.__T0;
  const visibleFrom = opts.visibleFrom != null ? opts.visibleFrom : T0;
  const visibleTo = opts.visibleTo != null ? opts.visibleTo : T0 + 100000;

  function timeToCoordinate(t) { return Math.round((t - T0) / 300 * 10); }
  function priceToCoordinate(price) { return Math.round((3000 - price) * 2); }

  const lineSeriesCalls = [];
  let candlestickStub = null;
  const ctxStub = makeCtxStub();

  const WRAP_RECT = { width: 800, height: 500 };
  const windowStub = { devicePixelRatio: 2 };
  const overlayEl = { style: {}, width: 0, height: 0, getContext: () => ctxStub };
  const wrapEl = { getBoundingClientRect: () => WRAP_RECT };
  const elements = {
    title: { textContent: '' },
    stats: { innerHTML: '' },
    warnbox: { innerHTML: '' },
    caveats: { textContent: '' },
    chart: {},
    overlay: overlayEl,
    wrap: wrapEl,
    legend: {},
    rows: { innerHTML: '', _handlers: {}, addEventListener(t, fn) { this._handlers[t] = fn; } },
  };
  const documentStub = {
    getElementById(id) {
      if (!(id in elements)) throw new Error(`no stub for getElementById(${JSON.stringify(id)})`);
      return elements[id];
    },
  };

  class ResizeObserverStub {
    constructor(cb) { this.cb = cb; }
    observe(el) { this.observedEl = el; }
  }

  const timeScaleStub = {
    getVisibleRange: () => ({ from: visibleFrom, to: visibleTo }),
    timeToCoordinate,
    subscribeVisibleTimeRangeChange(fn) { timeScaleStub._sub = fn; },
    setVisibleRange(r) { timeScaleStub._lastSetRange = r; },
  };

  const LightweightChartsStub = {
    LineType: { WithSteps: 1 },
    createChart(container) {
      assert.strictEqual(container, elements.chart, 'createChart called with the wrong element');
      return {
        addLineSeries(o) {
          const stub = { opts: o, data: null, setData(arr) { stub.data = arr; } };
          lineSeriesCalls.push(stub);
          return stub;
        },
        addCandlestickSeries(o) {
          candlestickStub = {
            opts: o, data: null, markers: null,
            setData(arr) { candlestickStub.data = arr; },
            setMarkers(arr) { candlestickStub.markers = arr; },
            priceToCoordinate,
          };
          return candlestickStub;
        },
        timeScale() { return timeScaleStub; },
      };
    },
  };

  const sandbox = {
    document: documentStub,
    window: windowStub,
    ResizeObserver: ResizeObserverStub,
    LightweightCharts: LightweightChartsStub,
    console,
  };
  vm.createContext(sandbox);
  const code = mainScriptRaw.replace('__DATA__', JSON.stringify(runFixture));
  vm.runInContext(code, sandbox, { filename: 'backtest_report_inline.js' });

  return { lineSeriesCalls, candlestickStub, ctxStub, elements, windowStub, WRAP_RECT };
}

// ---------------------------------------------------------------------------
// 2. Small known artifact: candle/indicator wiring, HalfTrend colours,
//    stepped-stop gaps, marker sort order, hand-computed box geometry.
// ---------------------------------------------------------------------------
const T0 = 1700000000;
const BAR = 300;
const N = 20;

function buildSmallFixture(bar) {
  const BAR = bar || 300;   // bar length in seconds (300 = M5, 900 = M15)
  const t = [], o = [], h = [], l = [], c = [];
  const ema9 = [], ema21 = [], ema55 = [], ema200 = [], htV = [], htTrend = [];
  for (let i = 0; i < N; i++) {
    const time = T0 + i * BAR;
    t.push(time); o.push(2400 + i); h.push(2405 + i); l.push(2395 + i); c.push(2402 + i);
    if (i < 3) {
      ema9.push(null); ema21.push(null); ema55.push(null); ema200.push(null);
      htV.push(null); htTrend.push(null);
    } else {
      ema9.push(2400 + i * 0.1); ema21.push(2399 + i * 0.1);
      ema55.push(2398 + i * 0.1); ema200.push(2397 + i * 0.1);
      htV.push(2396 + i * 0.1); htTrend.push(i % 2);   // both 0 (up) and 1 (down) occur
    }
  }
  return {
    __T0: T0,
    meta: {
      generated_at: T0, source: 'fixture', tf: 'M5', bars: N,
      start: t[0], end: t[N - 1], strict_window: true, entry_mode: 'adr',
      args: {}, caveats: ['news blackout', 'daily-loss brake', 'kill switch'],
    },
    stats: {
      trades: 2, wins: 2, losses: 0, win_rate: 100.0, net: 350.0,
      start_balance: 4000.0, end_balance: 4350.0, max_dd: 50.0, max_valley: 60.0,
      best: 250.0, worst: 100.0, clamp_pct: 0.0, risk_median: 1.0, risk_p90: 1.2,
    },
    candles: { t, o, h, l, c },
    ind: { ema9, ema21, ema55, ema200, ht: { v: htV, trend: htTrend } },
    trades: [
      {
        dir: 'BUY',
        legs: [{ t: T0 + 2 * BAR, px: 2400.00, oz: 100 }, { t: T0 + 4 * BAR, px: 2402.00, oz: 50 }],
        tp: 2410.00,
        stop_history: [{ t: T0 + 2 * BAR, stop: 2395.00 }, { t: T0 + 4 * BAR, stop: 2398.00 }],
        exit: 2405.00, exit_t: T0 + 6 * BAR, why: 'target', pl: 250.00, bal_after: 4250.00,
        regime: 'trend',
      },
      {
        dir: 'SELL',
        legs: [{ t: T0 + 10 * BAR, px: 2420.00, oz: 80 }],
        tp: null,   // mirrors --entry-mode fixed / a trade stopped before a target existed
        stop_history: [{ t: T0 + 10 * BAR, stop: 2425.00 }],
        exit: 2415.00, exit_t: T0 + 12 * BAR, why: 'reversal', pl: 100.00, bal_after: 4350.00,
        regime: 'range',
      },
    ],
  };
}

(function smallArtifactChecks() {
  const fixture = buildSmallFixture();
  const { lineSeriesCalls, candlestickStub, ctxStub, elements, windowStub, WRAP_RECT } =
    runScenario(fixture, { visibleFrom: T0, visibleTo: T0 + 13 * BAR });

  // -- candle count fed to the chart -----------------------------------
  assert.strictEqual(candlestickStub.data.length, N, 'candle count mismatch');
  console.log(`ok    ${N} candles fed to the candlestick series`);

  // -- indicator series lengths ------------------------------------------
  assert.strictEqual(lineSeriesCalls.length, 6,
    'expected 6 addLineSeries calls (ema9, ema21, ema55, ema200, halftrend, stop)');
  const [ema9S, ema21S, ema55S, ema200S, htS, stopS] = lineSeriesCalls;
  assert.strictEqual(ema9S.opts.color, '#e0e0e0');
  assert.strictEqual(ema21S.opts.color, '#ffb74d');
  assert.strictEqual(ema55S.opts.color, '#42a5f5');
  assert.strictEqual(ema200S.opts.color, '#ab47bc');
  for (const s of [ema9S, ema21S, ema55S, ema200S]) {
    assert.strictEqual(s.data.length, N, `EMA series (color ${s.opts.color}) length != candle count`);
  }
  console.log('ok    all 4 EMA series received one point per candle');

  // -- HalfTrend: one series, per-point colour, both colours present ------
  assert.strictEqual(htS.opts.color, HT_UP, 'HalfTrend series base colour should be HT_UP');
  const htColors = new Set(htS.data.filter((p) => 'color' in p).map((p) => p.color));
  assert.ok(htColors.has(HT_UP), 'HalfTrend series never uses HT_UP');
  assert.ok(htColors.has(HT_DOWN), 'HalfTrend series never uses HT_DOWN');
  fixture.ind.ht.v.forEach((v, i) => {
    if (v == null) assert.ok(!('value' in htS.data[i]), `null HalfTrend bar ${i} carries a value`);
  });
  console.log('ok    HalfTrend is one series carrying both HT_UP and HT_DOWN per-point colours');

  // -- stepped-stop series: whitespace gaps between trades -----------------
  // 1 == the stub's LightweightCharts.LineType.WithSteps (see runScenario)
  assert.strictEqual(stopS.opts.lineType, 1,
    'stop series does not use LineType.WithSteps');
  assert.strictEqual(stopS.data.length, 7,
    '2 + 2 stop_history points + 2 exit-carry points + 2 gap points, expected 7');
  for (let i = 1; i < stopS.data.length; i++) {
    assert.ok(stopS.data[i].time > stopS.data[i - 1].time, 'stop series not sorted ascending by time');
  }
  // stopS.data was built inside the vm sandbox (a different V8 realm), so its
  // arrays carry that realm's Array.prototype; re-materialize through the
  // OUTER Array.from before deepStrictEqual, else content-identical arrays
  // fail as "not reference-equal" purely on cross-realm prototype identity.
  const whitespace = Array.from(stopS.data)
    .filter((p) => !('value' in p)).map((p) => p.time);
  assert.deepStrictEqual(whitespace, [T0 + 6 * BAR + BAR, T0 + 12 * BAR + BAR],
    'expected exactly one whitespace gap point after each trade, at exit_t + 300');
  console.log('ok    stepped-stop series has whitespace gaps between the two trades, sorted');

  // -- markers: sorted by time (Lightweight Charts throws if not) ----------
  assert.strictEqual(candlestickStub.markers.length, 5,
    'trade1 (entry+add+exit) + trade2 (entry+exit) = 5 markers expected');
  for (let i = 1; i < candlestickStub.markers.length; i++) {
    assert.ok(candlestickStub.markers[i].time >= candlestickStub.markers[i - 1].time,
      'markers not sorted ascending by time -- setMarkers() throws at runtime on this');
  }
  const shapes = Array.from(candlestickStub.markers).map((m) => m.shape);
  assert.deepStrictEqual(shapes, ['arrowUp', 'circle', 'square', 'arrowDown', 'square'],
    'unexpected marker shape sequence');
  console.log('ok    5 markers, sorted ascending by time, expected shape sequence');

  // -- trade-box canvas geometry (hand-computed) ---------------------------
  assert.strictEqual(elements.overlay.width, WRAP_RECT.width * windowStub.devicePixelRatio);
  assert.strictEqual(elements.overlay.height, WRAP_RECT.height * windowStub.devicePixelRatio);
  assert.strictEqual(elements.overlay.style.width, WRAP_RECT.width + 'px');
  assert.strictEqual(elements.overlay.style.height, WRAP_RECT.height + 'px');
  const setTransformOp = ctxStub.__ops.find((o) => o.op === 'setTransform');
  assert.deepStrictEqual(setTransformOp.args, [2, 0, 0, 2, 0, 0], 'devicePixelRatio not applied via setTransform');

  const clearOp = ctxStub.__ops.find((o) => o.op === 'clearRect');
  assert.ok(clearOp, 'drawBoxes never cleared the canvas');
  assert.strictEqual(clearOp.w, elements.overlay.width);
  assert.strictEqual(clearOp.h, elements.overlay.height);

  const rects = ctxStub.__ops.filter((o) => o.op === 'fillRect' || o.op === 'strokeRect');
  assert.strictEqual(rects.length, 5,
    'trade1: risk fill + reward fill + stroke (3); trade2 (tp:null): risk fill + stroke (2) = 5');

  const close = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} != ${b}`);
  // trade 1 (BUY, tp=2410): entry px 2400 -> coord 1200, stop 2395 -> coord 1210,
  // tp 2410 -> coord 1180; x0 = timeToCoordinate(T0+600) = 20, x1 = timeToCoordinate(T0+1800) = 60, w = 40
  assert.strictEqual(rects[0].op, 'fillRect'); assert.strictEqual(rects[0].style, RED);
  close(rects[0].x, 20, 't1 risk x'); close(rects[0].y, 1200, 't1 risk y');
  close(rects[0].w, 40, 't1 risk w'); close(rects[0].h, 10, 't1 risk h');

  assert.strictEqual(rects[1].op, 'fillRect'); assert.strictEqual(rects[1].style, GREEN);
  close(rects[1].x, 20, 't1 reward x'); close(rects[1].y, 1180, 't1 reward y');
  close(rects[1].w, 40, 't1 reward w'); close(rects[1].h, 20, 't1 reward h');

  assert.strictEqual(rects[2].op, 'strokeRect'); assert.strictEqual(rects[2].style, GREEN_L);
  close(rects[2].x, 20.5, 't1 stroke x'); close(rects[2].y, 1200.5, 't1 stroke y');
  close(rects[2].w, 40, 't1 stroke w'); close(rects[2].h, 10, 't1 stroke h');

  // trade 2 (SELL, tp=null): entry px 2420 -> coord 1160, stop 2425 -> coord 1150;
  // x0 = timeToCoordinate(T0+3000) = 100, x1 = timeToCoordinate(T0+3600) = 120, w = 20
  // no reward rect: tp is null
  assert.strictEqual(rects[3].op, 'fillRect'); assert.strictEqual(rects[3].style, RED);
  close(rects[3].x, 100, 't2 risk x'); close(rects[3].y, 1150, 't2 risk y');
  close(rects[3].w, 20, 't2 risk w'); close(rects[3].h, 10, 't2 risk h');

  assert.strictEqual(rects[4].op, 'strokeRect'); assert.strictEqual(rects[4].style, GREEN_L);
  close(rects[4].x, 100.5, 't2 stroke x'); close(rects[4].y, 1150.5, 't2 stroke y');
  close(rects[4].w, 20, 't2 stroke w'); close(rects[4].h, 10, 't2 stroke h');

  console.log('ok    trade-box geometry matches hand-computed coordinates; tp:null trade draws no reward zone');
})();

// ---------------------------------------------------------------------------
// 3. Marker thinning above the 300-trade cutoff (real run has 1,210 trades)
// ---------------------------------------------------------------------------
(function markerThinningChecks() {
  function manyTrades(n) {
    const trades = [];
    for (let i = 0; i < n; i++) {
      const base = T0 + i * 3 * BAR;
      trades.push({
        dir: i % 2 === 0 ? 'BUY' : 'SELL',
        legs: [{ t: base, px: 2400, oz: 10 }, { t: base + BAR, px: 2401, oz: 5 }],
        tp: 2410, stop_history: [{ t: base, stop: 2395 }],
        exit: 2402, exit_t: base + 2 * BAR, why: 'target', pl: 10, bal_after: 4010,
        regime: null,
      });
    }
    return trades;
  }
  function fixtureWith(n) {
    const f = buildSmallFixture();
    f.trades = manyTrades(n);
    f.stats.trades = n;
    return f;
  }

  const below = runScenario(fixtureWith(300), {
    visibleFrom: T0, visibleTo: T0 + 300 * 3 * BAR + 1000,
  });
  assert.strictEqual(below.candlestickStub.markers.length, 300 * 3,
    'at the 300-trade cutoff, add markers should still be shown (entry+add+exit each)');

  const above = runScenario(fixtureWith(301), {
    visibleFrom: T0, visibleTo: T0 + 301 * 3 * BAR + 1000,
  });
  assert.strictEqual(above.candlestickStub.markers.length, 301 * 2,
    'above the 300-trade cutoff, add markers should be thinned (entry+exit only, ' +
    'matching the real ~1,210-trade run)');
  for (let i = 1; i < above.candlestickStub.markers.length; i++) {
    assert.ok(above.candlestickStub.markers[i].time >= above.candlestickStub.markers[i - 1].time,
      'markers not sorted ascending by time in the thinned (>300-trade) path');
  }

  console.log('ok    add-marker thinning kicks in above 300 trades (300: '
    + `${below.candlestickStub.markers.length} markers, 301: ${above.candlestickStub.markers.length}); `
    + 'exit/entry markers unaffected and still sorted');
})();

// ---------------------------------------------------------------------------
// 4. Markers really are sorted, not just already in order.
//
// Section 2's fixture happens to list its trades in chronological order, so
// its "markers sorted ascending" assertion would also pass with the
// template's `marks.sort(...)` call deleted -- that check alone doesn't
// prove the sort matters. This fixture lists the LATER trade first in
// RUN.trades (out of chronological order, which is otherwise plausible --
// e.g. after a filter/dedupe pass) so only an actual sort produces
// ascending output. Lightweight Charts throws at runtime on an unsorted
// markers array, so this is the one property here that is a crash, not a
// cosmetic bug.
// ---------------------------------------------------------------------------
(function markersAreActuallySortedCheck() {
  const f = buildSmallFixture();
  // swap: index 0 is now the chronologically LATER trade (was trades[1])
  f.trades = [f.trades[1], f.trades[0]];
  const { candlestickStub } = runScenario(f, { visibleFrom: T0, visibleTo: T0 + 13 * BAR });
  const times = Array.from(candlestickStub.markers).map((m) => m.time);
  const sorted = Array.from(times).sort((a, b) => a - b);
  assert.deepStrictEqual(times, sorted,
    'markers were not actually sorted -- input trade order was chronologically reversed ' +
    'and the output should not be; Lightweight Charts throws at runtime on this');
  console.log('ok    markers are actually re-sorted from out-of-chronological-order trade input');
})();

// ---------------------------------------------------------------------------
// 5. Back-to-back trades that SHARE a timestamp keep both stop lines.
//
// run() opens a new basket in the same loop iteration that closed the previous
// one, so a reversal exit and the next entry land on the same bar. A series
// holds one point per timestamp: the NEWER basket's stop must win it. The
// first version of this code skipped the collision as a duplicate, which cost
// 124 of the real run's 1,729 trades their initial stop point and left 71 of
// them with <= 1 point -- no stop line drawn at all.
// ---------------------------------------------------------------------------
(function backToBackStopLineChecks() {
  const f = buildSmallFixture();
  const A_EXIT = T0 + 6 * BAR;
  f.trades = [
    {
      dir: 'BUY',
      legs: [{ t: T0 + 2 * BAR, px: 2400.00, oz: 100 }],
      tp: 2410.00,
      stop_history: [{ t: T0 + 2 * BAR, stop: 2395.00 }],
      exit: 2405.00, exit_t: A_EXIT, why: 'reversal', pl: 250.00, bal_after: 4250.00,
      regime: 'trend',
    },
    {
      // reversal: this basket opens on the very bar that closed the one above
      dir: 'SELL',
      legs: [{ t: A_EXIT, px: 2405.00, oz: 80 }],
      tp: 2395.00,
      stop_history: [{ t: A_EXIT, stop: 2411.00 }, { t: T0 + 8 * BAR, stop: 2409.00 }],
      exit: 2399.00, exit_t: T0 + 9 * BAR, why: 'stop', pl: 100.00, bal_after: 4350.00,
      regime: 'trend',
    },
  ];
  f.stats.trades = 2;
  const { lineSeriesCalls } = runScenario(f, { visibleFrom: T0, visibleTo: T0 + 13 * BAR });
  const stopS = lineSeriesCalls[5];
  const pts = Array.from(stopS.data).map((p) => ({ time: p.time, value: p.value }));
  const at = (time) => pts.find((p) => p.time === time);

  // the shared bar carries the NEWER basket's initial stop, not the older
  // basket's carried-forward one
  assert.ok(at(A_EXIT), 'the shared entry/exit timestamp has no stop point at all');
  assert.strictEqual(at(A_EXIT).value, 2411.00,
    'on a shared timestamp the NEWER basket\'s initial stop must win (got ' +
    `${at(A_EXIT).value}, expected 2411 -- the incoming basket's stop)`);

  // and no whitespace may be dropped inside the second basket's span, which
  // would cut its line off one bar after it starts
  const inside = pts.filter((p) => p.time > A_EXIT && p.time <= T0 + 9 * BAR &&
    p.value === undefined);
  assert.deepStrictEqual(inside, [],
    'whitespace gap point landed inside the second basket, cutting its stop line');

  // every trade still contributes at least 2 valued points (a line, not a dot)
  f.trades.forEach((tr, i) => {
    const span = pts.filter((p) => p.value !== undefined &&
      p.time >= tr.stop_history[0].t && p.time <= tr.exit_t);
    assert.ok(span.length >= 2,
      `trade ${i} has ${span.length} stop point(s) -- no stop line would be drawn`);
  });
  console.log('ok    back-to-back trades sharing a timestamp: newer stop wins, both lines drawn');
})();

// ---------------------------------------------------------------------------
// 6. Bar length is read off the data, not assumed to be 300 s (--tf M15).
// ---------------------------------------------------------------------------
(function barSecondsChecks() {
  const M15 = 900;
  const f = buildSmallFixture(M15);
  f.meta.tf = 'M15';
  const { lineSeriesCalls, elements } = runScenario(f, {
    visibleFrom: T0, visibleTo: T0 + 13 * M15,
  });
  const stopS = lineSeriesCalls[5];
  const whitespace = Array.from(stopS.data)
    .filter((p) => !('value' in p)).map((p) => p.time);
  assert.deepStrictEqual(whitespace, [T0 + 7 * M15, T0 + 13 * M15],
    'stop-line gap points must sit one M15 bar after each exit, on the bar grid');
  // row-click zoom pads by 60 bars of the run's OWN timeframe
  const handler = elements.rows._handlers.click;
  assert.ok(handler, 'no click handler registered on the trade table');
  handler({ target: { closest: () => ({ dataset: { i: '0' } }) } });
  console.log('ok    bar length derived from the candle series (M15 gap points land on the grid)');
})();

// ---------------------------------------------------------------------------
// 7. Header honesty: null risk stats render "n/a", never "0.00%", and both
//    drawdown figures are shown and labelled.
// ---------------------------------------------------------------------------
(function headerHonestyChecks() {
  const f = buildSmallFixture();
  // --entry-mode fixed: nothing is risk-sized, so the engine emits nulls
  f.stats.clamp_pct = null; f.stats.risk_median = null; f.stats.risk_p90 = null;
  f.meta.entry_mode = 'fixed';
  const { elements } = runScenario(f, { visibleFrom: T0, visibleTo: T0 + 13 * BAR });
  const html = elements.stats.innerHTML;
  assert.ok(/n\/a\s*\/\s*p90\s*n\/a/.test(html),
    `null risk stats must render "n/a", got: ${html}`);
  assert.ok(!html.includes('0.00%'),
    '"0.00%" risk reads as "we risked nothing" -- it must not be rendered for nulls');
  assert.ok(!elements.warnbox.innerHTML.includes('minimum lot'),
    'a null clamp rate must not trip the >10% clamp warning');

  // both drawdowns, labelled -- max_valley (open equity) is never smaller
  const clean = runScenario(buildSmallFixture(), { visibleFrom: T0, visibleTo: T0 + 13 * BAR });
  const h2 = clean.elements.stats.innerHTML;
  assert.ok(h2.includes('Max drawdown (closed)') && h2.includes('$50.00'),
    'closed-balance drawdown missing or unlabelled');
  assert.ok(h2.includes('Max valley (open equity)') && h2.includes('$60.00'),
    'open-equity valley (max_valley) is in the artifact but not shown');
  console.log('ok    header shows n/a for null risk stats and both drawdown figures');
})();

// ---------------------------------------------------------------------------
// 8. Balance-band banner (spec section 5): $500-$2,000 warns on the page, not
//    only in stdout, whatever this particular window happened to clamp.
// ---------------------------------------------------------------------------
(function balanceBandChecks() {
  function warnFor(balance, clampPct) {
    const f = buildSmallFixture();
    f.stats.start_balance = balance;
    f.stats.clamp_pct = clampPct;
    return runScenario(f, { visibleFrom: T0, visibleTo: T0 + 13 * BAR })
      .elements.warnbox.innerHTML;
  }
  assert.ok(/\$500.\$2,000 warn band/.test(warnFor(1200, 0.0)),
    'a $1,200 start balance must raise the warn-band banner even at 0% clamp');
  assert.ok(/warn band/.test(warnFor(500, 0.0)),
    'the band is inclusive at its $500 floor');
  assert.ok(!/warn band/.test(warnFor(2000, 0.0)),
    '$2,000 is the top of the band and must not raise it');
  assert.ok(!/warn band/.test(warnFor(10000, 0.0)),
    'a healthy balance must raise no banner');
  // the >10% clamp banner is a separate, unchanged threshold
  assert.ok(/forced to the 0.01 minimum lot/.test(warnFor(10000, 16.7)),
    'the >10% clamp banner must still fire on its own');
  assert.ok(!/forced to the 0.01 minimum lot/.test(warnFor(10000, 10.0)),
    'the "results distorted" threshold stays at >10%, not >=10%');
  console.log('ok    $500-$2,000 start balance raises its own banner; >10% clamp banner unchanged');
})();

console.log('\nPASS -- all headless assertions passed');
