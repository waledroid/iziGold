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


def test_dashboard_served(client):
    client.post("/ui/profile", json={})  # Skip: profile row must exist for /ui to serve the dashboard
    r = client.get("/ui")
    assert r.status_code == 200
    assert "XAU Assistant" in r.text
    for needle in ("/ui/state", "/ui/equity", "/ui/stats", "/ui/signals", "/ui/switch", "/ui/trades"):
        assert needle in r.text
