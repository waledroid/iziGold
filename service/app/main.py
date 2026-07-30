from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.analysis import analyze_forecast
from app.config import settings
from app.db import SignalDb
from app.forecaster import get_forecaster
from app.models import AnalyzeRequest, AnalyzeResponse
from app.regime import classify_regime, last_atr
from app.telegram import format_report, send_alert
from app.verdict import combine


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
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
