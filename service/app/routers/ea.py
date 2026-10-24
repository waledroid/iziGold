"""Expert Advisor (MT5) ingestion, analysis, heartbeat, and trade reporting routes."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app import overlays, preview
from app.agent import AgentError, build_context
from app.analysis import analyze_forecast
from app.basket import derive_basket_band
from app.config import settings
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NotifyRequest,
    ProposalResultRequest,
    TradeEventRequest,
)
from app.regime import classify_regime, last_atr
from app.routers.stream import broadcast_chart_event
from app.telegram import (
    BRAKE_RESET_KB,
    EXIT_KB,
    EXIT_NOW_KB,
    PROPOSAL_KB,
    TARGET_KB,
    format_proposal,
)
from app.ticker import ticker_tick
from app.trade_report import (
    _basket_legs,
    _prune_screenshots,
    _report_trade_event,
    _trade_caption,
)
from app.verdict import combine

log = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["ea"])

_CANDLE_WINDOW_CAP = 2000
PROPOSAL_APPROVAL_TTL_S = 120
COMMAND_RESULT_TTL_S = 180


def _get_app():
    import app.main
    return app.main.app


def _get_classify_regime():
    m = sys.modules.get("app.main")
    if m is not None and hasattr(m, "classify_regime"):
        return m.classify_regime
    return classify_regime


def _get_last_atr():
    m = sys.modules.get("app.main")
    if m is not None and hasattr(m, "last_atr"):
        return m.last_atr
    return last_atr


def _merge_candle_window(existing: list, incoming: list) -> list:
    """Merge incoming candles into existing keyed by .t: overlapping
    timestamps are replaced by incoming, new timestamps append.
    Sorted by t and capped to _CANDLE_WINDOW_CAP."""
    merged = {c.t: c for c in existing}
    for c in incoming:
        merged[c.t] = c
    return [merged[t] for t in sorted(merged)][-_CANDLE_WINDOW_CAP:]


def _rc_key(rc):
    c = rc["candles"][-1]
    return (rc["symbol"], rc["timeframe"], len(rc["candles"]), c.t, c.c)


def _linked_channel(app) -> str | None:
    try:
        return app.state.db.get_kv("channel_id") or None
    except Exception:
        return None


async def _mirror(app, text: str | None = None,
                  photo_bytes: bytes | None = None, caption: str = "") -> None:
    """Mirror one already-sent owner message to the linked channel."""
    cid = _linked_channel(app)
    tg = getattr(app.state, "telegram", None)
    if cid is None or tg is None:
        return
    try:
        if photo_bytes is not None:
            await asyncio.to_thread(tg.send_photo_to, cid, caption, photo_bytes)
        elif text:
            await asyncio.to_thread(tg.send_message_to, cid, text)
    except Exception:
        pass


async def _news_headsup(app, hb) -> None:
    """Send ONE Telegram notice per high-impact USD event as it enters the
    notice window: (blackout radius + 5) minutes before the event. Latched
    in kv 'news_alerted' keyed by the event's absolute minute."""
    events = [e for e in getattr(hb, "news", []) if e.in_s > 0]
    if not events:
        try:
            from app.news_feed import fetch_upcoming_news
            import types
            ext = fetch_upcoming_news(horizon_s=86400)
            if ext:
                events = [types.SimpleNamespace(**ev) for ev in ext]
        except Exception:
            pass
    if not events:
        return
    tg = getattr(app.state, "telegram", None)
    if tg is None:
        return
    radius = getattr(hb, "news_blackout_min", 30)
    window_s = (radius + 5) * 60
    now = time.time()
    db = app.state.db
    try:
        alerted = set(json.loads(db.get_kv("news_alerted") or "[]"))
    except ValueError:
        alerted = set()
    now_min = int(now // 60)
    alerted = {k for k in alerted if k >= now_min - 60}   # prune passed events
    fired = False
    for e in events:
        if e.in_s > window_s:
            continue
        key = int((now + e.in_s) // 60)
        if any(abs(key - k) <= 1 for k in alerted):
            continue
        alerted.add(key)
        fired = True
        name = getattr(e, "name", None) or getattr(e, "title", "high-impact USD event")
        text = (f"⏳ News blackout ahead: {name} in "
                f"{e.in_s // 60}m — entries frozen ±{radius}m around it.")
        try:
            await asyncio.to_thread(tg.send_message, text)
        except Exception:
            pass
        await _mirror(app, text=text)
    if fired or alerted != set():
        db.set_kv("news_alerted", json.dumps(sorted(alerted)))


def maybe_propose(req: AnalyzeRequest, resp: AnalyzeResponse) -> None:
    """Proposal lifecycle + alert diet. Never raises."""
    app = _get_app()
    db = app.state.db
    tg = getattr(app.state, "telegram", None)

    def edit(pid_row, suffix):
        if tg is None or pid_row["tg_message_id"] is None:
            return
        tg.edit_message(pid_row["tg_message_id"],
                        f"{'📥' if pid_row['kind']=='entry' else '📤'} "
                        f"{pid_row['direction']} @ {pid_row['price']} — {suffix}")

    def is_stale(row) -> bool:
        return (
            (row["kind"] == "entry" and (
                req.signal == "EXIT" or
                (req.signal in ("BUY", "SELL") and req.signal != row["direction"])))
            or (row["kind"] == "exit" and req.signal in ("BUY", "SELL"))
        )

    pending = db.pending_proposal()
    if pending is not None and is_stale(pending):
        if db.set_proposal_status(pending["id"], "expired", expected="pending"):
            edit(pending, "⌛ expired (strategy stance changed)")
        pending = None

    approved = db.pending_proposal(status="approved")
    if approved is not None and is_stale(approved):
        if db.set_proposal_status(approved["id"], "expired", expected="approved"):
            edit(approved, "⌛ expired before execution (stance changed)")

    if req.signal not in ("BUY", "SELL", "EXIT"):
        return
    blackout_entry = bool(getattr(req, "news_blackout", False)) and req.signal in ("BUY", "SELL")
    if db.exec_mode() != "manual" and not blackout_entry:
        return
    kind = "exit" if req.signal == "EXIT" else "entry"
    if kind == "exit":
        latest = app.state.latest_heartbeat
        if latest is None or not latest[1].positions:
            return
    price = req.candles[-1].c if req.candles else 0.0
    if pending is not None and pending["kind"] == kind and \
       (kind == "exit" or pending["direction"] == req.signal):
        return
    if kind == "entry":
        direction = req.signal
    else:
        last = db.last_executed_entry()
        direction = last["direction"] if last else "BUY"
    pid = db.create_proposal(kind, direction, req.strategy_id, price, None)
    if tg is not None:
        markup = (PROPOSAL_KB(pid) if kind == "entry" else EXIT_KB(pid))
        text = format_proposal(kind, direction, price, resp, symbol=req.symbol)
        if blackout_entry:
            text = ("⚠️ NEWS BLACKOUT — auto entry paused. Take it yourself?\n" + text)
        sent = tg.send_message(text, reply_markup=markup)
        if sent and sent.get("result", {}).get("message_id"):
            db.set_proposal_message(pid, sent["result"]["message_id"])
        cid = _linked_channel(app)
        if cid is not None:
            try:
                tg.send_message_to(cid, format_proposal(kind, direction, price, resp, symbol=req.symbol))
            except Exception:
                pass


from app.agent_supervisor import (
    agent_cache_key as _agent_cache_key,
    schedule_task as _schedule,
    agent_lookup as _agent_lookup,
    agent_grade_job as _agent_grade_job,
    strategy_facts_for as _strategy_facts_for,
    schedule_grade as _schedule_grade,
    agent_supervisor,
)


def _sweep_stale_proposals(app) -> None:
    """Runs at the top of every /heartbeat, before pop_approved_command."""
    db = app.state.db
    tg = getattr(app.state, "telegram", None)

    def edit(row, suffix):
        if tg is None or row["tg_message_id"] is None:
            return
        if row["kind"] == "reset_brake":
            tg.edit_message(row["tg_message_id"], f"🔓 brake reset — {suffix}")
            return
        tg.edit_message(row["tg_message_id"],
                        f"{'📥' if row['kind']=='entry' else '📤'} "
                        f"{row['direction']} @ {row['price']} — {suffix}")

    for row in db.stale_approved(PROPOSAL_APPROVAL_TTL_S):
        if db.set_proposal_status(row["id"], "expired", expected="approved"):
            edit(row, "⌛ expired before execution (approval timed out)")

    for row in db.stale_dispatched(COMMAND_RESULT_TTL_S):
        if db.set_proposal_status(row["id"], "blocked", expected="dispatched"):
            edit(row, "🚫 no confirmation from EA — check the terminal")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
def health():
    app = _get_app()
    return {"status": "ok",
            "forecaster": type(app.state.forecaster).__name__,
            "db": settings.db_path}


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    app = _get_app()
    app.state.last_candles = req.candles
    if not hasattr(app.state, "candles_by_series"):
        app.state.candles_by_series = {}

    series_key = (req.symbol, req.timeframe)
    existing = app.state.candles_by_series.get(series_key, [])
    candles = _merge_candle_window(existing, req.candles)
    app.state.candles_by_series[series_key] = candles

    app.state.recent_candles = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "candles": candles,
    }
    try:
        app.state.db.upsert_candles(req.symbol, req.timeframe, req.candles)
    except Exception:
        log.exception("persisting candles for %s %s failed",
                      req.symbol, req.timeframe)
    regime = _get_classify_regime()(req.candles)
    atr_value = _get_last_atr()(req.candles)
    closes = [x.c for x in req.candles]
    try:
        fc = app.state.forecaster.forecast(closes, settings.horizon)
        direction, confidence = analyze_forecast(fc, closes[-1], atr_value)
        ai_available = True
    except Exception:
        direction, confidence, ai_available = "neutral", 0.0, False

    grade_pending = False
    grade_source = settings.forecaster
    agent = getattr(app.state, "agent", None)
    ai_on = app.state.db.ai_enabled() if getattr(app.state, "db", None) else False
    if agent is not None and ai_on:
        cached = _agent_lookup(app, req.symbol, req.timeframe,
                               int(req.candles[-1].t))
        if cached and cached.get("direction"):
            direction = cached.get("direction") or "neutral"
            try:
                confidence = float(cached.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            ai_available = True
            grade_source = "agent"
        else:
            direction, confidence = "neutral", 0.0
            ai_available = False
            grade_pending = True
            grade_source = "pending"

    resp = AnalyzeResponse(
        direction=direction, confidence=confidence, regime=regime,
        verdict=combine(req.signal, direction, confidence, settings.confirm_threshold),
        mode=settings.mode, ai_available=ai_available,
        grade_pending=grade_pending, grade_source=grade_source)

    if len(req.candles) >= 2:
        try:
            app.state.db.resolve_outcomes(req.candles, settings.horizon,
                                          symbol=req.symbol,
                                          timeframe=req.timeframe)
        except Exception:
            log.exception("resolve_outcomes failed for %s %s",
                          req.symbol, req.timeframe)
        try:
            app.state.db.resolve_grade_outcomes(req.candles, settings.horizon,
                                                symbol=req.symbol,
                                                timeframe=req.timeframe)
        except Exception:
            log.exception("resolve_grade_outcomes failed for %s %s",
                          req.symbol, req.timeframe)

    if req.spread_min > 0 or req.spread_avg > 0 or req.spread_max > 0:
        app.state.db.upsert_spread(
            bar_time=req.candles[-1].t, spread_min=req.spread_min,
            spread_avg=req.spread_avg, spread_max=req.spread_max)
    if req.signal != "NONE":
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=req.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, verdict=resp.verdict, mode=settings.mode,
            ai_available=ai_available, strategy_id=req.strategy_id, is_active=True,
            timeframe=req.timeframe)
    try:
        maybe_propose(req, resp)
    except Exception:
        pass
    for shadow in req.shadows:
        if shadow.signal == "NONE":
            continue
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=shadow.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, mode=settings.mode, ai_available=ai_available,
            verdict=combine(shadow.signal, direction, confidence,
                            settings.confirm_threshold),
            strategy_id=shadow.strategy_id, is_active=False,
            timeframe=req.timeframe)
    if agent is not None and ai_on and grade_pending and req.signal in ("BUY", "SELL", "EXIT"):
        _schedule_grade(app, req, regime, atr_value, resp)
    return resp


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(hb: HeartbeatRequest):
    app = _get_app()
    sym = hb.symbol or "XAUUSD"
    if not hb.positions:
        hb.floating_pl = 0.0
    else:
        hb.floating_pl = round(sum(getattr(p, "profit", 0.0) for p in hb.positions), 2)
    if not hasattr(app.state, "latest_heartbeats"):
        app.state.latest_heartbeats = {}
    previous_sym = app.state.latest_heartbeats.get(sym)
    app.state.latest_heartbeats[sym] = (time.time(), hb)
    previous = app.state.latest_heartbeat
    prev_algo_trading = previous[1].algo_trading if previous is not None else True
    app.state.latest_heartbeat = (time.time(), hb)
    app.state.db.insert_heartbeat({**hb.model_dump(exclude={"positions"}),
                                   "open_count": len(hb.positions)})
    if not hasattr(app.state, "ticker_busy_symbols"):
        app.state.ticker_busy_symbols = set()
    if sym not in app.state.ticker_busy_symbols and getattr(app.state, "telegram", None) is not None:
        app.state.ticker_busy_symbols.add(sym)
        app.state.ticker_busy = True
        hb_now = time.time()

        async def _ticker_bg(hb=hb, hb_now=hb_now, previous=previous_sym, sym=sym):
            try:
                await asyncio.to_thread(ticker_tick, app, hb, hb_now, previous)
            except Exception:
                pass
            finally:
                app.state.ticker_busy_symbols.discard(sym)
                app.state.ticker_busy = bool(app.state.ticker_busy_symbols)

        app.state.ticker_task = asyncio.create_task(_ticker_bg())

    if previous is not None and (time.time() - previous[0]) > 60:
        tg = getattr(app.state, "telegram", None)
        if tg is not None:
            gap_s = int(time.time() - previous[0])
            text = ("🟢 EA back online after %ds — %s, algo %s"
                    % (gap_s, hb.active_strategy or "?",
                       "ON" if hb.algo_trading else "OFF ⚠️"))
            try:
                await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
    if prev_algo_trading != hb.algo_trading:
        tg = getattr(app.state, "telegram", None)
        if tg is not None:
            text = ("⚠️ MT5 Algo Trading is OFF — trades cannot execute until you enable it"
                    if not hb.algo_trading else "✅ Algo Trading back ON")
            try:
                await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
            await _mirror(app, text=text)

    try:
        await _news_headsup(app, hb)
    except Exception:
        pass

    stored = app.state.db.get_kv("active_strategy")
    dynamic_mode = app.state.db.dynamic_strategy()

    if dynamic_mode == "auto":
        from app.strategy_router import strategy_router
        rc = getattr(app.state, "recent_candles", None)
        candles = rc.get("candles", []) if rc else []
        curr = hb.active_strategy or stored or "iziGold15"
        opt_strat, opt_reason = strategy_router.evaluate_optimal_strategy(
            candles=candles,
            current_active=curr,
            open_count=getattr(hb, "open_count", len(getattr(hb, "positions", []))),
        )
        if opt_strat != curr and opt_strat != stored:
            log.info("DynamicStrategyRouter: shifting active_strategy -> %s (%s)", opt_strat, opt_reason)
            app.state.db.set_kv("active_strategy", opt_strat)
            stored = opt_strat

    hb_sym = getattr(hb, "symbol", "") or "XAUUSD"
    pending_switches = getattr(app.state, "pending_switches", {})
    switch_to = pending_switches.get(hb_sym)
    if not switch_to and hb_sym == "XAUUSD":
        if stored and hb.active_strategy != stored and "bit" not in stored.lower():
            switch_to = stored
    if switch_to and hb.active_strategy == switch_to:
        if hb_sym in pending_switches:
            del pending_switches[hb_sym]
        switch_to = None
    app.state.pending_switch = switch_to

    def _hb_commands():
        try:
            _sweep_stale_proposals(app)
        except Exception:
            pass
        return app.state.db.pop_approved_command()

    cmd_row = await asyncio.to_thread(_hb_commands)
    command = None
    target_symbol = ""
    if cmd_row is not None:
        target_symbol = cmd_row.get("symbol", "") or ""
        if not target_symbol:
            sid = str(cmd_row.get("strategy_id", "")).lower()
            if "bit" in sid:
                target_symbol = "BTCUSD"
            elif sid in ("izi5", "izi15", "izigold5", "izigold15", "halftrend_ema_v1", "halftrend_m15_v1"):
                target_symbol = "XAUUSD"
        if cmd_row["kind"] == "entry":
            command = {"cmd": "execute", "proposal_id": cmd_row["id"],
                       "direction": cmd_row["direction"]}
        elif cmd_row["kind"] == "reset_brake":
            command = {"cmd": "reset_brake", "proposal_id": cmd_row["id"]}
        elif cmd_row["kind"] == "move_sl":
            command = {"cmd": "move_sl", "proposal_id": cmd_row["id"]}
        else:
            pending_tk = None
            pending_map = getattr(app.state, "pending_close_tickets", {})
            if target_symbol and target_symbol in pending_map:
                pending_tk = pending_map.pop(target_symbol)
            elif hb_sym in pending_map:
                pending_tk = pending_map.pop(hb_sym)

            if pending_tk:
                command = {"cmd": "close_ticket", "proposal_id": cmd_row["id"], "ticket": int(pending_tk)}
            else:
                command = {"cmd": "close_all", "proposal_id": cmd_row["id"]}
    runtime = app.state.db.runtime_config()
    hb_sym = hb.symbol or "XAUUSD"
    hb_tf = "M15" if "15" in (hb.active_strategy or "") else "M5"
    if hb.bar_t > 0:
        broadcast_chart_event(app, {
            "type": "bar",
            "symbol": hb_sym,
            "timeframe": hb_tf,
            "candle": {"t": hb.bar_t, "o": hb.bar_o, "h": hb.bar_h, "l": hb.bar_l, "c": hb.bar_c}
        })
    tp_pct = runtime.get("profit_target_pct", 2.0)
    basket_data = derive_basket_band(app.state.db, hb, profit_target_pct=tp_pct)
    basket_data["symbol"] = hb_sym
    broadcast_chart_event(app, {
        "type": "basket",
        "symbol": hb_sym,
        "basket": basket_data
    })
    return HeartbeatResponse(
        switch_to=app.state.pending_switch,
        target_symbol=target_symbol,
        mode=runtime["execution_mode"],
        entry_mode=runtime["entry_mode"],
        htf_enforce=runtime["htf_enforce"],
        ema200_enforce=runtime["ema200_enforce"],
        rsi_enforce=runtime["rsi_enforce"],
        m15_adds=runtime["m15_adds"],
        entries_paused=app.state.db.entries_paused(hb_sym),
        risk_per_trade_pct=runtime["risk_per_trade_pct"],
        max_drawdown_pct=runtime["max_drawdown_pct"],
        profit_target_pct=runtime["profit_target_pct"],
        window_start_hour=runtime["window_start_hour"],
        window_end_hour=runtime["window_end_hour"],
        max_daily_exposure_min=runtime["max_daily_exposure_min"],
        heartbeat_sec=max(1, min(int(settings.heartbeat_sec), 60)),
        command=command
    )


