"""DeepSeek Supervisory Agent & Asynchronous Grading Loop for XAU Assistant.

Orchestrates background grading of actionable signals, state supervision,
performance reflection, and intent execution.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from app.agent import AgentError, build_context
from app.config import settings

log = logging.getLogger("uvicorn.error")


def agent_cache_key(symbol: str, timeframe: str, bar_time: int) -> Tuple[str, str, int]:
    return (symbol, timeframe, int(bar_time))


def schedule_task(app, coro) -> bool:
    loop = getattr(app.state, "loop", None)
    if loop is None:
        return False
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
        return True
    except Exception:
        log.exception("scheduling agent task failed")
        return False


def agent_lookup(app, symbol: str, timeframe: str, bar_time: int):
    """Completed grade for THIS bar, from memory then SQLite. None = pending."""
    key = agent_cache_key(symbol, timeframe, bar_time)
    cached = app.state.agent_grade_cache.get(key)
    if cached is not None:
        return cached
    try:
        row = app.state.db.latest_ai_grade(symbol, timeframe, bar_time)
    except Exception:
        return None
    if row is not None:
        app.state.agent_grade_cache[key] = row
    return row


async def agent_grade_job(app, req, regime, atr_value, resp, key, facts=None) -> None:
    """One grade. Never raises: any failure simply leaves the bar ungraded."""
    app.state.agent_completed = getattr(app.state, "agent_completed", 0)
    app.state.agent_failed = getattr(app.state, "agent_failed", 0)
    try:
        heartbeat = app.state.latest_heartbeat[1] if app.state.latest_heartbeat else None
        ctx = build_context(
            symbol=req.symbol, timeframe=req.timeframe, signal=req.signal,
            strategy_id=req.strategy_id, candles=req.candles, regime=regime,
            atr_value=atr_value, resp=resp, heartbeat=heartbeat, db=app.state.db,
            context_bars=settings.agent_context_bars, facts=facts)
        grade = await asyncio.to_thread(app.state.agent.complete, ctx)
    except AgentError as exc:
        app.state.agent_failed += 1
        log.warning("agent grade unavailable: %s", exc)
        return
    except Exception:
        app.state.agent_failed += 1
        log.exception("agent grade failed")
        return
    finally:
        with app.state.agent_lock:
            app.state.agent_inflight.discard(key)

    bar_time = int(req.candles[-1].t)
    try:
        grade_id = app.state.db.insert_ai_grade(
            bar_time=bar_time, symbol=req.symbol, timeframe=req.timeframe,
            strategy_id=req.strategy_id, signal=req.signal,
            model=grade.model or settings.agent_model,
            prompt_version=settings.agent_prompt_version,
            direction=grade.direction, confidence=grade.confidence,
            regime_agreement=grade.regime_agreement,
            risk_flags=",".join(grade.risk_flags), action=grade.action,
            rationale=grade.rationale, tokens_in=grade.tokens_in,
            tokens_out=grade.tokens_out, latency_ms=grade.latency_ms,
            entry_quality=grade.entry_quality, expected_r=grade.expected_r,
            invalidation=grade.invalidation,
            window_state=((facts or {}).get("window") or {}).get("state", ""),
            regime=(facts or {}).get("regime_classical", ""),
            session=(facts or {}).get("session", ""))
        row = app.state.db.latest_ai_grade(req.symbol, req.timeframe, bar_time)
        app.state.agent_grade_cache[key] = row
        app.state.agent_completed += 1
    except Exception:
        app.state.agent_failed += 1
        log.exception("persisting agent grade failed")
        return

    if grade.action != "none":
        try:
            from app.agent_actions import execute_intent
            await execute_intent(app, grade, symbol=req.symbol,
                                 timeframe=req.timeframe, bar_time=bar_time,
                                 grade_id=grade_id)
        except Exception:
            log.exception("agent action broker failed")


def strategy_facts_for(app, req, bar_time):
    """Cached strategy-facts computation (one per bar, not per re-post)."""
    from app.agent_context import strategy_facts
    key = agent_cache_key(req.symbol, req.timeframe, int(bar_time))
    cache = getattr(app.state, "facts_cache", None)
    if cache is None:
        cache = app.state.facts_cache = {}
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        facts = strategy_facts(req.candles, symbol=req.symbol,
                               timeframe=req.timeframe,
                               strategy_id=req.strategy_id, db=app.state.db,
                               sr_cache=getattr(app.state, "sr_cache", None))
    except Exception:
        facts = {}
    for stale in [k for k in cache if k[0] != key[0] or k[1] != key[1] or k[2] != key[2]]:
        cache.pop(stale, None)
    cache[key] = facts
    return facts


def schedule_grade(app, req, regime, atr_value, resp) -> None:
    """Fire-and-forget grade for an actionable signal, capped at 2 in flight."""
    if getattr(app.state, "agent", None) is None or not app.state.db.ai_enabled():
        return
    key = agent_cache_key(req.symbol, req.timeframe, int(req.candles[-1].t))
    with app.state.agent_lock:
        if key in app.state.agent_inflight or len(app.state.agent_inflight) >= 2:
            return
        app.state.agent_inflight.add(key)
    app.state.agent_scheduled = getattr(app.state, "agent_scheduled", 0) + 1
    facts = strategy_facts_for(app, req, req.candles[-1].t)
    if not schedule_task(app, agent_grade_job(app, req, regime, atr_value, resp,
                                              key, facts)):
        with app.state.agent_lock:
            app.state.agent_inflight.discard(key)


async def agent_supervisor(app) -> None:
    """Periodic state watch -- bounded, silent, fail-open."""
    interval = max(int(settings.agent_supervisor_interval_s), 30)
    while True:
        try:
            await asyncio.sleep(interval)
            if not getattr(app.state, "db", None) or not app.state.db.ai_enabled():
                continue
            try:
                from app.agent_actions import supervise as _watch
            except Exception:
                continue
            try:
                await _watch(app)
            except Exception:
                log.exception("agent supervisor tick failed")
            try:
                from app.agent_actions import reflect as _reflect
            except Exception:
                continue
            try:
                await _reflect(app)
            except Exception:
                log.exception("agent reflection tick failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("agent supervisor loop error")

