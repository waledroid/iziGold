import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "hb.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _hb(active="halftrend_ema_v1", equity=10000.0):
    return {"equity": equity, "balance": 10000.0, "floating_pl": 12.5,
            "positions": [{"ticket": 1, "direction": "BUY", "lots": 0.1,
                           "open_price": 2400.0, "sl": 2390.0, "profit": 12.5}],
            "kill_switch": False, "hwm": 10100.0, "exposure_min": 5,
            "window_open": True, "spread_points": 25.0, "active_strategy": active}


def test_heartbeat_stores_and_returns_empty(client):
    r = client.post("/heartbeat", json=_hb())
    body = r.json()
    assert r.status_code == 200
    assert body["switch_to"] is None
    assert body["mode"] == "auto"   # echoes the stored mode (fresh default)
    assert body["command"] is None
    from app import main
    assert main.app.state.latest_heartbeat[1].equity == 10000.0
    rows = main.app.state.db.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()
    assert rows[0] == 1


def test_heartbeat_downsampled_to_one_per_minute(client):
    from app import main
    client.post("/heartbeat", json=_hb(equity=1.0))
    client.post("/heartbeat", json=_hb(equity=2.0))   # < 60 s later: memory only
    n = main.app.state.db.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    assert n == 1
    assert main.app.state.latest_heartbeat[1].equity == 2.0   # memory has newest


def test_switch_queue_delivers_until_confirmed(client):
    r = client.post("/api/switch", json={"strategy_id": "boll_stochrsi_v1"})
    assert r.status_code == 200 and r.json() == {"pending": "boll_stochrsi_v1"}
    # delivered while the EA still reports the old strategy
    r = client.post("/heartbeat", json=_hb(active="halftrend_ema_v1"))
    body = r.json()
    assert body["switch_to"] == "boll_stochrsi_v1"
    assert body["mode"] == "auto"   # echoes the stored mode (fresh default)
    assert body["command"] is None
    r = client.post("/heartbeat", json=_hb(active="halftrend_ema_v1"))
    body = r.json()
    assert body["switch_to"] == "boll_stochrsi_v1"   # at-least-once
    # EA reports the new id active -> cleared
    r = client.post("/heartbeat", json=_hb(active="boll_stochrsi_v1"))
    body = r.json()
    assert body["switch_to"] is None
    assert body["mode"] == "auto"   # echoes the stored mode (fresh default)
    assert body["command"] is None
    r = client.post("/heartbeat", json=_hb(active="boll_stochrsi_v1"))
    body = r.json()
    assert body["switch_to"] is None


def test_switch_queue_cancel_clears_pending(client):
    r = client.post("/api/switch", json={"strategy_id": "boll_stochrsi_v1"})
    assert r.status_code == 200 and r.json() == {"pending": "boll_stochrsi_v1"}
    r = client.post("/api/switch", json={"strategy_id": ""})
    assert r.status_code == 200 and r.json() == {"pending": None}
    r = client.post("/heartbeat", json=_hb(active="halftrend_ema_v1"))
    body = r.json()
    assert body["switch_to"] is None
    assert body["mode"] == "auto"   # echoes the stored mode (fresh default)
    assert body["command"] is None
