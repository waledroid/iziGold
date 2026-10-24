"""DeepSeek supervisory agent — an OPTIONAL async layer over the strategy.

Design contract (why this file never blocks trading):

* The strategy remains the sole trading decision maker. In AUTO the EA already
  executed before any of this runs.
* Everything here is asynchronous and fail-open. If the agent is disabled, not
  configured, slow, or returns garbage, `grade_signal` returns None, the
  synchronous /analyze path keeps its neutral rules answer, and the system
  trades exactly as it did before this module existed.
* Model swaps happen through this module and `app/forecaster.py`; nothing else
  in the service imports a provider SDK.
* The agent never places orders directly. It may only emit an *intent*, which
  `app/agent_actions.py` validates against hard bounds and translates onto the
  existing, audited heartbeat-command / proposal rails.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx

# The strategy knowledge the agent is hired for. Kept compact (tokens cost
# money on every grade) but specific enough that the model reasons about THIS
# system rather than generic gold commentary.
SYSTEM_PROMPT = """You are the supervisory grading agent for the XAU Assistant, \
an MT5 XAUUSD system. You are the ONLY AI grader. You never place orders.

THE SYSTEM YOU GRADE
- Strategy is the sole decision maker. In AUTO it already executed before you \
are consulted; your grade is informational and supervisory.
- Active lane M15/M5: iziGold15 / iziGold5 — HalfTrend flip (amplitude 4), then a \
waiting bar, then entry at the next bar's open only if it opens on the trend's \
side of EMA. Stop = HalfTrend wick extreme + ATR pad.
- Bitcoin lanes: iziBit5 and iziBit15.
- Entry modes: ADR = 1.0% equity risk over the stop distance with pyramid adds \
(<=70% of previous leg) and a +2% cycle-balance target with a 50%-of-peak \
profit lock once >=1R. FIXED = single fixed lot, no adds, ride until reversal.
- Risk gates: 10% drawdown kill switch, 3% daily realized-loss brake, trading \
window 04-23 server time, 360 min daily exposure, ADX>=10, spread cap, news \
blackout around high-impact USD events.
- Sessions matter: rollover 23-01 and the Asian lull are hostile to trend \
entries; London and New York expansion are the productive windows.

YOUR JOB
1. Judge whether the current closed bar favours the strategy's signal.
2. Flag concrete risk: exhaustion, opposing structure, thin/erratic spread, \
news proximity, over-extension from the EMA, ranging tape with no follow-through.
3. When the situation genuinely warrants intervention, name ONE action.

ACTIONS (use "none" unless clearly justified)
- pause_entries / resume_entries: stop or allow NEW auto entries. Management \
and exits of any open basket are unaffected. Prefer this over closing.
- close_all: flatten the basket. Reserve for a broken thesis (opposing \
structure or a clear reversal against the position), not for ordinary noise.
- move_sl: tighten stops to current market (never loosens; the EA enforces).
- switch_lane / set_entry_mode: only if explicitly permitted.

HOW TO BE EXCELLENT AT *THIS* STRATEGY (read `strategy_facts` first)
- You are given the strategy's own numbers: HalfTrend trend/line, the strict \
window state, EMA9/21/55/200, ATR(14), ADX(14), RSI(14), MACD histogram, ATR \
percentile rank, spread history, nearest S/R walls and your OWN resolved \
accuracy. Use them; do not re-derive structure from closes by eye.
- `window.state` is decisive for entry quality: "entry_bar_next_open" is the \
strategy's A-grade setup; "waiting" is early (no entry yet); \
"missed_until_next_flip" means the move is already underway — late chasing is \
the known losing behaviour of this system, so grade late continuation setups \
down, do not celebrate them.
- Judge whether price is extended: |price_vs_ema55_atr| far above ~2 is \
stretched; entries far from the EMA mean a wider stop and worse R.
- `nearest_sr_walls` ahead of the direction is the clearest reason to lower \
confidence or pause: a wall within ~1 ATR is poor reward-to-risk.
- In a range regime with no ADX expansion and a mid-range price, most flips \
fail; require more evidence before confirming.
- Your accuracy is logged. If your per-direction accuracy is weak in a regime, \
say so and lower confidence rather than repeating the error confidently.

DECISION DISCIPLINE
- Prefer "none". A grader that interferes constantly destroys a working edge; \
intervene only when you can name the specific failed condition.
- Never duplicate an EA gate as an action. The EA already blocks on kill \
switch, daily brake, spread, trading window, exposure and ADX.
- Give an `invalidation` price (where the thesis is wrong) whenever you \
express direction, and set `expected_r` honestly: if it is below ~1, say so \
and do not confirm.

