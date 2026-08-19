# Mini App Phase 2 — Chart Frontend Implementation Plan

> **Historical note (2026-08-19):** every `9001` in this plan is the
> port as it was when the plan ran. The mini-app port is now
> `MINIAPP_PORT` in `service/.env` (default **9101**).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The interactive Lightweight-Charts page served by the mini-app service: live XAUUSD candles across 7 timeframes with zoom/pan/crosshair, live bid/ask/spread header, entry/SL/TP overlays and a position card, updating over the Phase 1 WebSocket — verifiable in a normal browser at 127.0.0.1:9001 with dev bypass.

**Architecture:** One static page (`miniapp.html`) + one vendored library file, served by `app/miniapp.py` (`GET /` + `/static` mount). The page speaks the Phase 1 contracts exactly: REST `/api/history?tf=` for backfill on TF switch, WS `/ws` for snapshot + deltas. Telegram WebApp SDK is included but optional at this phase (page must work in a plain browser).

**Tech Stack:** TradingView Lightweight Charts (standalone build, vendored, pinned version), vanilla JS/CSS in one HTML file, Telegram `telegram-web-app.js` (Telegram's own CDN — the sole allowed external).

**Spec:** `docs/superpowers/specs/2026-08-14-live-chart-miniapp-design.md` (Phase 2 scope + the frontend section)

## Global Constraints

- No third-party CDNs except `https://telegram.org/js/telegram-web-app.js` (load it with `defer` and feature-detect — the page must fully work when it fails to load, e.g. plain browser).
- Vendor `lightweight-charts` standalone production build (pin 4.2.x) into `service/app/static/vendor/lightweight-charts.standalone.production.js` — commit the file.
- WS client contract (from Phase 1 reviews, binding): treat `snapshot` as a RESET (deltas may arrive before it); parse `positions[]` and `tick` DEFENSIVELY (items may be malformed — skip anything without the expected numeric fields); candle messages only update the chart when `msg.tf` equals the currently displayed TF.
- Feed-offline banner when no WS message for >10 s OR the socket is closed; auto-reconnect with backoff (1s→2s→5s cap); banner clears on recovery. TF switch = fetch history, then continue applying WS candles for that TF.
- Read-only: zero controls that could affect trading. Auth stays as Phase 1 (dev bypass; the page passes `Telegram.WebApp.initData` in a query param/header IF present — plumbing only, validated in Phase 3).
- No changes to `apply_push`/bridge; `app/miniapp.py` gains only page/static serving (and passes the WS the initData plumbing untouched).
- Branch `feat/miniapp-phase2` from `main`; izi.md in the final task; suite green (known flake rule).

---

### Task 1: Serve the page + vendored library; page skeleton with live data

**Files:**
- Modify: `service/app/miniapp.py` (add `GET /` FileResponse + StaticFiles mount for `/static` — viewer-auth NOT required for the page itself, required for data, matching Phase 1's seam)
- Create: `service/app/static/vendor/lightweight-charts.standalone.production.js` (download pinned 4.2.x from unpkg, verify it's the standalone UMD build by checking `LightweightCharts` global in the file header; record version + sha256 in the commit message)
- Create: `service/app/static/miniapp.html`
- Test: `service/tests/test_miniapp.py` (append: `GET /` returns 200 text/html containing `id="chart"`; `/static/vendor/lightweight-charts.standalone.production.js` serves 200 with JS content-type; page route does NOT require viewer auth but `/api/history` still does)

**Interfaces:**
- Consumes (Phase 1, binding): `GET /api/history?tf=` → `{"tf","candles":[{t,o,h,l,c,v}]}`; `WS /ws` → `{"type":"snapshot",tick,positions,tfs}` then `{"type":"tick"|"candle"|"positions",...}`; TFS `["M1","M5","M15","M30","H1","H4","D1"]`.
- Produces: the complete page. Layout: header bar (symbol "XAUUSD", live bid/ask/spread, connection dot), TF button row, chart div (fills viewport), position card (hidden when flat), offline banner (hidden by default).

**Page implementation requirements (write real code for all of these):**
- Chart: `LightweightCharts.createChart` with dark theme (Telegram `themeParams` when available, sensible dark defaults otherwise), candlestick series, `timeScale` with seconds precision, crosshair mode normal, autosize on resize.
- Data mapping: service candle `{t,o,h,l,c}` → LW `{time:t, open:o, high:h, low:l, close:c}` (t is unix seconds — LW accepts numbers directly).
- TF switching: buttons from the snapshot's `tfs` (fallback to the constant list); active button highlighted; switch = `fetch('/api/history?tf=X')` → `series.setData`, remember current TF; WS `candle` messages for the current TF → `series.update` (LW handles same-time replace).
- Live header: `tick` messages update bid/ask/spread text (spread in points, 1 decimal); flash color on change is optional polish, skip if fiddly.
- Overlays: on `positions` messages (and snapshot), remove existing price lines then, for each VALID position (numeric entry/lots), add `createPriceLine` on the series: entry (dashed, neutral color, title "E 0.05"), SL (solid red, title "SL") when sl>0, TP (solid green, title "TP") when tp>0. Position card shows direction/lots/entry/floating $ for the first position (multi-leg: sum lots, lot-weighted entry, sum profit).
- Offline banner + reconnect per Global Constraints; connection dot green/red.
- initData plumbing: if `window.Telegram?.WebApp` exists, call `ready()`, `expand()`, and append `?initData=<encodeURIComponent(initData)>` to the history fetch URLs and WS URL. Harmless in plain browser (empty).

- [ ] Steps: append failing route tests → verify fail → implement miniapp.py serving + download vendor file → write miniapp.html → tests pass → full suite → commit `feat(miniapp): live chart page — Lightweight Charts + WS live updates`.

---

### Task 2: Live verification + izi

**Files:**
- Modify: `.claude/agents/izi.md` (§8: page exists at GET / on 9001, dev-bypass browser check procedure, WS client contract notes — snapshot=reset, defensive parsing, vendored lib version)

- [ ] **Step 1: Data-level live verification** (the bridge + miniapp should be running from Phase 1; restart via setup.sh phase if not): `curl -s 127.0.0.1:9001/ | grep -c chart` ≥1; vendor JS 200; `/api/history?tf=M5` non-empty with dev bypass; a scripted WS client (python websockets, 15 s) records ≥1 tick message and, if a bar closes in the window, a candle message — paste counts in the report.
- [ ] **Step 2: JS sanity** — `node --check` the inline script if node exists in WSL (extract to a temp file), else a careful manual syntax pass; document which.
- [ ] **Step 3: izi.md + full suite + commit** `docs(izi): mini-app chart page (Phase 2)`.

**Owner acceptance (after merge, not a task step):** the owner opens `http://127.0.0.1:9001/` in the Windows browser and sees live candles move — the spec's Phase 2 exit criterion.

---

## Self-Review Notes (applied)

- Spec frontend section ↔ Task 1 requirements list: TFs, zoom/pan/crosshair (LW defaults), live header, entry/SL/TP lines, position card, offline banner, no controls — all present.
- Phase 1 final-review notes carried in as binding WS-client constraints (snapshot=reset, defensive parsing) — plan Global Constraints.
- The page-route-is-unauthenticated choice matches the spec (static page public-ish, DATA auth'd) and Phase 3 hardens the data seam it already uses.
