"""DeepSeek supervisory agent: parsing, persistence, bounds, fail-open.

These tests never touch the network. The agent is exercised through its pure
functions plus a stub app, because the contract that matters is: when the
model is unavailable, nothing in the trading path changes.
"""

import asyncio
import time
import types

import pytest

from app.agent import (AgentClient, AgentGrade, AgentError, build_context,
                       parse_grade)
from app.config import settings
from app.db import SignalDb
from app.forecaster import RulesForecaster, get_forecaster


@pytest.fixture
def db(tmp_path):
    return SignalDb(str(tmp_path / "agent.db"))


def _candle(t, c, h=None, l=None):
    return types.SimpleNamespace(t=t, o=c, h=h if h is not None else c,
                                 l=l if l is not None else c, c=c, v=1.0)


def _stub_app(db, positions=None):
    hb = None
    if positions is not None:
        hb = (time.time(), types.SimpleNamespace(positions=positions))
    return types.SimpleNamespace(state=types.SimpleNamespace(
        db=db, telegram=None, latest_heartbeat=hb))


def _pos(direction="BUY"):
    return types.SimpleNamespace(direction=direction, lots=0.1, open_price=2400.0,
                                 sl=2390.0, profit=1.0)


# --- fast tier -------------------------------------------------------------

def test_rules_forecaster_is_neutral():
    fc = RulesForecaster().forecast([2400.0] * 50, 16)
    assert fc.q50[-1] == 2400.0 and fc.q10[-1] == 2400.0 and fc.q90[-1] == 2400.0


def test_get_forecaster_rules_and_unknown():
    assert isinstance(get_forecaster(types.SimpleNamespace(forecaster="rules")),
                      RulesForecaster)
    with pytest.raises(ValueError):
        get_forecaster(types.SimpleNamespace(forecaster="nope"))


# --- grade parsing ---------------------------------------------------------

def test_parse_grade_valid_and_fenced():
    g = parse_grade('{"direction":"bullish","confidence":0.72,'
                    '"regime_agreement":"agree","risk_flags":["extended"],'
                    '"action":"none","rationale":"trend intact"}', model="m")
    assert g.direction == "bullish" and g.confidence == 0.72
    assert g.risk_flags == ["extended"] and g.model == "m"

    fenced = parse_grade('```json\n{"direction":"bearish","confidence":0.5}\n```')
    assert fenced.direction == "bearish" and fenced.confidence == 0.5


def test_parse_grade_rejects_garbage():
    with pytest.raises(AgentError):
        parse_grade("not json at all")
    with pytest.raises(AgentError):
        parse_grade("[1,2,3]")


def test_parse_grade_clamps_and_whitelists():
    g = parse_grade('{"direction":"moon","confidence":5,'
                    '"action":"delete_everything","risk_flags":"x"}')
    assert g.direction == "neutral"
    assert g.confidence == 1.0
    assert g.action == "none"          # unknown action is not executed
    assert g.risk_flags == []          # non-list coerced to empty


def test_agent_client_unconfigured_raises():
    client = AgentClient(types.SimpleNamespace(
        agent_model="m", agent_base_url="", agent_api_key="",
        agent_timeout_s=5, agent_temperature=0.0,
        agent_session_header="", agent_session_id=""))
    assert client.configured is False
    with pytest.raises(AgentError):
        client.complete({})


# --- context ---------------------------------------------------------------

def test_build_context_is_bounded_and_complete():
    candles = [_candle(1_700_000_000 + i * 300, 2400 + i * 0.1)
               for i in range(200)]
    ctx = build_context(symbol="XAUUSD", timeframe="M5", signal="BUY",
                        strategy_id="izi5", candles=candles,
                        regime="trend", atr_value=3.2, context_bars=60)
    assert ctx["symbol"] == "XAUUSD" and ctx["signal"] == "BUY"
    assert len(ctx["closes"]) == 60          # context_bars bounds the tokens
    assert ctx["regime_classical"] == "trend"
    assert ctx["atr14"] == 3.2