@router.post("/proposal-result")
async def proposal_result(res: ProposalResultRequest):
    app = _get_app()
    db = app.state.db
    row = db.get_proposal(res.proposal_id)
    if row is None:
        return {"ok": False}
    status = "executed" if res.ok else "blocked"
    if not db.set_proposal_status(res.proposal_id, status, expected="dispatched"):
        return {"ok": False}
    tg = getattr(app.state, "telegram", None)
    if row["kind"] == "reset_brake":
        text = f"🔓 {res.detail}" if res.ok else f"🚫 brake reset failed: {res.detail}"
        if tg is not None:
            try:
                if row["tg_message_id"] is not None:
                    await asyncio.to_thread(tg.edit_message, row["tg_message_id"], text)
                else:
                    await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
            await _mirror(app, text=text)
        return {"ok": True}
    if row["kind"] == "move_sl":
        text = f"🔒 {res.detail}" if res.ok else f"🚫 SL move failed: {res.detail}"
        if tg is not None:
            try:
                if row["tg_message_id"] is not None:
                    await asyncio.to_thread(
                        tg.edit_message, row["tg_message_id"], text,
                        TARGET_KB() if res.ok else None)
                else:
                    await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
            await _mirror(app, text=text)
        return {"ok": True}
    if tg is not None and row["tg_message_id"] is not None:
        mark = "✅ executed" if res.ok else "🚫 blocked"
        try:
            await asyncio.to_thread(
                tg.edit_message, row["tg_message_id"],
                f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
                f"@ {row['price']} — {mark}: {res.detail}")
        except Exception:
            pass
        await _mirror(app, text=(
            f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
            f"@ {row['price']} — {mark}: {res.detail}"))
    elif tg is not None and not res.ok:
        try:
            await asyncio.to_thread(
                tg.send_message, f"🚫 close failed: {res.detail}")
        except Exception:
            pass
        await _mirror(app, text=f"🚫 close failed: {res.detail}")
    return {"ok": True}


