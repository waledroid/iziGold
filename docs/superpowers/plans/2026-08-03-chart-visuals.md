# Chart Visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MT5 chart shows the active strategy's HalfTrend + EMA lines on a readable dark theme; dashboard equity graph becomes a live XAUUSD candlestick chart with trades highlighted.

**Architecture:** EA side — `CStrategy` gets paint enable/clear hooks; `CHalfTrendEmaStrategy` draws its two lines from inside `ProcessClosedBar` (where the values already exist), so the 600-bar warm-up loop backfills the chart for free. Service side — `/analyze` caches its candle window in memory, `/ui/candles` serves it, and the dashboard draws it on the existing canvas with trade overlays from `/ui/trades`.

**Tech Stack:** MQL5 (compiled via MetaEditor CLI from WSL), FastAPI, vanilla JS canvas.

**Spec:** `docs/superpowers/specs/2026-08-03-chart-visuals-design.md`
Deviation from spec (approved here): instead of `Paint(bar_time)` called by the EA per bar, painting is a strategy-internal flag (`EnablePaint`) checked inside `ProcessClosedBar` — same outward behavior, and the strategy's own state (trend, `m_maxLowPrice`/`m_minHighPrice`, EMA buffer) is in scope where the drawing happens.

## Global Constraints

- Python work in `service/`, venv at `service/.venv`; run tests as `cd service && FORECASTER=fake .venv/bin/pytest -q`.
- MQL5 compiles via: `"/mnt/c/Program Files/MetaTrader 5/MetaEditor64.exe" /compile:"$(wslpath -w <data>/MQL5/Experts/XauAssistant.mq5)" /log:"$(wslpath -w <log>)"` after copying sources to the data folder `/mnt/c/Users/aatanda/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075/MQL5/` — or simply run `bash scripts/setup.sh` phase 6 does copy+compile (full run OK, use timeout 200). Require **0 errors, 0 warnings** in the parsed log.
- Chart object name prefixes: exactly `xau_ht_` and `xau_ema_`.
- Rolling paint window: 500 bars. Theme input: `input bool ApplyChartTheme = true`.
- Candle window size cap: 300; endpoint `GET /ui/candles` returns `{"symbol","timeframe","candles"}` with candle keys `t,o,h,l,c,v`.
- Commit prefix `feat(charts):`. The service on :9000 is running; restart it after service changes (`pkill -f 'uvicorn app.main:app'`, then from `service/`: `nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9000 >> service.log 2>&1 &`) and re-verify `/health` (first /analyze after restart is slow — model reload).

---

### Task 1: Review and commit the prior-session dashboard work

**Files:**
- Modify (already modified in working tree, uncommitted): `service/app/static/dashboard.html`, `service/app/static/onboarding.html`

**Interfaces:**
- Produces: committed baseline including `openLightbox(src)` and the trades-table thumbnail columns (`/ui/screenshot/${id}`, `/ui/render/${id}`) that Task 3's chart click-through reuses.

- [ ] **Step 1: Review the diff**

