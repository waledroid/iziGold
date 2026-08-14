# Live Chart Telegram Mini App — MT5 → FastAPI → WebSocket → Lightweight Charts

**Date:** 2026-08-14 · **Status:** user-approved design (tunnel: Cloudflare
named; access: owner + linked-channel members; build: 3 phases)

## Non-negotiables

- **The main service (port 9000) is never exposed.** Only a new, separate,
  read-only mini-app service (port 9001) goes through the tunnel. MT5,
  broker credentials, the dashboard, and the db stay local-only.
- **Read-only by construction**: the bridge's MetaTrader5 call set contains
  no order/modify functions; the mini app has no controls that touch
  trading. Closing the page affects nothing.
- **No third-party CDNs** in the page except Telegram's own
  `telegram-web-app.js` (required by Telegram). Lightweight Charts is
  vendored into `service/app/static/vendor/`.
- Existing bot behavior unchanged except: ticker message gains a
  [📈 Live Chart] button, `/chart` replies with the same Mini App link
  (PNG render remains only as fallback when the mini app isn't
  configured). Channel copies carry the link as TEXT (the
  no-`reply_markup`-in-channel invariant is structural and stays).
- Fail-open everywhere: bridge down → mini app shows "feed offline",
  trading unaffected; tunnel down → buttons open a dead page, trading
  unaffected.

## Components

### 1. Bridge — `bridge/mt5_feed.py` (Windows Python, long-running)

Uses the `MetaTrader5` package against the running terminal (same
environment `scripts/dump_bars.py` already uses). Loop:

- every ~500 ms: `symbol_info_tick(XAUUSD)` → bid/ask/time; spread derived.
- every ~2 s: for each subscribed TF (M1,M5,M15,M30,H1,H4,D1):
  `copy_rates_from_pos(symbol, tf, 0, 2)` → forming + last closed bar;
  full 500-bar backfill per TF at startup and on reconnect.
- every ~2 s: `positions_get(symbol)` → ticket, direction, lots, entry,
  SL, TP, floating P/L (own-magic filter NOT applied — read-only display
  of the symbol's positions; magic shown per position).

Pushes JSON batches to `http://127.0.0.1:9001/feed/push` with header
`X-Feed-Key: <FEED_KEY>` (random secret generated into `service/.env` by
setup; bridge reads it via the same file). Infinite retry with backoff;
never touches MT5 state. Started by the launcher/setup alongside MT5
(Windows `pythonw`/`start`), one instance (pid/lock file).

### 2. Mini-app service — `service/app/miniapp.py` (own FastAPI app, port 9001)

Run as a second uvicorn process (`uvicorn app.miniapp:app --port 9001
--host 127.0.0.1`; the tunnel is the only public path). State in memory:

- `candles[tf]`: ring buffer (max 500) of OHLCV; forming bar updated in
  place by `t` match (reuses the merge idea from `chart_cmd.py`).
- `tick`: latest bid/ask/spread/time. `positions`: latest snapshot.

Endpoints:

- `POST /feed/push` — bridge only: requires `X-Feed-Key` match; updates
  state; broadcasts deltas to WS clients. 403 otherwise.
- `GET /` — the Mini App page (static).
- `GET /api/history?tf=M5` — the ring buffer for one TF (auth'd).
- `WS /ws` — auth'd; on connect sends a snapshot (tick + positions +
  current TF set); then pushes `{type: "tick"|"candle"|"positions", ...}`
  messages as bridge pushes arrive. No client polling.

**Auth** (enforced from Phase 3; Phase 1–2 use `MINIAPP_DEV_BYPASS=true`
in `.env` for local testing): Telegram WebApp `initData` passed by the
page on every API/WS request; server validates the HMAC-SHA256 signature
against the bot token (Telegram's documented algorithm, with freshness
check on `auth_date`), then authorizes `user.id` == owner chat id OR
`getChatMember(channel_id, user.id)` ∈ {creator, administrator, member}
(Bot API call, result cached 10 min). Bot token / owner id / channel id
are read from the main service's `.env` + profile db (read-only sqlite
open) — the miniapp process never writes the db.

### 3. Frontend — `service/app/static/miniapp.html` + vendored Lightweight Charts

Dark theme (Telegram `themeParams` aware). Features: candlestick series
with zoom/pan/crosshair; TF buttons M1|M5|M15|M30|H1|H4|D1 (history via
`/api/history`, live via the single WS); header with live bid/ask/spread;
entry (dashed) and SL/TP (solid red/green) price lines + a position card
(direction, lots, entry, floating $) whenever positions exist; "feed
offline" banner when the WS drops or the bridge goes stale (>10 s without
a tick); auto-reconnect WS. No trading controls of any kind.

### 4. Telegram wiring (main service; Phase 3)

- One-time BotFather `/newapp` registration → permanent direct link
  `https://t.me/<bot>/chart` (works in private chats AND channels).
  Stored as `MINIAPP_LINK` in `.env`/profile.
- Ticker owner message gains inline URL button `[📈 Live Chart]`
  (`ticker.py`: owner variant only; channel variant appends the link as a
  text line instead).
- `/chart` replies with the link (+ button) when `MINIAPP_LINK` is set;
  falls back to the existing PNG snapshot when not.

### 5. Deployment — ngrok free static domain (amended 2026-08-14)

Owner has no domain; chose ngrok's free tier (one permanent static
domain per account, e.g. `<name>.ngrok-free.app`). `ngrok http
--url=<static-domain> 9001` started by the launcher; safe to skip when
unconfigured (setup prints SKIP). Trade-off accepted: ngrok's free tier
shows a one-tap interstitial ("Visit site") on first browser visit per
session. Owner prerequisites (one-time, ~5 min): free ngrok account,
claim the free static domain, paste authtoken (stored as NGROK_AUTHTOKEN
in service/.env, never committed), BotFather `/newapp` with the static
URL. Upgrade path unchanged: a paid domain + Cloudflare named tunnel is
a pure config swap (one URL setting), no rebuild.

## Phases (separate plan + branch each)

1. **Bridge + backend**: `bridge/mt5_feed.py`, `app/miniapp.py` (state,
   push, history, WS), FEED_KEY generation in setup, dev bypass. Testable
   with curl + a WS client locally; service tests with a fake bridge.
2. **Frontend**: vendored library, the page, TF switching, overlays,
   position card, live WS updates, offline banner. Testable in a normal
   browser at `127.0.0.1:9001` with dev bypass. **Phase 2.5 (owner
   request after seeing the page): indicator overlays** — server
   computes HalfTrend (amplitude 4) + EMA 9/21/55/200 per TF with
   `app/indicators.py` (exact EA math) alongside history; page draws
   them (HalfTrend as blue/red segmented line, EMA-55 gold, EMA-200
   purple, 9/21 dim); live: EMAs advance client-side with the exact
   recurrence, full refresh on bar rollover.
3. **Auth + Telegram + tunnel**: initData validation + channel-membership
   authorization, BotFather registration, ticker button + `/chart`
   repoint + channel text link, cloudflared config + launcher/setup
   integration, izi runbook.

## Testing

Service: unit tests for feed state (ring buffers, forming-bar update),
push auth (key required), history endpoint, WS snapshot+delta with a fake
bridge, initData HMAC validation (known-vector test), membership cache.
Bridge: not unit-testable from WSL (needs terminal) — verified live like
dump_bars; code review + a `--once` self-test mode printing one snapshot.
Frontend: reviewed + manually exercised in browser (Phase 2 exit
criterion: live candle motion visible locally). izi.md per phase.
