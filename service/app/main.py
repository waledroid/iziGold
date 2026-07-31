import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.analysis import analyze_forecast
from app.config import settings
from app.db import SignalDb
from app.forecaster import get_forecaster
from app.models import (AnalyzeRequest, AnalyzeResponse, HeartbeatRequest,
                        HeartbeatResponse, TradeEventRequest)
from app.regime import classify_regime, last_atr
from app.render import render_trade_chart
from app.telegram import (TelegramClient, format_report, handle_command,
                          pinned_tick, send_alert)
from app.verdict import combine

_SCREENSHOT_RETENTION = 500


async def telegram_poller(app: FastAPI):
    """Long-poll Telegram for commands and reply. Fail-open: any exception
    just backs off and continues.

    Every TelegramClient call is dispatched via asyncio.to_thread so the
    (synchronous, up-to-30s-blocking) HTTP call runs off the event loop —
    otherwise it would stall the entire FastAPI app for the duration of
    each long-poll, and task.cancel() would be unable to interrupt an
    in-flight call since the block isn't at an await point."""
    offset = 0
    chat_id = str(settings.telegram_chat_id)
    while True:
        try:
            updates = await asyncio.to_thread(app.state.telegram.get_updates, offset)
            for upd in updates:
                offset = upd.get("update_id", offset - 1) + 1
                message = upd.get("message") or {}
                text = message.get("text") or ""
                msg_chat_id = str(message.get("chat", {}).get("id"))
                if text.startswith("/") and msg_chat_id == chat_id:
                    reply = handle_command(text, app)
                    if reply is not None:
                        await asyncio.to_thread(app.state.telegram.send_message, reply)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def pinned_editor(app: FastAPI):
    """Every 60 s, create-pin-or-edit the pinned live-status message.
    Fail-open: any exception is swallowed and the loop just retries next
    tick. The (synchronous, blocking) TelegramClient calls happen inside
    `pinned_tick`, dispatched via asyncio.to_thread so they run off the
    event loop -- same reasoning as `telegram_poller` above."""
    while True:
        try:
            await asyncio.to_thread(pinned_tick, app, app.state.telegram)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    app.state.latest_heartbeat = None
    app.state.pending_switch = None
    app.state.last_candles = None
    app.state.screenshot_dir = Path(settings.screenshot_dir)
    app.state.screenshot_dir.mkdir(parents=True, exist_ok=True)
    app.state.telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.telegram_bot_token and settings.telegram_chat_id else None)
    app.state.telegram_task = (
        asyncio.create_task(telegram_poller(app)) if app.state.telegram else None)
    app.state.pinned_task = (
        asyncio.create_task(pinned_editor(app)) if app.state.telegram else None)
    yield
    for task in (app.state.telegram_task, app.state.pinned_task):
        if task is not None:
            task.cancel()
            try:
                # The underlying TelegramClient call runs in a worker thread
                # (asyncio.to_thread) which task.cancel() cannot interrupt
                # mid-call, so an in-flight ~30s long-poll would otherwise
                # make a bare `await task` hang shutdown for up to 30s.
                # Bound the wait instead and move on regardless.
                await asyncio.wait_for(task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


app = FastAPI(title="XAU Assistant AI Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok",
            "forecaster": type(app.state.forecaster).__name__,
            "db": settings.db_path}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    app.state.last_candles = req.candles
    regime = classify_regime(req.candles)
    atr_value = last_atr(req.candles)
    closes = [x.c for x in req.candles]
    try:
        fc = app.state.forecaster.forecast(closes, settings.horizon)
        direction, confidence = analyze_forecast(fc, closes[-1], atr_value)
        ai_available = True
    except Exception:
        direction, confidence, ai_available = "neutral", 0.0, False

    resp = AnalyzeResponse(
        direction=direction, confidence=confidence, regime=regime,
        verdict=combine(req.signal, direction, confidence, settings.confirm_threshold),
        mode=settings.mode, ai_available=ai_available)

    app.state.db.resolve_outcomes(req.candles, settings.horizon)
    if req.signal != "NONE":
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=req.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, verdict=resp.verdict, mode=settings.mode,
            ai_available=ai_available, strategy_id=req.strategy_id, is_active=True)
        send_alert(format_report(req, resp), settings)
    for shadow in req.shadows:
        if shadow.signal == "NONE":
            continue
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=shadow.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, mode=settings.mode, ai_available=ai_available,
            verdict=combine(shadow.signal, direction, confidence,
                            settings.confirm_threshold),
            strategy_id=shadow.strategy_id, is_active=False)
    return resp


