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


def test_shadows_logged_active_alerted_once(client, monkeypatch):
    from app import main
    alerts = []
    monkeypatch.setattr(main, "send_alert", lambda text, settings: alerts.append(text))
    payload = _payload("BUY")
    payload["strategy_id"] = "halftrend_ema_v1"
    payload["shadows"] = [{"strategy_id": "stub", "signal": "SELL"},
                          {"strategy_id": "quiet", "signal": "NONE"}]
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    rows = main.app.state.db.conn.execute(
        "SELECT strategy_id, signal, is_active FROM signals ORDER BY id").fetchall()
    assert rows == [("halftrend_ema_v1", "BUY", 1), ("stub", "SELL", 0)]
    assert len(alerts) == 1          # active signal only; shadows are silent


def test_old_style_request_tagged_unknown(client):
    from app import main
    r = client.post("/analyze", json=_payload("BUY"))   # no new fields
    assert r.status_code == 200
    row = main.app.state.db.conn.execute(
        "SELECT strategy_id, is_active FROM signals").fetchone()
    assert row == ("unknown", 1)


def test_analyze_records_timeframe(client):
    payload = _payload("BUY")
    payload["timeframe"] = "M5"
    payload["strategy_id"] = "halftrend_ema_v1"
    payload["shadows"] = [{"strategy_id": "stub", "signal": "SELL"}]
    client.post("/analyze", json=payload)
    from app import main
    rows = main.app.state.db.conn.execute(
        "SELECT strategy_id, timeframe FROM signals ORDER BY id").fetchall()
    assert rows == [("halftrend_ema_v1", "M5"), ("stub", "M5")]
    assert "halftrend_ema_v1 @M5" in main.app.state.db.stats()["by_strategy"]
