from app.db import SignalDb
from tests.fixtures import trend_candles


def test_insert_and_resolve(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    sig_bar = candles[100].t
    db.insert_signal(bar_time=sig_bar, symbol="XAUUSD", signal="BUY",
                     price=candles[100].c, direction="bullish", confidence=0.8,
                     regime="trend", verdict="confirm", mode="grading", ai_available=True)
    assert db.resolve_outcomes(candles) == 1
    s = db.stats()
    assert s["resolved"] == 1 and s["ai_correct_pct"] == 100.0


def test_unresolved_when_horizon_not_reached(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    db.insert_signal(bar_time=candles[-1].t, symbol="XAUUSD", signal="SELL",
                     price=candles[-1].c, direction="bearish", confidence=0.7,
                     regime="trend", verdict="confirm", mode="grading", ai_available=True)
    assert db.resolve_outcomes(candles) == 0
