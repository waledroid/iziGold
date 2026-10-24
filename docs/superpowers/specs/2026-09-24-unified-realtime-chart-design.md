# Unified Realtime Chart — one spec, one feed, one frontend

**Date:** 2026-09-24 · **Status:** in progress (Phases 0–1 landed, uncommitted; Phases 2–4 pending)

> This is the continuation point for the "unified chart display" work. The
> goal: the same chart looks and behaves identically on the MT5 terminal,
> the Telegram mini-app (`:9101`) and the dashboard (`:9000`), driven by a
> single palette/parameter spec and a single realtime feed — and indicators
> follow price action tick-by-tick. Live entry/TP/SL lines still appear only
> while the EA holds an open basket; the owner-added optional pre-entry
> preview is separate, explicitly toggled, and clearly approximate.

## 1. Problem

1. **Five renderers, drifting.** MT5 native objects, `render.py` matplotlib
   PNGs, `dashboard.html`, `miniapp.html`, and the backtest report each
   re-specify colours/params. Concrete drift found 2026-09-24:

   | Element | MT5 (MQL) | PNG | Dashboard | Mini-app |
   |---|---|---|---|---|
   | HalfTrend up/down | dodgerblue/orangered | dodgerblue/orangered | single `#38bdf8` | dodgerblue/orangered |
   | EMA9 / EMA21 | `82,72,48` / `82,52,52` | `#888888` | gray | `#888888` |
   | EMA trade (55/50) | lime green | gold | `#f5c542` | `#f5c542` |
   | EMA200 | white | mediumpurple | `#a78bfa` | `#a78bfa` |

   The three web surfaces agree; MT5 was the outlier. Decision: **web wins**
   (gold trade-EMA, purple EMA200, gray EMA9/21, dodgerblue/orangered HT).

2. **No realtime.** The dashboard polls `/api/candles` + `/api/overlays`
   every 30 s; indicators are computed on closed bars only; the forming bar
   is static. Only the mini-app has a live feed (bridge WebSocket).

3. **Trade context is not event-driven.** Entry/SL/TP are re-derived per
   surface instead of broadcast as one canonical "basket band" from the EA's
   trade-event stream.

## 2. Non-negotiables

- Single spec (`config/chart_spec.json`) is the only source of colours/params.
- Realtime feed is **read-only and off the trading critical path** (the EA's
  heartbeat/trading must not be affected by chart cadence).
- Fail-open everywhere: feed down → "offline" banner, chart freezes, trading
  unaffected.
- Live entry/TP/SL lines render **only while a basket is open**, and freeze
  as history on close.
- The pre-entry preview is **optional and off by default**. When enabled, it
  is explicitly an approximate service-side estimate; it must never be
  mistaken for, or fed back into, an EA decision.

## 3. Phase 0 — already landed (uncommitted this session)

- `config/chart_spec.json` — palette + params (theme, series, `trade_ema_len`,
  `sr_zones`, `trade_boxes`).
- `service/app/chart_spec.py` — loaders + `hex→mpl/css/mql` converters +
  `blend_mt5(base, alpha)` for MT5's no-alpha objects.
- `service/app/overlays.py` — shared indicator layer (`build_izi5`,
  `build_izi15`, `build_boll`, `indicator_series`), EMA lengths from the spec.
- `service/app/main.py` — overlay builders delegate to `app.overlays`; new
  `GET /api/chart-spec`.
- `service/app/miniapp.py` — `_indicator_series` removed; delegates to
  `overlays.indicator_series`.
- `service/app/render.py` — spec-driven colours + dark theme (background
  `#131722`), so Telegram PNGs match the web/MT5.

Also earlier this session: golden backtest pins re-generated for the 4→22
trading window (737 passed, 1 deselected).

### Phase 1 — landed, uncommitted (2026-10-01)

