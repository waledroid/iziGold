# Chart visuals — MT5 strategy painting + dashboard price chart

**Date:** 2026-08-03
**Goal:** make the MT5 chart show what the active strategy actually computes
(HalfTrend + EMA lines, readable theme), and replace the dashboard's equity
graph with a live XAUUSD candlestick chart with trades highlighted in context.

## Decisions (from brainstorming)

- MT5: strategy lines AND a clean dark theme, both EA-applied; theme behind
  `input bool ApplyChartTheme = true`.
- Dashboard: equity **graph** replaced by live price chart + trade markers;
  stat tiles stay; `/ui/equity` endpoint stays (unused by the page).
- The uncommitted dashboard work already in the tree (screenshot/render
  thumbnails + lightbox in the trades table, trade events on the old equity
  graph) is reviewed and committed FIRST as the base — it is prior-session
  work that was never reviewed.
- Out of scope: painting for `boll_stochrsi_v1` (no-op default), candle
  persistence across service restarts (window refills next bar), Telegram
  changes (separate spec).

## A. EA side (`mt5/`)

### Theme (SignalManager or new ChartTheme helper in the EA include dir)
On `OnInit` when `ApplyChartTheme`: `ChartSetInteger` — dark background
(#131722-ish), white fg, green bull / red bear candles (solid, same border),
muted grid, no volumes. One function, called once.

### Strategy painting
- `CStrategy` gains `virtual void Paint(datetime bar_time) {}` (no-op) and
  `virtual void ClearPaint() {}`.
- EA calls `active.Paint(closed_bar_time)` once per closed bar in
  `ProcessBar()` (after evaluation), and `ClearPaint()` on strategy switch
  and `OnDeinit`.
- `CHalfTrendEmaStrategy::Paint` draws, for the just-closed bar:
  - HalfTrend segment: `OBJ_TREND` from (prev bar, prev ht) to (bar, ht),
    color DodgerBlue when trend up, OrangeRed when down.
  - EMA segment: same technique, Gold, width 1.
  - Object names prefixed `xau_ht_`/`xau_ema_` + bar time; ray flags off.
  - Keeps a rolling window: after drawing, delete objects older than 500
    bars (by name timestamp). `ClearPaint()` deletes all with the prefixes
    (use `ObjectsDeleteAll(0, prefix)`).
- On attach, backfill: paint the last 300 bars in a loop so the chart is
  immediately populated (strategy exposes the values it computes per bar —
  the strategy already recomputes indicators per bar internally; backfill
  calls the same per-bar computation path).

### Verification
MetaEditor CLI compile 0 errors (warnings reported); visual check by user.

## B. Service + dashboard

### Candle window (`app/main.py` + `app/db.py` NOT touched; in-memory)
- `/analyze` stores `req.candles` (last ≤300) + symbol/timeframe on
  `app.state.recent_candles` (plain dict; single writer — sync endpoint).
- New endpoint `GET /ui/candles` → `{"symbol": str, "timeframe": str,
  "candles": [{t,o,h,l,c,v}...]}`; empty list before first /analyze.

### Dashboard (`app/static/dashboard.html`)
- Commit the existing uncommitted work first (thumbnails + lightbox etc.)
  after review.
- Replace the equity graph panel with a canvas candlestick chart:
  - data: `/ui/candles` (refresh 30s, and on resize redraw);
  - candles: green/red, wick + body, right-aligned latest, ~last 150 shown;
  - trades overlay from `/ui/trades`: ▲ entry marker at open price/time,
    ▼ at close, shaded vertical band across each closed trade's holding
    period (green tint if profit ≥ 0 else red), open trades shaded to the
    right edge with dashed SL line;
  - clicking a marker/band opens the existing lightbox with that trade's
    `/ui/render/{id}` image;
  - tooltip on hover: OHLC + time of the candle under cursor.
- The `equity()` JS function and its interval are removed; everything else
  (state/stats/signals/trades polling) unchanged.

### Tests
- Contract tests: `/ui/candles` empty before analyze; after posting a
  fixture `/analyze`, returns the same candles (tail ≤300), symbol,
  timeframe; second analyze replaces the window.
- Existing test suite stays green.
