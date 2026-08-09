"""Spread telemetry (ea-scope spec §3): AnalyzeRequest optional fields,
spread_history upserts on /analyze, and the spread_stats helper."""
import importlib

import pytest
from fastapi.testclient import TestClient

from app.db import SignalDb
from tests.fixtures import trend_candles


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _payload(signal="NONE", **spread):
    p = {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
         "candles": [c.model_dump() for c in trend_candles(200)]}
    p.update(spread)
    return p


def _spread_rows(client):
    from app import main
    return main.app.state.db.conn.execute(
        "SELECT bar_time, spread_min, spread_avg, spread_max"
        " FROM spread_history").fetchall()


# -- contract: fields are optional and default to 0.0 (old EAs keep working)

def test_analyze_without_spread_fields_still_works(client):
    r = client.post("/analyze", json=_payload("BUY"))
    assert r.status_code == 200


def test_analyze_request_model_defaults():
    from app.models import AnalyzeRequest
    req = AnalyzeRequest(symbol="XAUUSD", timeframe="M5", signal="NONE",
                         candles=trend_candles(60))
    assert req.spread_min == 0.0
    assert req.spread_avg == 0.0
    assert req.spread_max == 0.0


# -- upsert behavior

def test_analyze_with_spread_fields_writes_row(client):
    payload = _payload("NONE", spread_min=10.0, spread_avg=12.5, spread_max=18.0)
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    rows = _spread_rows(client)
    assert len(rows) == 1
    bar_time, smin, savg, smax = rows[0]
    assert bar_time == payload["candles"][-1]["t"]
    assert (smin, savg, smax) == (10.0, 12.5, 18.0)


def test_analyze_all_zero_spread_writes_nothing(client):
    r = client.post("/analyze", json=_payload("NONE"))
    assert r.status_code == 200
    r = client.post("/analyze", json=_payload(
        "NONE", spread_min=0.0, spread_avg=0.0, spread_max=0.0))
    assert r.status_code == 200
    assert _spread_rows(client) == []


def test_analyze_same_bar_replaces_row(client):
    payload = _payload("NONE", spread_min=10.0, spread_avg=12.0, spread_max=15.0)
    client.post("/analyze", json=payload)
    payload2 = dict(payload, spread_min=11.0, spread_avg=13.0, spread_max=16.0)
    client.post("/analyze", json=payload2)
    rows = _spread_rows(client)
    assert len(rows) == 1
    assert rows[0][1:] == (11.0, 13.0, 16.0)


# -- spread_stats helper

def test_spread_stats_on_seeded_rows(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    base = 1_700_000_000
    db.upsert_spread(bar_time=base, spread_min=10.0, spread_avg=12.0, spread_max=20.0)
    db.upsert_spread(bar_time=base + 300, spread_min=8.0, spread_avg=14.0, spread_max=16.0)
    db.upsert_spread(bar_time=base + 600, spread_min=9.0, spread_avg=10.0, spread_max=30.0)
    s = db.spread_stats(hours=24)
    assert s["n"] == 3
    assert s["min"] == 8.0
    assert s["max"] == 30.0
    assert s["avg"] == pytest.approx(12.0)


def test_spread_stats_window_excludes_old_rows(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    base = 1_700_000_000
    # 25 hours before the newest row -- outside a 24 h window
    db.upsert_spread(bar_time=base - 25 * 3600, spread_min=1.0, spread_avg=1.0, spread_max=1.0)
    db.upsert_spread(bar_time=base, spread_min=10.0, spread_avg=12.0, spread_max=20.0)
    s = db.spread_stats(hours=24)
    assert s["n"] == 1
    assert s["min"] == 10.0 and s["max"] == 20.0 and s["avg"] == 12.0


def test_spread_stats_empty(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    s = db.spread_stats()
    assert s == {"n": 0, "min": 0.0, "avg": 0.0, "max": 0.0}