HARD LIMITS
- You cannot change risk %, drawdown, the kill switch, or any risk gate.
- You cannot open a new position. You cannot widen a stop. You cannot disable \
these limits. Attempts are rejected and audited.

EVIDENCE DISCIPLINE (strict)
- Use ONLY values present in the context JSON. Never invent figures for \
spread, daily loss, drawdown, exposure, positions or news.
- If account/heartbeat data is missing or marked unavailable, say so and treat \
those values as unknown. Do not guess them in the rationale or risk_flags.
- The candle arrays may be synthetic (a smoke test); say so if the tape looks \
machine-generated rather than real market structure.
- READ `field_notes`: `daily_loss_budget_used_pct` is the percentage of the \
daily-loss BUDGET consumed (0-100+), NOT a loss percentage and NOT a breach. \
The EA blocks entries itself when the budget is spent. Do not treat a high \
value as a reason to pause.
- Pausing entries is for genuine market/thesis reasons, never for a risk gate \
the EA already enforces (kill switch, brake, spread, window, exposure, ADX). \
Those gates are automatic; duplicating them with a pause is a bug.

ANSWER WITH JSON ONLY, no prose and no markdown:
{"direction":"bullish|bearish|neutral","confidence":0.0,"regime_agreement":\
"agree|disagree|neutral","entry_quality":0,"expected_r":0.0,"invalidation":null,\
"risk_flags":["..."],"action":"none","action_args":{},\
"rationale":"one or two sentences"}
- entry_quality: 0-100, how good THIS setup is by the strategy's own rules \
(window state, extension, walls, regime). 0 when no actionable setup.
- expected_r: realistic reward-to-risk multiple you expect from the setup \
(0.0 if unknown). Be conservative; this is scored against outcomes later.
- invalidation: the price at which your thesis is wrong (null if none)."""

VALID_DIRECTIONS = ("bullish", "bearish", "neutral")
VALID_ACTIONS = ("none", "pause_entries", "resume_entries", "close_all",
                 "move_sl", "switch_lane", "set_entry_mode")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


REFLECT_PROMPT = """You are the supervisory grading agent for the XAU Assistant, \
reviewing YOUR OWN resolved mistake so the next grade is better.

You will receive one grade that turned out WRONG (price moved against your \
stated direction over the grading horizon) together with the strategy facts \
that were available at the time.

Write ONE concrete, checkable lesson (max 240 characters). Rules:
- Name the specific condition you misread (window.state, extension from EMA-55, \
S/R wall, regime, session, spread), not a platitude.
- Make it actionable for a future bar of THIS system ("when X and Y, do Z").
- If the mistake was simply noise/no-edge, say that and prefer silence next time.
- Never propose changing risk, stops, gates or the strategy.

