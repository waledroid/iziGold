from app.db import SignalDb


def _mk(tmp_path):
    return SignalDb(str(tmp_path / "t.db"))


def bar(t, c=100.0):
    return {"t": t, "o": c - 1, "h": c + 2, "l": c - 2, "c": c, "v": 10.0}


def test_upsert_and_get_roundtrip(tmp_path):
    db = _mk(tmp_path)
    n = db.upsert_candles("XAUUSD", "M5", [bar(300), bar(600, 101.0)])
    assert n == 2
    rows = db.get_candles("XAUUSD", "M5")
    assert [r["t"] for r in rows] == [300, 600]
    assert rows[1]["c"] == 101.0


def test_upsert_replaces_same_bar_time(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(300, 100.0)])
    db.upsert_candles("XAUUSD", "M5", [bar(300, 105.0)])   # forming bar re-sent
    rows = db.get_candles("XAUUSD", "M5")
    assert len(rows) == 1 and rows[0]["c"] == 105.0


def test_upsert_accepts_objects(tmp_path):
    class C:
        t, o, h, l, c, v = 900, 1.0, 2.0, 0.5, 1.5, 3.0
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [C()])
    assert db.get_candles("XAUUSD", "M5")[0]["c"] == 1.5


def test_get_candles_range_and_limit(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(t) for t in range(300, 3300, 300)])
    rows = db.get_candles("XAUUSD", "M5", start_ts=600, end_ts=1200)
    assert [r["t"] for r in rows] == [600, 900, 1200]
    newest3 = db.get_candles("XAUUSD", "M5", limit=3)
    assert [r["t"] for r in newest3] == [2400, 2700, 3000]   # newest N, ascending


def test_series_are_isolated_by_symbol_and_tf(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(300)])
    db.upsert_candles("XAUUSD", "M15", [bar(900)])
    assert len(db.get_candles("XAUUSD", "M5")) == 1
    assert db.candles_range("XAUUSD", "M15") == {"start": 900, "end": 900, "count": 1}
    assert db.candles_range("EURUSD", "M5") is None
    assert db.latest_candle_series() == ("XAUUSD", "M15")


def test_latest_candle_series_empty(tmp_path):
    assert _mk(tmp_path).latest_candle_series() is None
