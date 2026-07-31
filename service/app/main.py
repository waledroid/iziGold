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
from app.telegram import TelegramClient, format_report, handle_command, send_alert
from app.verdict import combine

_SCREENSHOT_RETENTION = 500


async def telegram_poller(app: FastAPI):
    """Long-poll Telegram for commands and reply. Fail-open: any exception
    just backs off and continues."""
    offset = 0
    chat_id = str(settings.telegram_chat_id)
    while True:
        try:
            updates = app.state.telegram.get_updates(offset)
            for upd in updates:
                offset = upd.get("update_id", offset - 1) + 1
                message = upd.get("message") or {}
                text = message.get("text") or ""
                msg_chat_id = str(message.get("chat", {}).get("id"))
                if text.startswith("/") and msg_chat_id == chat_id:
                    reply = handle_command(text, app)
                    if reply is not None:
                        app.state.telegram.send_message(reply)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    app.state.latest_heartbeat = None
    app.state.pending_switch = None
    app.state.screenshot_dir = Path(settings.screenshot_dir)
    app.state.screenshot_dir.mkdir(parents=True, exist_ok=True)
    app.state.telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.telegram_bot_token and settings.telegram_chat_id else None)
    app.state.telegram_task = (
        asyncio.create_task(telegram_poller(app)) if app.state.telegram else None)
    yield
    if app.state.telegram_task is not None:
        app.state.telegram_task.cancel()
        try:
            await app.state.telegram_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="XAU Assistant AI Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok",
            "forecaster": type(app.state.forecaster).__name__,
            "db": settings.db_path}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
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
def trade_event(ev: TradeEventRequest):
    trade_id = app.state.db.insert_trade(ev.model_dump())
    return {"id": trade_id}


@app.post("/screenshot")
async def screenshot(event: int, request: Request):
    body = await request.body()
    dir_path = app.state.screenshot_dir
    file_path = dir_path / f"{event}.png"
    file_path.write_bytes(body)
    app.state.db.set_screenshot(event, str(file_path))
    _prune_screenshots(dir_path)
    return {"saved": str(file_path)}


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
