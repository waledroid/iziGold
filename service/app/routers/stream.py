"""Real-time SSE chart streaming and host bridge feed ingestion."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse

from app.basket import derive_basket_band

router = APIRouter(tags=["stream"])


def broadcast_chart_event(app: FastAPI, event: dict) -> None:
    """Broadcast an event to all connected SSE clients."""
    subs = getattr(app.state, "chart_subscribers", None)
    if not subs:
        return
    data_str = json.dumps(event)
    msg = f"data: {data_str}\n\n"
    dead = []
    for q in list(subs):
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    for q in dead:
        subs.discard(q)


@router.get("/api/basket")
def ui_basket(request: Request, symbol: str | None = None):
    """Canonical event-driven basket band (Phase 3 of Unified Realtime Chart).
    Returns the live trade band (entry, sl, tp, state) for the active lane.
    """
    target_sym = (symbol or "XAUUSD").upper()
    latest_hbs = getattr(request.app.state, "latest_heartbeats", {})
    hb_tuple = latest_hbs.get(target_sym)
    if hb_tuple is None and target_sym == "XAUUSD":
        hb_tuple = getattr(request.app.state, "latest_heartbeat", None)
    hb_req = hb_tuple[1] if hb_tuple is not None else None
    runtime = request.app.state.db.runtime_config()
    tp_pct = runtime.get("profit_target_pct", 2.0)
    band = derive_basket_band(request.app.state.db, hb_req, profit_target_pct=tp_pct)
    band["symbol"] = target_sym
    return band


@router.get("/api/stream/chart")
async def stream_chart(request: Request, symbol: str | None = None):
    """Server-Sent Events endpoint for real-time chart, ticker, and basket updates."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    subs = getattr(request.app.state, "chart_subscribers", None)
    if subs is not None:
        subs.add(queue)

    target_sym = (symbol or "XAUUSD").upper()

    async def event_generator():
        try:
            latest_hbs = getattr(request.app.state, "latest_heartbeats", {})
            hb_tuple = latest_hbs.get(target_sym)
            if hb_tuple is None and target_sym == "XAUUSD":
                hb_tuple = getattr(request.app.state, "latest_heartbeat", None)
            hb_req = hb_tuple[1] if hb_tuple is not None else None
            runtime = request.app.state.db.runtime_config()
            tp_pct = runtime.get("profit_target_pct", 2.0)
            initial_basket = derive_basket_band(request.app.state.db, hb_req, profit_target_pct=tp_pct)
            initial_basket["symbol"] = target_sym
            yield f"data: {json.dumps({'type': 'basket', 'symbol': target_sym, 'basket': initial_basket})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            if subs is not None:
                subs.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/feed/push")
async def feed_push(batch: dict, request: Request):
    """Read-only feed ingestion from the host MT5 bridge.
    Broadcasts tick, forming candle, and positions to connected real-time clients.
    """
    tick = batch.get("tick")
    if isinstance(tick, dict):
        broadcast_chart_event(request.app, {"type": "tick", "tick": tick})
    bars = batch.get("bars")
    if isinstance(bars, dict) and "M5" in bars:
        m5_bars = bars["M5"]
        if m5_bars and isinstance(m5_bars, list):
            last_c = m5_bars[-1]
            if isinstance(last_c, dict) and "t" in last_c:
                broadcast_chart_event(request.app, {"type": "bar", "candle": last_c})
    return {"status": "ok"}