Run: `git diff service/app/static/dashboard.html service/app/static/onboarding.html`
Read the whole diff. Expected content: thumbnail CSS (`.thumb-img`), a lightbox overlay (`#lightbox`, `#lightbox-img`, `openLightbox`), trades table gains `screenshot`/`render` columns, trade-event markers on the equity graph, plus onboarding cosmetic changes. Review for: XSS (all dynamic values must go through the file's existing escaping conventions or be numeric), no inline event handlers receiving unescaped strings (`onclick="openLightbox(this.src)"` is safe — src is same-origin numeric-id URL), no leaking of secrets. If you find a Critical problem, fix it minimally in place and note it in your report.

- [ ] **Step 2: Sanity-check the page serves**

Run: `curl -sf http://127.0.0.1:9000/ui | grep -c "openLightbox\|thumb-img"`
Expected: count ≥ 2 (the served page is the modified file — static files are read per-request).

- [ ] **Step 3: Run the service test suite**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q`
Expected: all pass (static HTML has no pytest coverage; this guards against accidental repo state issues).

- [ ] **Step 4: Commit**

```bash
git add service/app/static/dashboard.html service/app/static/onboarding.html
git commit -m "feat(charts): trade screenshot/render thumbnails + lightbox (prior-session work, reviewed)"
```

---

### Task 2: Candle window + `/ui/candles`

**Files:**
- Modify: `service/app/main.py` (in `analyze()` around line 168-206, and a new route after `ui_signals`)
- Test: `service/tests/test_api.py` (append)

**Interfaces:**
- Consumes: `AnalyzeRequest` (models.py: `symbol`, `timeframe`, `candles: list[Candle]`).
- Produces: `app.state.recent_candles: dict | None` with keys `symbol: str, timeframe: str, candles: list[Candle]`; route `GET /ui/candles` → `{"symbol": str, "timeframe": str, "candles": [{"t","o","h","l","c","v"}...]}` (empty strings + empty list when no analyze yet). Task 3 consumes this route.

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_api.py` (it already has a `client` fixture and candle-payload helpers — reuse the existing fixture names found in the file; the test below assumes a helper `make_analyze_payload()` exists or builds the dict inline exactly like the file's existing analyze tests do):

```python
def test_ui_candles_empty_before_analyze(client):
    r = client.get("/ui/candles")
    assert r.status_code == 200
    body = r.json()
    assert body == {"symbol": "", "timeframe": "", "candles": []}


def test_ui_candles_returns_last_analyze_window(client, analyze_payload):
    # analyze_payload: reuse/extend the file's existing valid /analyze payload
    client.post("/analyze", json=analyze_payload)
    r = client.get("/ui/candles")
    body = r.json()
    assert body["symbol"] == analyze_payload["symbol"]
    assert body["timeframe"] == analyze_payload["timeframe"]
    assert len(body["candles"]) == min(len(analyze_payload["candles"]), 300)
    assert body["candles"][-1]["c"] == analyze_payload["candles"][-1]["c"]


def test_ui_candles_window_capped_at_300(client, analyze_payload):
    base = analyze_payload["candles"][0]
    analyze_payload["candles"] = [
        {**base, "t": base["t"] + i * 300} for i in range(350)
    ]
    client.post("/analyze", json=analyze_payload)
    r = client.get("/ui/candles")
    assert len(r.json()["candles"]) == 300
    assert r.json()["candles"][-1]["t"] == base["t"] + 349 * 300
```

If `test_api.py` has no reusable payload fixture, add one `@pytest.fixture def analyze_payload()` returning the same dict shape the file's existing analyze tests post (200 candles, signal NONE, `strategy_id` any).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q tests/test_api.py -k ui_candles`
Expected: FAIL (404 on /ui/candles).

- [ ] **Step 3: Implement**

In `service/app/main.py`:
1. In the lifespan function, after the existing `app.state` assignments add: `app.state.recent_candles = None`.
2. In `analyze()`, immediately after the request is received (before forecasting) add:

```python
    app.state.recent_candles = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "candles": req.candles[-300:],
    }
```

3. New route next to the other `/ui/*` GET routes:

```python
@app.get("/ui/candles")
def ui_candles():
    rc = app.state.recent_candles
    if not rc:
        return {"symbol": "", "timeframe": "", "candles": []}
    return {"symbol": rc["symbol"], "timeframe": rc["timeframe"],
            "candles": [c.model_dump() for c in rc["candles"]]}
```

- [ ] **Step 4: Run tests to verify they pass, then the whole suite**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q tests/test_api.py -k ui_candles && FORECASTER=fake .venv/bin/pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_api.py
git commit -m "feat(charts): in-memory candle window + /ui/candles endpoint"
```

---

### Task 3: Dashboard price chart replaces the equity graph

**Files:**
- Modify: `service/app/static/dashboard.html`

**Interfaces:**
- Consumes: `GET /ui/candles` (Task 2), existing `GET /ui/trades` (rows have `id, ts, event, direction, price, profit`; verify exact field names by `curl -s http://127.0.0.1:9000/ui/trades`), existing `openLightbox(src)` from Task 1.
- Produces: `priceChart()` JS function; the equity panel's canvas is reused with id unchanged (whatever id the current `equity()` draws to — find it in the file; referred to as `CANVAS_ID` below and in code as the same `$(...)` lookup the old function used).

- [ ] **Step 1: Replace the equity function**

In `dashboard.html`:
1. Change the panel heading text for the graph from equity wording to `XAUUSD — live` (keep the surrounding markup/classes).
2. Delete the body of `async function equity(){...}` and replace with `priceChart` below, reusing the SAME canvas element the old function drew on (keep whatever `$('...')` id it used — do not invent a new element). Delete the old trade-event-on-equity plotting if the prior-session diff added it (superseded).
3. Replace the schedule lines `equity();` / `setInterval(equity,30000);` / `window.addEventListener('resize', equity);` with the same three for `priceChart`.

```javascript
let _pcTrades = [];   // click hit-testing: {x1,x2,tradeId}
async function priceChart(){
 const {candles}=await j('/ui/candles');
 const cv=$( /* SAME id the old equity() used */ 'eq');
 const ctx=cv.getContext('2d');
 const w=cv.width=cv.clientWidth, h=cv.height=cv.clientHeight||220;
 ctx.clearRect(0,0,w,h);
 if(!candles.length){ctx.fillStyle='#888';ctx.fillText('waiting for first bar…',10,20);return;}
 const win=candles.slice(-150);
 const padX=8,padY=14,cw=(w-2*padX)/win.length;
 let lo=Math.min(...win.map(c=>c.l)), hi=Math.max(...win.map(c=>c.h));
 const span=(hi-lo)||1; lo-=span*0.05; hi+=span*0.05;
 const X=i=>padX+i*cw+cw/2, Y=p=>padY+(hi-p)/(hi-lo)*(h-2*padY);
 // trades overlay first (bands under candles)
 let trades=[];
 try{ trades=(await j('/ui/trades?limit=50')).trades||[]; }catch(e){}
 const t0=win[0].t, t1=win[win.length-1].t, barS=win[1]?win[1].t-win[0].t:300;
 const TX=ts=>X(Math.max(0,Math.min(win.length-1,(ts-t0)/barS)));
 _pcTrades=[];
 const opens=trades.filter(t=>t.event==='open').reverse();
 const closes=trades.filter(t=>t.event==='close').reverse();
 for(const o of opens){
   const c=closes.find(cl=>cl.ts>=o.ts);           // first close at/after open
   const x1=TX(o.ts), x2=c?TX(c.ts):X(win.length-1);
   if(x2<padX||x1>w-padX) continue;
   const pnl=c?(c.profit||0):0;
   ctx.fillStyle=c?(pnl>=0?'rgba(46,204,113,.12)':'rgba(231,76,60,.12)'):'rgba(241,196,15,.10)';
   ctx.fillRect(x1,padY,Math.max(x2-x1,3),h-2*padY);
   _pcTrades.push({x1,x2,tradeId:o.id});
   ctx.fillStyle=o.direction==='BUY'?'#2ecc71':'#e74c3c';
   const ey=Y(o.price);
   ctx.beginPath();ctx.moveTo(x1,ey-6);ctx.lineTo(x1-4,ey+3);ctx.lineTo(x1+4,ey+3);ctx.closePath();ctx.fill();
   if(c){const cy=Y(c.price);ctx.fillStyle='#aaa';
     ctx.beginPath();ctx.moveTo(x2,cy+6);ctx.lineTo(x2-4,cy-3);ctx.lineTo(x2+4,cy-3);ctx.closePath();ctx.fill();}
 }
 // candles
 for(let i=0;i<win.length;i++){const c=win[i];
   const up=c.c>=c.o; ctx.strokeStyle=ctx.fillStyle=up?'#2ecc71':'#e74c3c';
   ctx.beginPath();ctx.moveTo(X(i),Y(c.h));ctx.lineTo(X(i),Y(c.l));ctx.stroke();
   const bh=Math.max(1,Math.abs(Y(c.o)-Y(c.c)));
   ctx.fillRect(X(i)-Math.max(1,cw*0.35),Math.min(Y(c.o),Y(c.c)),Math.max(2,cw*0.7),bh);}
 // last-price label
 const last=win[win.length-1];
 ctx.fillStyle='#ddd';ctx.fillText(last.c.toFixed(2), w-58, Y(last.c)-4);
 cv.onclick=e=>{const r=cv.getBoundingClientRect();const x=e.clientX-r.left;
   const hit=_pcTrades.find(t=>x>=t.x1-5&&x<=t.x2+5);
   if(hit) openLightbox('/ui/render/'+hit.tradeId);};
}
```

Adjust two things to the real file while integrating (these are lookups, not design choices): the canvas id in `$('eq')`, and the `/ui/trades` response field names (`ts`/`profit`/`price`/`direction`/`event`/`id`) — check with `curl -s "http://127.0.0.1:9000/ui/trades?limit=5"` and use exactly what the API returns.

- [ ] **Step 2: Verify in the running service**

Run: `curl -sf http://127.0.0.1:9000/ui | grep -c "priceChart" && curl -s http://127.0.0.1:9000/ui/candles | head -c 200`
Expected: `priceChart` present ≥ 3 times (definition + 3 schedule references may fold to ≥3); `/ui/candles` returns candles (the live EA posts every 5 min — if empty, note it and rely on Task 2's tests).
Also open-in-browser check is the user's; note in the report that visual confirmation is pending user.

- [ ] **Step 3: Run the full suite (regression only)**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add service/app/static/dashboard.html
git commit -m "feat(charts): live price chart with trade highlights replaces equity graph"
```

---

### Task 4: EA — paint hooks, HalfTrend/EMA lines, chart theme

**Files:**
- Modify: `mt5/Include/XauAssistant/Strategy.mqh`, `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`, `mt5/Experts/XauAssistant.mq5`

**Interfaces:**
- Consumes: existing `CHalfTrendEmaStrategy::ProcessClosedBar(int shift)` internals (`m_trend`, `m_maxLowPrice`, `m_minHighPrice`, `emaBuf[0]`).
- Produces: `CStrategy::EnablePaint(bool on)`, `CStrategy::ClearPaint()` (virtual, no-op default); `ApplyDarkTheme()` in the EA; object prefixes `xau_ht_`/`xau_ema_`.

- [ ] **Step 1: Add paint hooks to `Strategy.mqh`**

Inside `class CStrategy` add (after the existing virtuals):

```mql5
protected:
   bool m_paint;
public:
   CStrategy() : m_paint(false) {}
   // Painting: the EA enables this on the ACTIVE strategy only. Strategies
   // that support it draw their indicator state per closed bar; default no-op.
   virtual void EnablePaint(bool on) { m_paint = on; if(!on) ClearPaint(); }
   virtual void ClearPaint() {}
```

- [ ] **Step 2: Implement painting in `HalfTrendEma.mqh`**

Add private members: `datetime m_prevPaintBar; double m_prevHt, m_prevEma;` (init `0` in the constructor initializer list). Add private methods:

```mql5
   void DrawSeg(string prefix, datetime t1, double v1, datetime t2, double v2,
                color clr)
     {
      if(t1 == 0 || v1 == 0 || v2 == 0) return;
      string name = prefix + (string)(long)t2;
      if(ObjectFind(0, name) >= 0) return;
      if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, v1, t2, v2)) return;
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, prefix == "xau_ht_" ? 2 : 1);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      // rolling window: drop the segment that just left the 500-bar window
      datetime old = t2 - 500 * PeriodSeconds(PERIOD_CURRENT);
      ObjectDelete(0, prefix + (string)(long)old);
     }

   void PaintBar(int shift, double emaVal)
     {
      if(!m_paint) return;
      datetime bt = iTime(_Symbol, PERIOD_CURRENT, shift);
      double ht = (m_trend == 0) ? m_maxLowPrice : m_minHighPrice;
      color htClr = (m_trend == 0) ? clrDodgerBlue : clrOrangeRed;
      DrawSeg("xau_ht_", m_prevPaintBar, m_prevHt, bt, ht, htClr);
      if(emaVal > 0)
         DrawSeg("xau_ema_", m_prevPaintBar, m_prevEma, bt, emaVal, clrGold);
      m_prevPaintBar = bt; m_prevHt = ht;
      if(emaVal > 0) m_prevEma = emaVal;
     }
