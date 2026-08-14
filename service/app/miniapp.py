"""Read-only mini-app feed service (port 9001) — the ONLY thing the
Phase 3 tunnel will expose. The Windows bridge POSTs batches to
/feed/push; browsers get history over REST and live deltas over one
WebSocket. No trading controls exist here by construction.

Runs as its own process: uvicorn app.miniapp:app --host 127.0.0.1
--port 9001. State is in-memory only — a restart just refills from the
bridge's next backfill push (fail-open).
"""
import asyncio
import contextlib
from collections import deque

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)

from app.config import settings

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


class _Hub:
    """WS clients + broadcast. Slow/dead clients are dropped, never
    awaited to death (send failures disconnect that client only)."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


state = FeedState()
hub = _Hub()


def require_viewer(request: Request = None):
    """Phase 1: dev bypass only. Phase 3 replaces the body with Telegram
    initData validation + owner/channel-membership authorization — same
    dependency, same call sites."""
    if settings.miniapp_dev_bypass:
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
    deltas = state.apply_push(batch)
    for d in deltas:
        await hub.broadcast(d)
    return {"ok": True, "deltas": len(deltas)}


@app.get("/api/history")
def history(tf: str, _=Depends(require_viewer)):
    if tf not in TFS:
        raise HTTPException(status_code=400, detail=f"unknown tf {tf}")
    return {"tf": tf, "candles": list(state.candles[tf])}


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    if not settings.miniapp_dev_bypass:
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