- [x] **MQL colour edits** to match the spec (source-only; recompile in
      MetaEditor on Windows — cannot compile here). Exact literals:

  | Site | file | old | new (`C'R,G,B'`) |
  |---|---|---|---|
  | HalfTrend up/down | `Strategies/HalfTrendEma.mqh` (~173) | `clrDodgerBlue`/`clrOrangeRed` | `C'30,144,255'` / `C'255,69,0'` |
  | trade EMA | `HalfTrendEma.mqh` (~176) | `clrLimeGreen` | `C'245,197,66'` |
  | EMA9 | `HalfTrendEma.mqh` (~179) | `C'82,72,48'` | `C'136,136,136'` |
  | EMA21 | `HalfTrendEma.mqh` (~180) | `C'82,52,52'` | `C'136,136,136'` |
  | EMA200 | `HalfTrendEma.mqh` (~181) | `clrWhite` | `C'167,139,250'` |
  | S/R support outline | `SrBoxes.mqh` `Outline()` | filled tier tints | `C'173,216,230'` |
  | S/R resistance outline | `SrBoxes.mqh` `Outline()` | filled tier tints | `C'255,245,157'` |
  | risk box | `TradeBoxes.mqh` `RiskColor()` | `C'66,32,36'` | `C'44,29,37'` |
  | profit box | `TradeBoxes.mqh` `ProfitColor()` | `C'26,56,44'` | `C'22,45,43'` |

  (Only the filled trade-box values are `blend(base, #131722, alpha)`;
  outline-only S/R uses the unblended role colours.)

- [x] `service/tests/test_chart_spec.py` — parity test (mirror
      `test_strategy_config.py`): parse `C'R,G,B'` literals from the MQL
      files above and assert they equal the spec / its pre-blend.
- [x] `dashboard.html` + `miniapp.html` consume the shared `chart` object
      carried by `/api/overlays` and `/api/history` (dashboard gains
      spec-driven segmented HalfTrend colouring).
- [x] MACD removed from chart payloads/panels/MT5 default; RSI remains.
- [x] Outline-only S/R on MT5, PNG, dashboard, and mini-app.
- [x] Optional pre-entry preview toggle on both web charts.
- [x] Full fast suite green (`FORECASTER=fake pytest -q`: 751 passed, 1 deselected).

## 4. Design

### A. Single chart spec (Phase 0, done)

`config/chart_spec.json` → `app/chart_spec.py` → `/api/chart-spec`. Frontends
build series from this; renderers derive their own format (CSS hex, matplotlib
RGBA, MQL `C'R,G,B'`). Adding/renaming a colour happens in one file.

### B. Single indicator layer (Phase 0, done)

`app/overlays.py` is the one place EMA/HalfTrend/RSI/MACD/Bollinger series
are built for both the dashboard and the mini-app; EMA lengths come from the
spec (`trade_ema_len`: M15 → 50, else 55). Two wire formats are preserved
intentionally (dashboard `[v, trend]`, mini-app `{v, trend}` + RSI/MACD).

### C. Realtime data path (Phase 2) — indicators follow price in realtime

The bridge (`bridge/mt5_feed.py`) is already the read-only realtime source
(tick ~500 ms, bars ~2 s, positions ~2 s) but only feeds the mini-app. Extend:

- Bridge emits **forming-bar deltas at tick cadence** for the trade TF (M5),
  plus the existing tick/positions batches.
- The main service (`:9000`) gains a realtime push (SSE or WS) fed by the
  bridge, so the dashboard stops polling.
