# /chart — real-time chart snapshot on demand

**Date:** 2026-08-11 · **Status:** user-approved design

A `/chart` Telegram command that replies with a freshly rendered chart of the
current market — closed candles **plus the forming bar**, so it is real-time
(≤5 s behind, the heartbeat cadence) rather than waiting for the bar to
close.

## EA change (one file: `mt5/Include/XauAssistant/UiApi.mqh`)

`PostHeartbeat` reads the forming bar itself (no caller/signature change):
`CopyRates(_Symbol, PERIOD_CURRENT, 0, 1, r)` and appends to the heartbeat
JSON, after `algo_trading`:

- `"bar_t"`: bar-0 open time as unix seconds (server time, matching
  `/analyze` candle `t`)
- `"bar_o", "bar_h", "bar_l", "bar_c"`: bar-0 OHLC, `DoubleToString(x, 2)`

On `CopyRates` failure: send all five as `0` (fail-open; service treats 0 as
"no forming bar"). Compile gated 0 errors / 0 warnings via MetaEditor CLI;
EA hot-reloads with existing input values.

## Service changes

- **`models.py`**: `HeartbeatRequest` gains `bar_t: int = 0`,
  `bar_o: float = 0`, `bar_h: float = 0`, `bar_l: float = 0`,
  `bar_c: float = 0` — optional, old EAs keep working.
- **Forming-bar merge** (pure helper, new `app/chart_cmd.py`):
  `merge_forming_bar(candles, hb) -> list` — returns the accumulator candles
  with the forming bar appended, or replacing the last candle when
  `hb.bar_t` equals the last candle's `t` (same bar re-posted), unchanged
  when `bar_t` is 0/absent or older than the last closed candle.
- **`/chart` command** (poller special-case in `main.py`, since
  `handle_command` returns text only): builds the merged series from
  `app.state.recent_candles` + latest heartbeat, renders via the existing
  `render.py` machinery (HalfTrend, EMA 9/21/55/200, latest-price label);
  when the latest heartbeat shows open positions, overlays the basket's
  entry/SL lines the same way trade renders do. Sends `send_photo` to the
  owner with caption `📈 <symbol> <timeframe> — <price> (as of HH:MM:SS)`.
- **Fallbacks (fail-open):** no candle buffer yet → text reply "no candles
  yet — waiting for the first bar post". Heartbeat missing/stale (>60 s) or
  `bar_t=0` → render closed bars only, caption notes "closed bars only".
  Render failure → text reply "chart render failed" (never raises into the
  poller).
- **Channel mirror:** when linked, mirror as `👤 /chart` text + the same
  photo (charts contain prices only — privacy-filter clean by
  construction). Owner first, fail-open, no reply_markup (structural).
- **Pinned help:** add `/chart — current chart snapshot`, bump
  `PINNED_HELP_VERSION` to "5".

## Testing

- `merge_forming_bar`: append case, replace-same-bar case, `bar_t=0` no-op,
  older-than-last no-op.
- `/chart` paths with fake transport: photo reply with caption; no-candles
  text; stale-heartbeat caption; channel mirror photo after owner photo;
  render-failure text.
- Heartbeat contract: new fields optional, old payload still validates,
  response shape unchanged.
- MQL5: CLI compile 0/0; JSON field order matches `HeartbeatRequest`.

## izi.md

Same branch: `/chart` command (what it renders, freshness, fallbacks),
heartbeat's new forming-bar fields, pinned help v5.
