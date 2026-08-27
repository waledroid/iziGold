"""Read-only mini-app feed service (port MINIAPP_PORT, default 9101) —
the ONLY thing the
Phase 3 tunnel will expose. The Windows bridge POSTs batches to
/feed/push; browsers get history over REST and live deltas over one
WebSocket. No trading controls exist here by construction.

Runs as its own process: uvicorn app.miniapp:app --host 127.0.0.1
--port "$MINIAPP_PORT" (scripts/setup.sh passes it; .env is the single
source of truth — the default moved off 9001 on 2026-08-19 because a
Docker mosquitto owns that port on the owner's machine). State is
in-memory only — a restart just refills from the bridge's next backfill
push (fail-open).
"""
import asyncio
import datetime as _dt
import time
import hmac
import math
import sqlite3
import urllib.parse
from collections import deque
from pathlib import Path

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import miniapp_auth
from app.config import settings
from app.indicators import ema, halftrend
from app.models import Candle
from app.reports import (_empty_report, _group_baskets, _report_day,
                          _report_month, _server_date)

STATIC_DIR = Path(__file__).parent / "static"

TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
MAX_CANDLES = 500
CANDLE_FIELDS = {"t", "o", "h", "l", "c", "v"}


class FeedState:
    """Ring-buffered candles per TF + latest tick/positions. apply_push
    validates loosely and returns the delta messages to broadcast —
    garbage in a batch is dropped, never raised (the bridge is trusted
    but the loop must survive anything)."""

    def __init__(self):
        self.candles = {tf: deque(maxlen=MAX_CANDLES) for tf in TFS}
        self.tick = None
        self.positions = []

    def apply_push(self, batch: dict) -> list[dict]:
        deltas = []
        if not isinstance(batch, dict):
            return deltas
        tick = batch.get("tick")
        if isinstance(tick, dict) and "bid" in tick and "ask" in tick:
            self.tick = tick
            deltas.append({"type": "tick", "tick": tick})
        candles = batch.get("candles")
        if isinstance(candles, dict):
            for tf, rows in candles.items():
                if tf not in TFS or not isinstance(rows, list):
                    continue
                buf = self.candles[tf]
                for row in rows:
                    if not isinstance(row, dict) or not CANDLE_FIELDS <= set(row):
                        continue
                    # Validate numeric types (reject bool, NaN, Infinity)
                    if not all(isinstance(row[k], (int, float)) and not isinstance(row[k], bool)
                               and math.isfinite(row[k])
                               for k in ["t", "o", "h", "l", "c", "v"]):
                        continue
                    if buf and row["t"] == buf[-1]["t"]:
                        buf[-1] = row                     # forming-bar update
                    elif buf and row["t"] < buf[-1]["t"]:
                        continue                          # stale, ignore
                    else:
                        buf.append(row)
                    deltas.append({"type": "candle", "tf": tf, "candle": row})
        positions = batch.get("positions")
        if isinstance(positions, list):
            self.positions = positions
            deltas.append({"type": "positions", "positions": positions})
        return deltas


_STARTED = time.time()
app = FastAPI(title="xau-miniapp", docs_url=None, redoc_url=None, openapi_url=None)
# docs_url/redoc_url/openapi_url off: Swagger UI pulls a CDN script and
# /docs, /redoc, /openapi.json are otherwise auth-free by FastAPI default
# (docs_url=None alone leaves /openapi.json registered) -- harmless on
# 127.0.0.1 but not once this process sits behind the public tunnel.
# Mount ONLY the vendor subdirectory, not the whole shared static/ dir —
# this process is the one Phase 3 tunnels publicly, and static/ also holds
# main.py's dashboard.html/onboarding.html (trading controls), which must
# never be reachable from here. The page itself is served by GET / below.
app.mount("/static/vendor", StaticFiles(directory=STATIC_DIR / "vendor"),
          name="static-vendor")


