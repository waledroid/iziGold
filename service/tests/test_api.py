import importlib

import pytest
from fastapi.testclient import TestClient

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


def _payload(signal="BUY"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "candles": [c.model_dump() for c in trend_candles(200)]}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_analyze_buy_in_uptrend_confirms(client):
    r = client.post("/analyze", json=_payload("BUY"))
    body = r.json()
    assert r.status_code == 200
    assert body["direction"] == "bullish"
    assert body["verdict"] in ("confirm", "neutral")
    assert body["ai_available"] is True


def test_analyze_none_still_returns(client):
    r = client.post("/analyze", json=_payload("NONE"))
    assert r.status_code == 200 and r.json()["verdict"] == "neutral"


def test_fail_open_on_model_error(client):
    from app import main

    class Boom:
        def forecast(self, closes, horizon):
            raise RuntimeError("model exploded")

    main.app.state.forecaster = Boom()
    r = client.post("/analyze", json=_payload("BUY"))
    body = r.json()
    assert r.status_code == 200
    assert body["ai_available"] is False
    assert body["direction"] == "neutral" and body["confidence"] == 0.0
