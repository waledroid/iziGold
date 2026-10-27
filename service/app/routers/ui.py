"""Dashboard operator UI, configuration, and monitoring routes."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app import chart_spec, overlays, preview
from app.agent_context import compute_volume_profile
from app.db import profile_completion
from app.models import Candle
from app.sr_zones import sr_zones_from_db

log = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["ui"])

_STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_LEGACY_PAGES = {"": "/", "backtest": "/backtest", "onboarding": "/onboarding"}

_OVERLAY_BUILDERS = {
    "iziGold5": overlays.build_izi5,
    "iziGold15": overlays.build_izi15,
    "izi5": overlays.build_izi5,
    "izi15": overlays.build_izi15,
    "iziBit5": overlays.build_izi5,
    "iziBit15": overlays.build_izi15,
    "boll_stochrsi_v1": overlays.build_boll,
    "boll_stochrsi": overlays.build_boll,
    "halftrend_ema_v1": overlays.build_izi5,
    "halftrend_m15_v1": overlays.build_izi15,
}


def _get_app(request: Request | None = None):
    if request is not None and hasattr(request, "app"):
        return request.app
    import app.main
    return app.main.app


def _check_dashboard_auth(req: Request) -> None:
    from app.config import settings
    pin = getattr(settings, "dashboard_pin", "").strip()
    if not pin:
        return
    token = req.headers.get("X-Dashboard-PIN") or req.headers.get("X-API-Key")
    if not token:
        auth = req.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if not token:
        token = req.query_params.get("pin")
    if not token:
        token = req.cookies.get("dashboard_pin")
    if token != pin:
        raise HTTPException(status_code=401, detail="invalid or missing dashboard PIN")


def _edit_proposal_message(app, row: dict, suffix: str) -> None:
    """Best-effort: keep proposal's Telegram message in sync with dashboard decision."""
    tg = getattr(app.state, "telegram", None)
    if tg is None or row.get("tg_message_id") is None:
        return
    try:
        tg.edit_message(row["tg_message_id"],
                        f"{'📥' if row['kind'] == 'entry' else '📤'} "
                        f"{row['direction']} @ {row['price']} — {suffix}")
    except Exception:
        pass


def _rc_key(rc: dict | None) -> tuple | None:
    if not rc or not rc.get("candles"):
        return None
    c = rc["candles"]
    return (rc["symbol"], rc["timeframe"], len(c), c[-1].t, c[-1].c)


def _live_profit_target(app) -> float | None:
    try:
        return app.state.db.runtime_config().get("profit_target_pct")
    except Exception:
        return None


def _mask_secret(value: str) -> str:
    """Never return the full secret: '••••' + last 4 chars (or all dots if shorter)."""
    tail = value[-4:] if len(value) >= 4 else value
    return "••••" + tail


@router.get("/")
def ui_root(request: Request = None):
    target_app = _get_app(request)
    try:
        has_profile = target_app.state.db.get_profile() is not None
    except Exception:
        has_profile = True
    if not has_profile:
        return RedirectResponse("/onboarding", status_code=307)
    return FileResponse(Path(__file__).parent.parent / "static" / "dashboard.html",
                        media_type="text/html")


@router.get("/onboarding")
def ui_onboarding():
    return FileResponse(Path(__file__).parent.parent / "static" / "onboarding.html",
                        media_type="text/html")


@router.get("/vocabs")
def ui_vocabs():
    return FileResponse(Path(__file__).parent.parent / "static" / "vocabs.html",
                        media_type="text/html")


@router.get("/reports")
def ui_reports():
    return FileResponse(Path(__file__).parent.parent / "static" / "miniapp.html",
                        media_type="text/html")


@router.get("/api/report")
def ui_api_report(view: str = "month", month: str | None = None,
                  date: str | None = None, request: Request = None):
    import datetime as _dt
    from app.reports import _empty_report, _report_day, _report_month, _server_date
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
        target_app = _get_app(request)
        conn = target_app.state.db.conn
        return _report_month(conn, y, m) if view == "month" else _report_day(conn, d)
    except Exception:
        return _empty_report(view, key)