class _Hub:
    """WS clients + broadcast. Slow/dead clients are dropped, never
    awaited to death (send failures disconnect that client only)."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_json(msg), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


state = FeedState()
hub = _Hub()


def viewer_allowed(init_data: str | None) -> bool:
    """Check if viewer is allowed. Shared by REST and WS. Dev bypass is
    the first check, unchanged from Phase 1 (`settings.miniapp_dev_bypass`,
    short-circuits before `init_data` is even looked at). Past that,
    Phase 3's real Telegram initData validation + owner/channel-membership
    authorization lives in `app.miniapp_auth.viewer_ok`."""
    if settings.miniapp_dev_bypass:
        return True
    return miniapp_auth.viewer_ok(init_data)


def require_viewer(request: Request):
    """REST dependency (`GET /api/history`): initData comes from the
    `X-Telegram-Init-Data` header first (so it never lands in a reverse
    proxy's/tunnel's URL access log), falling back to the `?initData=`
    query param for parity with the WS path and manual testing."""
    init_data = request.headers.get("X-Telegram-Init-Data") \
        or request.query_params.get("initData")
    if viewer_allowed(init_data):
        return True
    raise HTTPException(status_code=403, detail="viewer auth required")


@app.post("/feed/push")
async def feed_push(request: Request):
    # Constant-time comparison (security-review fix, 2026-08-15): a plain
    # `!=` leaks timing proportional to the matching-prefix length, which
    # is a real side channel for a bearer-shaped secret sent over the
    # network. `not settings.feed_key` still guards first -- an
    # unconfigured key must fail closed even though
    # `hmac.compare_digest(b"", b"")` alone would return True.
    provided_key = request.headers.get("X-Feed-Key", "")
    if not settings.feed_key or not hmac.compare_digest(
            provided_key.encode("utf-8"), settings.feed_key.encode("utf-8")):
        raise HTTPException(status_code=403, detail="bad feed key")
    try:
        batch = await request.json()
    except Exception:
        return {"ok": False}
    try:
        deltas = state.apply_push(batch)
        state.last_push_ts = time.time()
    except Exception:
        return {"ok": False}
    for d in deltas:
        await hub.broadcast(d)
    # `depth` = the shallowest TF ring buffer. The bridge uses it to detect a
    # service that came back EMPTY (watchdog/deploy restart) and re-send its
    # 500-bar backfill — a restart faster than one push cycle would otherwise
    # leave the chart with 1-2 candles and no indicators (2026-08-17).
    depth = min((len(state.candles[tf]) for tf in TFS), default=0)
    return {"ok": True, "deltas": len(deltas), "depth": depth}


@app.get("/healthz")
def healthz():
    """Auth-free liveness probe (setup.sh's start/restart check uses this,
    not /openapi.json -- that route is gone now that docs are disabled)."""
    # feed_age_s: seconds since the last bridge push (None = never). The
    # watchdog reads this to decide the Windows bridge is dead — the earlier
    # "miniapp.log mtime" proxy was fooled by the watchdog's OWN probes.
    lp = getattr(state, "last_push_ts", None)
    return {"ok": True,
            "feed_age_s": (None if lp is None else round(time.time() - lp, 1)),
            "uptime_s": round(time.time() - _STARTED, 1)}


@app.get("/")
def page():
    """The live chart page itself. Deliberately NOT behind require_viewer —
    Telegram loads this URL directly inside the WebApp webview before any
    initData is available to check; the data endpoints (/api/history, /ws)
    stay gated. Matches Phase 1's auth seam."""
    return FileResponse(STATIC_DIR / "miniapp.html", media_type="text/html")


@app.get("/api/history")
def history(tf: str, _=Depends(require_viewer)):
    if tf not in TFS:
        raise HTTPException(status_code=400, detail=f"unknown tf {tf}")
    rows = list(state.candles[tf])
    return {"tf": tf, "candles": rows, **_indicator_series(rows, tf)}


# Trading-EMA length per timeframe: the M15 lane trades EMA 45 (2026-08-27
# sweep), everything else keeps 55. The "ema55" key below is the series
# SLOT the frontend binds to, not a promise about the length — the legend
# label follows the tab's timeframe in miniapp.html.
_TRADE_EMA_LEN = {"M15": 45}


def _indicator_series(rows: list[dict], tf: str = "M5") -> dict:
    """EMA-9/21/trade/200 + HalfTrend(amplitude=4), computed fresh from the
    ring buffer on every request (<=500 candles: cheap, no cache needed).
    Uses the exact same app.indicators math the render.py trade-chart PNGs
    use, so the mini-app overlays match the EA's live MT5 chart.
    ema()/halftrend() already degrade to all-None/empty for short input
    (see their docstrings), so a <2-candle TF naturally yields empty/null
    arrays here without any special-casing.
    """
    closes = [r["c"] for r in rows]
    candle_objs = [Candle(**r) for r in rows]
    ht = halftrend(candle_objs, amplitude=4)
    return {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema55": ema(closes, _TRADE_EMA_LEN.get(tf, 55)),
        "ema200": ema(closes, 200),
        "halftrend": [({"v": e[0], "trend": e[1]} if e else None) for e in ht],
    }