ANSWER WITH JSON ONLY: {"lesson":"..."}"""


class AgentError(RuntimeError):
    """Any agent failure. Callers MUST treat this as 'grading unavailable'."""


@dataclass
class AgentGrade:
    direction: str = "neutral"
    confidence: float = 0.0
    regime_agreement: str = "neutral"
    risk_flags: list = field(default_factory=list)
    action: str = "none"
    action_args: dict = field(default_factory=dict)
    rationale: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    model: str = ""
    entry_quality: int | None = None
    expected_r: float | None = None
    invalidation: float | None = None


def _round(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_context(*, symbol: str, timeframe: str, signal: str, strategy_id: str,
                  candles, regime: str, atr_value: float,
                  resp=None, heartbeat=None, db=None, context_bars: int = 120,
                  facts: dict | None = None) -> dict:
    """Compact, token-bounded snapshot of the market and the system state.

    Never raises: a context problem must degrade the grade, not the service.
    """
    try:
        bars = list(candles)[-int(context_bars):]
        closes = [_round(c.c) for c in bars]
        ctx = {
            "symbol": symbol,
            "timeframe": timeframe,
            "signal": signal,
            "strategy_id": strategy_id,
            "regime_classical": regime,
            "atr14": _round(atr_value),
            "last_close": _round(bars[-1].c) if bars else None,
            "bar_time": int(bars[-1].t) if bars else 0,
            "closes": closes,
            "highs": [_round(c.h) for c in bars],
            "lows": [_round(c.l) for c in bars],
        }
        if resp is not None:
            ctx["rules_grade"] = {"direction": getattr(resp, "direction", "neutral"),
                                  "confidence": getattr(resp, "confidence", 0.0),
                                  "verdict": getattr(resp, "verdict", "neutral")}
        if facts:
            # The strategy's own numbers (HalfTrend window, EMA stack in ATR
            # units, ADX/RSI/MACD, spread, S/R walls, the agent's accuracy).
            ctx["strategy_facts"] = facts
        if heartbeat is not None:
            ctx["account"] = {
                "equity": _round(getattr(heartbeat, "equity", 0.0)),
                "balance": _round(getattr(heartbeat, "balance", 0.0)),
                "floating_pl": _round(getattr(heartbeat, "floating_pl", 0.0)),
                "kill_switch": bool(getattr(heartbeat, "kill_switch", False)),
                "exposure_min": getattr(heartbeat, "exposure_min", 0),
                "spread_points": _round(getattr(heartbeat, "spread_points", 0.0)),
                "active_strategy": getattr(heartbeat, "active_strategy", ""),
                "entry_mode": getattr(heartbeat, "entry_mode", ""),
                # NOT a loss percentage. This is how much of the EA's
                # daily-loss BUDGET has been consumed (0-100+): 17.9 means
                # "17.9% of the budget used", which is NOT a breach. The
                # budget itself is MaxDailyLossPct (EA input, default 3.0% of
                # balance) and the EA enforces it. A grader that misreads this
                # pauses trading on a phantom breach.
                "daily_loss_budget_used_pct": _round(
                    getattr(heartbeat, "daily_loss_pct", 0.0)),
                "brake_reset_today": bool(getattr(heartbeat, "brake_reset", False)),
                "algo_trading": bool(getattr(heartbeat, "algo_trading", True)),
                "news_blackout_min": getattr(heartbeat, "news_blackout_min", 0),
                "positions": [
                    {"direction": p.direction, "lots": _round(p.lots),
                     "open_price": _round(p.open_price), "sl": _round(p.sl),
                     "profit": _round(p.profit)}
                    for p in (getattr(heartbeat, "positions", []) or [])[:5]
                ],
            }
        else:
            # No live heartbeat (service fresh, EA detached, weekend). State it
            # explicitly: a grader that silently invents spread/drawdown/loss
            # numbers is worse than one that says "unknown".
            ctx["account"] = {"status": "unavailable",
                              "reason": "no EA heartbeat received yet"}
            ctx["data_warning"] = ("account/spread/position values are NOT "
                                   "available for this grade")
        if db is not None:
            try:
                ctx["recent_ai_grades"] = [
                    {"bar_time": g.get("bar_time"), "direction": g.get("direction"),
                     "confidence": g.get("confidence"), "action": g.get("action")}
                    for g in db.recent_ai_grades(5)
                ]
                # Post-mortems of past mistakes: the accumulating edge. Kept to
                # a handful so prompt cost stays bounded.
                ctx["lessons_from_mistakes"] = [
                    l.get("lesson") for l in db.recent_lessons(5)
                    if l.get("lesson")
                ]
                stats = db.stats()
                ctx["accuracy"] = {"total": stats.get("total"),
                                   "resolved": stats.get("resolved"),
                                   "ai_correct_pct": stats.get("ai_correct_pct")}
                cfg = db.runtime_config()
                ctx["operator_settings"] = {
                    "execution_mode": cfg.get("execution_mode"),
                    "entry_mode": cfg.get("entry_mode"),
                    "risk_per_trade_pct": cfg.get("risk_per_trade_pct"),
                    "max_drawdown_pct": cfg.get("max_drawdown_pct"),
                    "profit_target_pct": cfg.get("profit_target_pct"),
                    "trading_window_hours": [cfg.get("window_start_hour"),
                                             cfg.get("window_end_hour")],
                    "max_daily_exposure_min": cfg.get("max_daily_exposure_min"),
                    "entries_paused": cfg.get("entries_paused"),
                }
            except Exception:
                pass
        ctx["field_notes"] = {
            "daily_loss_budget_used_pct": (
                "percent of the daily-loss BUDGET already consumed (0-100+). "
                "This is NOT a loss percentage and NOT a breach indicator. "
                "The EA blocks new entries itself when the budget is spent."),
            "spread_points": "current spread in broker points (informational)",
            "exposure_min": "minutes of open exposure used today",
            "operator_settings.max_daily_exposure_min": "daily exposure budget",
        }
        return ctx
    except Exception as exc:                       # pragma: no cover - defensive
        raise AgentError(f"context build failed: {exc}") from exc


def parse_grade(content: str, model: str = "",
                tokens_in: int = 0, tokens_out: int = 0,
                latency_ms: int = 0) -> AgentGrade:
    """Strict validation of the model's JSON. Raises AgentError on garbage so
    the caller can treat the grade as unavailable (never as a neutral default
    that would silently look like a real judgement)."""
    text = (content or "").strip()
    match = _JSON_FENCE.search(text)
    if match:
        text = match.group(1)
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AgentError(f"unparsable grade: {text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise AgentError("grade was not a JSON object")

    direction = str(data.get("direction", "")).lower()
    if direction not in VALID_DIRECTIONS:
        direction = "neutral"
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    agreement = str(data.get("regime_agreement", "neutral")).lower()
    if agreement not in ("agree", "disagree", "neutral"):
        agreement = "neutral"

    action = str(data.get("action", "none")).lower()
    if action not in VALID_ACTIONS:
        action = "none"
    args = data.get("action_args")
    if not isinstance(args, dict):
        args = {}

    flags = data.get("risk_flags")
    if not isinstance(flags, list):
        flags = []
    flags = [str(f)[:80] for f in flags[:6]]

    # Typed decision fields. Optional: a model that omits them still produces
    # a usable grade rather than failing the whole call.
    entry_quality = data.get("entry_quality")
    try:
        entry_quality = int(round(float(entry_quality))) if entry_quality is not None else None
        if entry_quality is not None:
            entry_quality = min(max(entry_quality, 0), 100)
    except (TypeError, ValueError):
        entry_quality = None
    expected_r = data.get("expected_r")
    try:
        expected_r = float(expected_r) if expected_r is not None else None
        if expected_r is not None and not math.isfinite(expected_r):
            expected_r = None
        if expected_r is not None:
            expected_r = min(max(expected_r, -5.0), 20.0)
    except (TypeError, ValueError):
        expected_r = None
    invalidation = data.get("invalidation")
    try:
        invalidation = float(invalidation) if invalidation is not None else None
        if invalidation is not None and (
                not math.isfinite(invalidation) or invalidation <= 0):
            invalidation = None
    except (TypeError, ValueError):
        invalidation = None

    rationale = str(data.get("rationale", ""))[:400]
    return AgentGrade(direction=direction, confidence=confidence,
                      regime_agreement=agreement, risk_flags=flags,
                      action=action, action_args=args, rationale=rationale,
                      tokens_in=tokens_in, tokens_out=tokens_out,
                      latency_ms=latency_ms, model=model,
                      entry_quality=entry_quality, expected_r=expected_r,
                      invalidation=invalidation)


class AgentClient:
    """OpenAI-compatible chat client (opencode-go / DeepSeek). Stateless."""

    def __init__(self, settings):
        self._model = settings.agent_model
        self._base_url = (settings.agent_base_url or "").rstrip("/")
        self._api_key = settings.agent_api_key
        self._timeout = settings.agent_timeout_s
        self._temperature = settings.agent_temperature
        # Some gateways (opencode-go) reject requests without a routing
        # session header. Stable for the process life; harmless elsewhere.
        self._session_header = settings.agent_session_header or "x-opencode-session"
        self._session_id = settings.agent_session_id or uuid.uuid4().hex

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def complete(self, context: dict) -> AgentGrade:
        started = time.time()
        content, usage = self._chat(
            SYSTEM_PROMPT, json.dumps(context, separators=(",", ":")))
        latency_ms = int((time.time() - started) * 1000)
        return parse_grade(content, model=self._model,
                           tokens_in=int(usage.get("prompt_tokens") or 0),
                           tokens_out=int(usage.get("completion_tokens") or 0),
                           latency_ms=latency_ms)

    def _chat(self, system: str, user: str) -> tuple[str, dict]:
        """Raw completion with a custom system prompt (grades vs reflection)."""
        if not self.configured:
            raise AgentError("agent not configured (base_url/api_key)")
        payload = {
            "model": self._model,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json",
                         self._session_header: self._session_id},
                json=payload, timeout=self._timeout)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            raise AgentError(f"agent request failed: {exc}") from exc
        try:
            choice = (body.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
        except Exception as exc:
            raise AgentError(f"malformed agent response: {exc}") from exc
        return content, (body.get("usage") or {})

    def reflect(self, payload: dict) -> str:
        """Ask for ONE checkable lesson about a resolved mistake."""
        content, _ = self._chat(REFLECT_PROMPT,
                                json.dumps(payload, separators=(",", ":")))
        text = (content or "").strip()
        match = _JSON_FENCE.search(text)
        if match:
            text = match.group(1)
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise AgentError(f"unparsable lesson: {text[:160]!r}") from exc
        lesson = str((data or {}).get("lesson", "")).strip()
        if not lesson:
            raise AgentError("empty lesson")
        return lesson[:400]


def supervise(*_args, **_kwargs):        # pragma: no cover - P2 hook
    """Placeholder for the periodic state-watch loop (P2). Present so the
    lifespan wiring is stable while the action broker lands."""
    return None