```

In `ProcessClosedBar`, at the very end (after the EMA `CopyBuffer` block), add:

```mql5
      double emaForPaint = 0;
      double emaPB[];
      if(CopyBuffer(m_emaHandle, 0, shift, 1, emaPB) == 1) emaForPaint = emaPB[0];
      PaintBar(shift, emaForPaint);
```

(Reuse of the earlier `emaBuf` is fine instead of a second CopyBuffer if it is still in scope at that point — prefer reusing it; the block above is the fallback shape.)

Override in the class:

```mql5
   virtual void ClearPaint()
     {
      ObjectsDeleteAll(0, "xau_ht_");
      ObjectsDeleteAll(0, "xau_ema_");
      ChartRedraw();
     }
```

Note: the warm-up loop in `Evaluate()` (`for(int s = from; s >= 1; s--) ProcessClosedBar(s);`) now backfills up to 600 painted bars automatically when painting is enabled before the first Evaluate. The rolling delete keeps steady-state at ~500; the ~100 extra warm-up segments age out naturally — acceptable.

- [ ] **Step 3: Theme + wiring in `XauAssistant.mq5`**

Add input after `MagicNumber`: `input bool ApplyChartTheme = true;`

Add function before `OnInit`:

```mql5
void ApplyDarkTheme()
  {
   ChartSetInteger(0, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(0, CHART_COLOR_BACKGROUND, C'19,23,34');
   ChartSetInteger(0, CHART_COLOR_FOREGROUND, clrSilver);
   ChartSetInteger(0, CHART_COLOR_GRID, C'42,46,57');
   ChartSetInteger(0, CHART_COLOR_CHART_UP, clrMediumSeaGreen);
   ChartSetInteger(0, CHART_COLOR_CHART_DOWN, clrIndianRed);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BULL, clrMediumSeaGreen);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BEAR, clrIndianRed);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE, clrSilver);
   ChartSetInteger(0, CHART_COLOR_VOLUME, C'42,46,57');
   ChartSetInteger(0, CHART_SHOW_GRID, true);
   ChartSetInteger(0, CHART_SHOW_VOLUMES, CHART_VOLUME_HIDE);
   ChartSetInteger(0, CHART_SHOW_PERIOD_SEP, false);
   ChartRedraw();
  }
