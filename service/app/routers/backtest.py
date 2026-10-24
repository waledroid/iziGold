"""Backtesting and Monte Carlo simulation routes."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app import backtest_runner

router = APIRouter(tags=["backtest"])


def _day_ts(raw: str, end: bool = False) -> int:
    d = dt.date.fromisoformat(raw.strip())
    t = dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time.min,
                            tzinfo=dt.timezone.utc)
    return int(t.timestamp())


@router.get("/backtest")
def ui_backtest_page():
    return FileResponse(Path(__file__).parent.parent / "static" / "backtest.html",
                        media_type="text/html")


@router.get("/api/backtest/range")
def ui_backtest_range(request: Request):
    db = request.app.state.db
    series = db.latest_candle_series()
    symbol = series[0] if series else "XAUUSD"
    strategies = [{"id": sid, "label": s["label"], "supported": True}
                  for sid, s in backtest_runner.STRATEGIES.items()]
    strategies.append({"id": "boll_stochrsi", "label": "BollStochRsi",
                       "supported": False})
    return {"symbol": symbol,
            "range": db.candles_range(symbol, "M5"),
            "strategies": strategies}


@router.get("/api/backtest/runs")
def ui_backtest_runs(request: Request, limit: int = 20):
    db = request.app.state.db
    runs = []
    for row in db.recent_backtest_runs(limit):
        row = dict(row)
        row["params"] = json.loads(row.pop("params_json") or "{}")
        row["stats"] = json.loads(row.pop("stats_json") or "null")
        runs.append(row)
    return {"runs": runs}


@router.post("/api/backtest")
def ui_backtest_start(body: dict, request: Request):
    db = request.app.state.db
    strategy = str(body.get("strategy", "")).strip()
    if strategy == "boll_stochrsi":
        raise HTTPException(status_code=400,
                            detail="boll_stochrsi is not yet supported by the replay engine")
    if strategy not in backtest_runner.STRATEGIES:
        raise HTTPException(status_code=400, detail="unknown strategy")
    try:
        start_ts = _day_ts(str(body.get("start", "")))
        end_ts = _day_ts(str(body.get("end", "")), end=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD")
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start must be before end")
    try:
        balance = float(body.get("balance", 10000))
        risk_pct = float(body.get("risk_pct", 1.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="balance/risk_pct must be numbers")
    if not math.isfinite(balance) or not math.isfinite(risk_pct):
        raise HTTPException(status_code=400, detail="balance/risk_pct must be finite numbers")
    if balance < 500:
        raise HTTPException(status_code=400,
                            detail="balance must be >= 500 (engine minimum)")
    if not 0 < risk_pct <= 10:
        raise HTTPException(status_code=400, detail="risk_pct must be in (0, 10]")
    entry_mode = str(body.get("entry_mode", "adr"))
    exit_scheme = str(body.get("exit_scheme", "target-exit"))
    ema200_confirm = str(body.get("ema200_confirm", "off"))
    m15_bias = str(body.get("m15_bias", "off"))
    if entry_mode not in ("adr", "fixed"):
        raise HTTPException(status_code=400, detail="entry_mode must be adr|fixed")
    if exit_scheme not in ("target-exit", "floor-a", "floor-b", "floor-a-adds"):
        raise HTTPException(status_code=400, detail="bad exit_scheme")
    sr_filter = str(body.get("sr_filter", "off"))
    if ema200_confirm not in ("off", "on") or m15_bias not in ("off", "on") \
            or sr_filter not in ("off", "on"):
        raise HTTPException(status_code=400,
                            detail="ema200_confirm/m15_bias/sr_filter must be off|on")
    series = db.latest_candle_series()
    symbol = series[0] if series else "XAUUSD"
    rng = db.candles_range(symbol, "M5")
    if rng is None or end_ts < rng["start"] or start_ts > rng["end"]:
        avail = (f"{dt.datetime.fromtimestamp(rng['start'], dt.timezone.utc):%Y-%m-%d} .. "
                 f"{dt.datetime.fromtimestamp(rng['end'], dt.timezone.utc):%Y-%m-%d}"
                 if rng else "none -- run the backfill first")
        raise HTTPException(status_code=400,
                            detail=f"no candles in that range (available: {avail})")
    n_bars = db.count_candles(symbol, "M5", start_ts, end_ts)
    if n_bars < 300:
        raise HTTPException(status_code=400,
                            detail=f"only {n_bars} M5 bars in that range -- need at least 300")
    params = {"strategy": strategy, "symbol": symbol,
              "start_ts": start_ts, "end_ts": end_ts,
              "balance": balance, "risk_pct": risk_pct,
              "entry_mode": entry_mode, "exit_scheme": exit_scheme,
              "ema200_confirm": ema200_confirm, "m15_bias": m15_bias,
              "sr_filter": sr_filter}
    try:
        run_id = backtest_runner.start_run(db, params)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run_id}


@router.get("/api/backtest/{run_id}")
def ui_backtest_status(run_id: int, request: Request):
    row = request.app.state.db.get_backtest_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such run")
    row = dict(row)
    row["params"] = json.loads(row.pop("params_json") or "{}")
    row["stats"] = json.loads(row.pop("stats_json") or "null")
    return row


@router.get("/api/backtest/{run_id}/report")
def ui_backtest_report(run_id: int, request: Request):
    row = request.app.state.db.get_backtest_run(run_id)
    if not row or not row["report_path"] or not Path(row["report_path"]).exists():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(row["report_path"], media_type="text/html")


@router.post("/api/backtest/{run_id}/monte-carlo")
def api_backtest_run_monte_carlo(run_id: int, request: Request, num_simulations: int = 1000):
    row = request.app.state.db.get_backtest_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such run")
    stats = json.loads(row.get("stats_json") or "{}") or {}
    trades = stats.get("trades", [])
    from app.monte_carlo import run_monte_carlo
    initial_balance = float(stats.get("initial_balance", 5000.0) or 5000.0)
    return run_monte_carlo(trades, initial_balance=initial_balance, num_simulations=num_simulations)


@router.post("/api/backtest/monte-carlo")
async def api_monte_carlo_direct(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    trades = body.get("trades", [])
    initial_balance = float(body.get("initial_balance", 5000.0))
    num_simulations = int(body.get("num_simulations", 1000))
    from app.monte_carlo import run_monte_carlo
    return run_monte_carlo(trades, initial_balance=initial_balance, num_simulations=num_simulations)
