### Task 7: `--web PATH` — the self-contained report page

**Files:**
- Create: `service/app/static/backtest_report.html` (the template; also the source the Mini App tab will reuse in a later phase)
- Create: `scripts/backtest_report.py` (the writer — keeps `backtest.py` from growing further; it is already ~1,450 lines)
- Modify: `scripts/backtest.py` — add the `--web` flag and call the writer
- Create: `service/tests/test_backtest_web.py`

**Interfaces:**
- Consumes: `build_run_json()` from Task 6.
- Produces: `backtest_report.write_report(artifact: dict, out_path: str) -> None` — inlines the vendored chart library, the template and the artifact into one HTML file.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_web.py`:

```python
"""The report must be one self-contained file: no network, no server."""
import importlib.util
import json
import pathlib
import re

from tests.test_backtest_golden import BARS, _load_bt

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _writer():
    spec = importlib.util.spec_from_file_location(
        "btr", ROOT / "scripts" / "backtest_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, 10000.0, False)
    args = bt.build_parser().parse_args(["--balance", "10000"])
    return bt.build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": dd, "valley": valley})


def test_report_is_written_and_is_not_empty(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    assert out.stat().st_size > 100_000


def test_report_references_no_external_assets(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for m in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', html):
        assert not m.startswith(("http://", "https://", "//")), \
            f"external asset would break offline use: {m}"


def test_report_embeds_the_chart_library_and_the_run(tmp_path):
    out = tmp_path / "r.html"
    art = _artifact()
    _writer().write_report(art, str(out))
    html = out.read_text(encoding="utf-8")
    assert "createChart" in html, "chart library not inlined"
    assert '"trades"' in html, "run artifact not inlined"
    assert str(art["stats"]["trades"]) in html


def test_no_placeholder_survives_into_the_output(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for token in ("__LIB__", "__DATA__", "__TITLE__"):
        assert token not in html, f"unsubstituted placeholder {token}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_web.py -q
```

Expected: FAIL — `scripts/backtest_report.py` does not exist.

- [ ] **Step 3: Write the page template**

Create `service/app/static/backtest_report.html`. Three placeholders are substituted by the writer: `__LIB__`, `__DATA__`, `__TITLE__`.

The chart matches the Mini App exactly (see `service/app/static/miniapp.html` lines ~1415-1435 for the series setup it mirrors): HalfTrend is ONE line whose colour changes per point — `HT_UP_COLOR = "#1e90ff"`, `HT_DOWN_COLOR = "#ff4500"` — plus EMA 9/21/55/200.

Trade boxes are drawn on an overlay `<canvas>` sitting above the chart, using only the public coordinate API (`timeScale().timeToCoordinate()`, `series.priceToCoordinate()`). This deliberately avoids the v4 plugin/primitive internals.

```html
<!doctype html>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0e1116; color:#d7dce3;
         font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:10px 14px; border-bottom:1px solid #232833; }
  h1 { margin:0 0 6px; font-size:15px; font-weight:600; }
  .stats { display:flex; flex-wrap:wrap; gap:14px; font-variant-numeric:tabular-nums; }
  .stat b { display:block; font-size:16px; }
  .stat span { color:#8b95a5; font-size:11px; text-transform:uppercase; }
  .pos { color:#26a69a; } .neg { color:#ef5350; }
  .warn { margin:8px 14px 0; padding:8px 10px; border-radius:6px;
          background:#3a2a12; border:1px solid #6b4a18; color:#f0c674; }
  .caveats { margin:6px 14px 10px; color:#8b95a5; font-size:11px; }
  #wrap { position:relative; height:62vh; margin:0 14px; }
  #chart, #overlay { position:absolute; inset:0; }
  #overlay { pointer-events:none; }
  #legend { position:absolute; top:6px; left:8px; z-index:3; font-size:11px;
            background:rgba(14,17,22,.72); padding:4px 7px; border-radius:4px;
            pointer-events:none; }
  #legend i { font-style:normal; margin-right:8px; }
  table { width:calc(100% - 28px); margin:12px 14px 40px; border-collapse:collapse;
          font-variant-numeric:tabular-nums; }
  th,td { padding:5px 8px; border-bottom:1px solid #1c212b; text-align:right; }
  th { color:#8b95a5; font-weight:500; text-align:right; position:sticky; top:0;
       background:#0e1116; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:#161b24; }
</style>

<header>
  <h1 id="title">XAUUSD backtest</h1>
  <div class="stats" id="stats"></div>
</header>
<div id="warnbox"></div>
<div class="caveats" id="caveats"></div>

<div id="wrap">
  <div id="chart"></div>
  <canvas id="overlay"></canvas>
  <div id="legend">
    <i style="color:#1e90ff">HalfTrend</i><i style="color:#e0e0e0">EMA9</i>
    <i style="color:#ffb74d">EMA21</i><i style="color:#42a5f5">EMA55</i>
    <i style="color:#ab47bc">EMA200</i>
  </div>
</div>

<table>
  <thead><tr><th>#</th><th>Opened</th><th>Dir</th><th>Legs</th><th>Entry</th>
  <th>Exit</th><th>Why</th><th>P/L</th><th>Balance</th></tr></thead>
  <tbody id="rows"></tbody>
</table>

<script>__LIB__</script>
<script>
var RUN = __DATA__;

var HT_UP = "#1e90ff", HT_DOWN = "#ff4500";
var GREEN = "rgba(38,166,154,0.18)", RED = "rgba(239,83,80,0.18)";
var GREEN_L = "#26a69a", RED_L = "#ef5350";

function money(v) { return (v >= 0 ? "+$" : "-$") + Math.abs(v).toFixed(2); }
function stamp(t) { return new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "); }

// ---- header -------------------------------------------------------------
(function header() {
  var s = RUN.stats, m = RUN.meta;
  document.getElementById("title").textContent =
    "XAUUSD " + m.tf + " backtest — " + stamp(m.start) + " to " + stamp(m.end) +
    "  (" + m.bars.toLocaleString() + " bars, " +
    (m.strict_window ? "strict" : "loose") + " entry window)";
  var cells = [
    ["Net P/L", money(s.net), s.net >= 0 ? "pos" : "neg"],
    ["Trades", String(s.trades), ""],
    ["Win rate", s.win_rate.toFixed(1) + "%", ""],
    ["Max drawdown", "$" + s.max_dd.toFixed(2), "neg"],
    ["Balance", "$" + s.start_balance.toFixed(0) + " → $" + s.end_balance.toFixed(2), ""],
    ["Risk taken", s.risk_median.toFixed(2) + "% / p90 " + s.risk_p90.toFixed(2) + "%", ""]
  ];
  document.getElementById("stats").innerHTML = cells.map(function (c) {
    return '<div class="stat"><b class="' + c[2] + '">' + c[1] + "</b><span>" + c[0] + "</span></div>";
  }).join("");
  if (s.clamp_pct > 10) {
    document.getElementById("warnbox").innerHTML =
      '<div class="warn"><b>' + s.clamp_pct.toFixed(1) +
      "% of entries were forced to the 0.01 minimum lot.</b> At this starting " +
      "balance the 1% risk rule is often not obeyed — this run measures " +
      "minimum-lot behaviour as much as the rulebook. $4,000 minimum for " +
      "meaningful results; $10,000+ for a clean test.</div>";
  }
  document.getElementById("caveats").textContent = "Not modelled: " + m.caveats.join(" · ");
})();

// ---- chart --------------------------------------------------------------
var chart = LightweightCharts.createChart(document.getElementById("chart"), {
  layout: { background: { color: "#0e1116" }, textColor: "#8b95a5" },
  grid: { vertLines: { color: "#171b24" }, horzLines: { color: "#171b24" } },
  rightPriceScale: { borderColor: "#232833" },
  timeScale: { borderColor: "#232833", timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 }
});

var noLabel = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
function line(color, width) {
  return chart.addLineSeries(Object.assign({ color: color, lineWidth: width }, noLabel));
}
var ema9 = line("#e0e0e0", 1), ema21 = line("#ffb74d", 1),
    ema55 = line("#42a5f5", 2), ema200 = line("#ab47bc", 1);
var htSeries = chart.addLineSeries(Object.assign({ color: HT_UP, lineWidth: 2 }, noLabel));
var stopSeries = chart.addLineSeries(Object.assign({
  color: "#f0c674", lineWidth: 1, lineStyle: 2,
  lineType: LightweightCharts.LineType.WithSteps }, noLabel));
var candles = chart.addCandlestickSeries({
  upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
  wickUpColor: "#26a69a", wickDownColor: "#ef5350" });

(function feed() {
  var C = RUN.candles, I = RUN.ind, n = C.t.length;
  var bars = [], e9 = [], e21 = [], e55 = [], e200 = [], ht = [];
  for (var i = 0; i < n; i++) {
    var t = C.t[i];
    bars.push({ time: t, open: C.o[i], high: C.h[i], low: C.l[i], close: C.c[i] });
    e9.push(I.ema9[i] == null ? { time: t } : { time: t, value: I.ema9[i] });
    e21.push(I.ema21[i] == null ? { time: t } : { time: t, value: I.ema21[i] });
    e55.push(I.ema55[i] == null ? { time: t } : { time: t, value: I.ema55[i] });
    e200.push(I.ema200[i] == null ? { time: t } : { time: t, value: I.ema200[i] });
    ht.push(I.ht.v[i] == null ? { time: t }
      : { time: t, value: I.ht.v[i], color: I.ht.trend[i] === 0 ? HT_UP : HT_DOWN });
  }
  candles.setData(bars);
  ema9.setData(e9); ema21.setData(e21); ema55.setData(e55); ema200.setData(e200);
  htSeries.setData(ht);

  // stepped stop line: one segment per trade, whitespace between them so
  // separate trades never join into one line
  var stops = [], seen = {};
  RUN.trades.forEach(function (tr) {
    tr.stop_history.forEach(function (h) { if (!seen[h.t]) { seen[h.t] = 1; stops.push({ time: h.t, value: h.stop }); } });
    if (!seen[tr.exit_t]) { seen[tr.exit_t] = 1; stops.push({ time: tr.exit_t, value: tr.stop_history[tr.stop_history.length - 1].stop }); }
    var gap = tr.exit_t + 300;
    if (!seen[gap]) { seen[gap] = 1; stops.push({ time: gap }); }
  });
  stops.sort(function (a, b) { return a.time - b.time; });
  stopSeries.setData(stops);

  // markers: entry, each pyramid add, exit
  var marks = [];
  RUN.trades.forEach(function (tr, i) {
    var up = tr.dir === "BUY";
    marks.push({ time: tr.legs[0].t, position: up ? "belowBar" : "aboveBar",
                 color: up ? GREEN_L : RED_L, shape: up ? "arrowUp" : "arrowDown",
                 text: "#" + (i + 1) });
    tr.legs.slice(1).forEach(function (leg) {
      marks.push({ time: leg.t, position: up ? "belowBar" : "aboveBar",
                   color: "#f0c674", shape: "circle", text: "+" });
    });
    marks.push({ time: tr.exit_t, position: up ? "aboveBar" : "belowBar",
                 color: tr.pl >= 0 ? GREEN_L : RED_L, shape: "square",
                 text: money(tr.pl) });
  });
  marks.sort(function (a, b) { return a.time - b.time; });
  candles.setMarkers(marks);
})();

// ---- trade boxes on the overlay canvas ----------------------------------
var cv = document.getElementById("overlay"), ctx = cv.getContext("2d");

function sizeCanvas() {
  var r = document.getElementById("wrap").getBoundingClientRect();
  var dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  cv.style.width = r.width + "px"; cv.style.height = r.height + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawBoxes() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  var ts = chart.timeScale(), vr = ts.getVisibleRange();
  if (!vr) return;
  RUN.trades.forEach(function (tr) {
    var t0 = tr.legs[0].t, t1 = tr.exit_t;
    if (t1 < vr.from || t0 > vr.to) return;              // off screen
    var x0 = ts.timeToCoordinate(Math.max(t0, vr.from));
    var x1 = ts.timeToCoordinate(Math.min(t1, vr.to));
    if (x0 == null || x1 == null) return;
    var entry = candles.priceToCoordinate(tr.legs[0].px);
    var stop0 = candles.priceToCoordinate(tr.stop_history[0].stop);
    if (entry == null || stop0 == null) return;
    var w = Math.max(1, x1 - x0);
    // risk zone: entry -> initial stop
    ctx.fillStyle = RED;
    ctx.fillRect(x0, Math.min(entry, stop0), w, Math.abs(stop0 - entry));
    // reward zone: entry -> target (fixed mode has no target: nothing drawn)
    if (tr.tp != null) {
      var tp = candles.priceToCoordinate(tr.tp);
      if (tp != null) {
        ctx.fillStyle = GREEN;
        ctx.fillRect(x0, Math.min(entry, tp), w, Math.abs(tp - entry));
      }
    }
    ctx.strokeStyle = tr.pl >= 0 ? GREEN_L : RED_L;
    ctx.lineWidth = 1;
    ctx.strokeRect(x0 + 0.5, Math.min(entry, stop0) + 0.5, w, Math.abs(stop0 - entry));
  });
}

function redraw() { sizeCanvas(); drawBoxes(); }
chart.timeScale().subscribeVisibleTimeRangeChange(drawBoxes);
new ResizeObserver(redraw).observe(document.getElementById("wrap"));
redraw();

// ---- trade table --------------------------------------------------------
document.getElementById("rows").innerHTML = RUN.trades.map(function (t, i) {
  return '<tr data-i="' + i + '"><td>' + (i + 1) + "</td><td>" + stamp(t.legs[0].t) +
    "</td><td>" + t.dir + "</td><td>" + t.legs.length + "</td><td>" +
    t.legs[0].px.toFixed(2) + "</td><td>" + t.exit.toFixed(2) + "</td><td>" +
    t.why + '</td><td class="' + (t.pl >= 0 ? "pos" : "neg") + '">' + money(t.pl) +
    "</td><td>" + t.bal_after.toFixed(2) + "</td></tr>";
}).join("");

document.getElementById("rows").addEventListener("click", function (ev) {
  var tr = ev.target.closest("tr"); if (!tr) return;
  var t = RUN.trades[+tr.dataset.i], pad = 60 * 300;   // 60 M5 bars of context
  chart.timeScale().setVisibleRange({ from: t.legs[0].t - pad, to: t.exit_t + pad });
});
</script>
```

- [ ] **Step 4: Write the writer**

Create `scripts/backtest_report.py`:

```python
#!/usr/bin/env python3
"""Turn a backtest run artifact into ONE self-contained HTML file.

Self-contained on purpose: the report is an artifact you keep, mail, and
compare against next month's. It must open from disk with no server, no
network and no build step, so the chart library, the page and the run data are
all inlined.

The template lives in service/app/static/ so the Mini App tab (spec section 4)
can reuse the same drawing code later instead of growing a second one.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "service" / "app" / "static" / "backtest_report.html"
LIB = ROOT / "service" / "app" / "static" / "vendor" / \
    "lightweight-charts.standalone.production.js"


def write_report(artifact, out_path):
    """artifact: the dict from backtest.build_run_json(). Writes out_path."""
    html = TEMPLATE.read_text(encoding="utf-8")
    meta = artifact.get("meta", {})
    title = f"XAUUSD backtest — {meta.get('bars', 0)} bars, " \
            f"{len(artifact.get('trades', []))} trades"
    # Substitute data LAST: the artifact is arbitrary JSON and must never be
    # re-scanned for placeholder tokens.
    html = html.replace("__TITLE__", title)
    html = html.replace("__LIB__", LIB.read_text(encoding="utf-8"))
    html = html.replace("__DATA__", json.dumps(artifact, separators=(",", ":")))
    Path(out_path).write_text(html, encoding="utf-8")
```

- [ ] **Step 5: Wire `--web` into the CLI**

In `build_parser()`'s **Output** group:

```python
    out.add_argument("--web", default=None, metavar="PATH",
                     help="write a self-contained HTML report (chart with "
                          "HalfTrend/EMA overlays and every trade drawn with "
                          "its SL/TP and stop path) to this file")
```

In `main()`, beside the `--json` handling:

```python
    if args.web:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from backtest_report import write_report
        art = build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        write_report(art, args.web)
        print(f"report     {args.web} "
              f"({Path(args.web).stat().st_size / 1e6:.1f} MB)")
```

Reuse the artifact if `--json` already built one in the same run rather than building it twice.

- [ ] **Step 6: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_web.py -q
```

Expected: 4 passed.

- [ ] **Step 7: Generate the real 12-month report and LOOK at it**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 4000 --web /tmp/backtest12m.html 2>&1 | tail -4
ls -la /tmp/backtest12m.html
```

Then open it (`explorer.exe` from WSL, or copy to the Desktop) and verify by eye:

1. Candles render across the whole 12 months and the page is usable (zoom/pan responsive).
2. HalfTrend is ONE line that changes colour blue↔orange-red — not two lines.
3. All four EMAs are present and the legend matches.
4. Trade boxes appear at trades, with the red risk zone below/above entry and the green target zone on the profit side.
5. The dashed stop line steps up (or down) within a pyramided trade.
6. Clicking a table row zooms the chart to that trade.
7. The header shows net P/L, win rate, drawdown, risk taken, and the caveat line.

Fix what does not match before committing. **Do not skip this step** — the unit tests prove the file is well-formed, not that the chart is right.

- [ ] **Step 8: Full suite**

```bash
cd service && .venv/bin/python -m pytest -q
```

Expected: all pass (437 existing + the new ones), 1 deselected.

- [ ] **Step 9: Commit**

```bash
git add scripts/backtest.py scripts/backtest_report.py \
        service/app/static/backtest_report.html service/tests/test_backtest_web.py
git commit -m "feat(backtest): --web writes a self-contained report with MT5-style overlays and trade boxes"
```

---