@router.post("/notify")
async def notify(req: NotifyRequest):
    """Fire-and-forget Telegram notice for events with no proposal/db row."""
    app = _get_app()
    text = req.text.strip()
    if not text:
        return {"ok": False}
    tg = getattr(app.state, "telegram", None)
    if tg is not None:
        markup = None
        button = req.button or ("exit" if req.exit_button else "")
        if button == "exit":
            latest = app.state.latest_heartbeat
            if latest is not None and latest[1].positions:
                markup = EXIT_NOW_KB(0)
        elif button == "target":
            latest = app.state.latest_heartbeat
            if latest is not None and latest[1].positions:
                markup = TARGET_KB()
        elif button == "reset_brake":
            markup = BRAKE_RESET_KB()
        try:
            await asyncio.to_thread(tg.send_message, text, markup)
        except Exception:
            pass
        await _mirror(app, text=text)
    return {"ok": True}


@router.get("/api/last-close-ticket")
async def last_close_ticket():
    """Newest close-deal ticket this service has recorded."""
    app = _get_app()
    row = app.state.db.conn.execute(
        "SELECT MAX(ticket) FROM trades WHERE event='close' AND ticket>0"
    ).fetchone()
    return {"ticket": int(row[0]) if row and row[0] else 0}


@router.post("/trade-event")
async def trade_event(ev: TradeEventRequest):
    app = _get_app()
    if ev.event == "close" and ev.ticket:
        row = app.state.db.conn.execute(
            "SELECT MIN(id) FROM trades WHERE event='close' AND ticket=?",
            (ev.ticket,)).fetchone()
        if row is not None and row[0] is not None:
            return {"id": row[0]}
    trade_id = app.state.db.insert_trade(ev.model_dump())
    ev_sym = ev.symbol or "XAUUSD"
    broadcast_chart_event(app, {
        "type": "trade",
        "symbol": ev_sym,
        "trade_id": trade_id,
        "event": ev.event,
        "price": ev.price,
        "direction": ev.direction
    })
    runtime = app.state.db.runtime_config()
    tp_pct = runtime.get("profit_target_pct", 2.0)
    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    hb_tuple = latest_hbs.get(ev_sym)
    if hb_tuple is None and ev_sym == "XAUUSD":
        hb_tuple = getattr(app.state, "latest_heartbeat", None)
    hb_req = hb_tuple[1] if hb_tuple is not None else None
    basket_data = derive_basket_band(app.state.db, hb_req, profit_target_pct=tp_pct)
    basket_data["symbol"] = ev_sym
    broadcast_chart_event(app, {
        "type": "basket",
        "symbol": ev_sym,
        "basket": basket_data
    })
    try:
        legs = _basket_legs(app.state.db, trade_id)
    except Exception:
        legs = []
    task = asyncio.create_task(_report_trade_event(
        ev, trade_id, legs,
        last_candles=app.state.last_candles,
        screenshot_dir=app.state.screenshot_dir,
        db=app.state.db,
        telegram=app.state.telegram,
        mirror=lambda **kw: _mirror(app, **kw),
        sr_cache=getattr(app.state, "sr_cache", None)))
    app.state.report_tasks.add(task)
    task.add_done_callback(app.state.report_tasks.discard)
    return {"id": trade_id}


