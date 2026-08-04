import importlib

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import trend_candles


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_state_empty_then_populated(client):
    r = client.get("/ui/state")
    assert r.status_code == 200
    assert r.json()["heartbeat"] is None and r.json()["age_s"] is None
    client.post("/heartbeat", json={"equity": 1.0, "balance": 1.0, "floating_pl": 0.0})
    body = client.get("/ui/state").json()
    assert body["heartbeat"]["equity"] == 1.0
    assert body["age_s"] is not None and body["age_s"] < 5


def test_equity_and_stats_endpoints(client):
    client.post("/heartbeat", json={"equity": 5.0, "balance": 5.0, "floating_pl": 0.0})
    eq = client.get("/ui/equity").json()
    assert eq["series"][-1]["equity"] == 5.0
    st = client.get("/ui/stats").json()
    assert "by_strategy" in st


def test_signals_endpoint(client):
    payload = {"symbol": "XAUUSD", "timeframe": "M15", "signal": "BUY",
               "strategy_id": "halftrend_ema_v1",
               "candles": [c.model_dump() for c in trend_candles(200)]}
    client.post("/analyze", json=payload)
    body = client.get("/ui/signals").json()
    assert body["signals"][0]["strategy_id"] == "halftrend_ema_v1"
    assert body["signals"][0]["signal"] == "BUY"


def test_switch_rejects_invalid_strategy_id(client):
    r = client.post("/ui/switch", json={"strategy_id": "<script>x</script>"})
    assert r.status_code == 400


def test_switch_accepts_valid_strategy_id(client):
    r = client.post("/ui/switch", json={"strategy_id": "halftrend_ema_v1"})
    assert r.status_code == 200
    assert r.json()["pending"] == "halftrend_ema_v1"


def test_overlays_empty_when_no_candles(client):
    r = client.get("/ui/overlays?strategy=halftrend_ema_v1")
    assert r.status_code == 200
    assert r.json() == {}


def test_overlays_empty_for_unknown_strategy(client):
    payload = {"symbol": "XAUUSD", "timeframe": "M15", "signal": "NONE",
               "strategy_id": "halftrend_ema_v1",
               "candles": [c.model_dump() for c in trend_candles(200)]}
    client.post("/analyze", json=payload)
    r = client.get("/ui/overlays?strategy=nope")
    assert r.status_code == 200
    assert r.json() == {}


def test_overlays_halftrend_ema_v1(client):
    candles = trend_candles(200)
    payload = {"symbol": "XAUUSD", "timeframe": "M15", "signal": "NONE",
               "strategy_id": "halftrend_ema_v1",
               "candles": [c.model_dump() for c in candles]}
    client.post("/analyze", json=payload)
    r = client.get("/ui/overlays?strategy=halftrend_ema_v1")
    assert r.status_code == 200
    body = r.json()
    n = len(candles)
    for key in ("halftrend", "ema55", "ema9", "ema21", "ema200"):
        assert key in body
        assert len(body[key]) == n
    settled = [v for v in body["halftrend"] if v is not None]
    assert settled  # trend data settles well before the end of 200 bars
    assert all(isinstance(v, list) and len(v) == 2 for v in settled)
    settled_ema = [v for v in body["ema55"] if v is not None]
    assert settled_ema


def test_overlays_boll_stochrsi_v1(client):
    candles = trend_candles(200)
    payload = {"symbol": "XAUUSD", "timeframe": "M15", "signal": "NONE",
               "strategy_id": "boll_stochrsi_v1",
               "candles": [c.model_dump() for c in candles]}
    client.post("/analyze", json=payload)
    r = client.get("/ui/overlays?strategy=boll_stochrsi_v1")
    assert r.status_code == 200
    body = r.json()
    n = len(candles)
    for key in ("bb_upper", "bb_mid", "bb_lower"):
        assert key in body
        assert len(body[key]) == n
    assert any(v is not None for v in body["bb_mid"])


def test_dashboard_served(client):
    client.post("/ui/profile", json={})  # Skip: profile row must exist for /ui to serve the dashboard
    r = client.get("/ui")
    assert r.status_code == 200
    assert "XAU Assistant" in r.text
    for needle in ("/ui/state", "/ui/candles", "/ui/stats", "/ui/signals", "/ui/switch",
                   "/ui/trades", "/ui/overlays"):
        assert needle in r.text