# --- persistence -----------------------------------------------------------

def test_ai_grade_roundtrip_and_latest_by_bar(db):
    db.insert_ai_grade(bar_time=100, symbol="XAUUSD", timeframe="M5",
                       direction="bullish", confidence=0.8, action="none")
    db.insert_ai_grade(bar_time=200, symbol="XAUUSD", timeframe="M5",
                       direction="bearish", confidence=0.6, action="none")
    assert db.latest_ai_grade("XAUUSD", "M5")["direction"] == "bearish"
    assert db.latest_ai_grade("XAUUSD", "M5", 100)["direction"] == "bullish"
    assert db.latest_ai_grade("XAUUSD", "M15") is None
    assert len(db.recent_ai_grades(10)) == 2


def test_agent_action_audit_and_rate_count(db):
    a1 = db.insert_agent_action(intent="pause_entries")
    db.finish_agent_action(a1, status="applied", result="pause_entries")
    db.insert_agent_action(intent="close_all")
    assert db.agent_actions_since(0) == 2
    assert db.recent_agent_actions(5)[0]["intent"] == "close_all"
    assert db.recent_agent_actions(5)[1]["status"] == "applied"


def test_entries_paused_kv(db):
    assert db.entries_paused() is False
    db.set_entries_paused(True)
    assert db.entries_paused() is True
    assert db.runtime_config()["entries_paused"] is True
    db.set_entries_paused(False)
    assert db.entries_paused() is False


def test_resolve_outcomes_is_series_scoped(db):
    db.insert_signal(bar_time=1000, symbol="XAUUSD", signal="BUY", price=2400.0,
                     direction="bullish", confidence=0.8, regime="trend",
                     verdict="confirm", mode="grading", ai_available=True,
                     strategy_id="izi5", is_active=True,
                     timeframe="M5")
    db.insert_signal(bar_time=1000, symbol="XAUUSD", signal="BUY", price=2400.0,
                     direction="bullish", confidence=0.8, regime="trend",
                     verdict="confirm", mode="grading", ai_available=True,
                     strategy_id="izi15", is_active=False,
                     timeframe="M15")
    candles = [_candle(1000 + i * 300, 2400.0 + i) for i in range(20)]
    assert db.resolve_outcomes(candles, 16, symbol="XAUUSD", timeframe="M5") == 1
    rows = db.conn.execute("SELECT timeframe, outcome_price FROM signals").fetchall()
    resolved = {tf: price for tf, price in rows}
    assert resolved["M5"] is not None
    assert resolved["M15"] is None          # the other series was untouched


# --- action broker bounds --------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_execute_intent_l0_is_skipped(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L0")
    db.save_profile({"account_type": "demo"})
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="pause_entries", rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.entries_paused() is False
    assert db.recent_agent_actions(1)[0]["status"] == "skipped"