@app.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(hb: HeartbeatRequest):
    app.state.latest_heartbeat = (time.time(), hb)
    app.state.db.insert_heartbeat({**hb.model_dump(exclude={"positions"}),
                                   "open_count": len(hb.positions)})
    if app.state.pending_switch and hb.active_strategy == app.state.pending_switch:
        app.state.pending_switch = None
    return HeartbeatResponse(switch_to=app.state.pending_switch)


@app.post("/ui/switch")
def ui_switch(body: dict):
    sid = str(body.get("strategy_id", "")).strip()
    if not sid:
        app.state.pending_switch = None
        return {"pending": None}
    app.state.pending_switch = sid
    return {"pending": sid}


@app.get("/ui/state")
def ui_state():
    latest = app.state.latest_heartbeat
    hb, age = None, None
    if latest is not None:
        age = round(time.time() - latest[0], 1)
        hb = latest[1].model_dump()
    return {"age_s": age, "heartbeat": hb,
            "pending_switch": app.state.pending_switch,
            "stats": app.state.db.stats()}


@app.get("/ui/equity")
def ui_equity():
    return {"series": app.state.db.equity_series()}


@app.get("/ui/stats")
def ui_stats():
    return app.state.db.stats()


@app.get("/ui/signals")
def ui_signals(limit: int = 50):
    return {"signals": app.state.db.recent_signals(limit)}


@app.get("/ui")
def ui_page():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html",
                        media_type="text/html")


@app.post("/trade-event")
async def trade_event(ev: TradeEventRequest):
    trade_id = app.state.db.insert_trade(ev.model_dump())
    if ev.event in ("open", "close") and app.state.last_candles:
        render_path = app.state.screenshot_dir / f"render_{trade_id}.png"
        try:
            ok = await asyncio.to_thread(
                render_trade_chart, app.state.last_candles, ev.model_dump(),
                str(render_path))
            if ok:
                app.state.db.set_render(trade_id, str(render_path))
                await asyncio.to_thread(_prune_screenshots, app.state.screenshot_dir)
                if app.state.telegram is not None:
                    caption = f"render: {ev.reason}"
                    await asyncio.to_thread(
                        _send_render_photo, app.state.telegram, caption, render_path)
        except Exception:
            pass
    return {"id": trade_id}


def _send_render_photo(telegram: TelegramClient, caption: str, path: Path) -> None:
    telegram.send_photo(caption, path.read_bytes())


@app.post("/screenshot")
async def screenshot(event: int, request: Request):
    body = await request.body()
    dir_path = app.state.screenshot_dir
    file_path = dir_path / f"{event}.png"
    # Dispatch the blocking file write and directory scan/prune via
    # asyncio.to_thread so they run off the event loop instead of stalling
    # every other request while they hit disk.
    await asyncio.to_thread(file_path.write_bytes, body)
    app.state.db.set_screenshot(event, str(file_path))
    await asyncio.to_thread(_prune_screenshots, dir_path)
    if app.state.telegram is not None:
        try:
            row = app.state.db.conn.execute(
                "SELECT event, direction, lots, price, reason, profit"
                " FROM trades WHERE id=?", (event,)).fetchone()
            if row is not None:
                caption = _trade_caption(*row)
                await asyncio.to_thread(app.state.telegram.send_photo, caption, body)
        except Exception:
            pass
    return {"saved": str(file_path)}


def _trade_caption(event, direction, lots, price, reason, profit) -> str:
    caption = f"{event} {direction} {lots}@{price} — {reason}"
    if event == "close":
        caption += f"; P/L {profit}"
    return caption


def _prune_screenshots(dir_path: Path, keep: int = _SCREENSHOT_RETENTION) -> None:
    files = sorted(dir_path.glob("*.png"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for stale in files[keep:]:
        stale.unlink()


@app.get("/ui/trades")
def ui_trades(limit: int = 50):
    return {"trades": app.state.db.recent_trades(limit)}


@app.get("/ui/screenshot/{trade_id}")
def ui_screenshot(trade_id: int):
    row = app.state.db.conn.execute(
        "SELECT screenshot_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    return FileResponse(path, media_type="image/png")


@app.get("/ui/render/{trade_id}")
def ui_render(trade_id: int):
    row = app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    return FileResponse(path, media_type="image/png")
