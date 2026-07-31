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


def test_migrates_pre_framework_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE signals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts INTEGER NOT NULL,
      bar_time INTEGER NOT NULL, symbol TEXT NOT NULL, signal TEXT NOT NULL,
      price REAL NOT NULL, direction TEXT, confidence REAL, regime TEXT,
      verdict TEXT, mode TEXT, ai_available INTEGER,
      outcome_price REAL, outcome_move REAL, ai_correct INTEGER)""")
    conn.execute("INSERT INTO signals (created_ts, bar_time, symbol, signal, price)"
                 " VALUES (1, 1, 'XAUUSD', 'BUY', 3000)")
    conn.commit()
    conn.close()
    db = SignalDb(path)  # must not raise; must add the new columns
    row = db.conn.execute("SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == (None, 1)


def test_insert_records_strategy(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    db.insert_signal(bar_time=1, symbol="XAUUSD", signal="BUY", price=3000.0,
                     direction="bullish", confidence=0.8, regime="trend",
                     verdict="confirm", mode="grading", ai_available=True,
                     strategy_id="halftrend_ema_v1", is_active=False)
    row = db.conn.execute("SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == ("halftrend_ema_v1", 0)


def test_per_strategy_stats(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    common = dict(bar_time=candles[100].t, symbol="XAUUSD", price=candles[100].c,
                  direction="bullish", confidence=0.8, regime="trend",
                  verdict="confirm", mode="grading", ai_available=True)
    db.insert_signal(signal="BUY", strategy_id="winner", is_active=True, **common)
    db.insert_signal(signal="SELL", strategy_id="loser", is_active=False, **common)
    db.resolve_outcomes(candles)
    s = db.stats()
    assert s["by_strategy"]["winner"] == {
        "signals": 1, "resolved": 1, "hit_pct": 100.0,
        "avg_move": s["by_strategy"]["winner"]["avg_move"]}
    assert s["by_strategy"]["winner"]["avg_move"] > 0   # uptrend: BUY gains
    assert s["by_strategy"]["loser"]["hit_pct"] == 0.0
    assert s["by_strategy"]["loser"]["avg_move"] < 0    # uptrend: SELL loses


def test_stats_split_per_timeframe(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    common = dict(bar_time=candles[100].t, symbol="XAUUSD", price=candles[100].c,
                  direction="bullish", confidence=0.8, regime="trend",
                  verdict="confirm", mode="grading", ai_available=True)
    db.insert_signal(signal="BUY", strategy_id="a", timeframe="M5", **common)
    db.insert_signal(signal="BUY", strategy_id="a", timeframe="M15", **common)
    db.resolve_outcomes(candles)
    s = db.stats()
    assert "a @M5" in s["by_strategy"] and "a @M15" in s["by_strategy"]
    assert s["by_strategy"]["a @M5"]["signals"] == 1


def test_stats_no_timeframe_keeps_plain_key(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    candles = trend_candles(200)
    db.insert_signal(bar_time=candles[100].t, symbol="XAUUSD", signal="BUY",
                     price=candles[100].c, direction="bullish", confidence=0.8,
                     regime="trend", verdict="confirm", mode="grading",
                     ai_available=True, strategy_id="legacy")
    assert "legacy" in db.stats()["by_strategy"]
