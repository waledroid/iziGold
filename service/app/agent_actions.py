"""Action broker: the ONLY place an agent intent can touch the trading system.

The agent never places orders. It names an intent; this module decides whether
that intent is allowed right now (authority level, account type, rate budget,
one-action-in-flight) and, if so, translates it onto rails that already exist
and are already audited:

    pause_entries / resume_entries -> kv "entries_paused" -> heartbeat
    close_all                     -> pre-approved exit proposal -> command
    move_sl                       -> pre-approved move_sl proposal -> command
    switch_lane / set_entry_mode  -> existing kv toggles (opt-in)

Every decision, allowed or rejected, is written to `agent_actions`. Nothing
here raises into callers: the caller is a background task, and a broker bug
must never surface as a trading failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger("uvicorn.error")

# The lane allowlist mirrors telegram.STRATEGY_LANES -- the agent must not be
# able to activate a shadow/experimental id the owner cannot even tap.
LANE_ALLOWLIST = ("iziGold5", "iziGold15", "iziBit5", "iziBit15", "izi5", "izi15")
ENTRY_MODE_ALLOWLIST = ("adr", "fixed")

ALLOWED_ACTIONS = ("pause_entries", "resume_entries", "close_all", "move_sl",
                   "switch_lane", "set_entry_mode")


def _audit(app, grade, *, status, reason="", result="",
           proposal_id=None) -> None:
    """Best-effort audit row. Never raises."""
    try:
        from app.config import settings
        action_id = app.state.db.insert_agent_action(
            intent=grade.action, level=settings.agent_level,
            payload=json.dumps(grade.action_args or {}, separators=(",", ":")),
            status="pending")
        app.state.db.finish_agent_action(action_id, status=status, result=result,
                                        reason=reason, proposal_id=proposal_id)
    except Exception:
        log.exception("agent action audit failed")


def _agent_level() -> str:
    from app.config import settings
    return (settings.agent_level or "L0").upper()


def _account_is_live(app) -> bool:
    """True unless the onboarding profile explicitly says demo.

    The profile is the only place the account type is known today (the EA
    heartbeat does not carry ACCOUNT_TRADE_MODE), so an unknown/missing profile
    counts as LIVE -- the safe direction when autonomous actions are gated."""
    try:
        profile = app.state.db.get_profile() or {}
    except Exception:
        return True
    return str(profile.get("account_type") or "").lower() != "demo"


def _budget_exhausted(app) -> str:
    from app.config import settings
    now = int(time.time())
    try:
        hour = app.state.db.agent_actions_since(now - 3600)
        day = app.state.db.agent_actions_since(now - 86400)
    except Exception:
        return "rate check failed"
    if hour >= settings.agent_max_actions_per_hour:
        return f"hourly budget spent ({hour})"
    if day >= settings.agent_max_actions_per_day:
        return f"daily budget spent ({day})"
    return ""


def _one_in_flight(app) -> bool:
    try:
        return app.state.db.active_proposal() is not None
    except Exception:
        return True     # cannot prove it is safe -> treat as busy


def _basket_direction(app) -> str:
    try:
        latest = app.state.latest_heartbeat
        positions = getattr(latest[1], "positions", []) if latest else []
        if positions:
            return positions[0].direction
    except Exception:
        pass
    return "BUY"


async def _notify(app, text: str) -> None:
    tg = getattr(app.state, "telegram", None)
    if tg is None:
        return
    try:
        import asyncio
        await asyncio.to_thread(tg.send_message, text)
    except Exception:
        pass


async def execute_intent(app, grade, *, symbol, timeframe, bar_time,
                         grade_id) -> None:
    """Validate and apply one agent intent. Fail-open, always audited."""
    try:
        action = (grade.action or "none").lower()
        if action not in ALLOWED_ACTIONS:
            _audit(app, grade, status="rejected",
                   reason=f"action not allowed: {action}")
            return
        if _agent_level() == "L0":
            _audit(app, grade, status="skipped",
                   reason="L0 observer — actions disabled")
            return

        from app.config import settings
        if not settings.agent_allow_live_actions and _account_is_live(app):
            _audit(app, grade, status="rejected",
                   reason="live account — agent actions disabled")
            await _notify(app, f"🤖 agent wanted to {action} but live actions "
                               f"are disabled (AGENT_ALLOW_LIVE_ACTIONS=false)")
            return

        reason = _budget_exhausted(app)
        if reason:
            _audit(app, grade, status="rejected", reason=reason)
            return

        # Evidence gate for real-money actions (no-op on demo/bounded).
        gate = calibration_gate(app)
        if gate.get("reason"):
            _audit(app, grade, status="rejected", reason=gate["reason"])
            await _notify(app, f"🤖 agent action blocked by calibration gate: "
                               f"{gate['reason']}")
            return

        db = app.state.db
        if action in ("pause_entries", "resume_entries"):
            db.set_entries_paused(action == "pause_entries")
            _audit(app, grade, status="applied",
                   result=action,
                   reason=grade.rationale[:200])
            await _notify(app, ("🤖 agent: new auto entries PAUSED"
                                if action == "pause_entries" else
                                "🤖 agent: auto entries RESUMED")
                              + f"\n{grade.rationale[:200]}")
            return

        if _one_in_flight(app):
            _audit(app, grade, status="rejected",
                   reason="another command is already in flight")
            return

        if action == "close_all":
            pid = db.create_proposal("exit", _basket_direction(app),
                                     "agent", 0.0, None)
            db.set_proposal_status(pid, "approved", expected="pending")
            _audit(app, grade, status="dispatched",
                   result="close_all proposal queued", proposal_id=pid,
                   reason=grade.rationale[:200])
            await _notify(app, "🤖 agent: closing the basket "
                               f"(proposal {pid})\n{grade.rationale[:200]}")
            return

        if action == "move_sl":
            pid = db.create_proposal("move_sl", _basket_direction(app),
                                     "agent", 0.0, None)
            db.set_proposal_status(pid, "approved", expected="pending")
            _audit(app, grade, status="dispatched",
                   result="move_sl proposal queued", proposal_id=pid,
                   reason=grade.rationale[:200])
            await _notify(app, "🤖 agent: tightening stops to market "
                               f"(proposal {pid})")
            return

        if action == "switch_lane":
            lane = str((grade.action_args or {}).get("strategy_id", ""))
            if not settings.agent_allow_lane_switch:
                _audit(app, grade, status="rejected",
                       reason="lane switching disabled")
                return
            if lane not in LANE_ALLOWLIST:
                _audit(app, grade, status="rejected",
                       reason=f"lane not allowed: {lane}")
                return
            db.set_kv("active_strategy", lane)
            _audit(app, grade, status="applied", result=f"lane -> {lane}")
            return

        if action == "set_entry_mode":
            mode = str((grade.action_args or {}).get("entry_mode", ""))
            if not settings.agent_allow_lane_switch:
                _audit(app, grade, status="rejected",
                       reason="entry-mode switching disabled")
                return
            if mode not in ENTRY_MODE_ALLOWLIST:
                _audit(app, grade, status="rejected",
                       reason=f"entry mode not allowed: {mode}")
                return
            db.set_entry_mode(mode)
            _audit(app, grade, status="applied", result=f"entry_mode -> {mode}")
            return
    except Exception:
        log.exception("agent execute_intent failed")


def calibration_gate(app) -> dict:
    """Evidence gate for actions that could touch a REAL account.

    Demo actions are only rate-bounded; live actions additionally require a
    minimum number of resolved grades and an accuracy floor, unless
    AGENT_GATE_BYPASS. This is the guard that stops an unproven agent from
    managing real money on the strength of its own confidence.
    """
    from app.config import settings
    acc = {"resolved": 0, "acc_pct": 0.0}
    try:
        acc = app.state.db.agent_accuracy()
    except Exception:
        pass
    status = {"resolved": acc.get("resolved", 0),
              "acc_pct": acc.get("acc_pct", 0.0),
              "min_resolved": settings.agent_min_resolved,
              "min_accuracy_pct": settings.agent_min_accuracy_pct,
              "bypass": settings.agent_gate_bypass,
              "live_gate_active": settings.agent_allow_live_actions,
              "reason": ""}
    if settings.agent_gate_bypass or not settings.agent_allow_live_actions:
        return status                   # demo/bounded path needs no evidence
    if acc.get("resolved", 0) < settings.agent_min_resolved:
        status["reason"] = (f"insufficient evidence: {acc.get('resolved', 0)}/"
                            f"{settings.agent_min_resolved} resolved grades")
    elif acc.get("acc_pct", 0.0) < settings.agent_min_accuracy_pct:
        status["reason"] = (f"accuracy {acc.get('acc_pct', 0.0)}% below "
                            f"{settings.agent_min_accuracy_pct}% floor")
    return status


async def reflect(app) -> None:
    """Write ONE lesson about a resolved mistake, then feed it back later.

    Bounded to a single reflection per supervisor tick and fully fail-open:
    reflection is a learning aid, never a trading input.
    """
    agent = getattr(app.state, "agent", None)
    if agent is None:
        return
    try:
        rows = app.state.db.unreflected_grades(1)
    except Exception:
        return
    if not rows:
        return
    row = rows[0]
    payload = {
        "signal": row.get("signal"), "your_direction": row.get("direction"),
        "confidence": row.get("confidence"), "entry_quality": row.get("entry_quality"),
        "expected_r": row.get("expected_r"), "window_state": row.get("window_state"),
        "regime": row.get("regime"), "session": row.get("session"),
        "risk_flags": row.get("risk_flags"), "your_rationale": row.get("rationale"),
        "outcome_move": row.get("outcome_move"),
    }
    try:
        lesson = await asyncio.to_thread(agent.reflect, payload)
    except Exception:
        log.exception("agent reflection failed")
        return
    try:
        app.state.db.insert_lesson(
            grade_id=row.get("id"), bar_time=row.get("bar_time"),
            symbol=row.get("symbol"), timeframe=row.get("timeframe"),
            direction=row.get("direction"), window_state=row.get("window_state") or "",
            regime=row.get("regime") or "", session=row.get("session") or "",
            entry_quality=row.get("entry_quality"),
            outcome_move=row.get("outcome_move"), lesson=lesson)
    except Exception:
        log.exception("persisting agent lesson failed")


async def supervise(app) -> None:
    """Periodic state watch: grade any actionable signal that has no grade.

    This is the safety net for grades lost to a restart (the async job dies
    with the process) and the mechanism that gives the agent a continuous view
    of the market rather than only the bars /analyze happened to deliver.
    Bounded and fail-open: at most one grade per tick, never raises.
    """
    agent = getattr(app.state, "agent", None)
    if agent is None:
        return
    db = app.state.db
    try:
        series = db.latest_candle_series()
        if not series:
            return
        symbol, timeframe = series
        pending = db.ungraded_signal(symbol, timeframe)
        if pending is None:
            return
        from app.agent import build_context
        from app.config import settings
        try:
            from app.main import _agent_cache_key
        except Exception:
            _agent_cache_key = None
        rows = db.get_candles(symbol, timeframe,
                              limit=max(int(settings.agent_context_bars), 60))
        if not rows:
            return
        from app.models import Candle
        from app.regime import classify_regime, last_atr
        candles = [Candle(**r) for r in rows]
        # Score any matured grades first, so reflection sees fresh outcomes.
        try:
            db.resolve_grade_outcomes(candles, 16, symbol=symbol,
                                      timeframe=timeframe)
        except Exception:
            pass
        # Compute the same classical regime/ATR the sync path uses, so a
        # backfilled grade is as well-informed as an inline one.
        try:
            regime = classify_regime(candles)
            atr_value = last_atr(candles)
        except Exception:
            regime, atr_value = "", 0.0
        heartbeat = app.state.latest_heartbeat[1] if app.state.latest_heartbeat else None
        from app.agent_context import strategy_facts
        try:
            facts = strategy_facts(candles, symbol=symbol, timeframe=timeframe,
                                   strategy_id=pending.get("strategy_id") or "",
                                   db=db,
                                   sr_cache=getattr(app.state, "sr_cache", None))
        except Exception:
            facts = {}
        ctx = build_context(symbol=symbol, timeframe=timeframe,
                            signal=pending["signal"],
                            strategy_id=pending.get("strategy_id") or "",
                            candles=candles,
                            regime=regime, atr_value=atr_value,
                            heartbeat=heartbeat,
                            db=db, context_bars=settings.agent_context_bars,
                            facts=facts)
        grade = await asyncio.to_thread(agent.complete, ctx)
        app.state.agent_completed = getattr(app.state, "agent_completed", 0) + 1
    except Exception:
        app.state.agent_failed = getattr(app.state, "agent_failed", 0) + 1
        log.exception("agent supervisor grade failed")
        return

    try:
        grade_id = db.insert_ai_grade(
            bar_time=int(pending["bar_time"]), symbol=symbol,
            timeframe=timeframe, strategy_id=pending.get("strategy_id") or "",
            signal=pending["signal"], model=grade.model or settings.agent_model,
            prompt_version=settings.agent_prompt_version,
            direction=grade.direction, confidence=grade.confidence,
            regime_agreement=grade.regime_agreement,
            risk_flags=",".join(grade.risk_flags), action=grade.action,
            rationale=grade.rationale, tokens_in=grade.tokens_in,
            tokens_out=grade.tokens_out, latency_ms=grade.latency_ms,
            entry_quality=grade.entry_quality, expected_r=grade.expected_r,
            invalidation=grade.invalidation,
            window_state=(facts or {}).get("window", {}).get("state", "")
            if isinstance(facts, dict) else "",
            regime=(facts or {}).get("regime_classical", "")
            if isinstance(facts, dict) else "",
            session=(facts or {}).get("session", "")
            if isinstance(facts, dict) else "")
        if _agent_cache_key is not None:
            app.state.agent_grade_cache[_agent_cache_key(
                symbol, timeframe, int(pending["bar_time"]))] = \
                db.latest_ai_grade(symbol, timeframe, int(pending["bar_time"]))
    except Exception:
        log.exception("persisting supervisor grade failed")
        return

    if grade.action != "none":
        try:
            await execute_intent(app, grade, symbol=symbol, timeframe=timeframe,
                                 bar_time=int(pending["bar_time"]),
                                 grade_id=grade_id)
        except Exception:
            log.exception("agent action broker failed")