@router.post("/screenshot")
async def screenshot(event: int, request: Request):
    app = request.app if request is not None else _get_app()
    body = await request.body()
    dir_path = app.state.screenshot_dir
    file_path = dir_path / f"{event}.png"
    await asyncio.to_thread(file_path.write_bytes, body)
    app.state.db.set_screenshot(event, str(file_path))
    await asyncio.to_thread(_prune_screenshots, dir_path)
    if app.state.telegram is not None:
        try:
            row = app.state.db.conn.execute(
                "SELECT event, direction, lots, price, reason, profit,"
                " COALESCE(htf_agree, -1), COALESCE(ema200_agree, -1),"
                " COALESCE(news_blackout, -1), COALESCE(rsi_agree, -1),"
                " COALESCE(sr_agree, -1), COALESCE(sr_tier, ''),"
                " COALESCE(symbol, 'XAUUSD'), COALESCE(strategy_id, ''),"
                " COALESCE(sl, 0.0), COALESCE(tp, 0.0)"
                " FROM trades WHERE id=?", (event,)).fetchone()
            if row is not None and row[0] == "open":
                caption = _trade_caption(*row)
                markup = EXIT_NOW_KB(event)
                await asyncio.to_thread(
                    app.state.telegram.send_photo, caption, body, markup)
        except Exception:
            pass
    return {"saved": str(file_path)}