def test_execute_intent_pause_on_demo(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_allow_live_actions", False)
    db.save_profile({"account_type": "demo"})
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="pause_entries", rationale="chop"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.entries_paused() is True
    assert db.recent_agent_actions(1)[0]["status"] == "applied"


def test_execute_intent_live_account_is_rejected(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_allow_live_actions", False)
    db.save_profile({"account_type": "live"})
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="pause_entries", rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.entries_paused() is False
    row = db.recent_agent_actions(1)[0]
    assert row["status"] == "rejected" and "live" in row["reason"]


def test_execute_intent_close_all_queues_proposal(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_allow_live_actions", False)
    db.save_profile({"account_type": "demo"})
    app = _stub_app(db, positions=[_pos("SELL")])
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="close_all", rationale="reversal"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    proposal = db.active_proposal()
    assert proposal is not None and proposal["kind"] == "exit"
    assert proposal["status"] == "approved"
    assert proposal["direction"] == "SELL"


def test_execute_intent_one_in_flight(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    db.save_profile({"account_type": "demo"})
    db.create_proposal("entry", "BUY", "s", 1.0, None)     # already in flight
    app = _stub_app(db, positions=[_pos("BUY")])
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="close_all", rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    row = db.recent_agent_actions(1)[0]
    assert row["status"] == "rejected" and "in flight" in row["reason"]


def test_execute_intent_rate_budget(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_max_actions_per_hour", 2)
    db.save_profile({"account_type": "demo"})
    for _ in range(2):
        db.insert_agent_action(intent="pause_entries")
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="resume_entries", rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.recent_agent_actions(1)[0]["status"] == "rejected"


def test_execute_intent_lane_switch_disabled(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_allow_lane_switch", False)
    db.save_profile({"account_type": "demo"})
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="switch_lane",
                        action_args={"strategy_id": "izi15"},
                        rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.get_kv("active_strategy") is None
    assert db.recent_agent_actions(1)[0]["status"] == "rejected"


# --- supervisor backfill (restart gap) -------------------------------------

class _FakeAgent:
    def __init__(self, grade):
        self.grade = grade
        self.calls = 0

    def complete(self, ctx):
        self.calls += 1
        return self.grade


def _seed_series(db, bars=80, signal="BUY"):
    db.conn.execute(
        "INSERT INTO candles (symbol, timeframe, bar_time, o, h, l, c, v)"
        " VALUES ('XAUUSD','M5',?,?,?,?,?,?)",
        (1789966200, 4370.0, 4372.0, 4368.0, 4371.0, 100.0))
    db.conn.commit()
    db.insert_signal(bar_time=1789966200, symbol="XAUUSD", signal=signal,
                     price=4371.0, direction="neutral", confidence=0.0,
                     regime="trend", verdict="neutral", mode="grading",
                     ai_available=False, strategy_id="izi5",
                     is_active=True, timeframe="M5")


def test_ungraded_signal_and_backfill(db):
    _seed_series(db)
    pending = db.ungraded_signal("XAUUSD", "M5")
    assert pending is not None and pending["signal"] == "BUY"

    fake = _FakeAgent(AgentGrade(direction="bullish", confidence=0.6,
                                 action="none", rationale="ok"))
    app = _stub_app(db)
    app.state.agent = fake
    app.state.agent_grade_cache = {}
    from app import agent_actions
    _run(asyncio.wait_for(agent_actions.supervise(app), timeout=10))
    assert fake.calls == 1
    assert db.latest_ai_grade("XAUUSD", "M5", 1789966200)["direction"] == "bullish"
    # nothing left outstanding -> a second tick does no work
    assert db.ungraded_signal("XAUUSD", "M5") is None


def test_supervise_noop_without_agent(db):
    _seed_series(db)
    app = _stub_app(db)
    app.state.agent = None
    from app import agent_actions
    _run(asyncio.wait_for(agent_actions.supervise(app), timeout=5))
    assert db.ungraded_signal("XAUUSD", "M5") is not None


# --- strategy facts --------------------------------------------------------

def _trend_candles(n=120):
    out = []
    for i in range(n):
        c = 2400.0 + i * 0.3
        out.append(_candle(1754000000 + i * 300, c, h=c + 0.5, l=c - 0.5))
    return out


def test_confirm_closes_from_shared_config():
    from app.agent_context import confirm_closes
    assert confirm_closes("izi5") == 2
    assert confirm_closes("izi15") == 1
    assert confirm_closes("unknown_lane") == 2      # documented fallback


def test_strategy_facts_shape():
    from app.agent_context import strategy_facts
    facts = strategy_facts(_trend_candles(), symbol="XAUUSD", timeframe="M5",
                           strategy_id="izi5")
    for key in ("price", "atr14", "adx14", "rsi14", "ema55", "ema200",
                "regime_classical", "window"):
        assert key in facts, key
    assert facts["window"]["state"] in ("waiting", "entry_bar_next_open",
                                        "missed_until_next_flip", "unknown")
    assert "bars_since_flip" in facts["window"]


def test_strategy_facts_defensive_on_short_input():
    from app.agent_context import strategy_facts
    assert strategy_facts([], symbol="XAUUSD", timeframe="M5") == {}


def test_parse_grade_typed_fields():
    g = parse_grade('{"direction":"bullish","confidence":0.7,"entry_quality":140,'
                    '"expected_r":-99,"invalidation":-5,"action":"none"}')
    assert g.entry_quality == 100          # clamped to 0-100
    assert g.expected_r == -5.0            # clamped to the sane range
    assert g.invalidation is None          # non-positive price is unusable

    g2 = parse_grade('{"direction":"bearish","confidence":0.5,"entry_quality":"60",'
                     '"expected_r":1.8,"invalidation":4390.5}')
    assert g2.entry_quality == 60 and g2.expected_r == 1.8
    assert g2.invalidation == 4390.5


def test_resolve_grade_outcomes_scores_grades(db):
    db.insert_signal(bar_time=1000, symbol="XAUUSD", signal="BUY", price=2400.0,
                     direction="bullish", confidence=0.8, regime="trend",
                     verdict="confirm", mode="grading", ai_available=True,
                     strategy_id="izi5", is_active=True,
                     timeframe="M5")
    db.insert_ai_grade(bar_time=1000, symbol="XAUUSD", timeframe="M5",
                       direction="bullish", confidence=0.8)
    db.insert_ai_grade(bar_time=1000, symbol="XAUUSD", timeframe="M5",
                       direction="bearish", confidence=0.3)
    candles = [_candle(1000 + i * 300, 2400.0 + i) for i in range(20)]
    assert db.resolve_grade_outcomes(candles, 16, symbol="XAUUSD",
                                    timeframe="M5") == 2
    acc = db.agent_accuracy()
    assert acc["resolved"] == 2
    assert acc["by_direction"]["bullish"]["acc_pct"] == 100.0
    assert acc["by_direction"]["bearish"]["acc_pct"] == 0.0
    assert acc["acc_pct"] == 50.0


# --- lessons loop ----------------------------------------------------------

def test_lesson_queue_and_feedback_loop(db):
    db.insert_ai_grade(bar_time=1000, symbol="XAUUSD", timeframe="M5",
                       direction="bullish", confidence=0.8, window_state="waiting",
                       regime="range", session="Asian session", entry_quality=30)
    db.insert_ai_grade(bar_time=1300, symbol="XAUUSD", timeframe="M5",
                       direction="bearish", confidence=0.7, window_state="entry_bar_next_open")
    candles = [_candle(1000 + i * 300, 2400.0 - i) for i in range(20)]
    db.resolve_grade_outcomes(candles, 16, symbol="XAUUSD", timeframe="M5")

    # A wrong directional grade is queued for reflection.
    queue = db.unreflected_grades(5)
    assert len(queue) == 1 and queue[0]["direction"] == "bullish"
    db.insert_lesson(grade_id=queue[0]["id"], bar_time=1000, symbol="XAUUSD",
                     timeframe="M5", direction="bullish",
                     window_state="waiting", regime="range",
                     session="Asian session", entry_quality=30,
                     outcome_move=-16.0,
                     lesson="In range regime with window waiting, wait for the entry bar.")
    assert db.unreflected_grades(5) == []          # reflected once, not re-queued
    assert "wait for the entry bar" in db.recent_lessons(5)[0]["lesson"]


def test_reflect_writes_lesson(monkeypatch, db):
    from app import agent_actions
    db.insert_ai_grade(bar_time=1000, symbol="XAUUSD", timeframe="M5",
                       direction="bullish", confidence=0.8)
    candles = [_candle(1000 + i * 300, 2400.0 - i) for i in range(20)]
    db.resolve_grade_outcomes(candles, 16, symbol="XAUUSD", timeframe="M5")

    class _FakeReflectAgent:
        def __init__(self):
            self.payloads = []
        def reflect(self, payload):
            self.payloads.append(payload)
            return "Do not confirm an extended entry far from EMA-55."

    app = _stub_app(db)
    app.state.agent = _FakeReflectAgent()
    _run(asyncio.wait_for(agent_actions.reflect(app), timeout=5))
    assert app.state.agent.payloads and app.state.agent.payloads[0]["your_direction"] == "bullish"
    assert db.recent_lessons(1)[0]["lesson"].startswith("Do not confirm")


# --- setup scorecard -------------------------------------------------------

def test_scorecard_groups_by_setup(db):
    for i, (direction, correct, ws, regime, session, quality) in enumerate([
            ("bullish", 1, "entry_bar_next_open", "trend", "London open", 80),
            ("bullish", 0, "entry_bar_next_open", "trend", "London open", 80),
            ("bearish", 0, "missed_until_next_flip", "range", "Asian session", 20)]):
        bar = 1000 + i * 1000
        db.insert_ai_grade(bar_time=bar, symbol="XAUUSD", timeframe="M5",
                           direction=direction, confidence=0.6, window_state=ws,
                           regime=regime, session=session, entry_quality=quality,
                           expected_r=1.5)
        db.conn.execute("UPDATE ai_grades SET ai_correct=? WHERE bar_time=?",
                        (correct, bar))
    db.conn.commit()
    card = db.agent_scorecard()
    assert card["overall"]["resolved"] == 3
    assert card["by_window_state"]["entry_bar_next_open"] == {"n": 2, "acc_pct": 50.0}
    assert card["by_regime"]["trend"]["n"] == 2
    assert card["by_session"]["Asian session"]["acc_pct"] == 0.0
    assert card["by_entry_quality"]["70-100"]["n"] == 2
    assert card["by_entry_quality"]["0-39"]["n"] == 1


# --- calibration gate ------------------------------------------------------

def test_calibration_gate_demo_is_open(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_allow_live_actions", False)
    app = _stub_app(db)
    gate = agent_actions.calibration_gate(app)
    assert gate["reason"] == ""          # demo path is only rate-bounded


def test_calibration_gate_blocks_live_without_evidence(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_allow_live_actions", True)
    monkeypatch.setattr(settings, "agent_gate_bypass", False)
    monkeypatch.setattr(settings, "agent_min_resolved", 10)
    app = _stub_app(db)
    gate = agent_actions.calibration_gate(app)
    assert "insufficient evidence" in gate["reason"]


def test_calibration_gate_passes_with_evidence(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_allow_live_actions", True)
    monkeypatch.setattr(settings, "agent_gate_bypass", False)
    monkeypatch.setattr(settings, "agent_min_resolved", 2)
    monkeypatch.setattr(settings, "agent_min_accuracy_pct", 50.0)
    for i in range(2):
        db.insert_ai_grade(bar_time=1000 + i, symbol="XAUUSD", timeframe="M5",
                           direction="bullish", confidence=0.7)
        db.conn.execute("UPDATE ai_grades SET ai_correct=1 WHERE bar_time=?",
                        (1000 + i,))
    db.conn.commit()
    app = _stub_app(db)
    assert agent_actions.calibration_gate(app)["reason"] == ""


def test_execute_intent_blocked_by_gate_on_live(monkeypatch, db):
    from app import agent_actions
    monkeypatch.setattr(settings, "agent_level", "L3")
    monkeypatch.setattr(settings, "agent_allow_live_actions", True)
    monkeypatch.setattr(settings, "agent_gate_bypass", False)
    monkeypatch.setattr(settings, "agent_min_resolved", 10)
    db.save_profile({"account_type": "live"})
    app = _stub_app(db)
    _run(agent_actions.execute_intent(
        app, AgentGrade(action="pause_entries", rationale="x"),
        symbol="XAUUSD", timeframe="M5", bar_time=1, grade_id=1))
    assert db.entries_paused() is False
    row = db.recent_agent_actions(1)[0]
    assert row["status"] == "rejected" and "evidence" in row["reason"]
