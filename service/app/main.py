import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.analysis import analyze_forecast
from app.config import settings
from app.db import SignalDb
from app.forecaster import get_forecaster
from app.models import AnalyzeRequest, AnalyzeResponse, HeartbeatRequest, HeartbeatResponse
from app.regime import classify_regime, last_atr
from app.telegram import format_report, send_alert
from app.verdict import combine


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    app.state.latest_heartbeat = None
    app.state.pending_switch = None
    yield


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