```

In `OnInit`, after the `SetActive` success check: `if(ApplyChartTheme) ApplyDarkTheme();` and `g_registry.Active().EnablePaint(true);`
In `ProcessBar`'s pending-switch success branch, BEFORE `SetActive`: `g_registry.Active().EnablePaint(false);` and after successful `SetActive`: `g_registry.Active().EnablePaint(true);`
In `OnDeinit`, before `g_registry.Clear()`: `CStrategy *a = g_registry.Active(); if(a != NULL) a.ClearPaint();`

- [ ] **Step 4: Compile via CLI**

Run `bash scripts/setup.sh` (timeout 200; it copies sources and compiles in phase 6) OR do the copy+compile manually per Global Constraints. Then:
`iconv -f UTF-16LE -t UTF-8 "/mnt/c/Users/aatanda/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075/MQL5/Experts/XauAssistant.setup-compile.log" | grep -i result`
Expected: `0 errors, 0 warnings`. Fix any compile errors (report them verbatim if non-trivial).

- [ ] **Step 5: Commit**

```bash
git add mt5/Include/XauAssistant/Strategy.mqh mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh mt5/Experts/XauAssistant.mq5
git commit -m "feat(charts): EA dark theme + HalfTrend/EMA line painting for active strategy"
```

---

### Task 5: Whole-feature verification + push

**Files:** none new.

- [ ] **Step 1: Full suite + service restart with new code**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q` → green.
Restart the service (Global Constraints pattern), wait for `/health`, then `curl -s http://127.0.0.1:9000/ui/candles | head -c 120` (will be empty until the next EA bar posts — that's expected; say which you observed).

- [ ] **Step 2: Recompile check is current**

Confirm the data-folder `XauAssistant.ex5` mtime is newer than the three modified MQL5 sources (`ls -la`).

- [ ] **Step 3: Push**

```bash
git push
```

Report to the controller: what the user must do — remove + re-attach the EA on the chart (new .ex5 + theme applies on attach), then look: dark theme, blue/orange HalfTrend line, gold EMA, dashboard price chart with trade bands after the next bars arrive.