TRADES_DEFAULT_LIMIT = 50
TRADES_MAX_LIMIT = 500   # server-side ceiling: a viewer can never force a full-table scan


def _open_trades_db_ro() -> sqlite3.Connection:
    """Same read-only open pattern as `miniapp_auth._resolve_credentials`:
    a `file:...?mode=ro` URI against `settings.db_path` with `timeout=1.0`
    so lock contention fails fast. This module must never write to the
    trading db (see miniapp_auth's module docstring) and must not import
    `app.main`/`app.db` (separate uvicorn processes, no shared writable
    SignalDb instance to reuse)."""
    uri = f"file:{urllib.parse.quote(settings.db_path, safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)


@app.get("/api/trades")
def trades(limit: int = TRADES_DEFAULT_LIMIT, _=Depends(require_viewer)):
    """Last `limit` trade-log rows + server-grouped baskets for the
    mini-app's past-trade markers. Fail-open like every other read here:
    ANY error (db missing, table missing, locked, garbage row) returns
    200 with empty lists rather than a 500 -- the chart must still render
    with no markers."""
    try:
        conn = _open_trades_db_ro()
        try:
            cur = conn.execute(
                "SELECT id, ts, event, direction, lots, price, profit, final "
                "FROM trades ORDER BY id DESC LIMIT ?",
                (min(max(int(limit), 0), TRADES_MAX_LIMIT),))
            raw = cur.fetchall()
        finally:
            conn.close()
        raw.reverse()  # ascending id order for basket grouping
        rows = [
            {"id": r[0], "ts": r[1], "event": r[2], "direction": r[3],
             "lots": r[4], "price": r[5], "profit": r[6], "final": r[7]}
            for r in raw
        ]
        baskets = _group_baskets(rows)
        return {"trades": rows, "baskets": baskets}
    except Exception:
        return {"trades": [], "baskets": []}


@app.get("/api/report")
def report(view: str = "month", month: str | None = None, date: str | None = None,
           _=Depends(require_viewer)):
    """Trades report for the mini-app's Trades tab. `view=month&month=YYYY-MM`
    (default: the current broker-calendar month) -> one row per trading
    day; `view=day&date=YYYY-MM-DD` -> one row per CLOSED basket that day.
    Day boundaries are broker server days (SERVER_UTC_OFFSET_H). Malformed
    params -> 400; every db-side failure (missing db/table, lock, garbage)
    is fail-open: 200 with empty rows, never a 500."""
    if view not in ("month", "day"):
        raise HTTPException(status_code=400, detail="view must be month|day")
    now_server = _server_date(int(time.time()))
    if view == "month":
        key = month or now_server.strftime("%Y-%m")
        try:
            y, m = key.split("-")
            y, m = int(y), int(m)
            if not (1 <= m <= 12 and 2000 <= y <= 2100):
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="month must be YYYY-MM")
        key = f"{y:04d}-{m:02d}"
    else:
        key = date or now_server.isoformat()
        try:
            d = _dt.date.fromisoformat(key)
        except Exception:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        key = d.isoformat()
    try:
        conn = _open_trades_db_ro()
        try:
            return _report_month(conn, y, m) if view == "month" else _report_day(conn, d)
        finally:
            conn.close()
    except Exception:
        return _empty_report(view, key)


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    # Browsers can't set custom headers on a WS handshake, so initData
    # rides the connection URL's query string here -- no header option
    # exists for this call site (see require_viewer for the REST path).
    # viewer_allowed() is sync and can do real blocking work on a
    # membership-cache miss (sqlite open + a 5 s httpx.get) -- run it off
    # the event loop so it can't freeze every other client's broadcasts
    # and handshakes while it waits. Also defensively caught: an
    # unhandled exception here must still produce the mandated 4403
    # close, never a bare crash of the handshake.
    try:
        allowed = await asyncio.to_thread(viewer_allowed, ws.query_params.get("initData"))
    except Exception:
        allowed = False
    if not allowed:
        await ws.close(code=4403)
        return
    await ws.accept()
    hub.clients.add(ws)
    try:
        await ws.send_json({"type": "snapshot", "tick": state.tick,
                            "positions": state.positions, "tfs": TFS})
        while True:
            # Keep the socket open; inbound messages are ignored (read-only
            # feed). Disconnect surfaces here.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.clients.discard(ws)