@router.api_route("/ui{path:path}", methods=["GET", "POST"])
def ui_legacy_redirect(path: str, request: Request):
    tail = path.lstrip("/")
    target = _LEGACY_PAGES.get(tail, "/api/" + tail)
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=307)


@router.post("/api/switch")
def ui_switch(body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    sid = str(body.get("strategy_id", "")).strip()
    if not sid:
        target_app.state.pending_switch = None
        target_app.state.db.set_kv("active_strategy", "")
        return {"pending": None}
    if not _STRATEGY_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid strategy_id")
    target_app.state.pending_switch = sid
    target_app.state.db.set_kv("active_strategy", sid)
    return {"pending": sid}


@router.post("/api/mode")
def ui_mode(body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    mode = str(body.get("mode", "")).strip().lower()
    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="mode must be auto|manual")
    target_app.state.db.set_exec_mode(mode)
    return {"mode": mode}


@router.post("/api/rules")
def ui_rules(body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    key = str(body.get("key", "")).strip()
    value = str(body.get("value", "")).strip()
    db = target_app.state.db
    if key == "ai_enabled":
        val_lower = value.lower()
        if val_lower not in ("on", "off", "true", "false", "1", "0"):
            raise HTTPException(status_code=400, detail="ai_enabled must be on|off")
        enabled = val_lower in ("on", "true", "1")
        db.set_ai_enabled(enabled)
        return {key: "on" if enabled else "off"}

    setters = {"entry_mode": db.set_entry_mode,
               "dynamic_strategy": db.set_dynamic_strategy,
               "htf_enforce": db.set_htf_enforce,
               "ema200_enforce": db.set_ema200_enforce,
               "rsi_enforce": db.set_rsi_enforce,
               "sr_enforce": db.set_sr_enforce,
               "m15_adds": db.set_m15_adds}
    if key not in setters:
        raise HTTPException(status_code=400, detail="unknown rule key")
    try:
        setters[key](value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {key: value}


@router.post("/api/proposal/quick")
def ui_proposal_quick(body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    db = target_app.state.db
    direction = str(body.get("direction", "BUY")).upper()
    strategy_id = str(body.get("strategy_id", "iziGold15"))
    price = float(body.get("price", 0.0))
    pid = db.create_proposal("entry", direction, strategy_id, price, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    return {"ok": True, "proposal_id": pid}


@router.post("/api/proposal/{pid}")
def ui_proposal_action(pid: int, body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    action = str(body.get("action", "")).strip().lower()
    if action not in ("take", "skip"):
        raise HTTPException(status_code=400, detail="action must be take|skip")
    db = target_app.state.db
    row = db.get_proposal(pid)
    if row is None:
        raise HTTPException(status_code=404, detail="no such proposal")
    status = "approved" if action == "take" else "skipped"
    if not db.set_proposal_status(pid, status, expected="pending"):
        return {"ok": False, "status": db.get_proposal(pid)["status"]}
    mark = "🟢 approved (dashboard)" if action == "take" else "🔴 skipped (dashboard)"
    _edit_proposal_message(target_app, row, mark)
    return {"ok": True, "status": status}


@router.post("/api/close-all")
def ui_close_all(request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    db = target_app.state.db
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="exit", status=st) is not None:
            raise HTTPException(status_code=409, detail=f"close already {st}")
    last = db.last_executed_entry()
    direction = last["direction"] if last else "BUY"
    latest = target_app.state.latest_heartbeat
    strategy_id = latest[1].active_strategy if latest else "dashboard"
    rc = target_app.state.recent_candles
    price = rc["candles"][-1].c if rc else 0.0
    pid = db.create_proposal("exit", direction, strategy_id, price, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    return {"ok": True, "proposal_id": pid}


class ClosePositionRequest(BaseModel):
    ticket: int
    symbol: str = ""


@router.post("/api/close-position")
def ui_close_position(req: ClosePositionRequest, request: Request = None) -> dict:
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    db = target_app.state.db

    sym = req.symbol or "XAUUSD"
    latest = target_app.state.latest_heartbeat
    strategy_id = latest[1].active_strategy if latest else "dashboard"
    rc = target_app.state.recent_candles
    price = rc["candles"][-1].c if rc else 0.0

    pid = db.create_proposal("exit", "SELL", strategy_id, price, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    if not hasattr(target_app.state, "pending_close_tickets"):
        target_app.state.pending_close_tickets = {}
    target_app.state.pending_close_tickets[sym] = req.ticket
    return {"ok": True, "proposal_id": pid, "ticket": req.ticket}


@router.get("/api/state")
def ui_state(request: Request = None):
    target_app = _get_app(request)
    latest = target_app.state.latest_heartbeat
    hb, age = None, None
    if latest is not None:
        age = round(time.time() - latest[0], 1)
        hb = latest[1].model_dump()
    proposal = target_app.state.db.active_proposal()
    ai_on = target_app.state.db.ai_enabled()
    from app.config import settings
    agent_client = getattr(target_app.state, "agent", None)
    if not ai_on:
        ai_status = "off"
    elif agent_client is None or not getattr(settings, "agent_api_key", None):
        ai_status = "missing_key"
    else:
        ai_status = "ready"

    agent_info = {"enabled": ai_on,
                  "status": ai_status,
                  "available": ai_on and ai_status == "ready",
                  "level": settings.agent_level,
                  "model": settings.agent_model,
                  "scheduled": getattr(target_app.state, "agent_scheduled", 0),
                  "completed": getattr(target_app.state, "agent_completed", 0),
                  "failed": getattr(target_app.state, "agent_failed", 0),
                  "inflight": len(getattr(target_app.state, "agent_inflight", ()))}
    try:
        agent_info["recent_grades"] = target_app.state.db.recent_ai_grades(5)
        agent_info["recent_actions"] = target_app.state.db.recent_agent_actions(5)
        agent_info["recent_lessons"] = target_app.state.db.recent_lessons(3)
        from app.agent_actions import calibration_gate
        agent_info["gate"] = calibration_gate(target_app)
    except Exception:
        pass
    hbs_dict = {}
    all_positions = []
    latest_hbs = getattr(target_app.state, "latest_heartbeats", {})
    now = time.time()
    for sym, (ts, h) in latest_hbs.items():
        h_dict = h.model_dump()
        hbs_dict[sym] = {
            "age_s": round(now - ts, 1),
            "heartbeat": h_dict,
        }
        for pos in h.positions:
            all_positions.append(pos.model_dump())

    curr_equity = (hb.get("equity") if hb else None) or 1000.0
    from app.config import calculate_adaptive_tier, get_market_status, is_weekend_market, is_gold_active_window
    adaptive_info = calculate_adaptive_tier(curr_equity)
    weekend_active = is_weekend_market()
    gold_active = is_gold_active_window()
    market_statuses = {
        "XAUUSD": get_market_status("XAUUSD"),
        "BTCUSD": get_market_status("BTCUSD"),
    }

    return {"age_s": age, "heartbeat": hb,
            "heartbeats": hbs_dict,
            "all_positions": all_positions,
            "is_weekend": weekend_active,
            "is_gold_active": gold_active,
            "market_status": market_statuses,
            "pending_switch": target_app.state.pending_switch,
            "mode": target_app.state.db.exec_mode(),
            "adaptive_tier": adaptive_info,
            "rules": {"entry_mode": target_app.state.db.entry_mode(),
                      "dynamic_strategy": target_app.state.db.dynamic_strategy(),
                      "ai_enabled": "on" if ai_on else "off",
                      "htf_enforce": target_app.state.db.htf_enforce(),
                      "ema200_enforce": target_app.state.db.ema200_enforce(),
                      "rsi_enforce": target_app.state.db.rsi_enforce(),
                      "sr_enforce": target_app.state.db.sr_enforce(),
                      "m15_adds": target_app.state.db.m15_adds()},
            "agent": agent_info,
            "proposal": proposal,
            "stats": target_app.state.db.stats()}


@router.get("/api/config")
def ui_config(request: Request = None):
    target_app = _get_app(request)
    cfg = target_app.state.db.runtime_config()
    latest = getattr(target_app.state, "latest_heartbeat", None)
    eq = latest[1].equity if latest and hasattr(latest[1], "equity") else 1000.0
    from app.config import calculate_adaptive_tier
    return {"config": cfg, "adaptive_tier": calculate_adaptive_tier(eq)}


@router.get("/api/agent/scorecard")
def ui_agent_scorecard(request: Request = None):
    target_app = _get_app(request)
    return target_app.state.db.agent_scorecard()


@router.get("/api/equity")
def ui_equity(request: Request = None):
    target_app = _get_app(request)
    return {"series": target_app.state.db.equity_series()}


@router.get("/api/stats")
def ui_stats(request: Request = None):
    target_app = _get_app(request)
    res = target_app.state.db.stats()
    res["slippage"] = target_app.state.db.slippage_stats()
    return res


@router.get("/api/signals")
def ui_signals(limit: int = 50, request: Request = None):
    target_app = _get_app(request)
    return {"signals": target_app.state.db.recent_signals(limit)}


@router.get("/api/candles")
def ui_candles(symbol: str = "", timeframe: str = "", request: Request = None):
    target_app = _get_app(request)
    rc = target_app.state.recent_candles
    if not symbol or (rc and rc.get("symbol") == symbol and (not timeframe or rc.get("timeframe") == timeframe)):
        if not rc or not rc["candles"]:
            return {"symbol": "", "timeframe": "", "candles": []}
        key = _rc_key(rc)
        cached = getattr(target_app.state, "candles_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        payload = {"symbol": rc["symbol"], "timeframe": rc["timeframe"],
                   "candles": [c.model_dump() for c in rc["candles"]]}
        target_app.state.candles_cache = (key, payload)
        return payload

    tf = timeframe or "M15"
    from app.routers.ea import _CANDLE_WINDOW_CAP
    db_rows = target_app.state.db.get_candles(symbol, tf, limit=_CANDLE_WINDOW_CAP)
    series_map = getattr(target_app.state, "candles_by_series", {})
    if (symbol, tf) in series_map:
        mem_candles = series_map[(symbol, tf)]
        # Prefer DB if it has newer or more complete candle history
        if db_rows:
            last_mem_t = mem_candles[-1].t if mem_candles else 0
            last_db_t = db_rows[-1]["t"] if db_rows else 0
            if last_db_t >= last_mem_t or len(db_rows) > len(mem_candles):
                return {"symbol": symbol, "timeframe": tf, "candles": db_rows}
        return {"symbol": symbol, "timeframe": tf,
                "candles": [c.model_dump() for c in mem_candles]}

    return {"symbol": symbol, "timeframe": tf, "candles": db_rows}


@router.get("/api/overlays")
def ui_overlays(strategy: str = "", symbol: str = "", request: Request = None):
    target_app = _get_app(request)
    rc = target_app.state.recent_candles
    tf = "M15" if "15" in strategy else "M5"
    series_map = getattr(target_app.state, "candles_by_series", {})
    candles = None

    if symbol:
        sym = symbol
        from app.routers.ea import _CANDLE_WINDOW_CAP
        db_rows = target_app.state.db.get_candles(sym, tf, limit=_CANDLE_WINDOW_CAP)
        mem_candles = series_map.get((sym, tf))
        if db_rows:
            last_mem_t = mem_candles[-1].t if mem_candles else 0
            last_db_t = db_rows[-1]["t"] if db_rows else 0
            if last_db_t >= last_mem_t or not mem_candles:
                candles = [Candle(**r) for r in db_rows]
            else:
                candles = mem_candles
        elif mem_candles:
            candles = mem_candles
        elif rc and rc.get("symbol") == sym and rc.get("timeframe") == tf and rc.get("candles"):
            candles = rc["candles"]
        elif rc and rc.get("symbol") == sym and rc.get("candles"):
            candles = rc["candles"]
    else:
        if rc and rc.get("candles"):
            candles = rc["candles"]
        elif ("XAUUSD", tf) in series_map:
            candles = series_map[("XAUUSD", tf)]
        else:
            from app.routers.ea import _CANDLE_WINDOW_CAP
            db_rows = target_app.state.db.get_candles("XAUUSD", tf, limit=_CANDLE_WINDOW_CAP)
            if db_rows:
                candles = [Candle(**r) for r in db_rows]

    if not candles:
        return {}
    builder = _OVERLAY_BUILDERS.get(strategy)
    if builder is None:
        return {}

    key = (symbol or (rc.get("symbol") if rc else "XAUUSD"), tf, len(candles), candles[-1].t if candles else 0, candles[-1].c if candles else 0.0)

    cache = getattr(target_app.state, "overlays_cache", None)
    if cache is None:
        cache = target_app.state.overlays_cache = {}
    hit = cache.get((key, strategy))
    if hit is not None:
        return hit

    closes = [c.c for c in candles]
    payload = builder(candles, closes)
    payload["sr_zones"] = sr_zones_from_db(target_app.state.db, target_app.state.sr_cache)
    payload["volume_profile"] = compute_volume_profile(candles, num_bins=28, value_area_pct=0.70)
    prev = preview.build_preview(
        candles,
        strategy_id=strategy,
        profit_target_pct=_live_profit_target(target_app),
    )
    payload["preview"] = prev
    payload["chart"] = chart_spec.load()

    for stale in [k for k in cache if k[0] != key]:
        cache.pop(stale, None)
    cache[(key, strategy)] = payload
    return payload


@router.get("/api/chart-spec")
def ui_chart_spec():
    return chart_spec.load()


@router.get("/api/profile")
def ui_profile(request: Request = None):
    target_app = _get_app(request)
    row = target_app.state.db.get_profile()
    completion_pct = profile_completion(row)
    if row is not None:
        row = dict(row)
        token = row.get("telegram_bot_token")
        if token:
            row["telegram_bot_token"] = _mask_secret(str(token))
        ngrok_token = row.get("ngrok_authtoken")
        if ngrok_token:
            row["ngrok_authtoken"] = _mask_secret(str(ngrok_token))
    return {"profile": row, "completion_pct": completion_pct}


@router.post("/api/profile")
async def ui_profile_save(body: dict, request: Request = None):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    body = dict(body) if isinstance(body, dict) else {}
    if str(body.get("telegram_bot_token", "")).startswith("•"):
        body.pop("telegram_bot_token", None)
    if str(body.get("ngrok_authtoken", "")).startswith("•"):
        body.pop("ngrok_authtoken", None)
    row = target_app.state.db.save_profile(body)
    if "telegram_bot_token" in body or "telegram_chat_id" in body:
        try:
            import app.main
            await app.main._apply_telegram(target_app)
        except Exception:
            pass
    return {"profile": row, "completion_pct": profile_completion(row)}


@router.post("/api/maintenance/prune")
def maintenance_prune(request: Request = None, heartbeat_days: int = 7, candle_days: int = 60, spread_days: int = 30):
    if request is not None:
        _check_dashboard_auth(request)
    target_app = _get_app(request)
    pruned = target_app.state.db.prune_old_data(
        heartbeat_days=heartbeat_days,
        candle_days=candle_days,
        spread_days=spread_days
    )
    checkpoint = target_app.state.db.checkpoint_wal()
    return {"status": "ok", "pruned": pruned, "wal_checkpoint": checkpoint}


@router.get("/api/trades")
def ui_trades(limit: int = 50, symbol: str = "", request: Request = None):
    target_app = _get_app(request)
    return {"trades": target_app.state.db.recent_trades(limit, symbol=symbol or None)}


@router.get("/api/screenshot/{trade_id}")
def ui_screenshot(trade_id: int, request: Request = None):
    target_app = _get_app(request)
    row = target_app.state.db.conn.execute(
        "SELECT screenshot_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    return FileResponse(path, media_type="image/png")


@router.get("/api/render/{trade_id}")
def ui_render(trade_id: int, request: Request = None):
    target_app = _get_app(request)
    row = target_app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    return FileResponse(path, media_type="image/png")