- **Indicator-follows-price via hybrid compute:** the frontend advances the
  *last* indicator point from the live forming close/HL client-side (EMA
  recurrence, HalfTrend using the forming bar's live high/low) on every tick;
  the server does a full recompute on bar rollover as the corrector. (The
  mini-app already advances EMA client-side; extend to HalfTrend and to the
  dashboard.) Full server recompute of ≤500 candles is cheap, so the
  "corrector" may simply be a periodic re-fetch.

### D. Trade band — entry/TP/SL, event-driven (Phase 3)

- The EA already emits `/trade-event` (open/add/close with `tp`, `sl`, legs)
  and `positions` (heartbeat). The service derives **one canonical basket
  band** per lane: `{direction, entry, sl, tp, legs[], state: open|closed,
  last_event}` and serves it (e.g. `GET /api/basket` + live delta).
- **TP is a virtual target** (`m_targetPct` of cycle-start balance →
  `CloseAll`), not a broker order. Display TP price = `tp` from the trade
  event when present, else derived from `entry` and `profit_target_pct`
  (single-leg approximation: `entry * (1 ± pct)`).
- Render rule: entry (dashed) / SL (red) / TP (green) lines + risk/profit
  boxes appear **only while `state == open`**; on close they freeze into
  history markers (mirroring `TradeBoxes.mqh`'s retained history).

### E. Unified frontend component (Phase 4)

Extract one shared chart JS module (series setup from `/api/chart-spec`,
indicator advance, basket band, S/R zones) so `miniapp.html` and
`dashboard.html` stop duplicating chart logic. Still vendored Lightweight
Charts (owner decision: keep it — no TradingView license).

## 5. Realtime architecture options

| Option | Latency | Complexity | Notes |
|---|---|---|---|
| Polling (status quo dashboard) | 30 s | lowest | rejected |
| SSE (one-way push) | ~100 ms | low | good fit for dashboard; auto-reconnect |
| WebSocket (bidirectional) | ~10 ms | medium | mini-app already uses it |
| Server full recompute per tick | ~100 ms | low (n≤500) | simplest, a little wasteful |
| Client-side advance + server corrector | ~10 ms | medium | lowest latency, needs drift guard |

**Recommendation:** keep WS for the mini-app; add **SSE** (or reuse the same
feed) for the dashboard; **hybrid indicator compute** (client advance +
server corrector on rollover). Bridge is the single realtime producer for
both frontends.

```
MT5 terminal
 ├─ EA (trading path) ── /heartbeat 1s ───────────────► main :9000
 └─ bridge mt5_feed.py (read-only, off-path)
      ├─ tick ~500ms ─────────────┐
      ├─ bars (forming+closed) ───┤► miniapp :9101 /feed/push ─► WS ─► miniapp.html
      └─ positions ───────────────┤           │
                                  └► main :9000 (SSE/WS) ─────────► dashboard.html
                                                       ├─ /api/chart-spec  (spec)
                                                       ├─ /api/overlays    (corrector)
                                                       └─ /api/basket      (trade band)
```

## 6. Phased plan

1. **Phase 0 — spec + shared layer** (earlier session): **landed, uncommitted.**
2. **Phase 1 — finish spec rollout:** MQL colour edits + `test_chart_spec.py`
   parity test + both frontends consume the shared `chart` object:
   **landed, uncommitted.**
3. **Phase 2 — realtime feed:** bridge forming-bar tick streaming + SSE/WS on
   the main service + client indicator advance.
4. **Phase 3 — trade band:** canonical basket-band derivation + event-driven
   entry/TP/SL lines (only while open).
5. **Phase 4 — shared frontend chart component.**

## 7. Open questions / decisions

- **SSE vs WS** for the dashboard feed (recommend SSE for simplicity, or reuse
  the mini-app's WS by having the main service proxy it).
- **TP price formula** — confirm the single-leg `entry*(1±pct)` approximation
  is acceptable, or wire the EA to send the exact target price in trade events.
- **Signal preview** — resolved: the owner added an explicit option. The
  preview toggle defaults off, persists per browser, and renders an
  approximate service-side band; it is not an EA pending-state signal.
- **Bridge → main-service path** — have the bridge push to both `:9000` and
  `:9101`, or have the main service subscribe to the mini-app feed (avoids
  double-push but couples the two processes). Recommend bridge pushes both.
