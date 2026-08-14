"""Read-only mini-app feed service (port 9001) — the ONLY thing the
Phase 3 tunnel will expose. The Windows bridge POSTs batches to
/feed/push; browsers get history over REST and live deltas over one
WebSocket. No trading controls exist here by construction.

Runs as its own process: uvicorn app.miniapp:app --host 127.0.0.1
--port 9001. State is in-memory only — a restart just refills from the
bridge's next backfill push (fail-open).
"""
import asyncio
import math
from collections import deque
from pathlib import Path

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.indicators import ema, halftrend
from app.models import Candle

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


app = FastAPI(title="xau-miniapp")
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


def viewer_allowed() -> bool:
    """Check if viewer is allowed. Shared by REST and WS.
    Phase 1: dev bypass only. Phase 3 replaces the body with Telegram
    initData validation + owner/channel-membership authorization."""
    return settings.miniapp_dev_bypass


def require_viewer(request: Request = None):
    """Phase 1: dev bypass only. Phase 3 replaces the body with Telegram
    initData validation + owner/channel-membership authorization — same
    dependency, same call sites."""
    if viewer_allowed():
        return True
    raise HTTPException(status_code=403, detail="viewer auth required")


@app.post("/feed/push")
async def feed_push(request: Request):
    if request.headers.get("X-Feed-Key", "") != settings.feed_key \
            or not settings.feed_key:
        raise HTTPException(status_code=403, detail="bad feed key")
    try:
        batch = await request.json()
    except Exception:
        return {"ok": False}
    try:
        deltas = state.apply_push(batch)
    except Exception:
        return {"ok": False}
    for d in deltas:
        await hub.broadcast(d)
    return {"ok": True, "deltas": len(deltas)}


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
    return {"tf": tf, "candles": rows, **_indicator_series(rows)}


def _indicator_series(rows: list[dict]) -> dict:
    """EMA-9/21/55/200 + HalfTrend(amplitude=4), computed fresh from the ring
    buffer on every request (<=500 candles: cheap, no cache needed). Uses
    the exact same app.indicators math the render.py trade-chart PNGs use,
    so the mini-app overlays match the EA's live MT5 chart. ema()/halftrend()
    already degrade to all-None/empty for short input (see their
    docstrings), so a <2-candle TF naturally yields empty/null arrays here
    without any special-casing.
    """
    closes = [r["c"] for r in rows]
    candle_objs = [Candle(**r) for r in rows]
    ht = halftrend(candle_objs, amplitude=4)
    return {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema55": ema(closes, 55),
        "ema200": ema(closes, 200),
        "halftrend": [({"v": e[0], "trend": e[1]} if e else None) for e in ht],
    }


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    if not viewer_allowed():
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
