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


def test_shadows_logged_active_proposed_once(client):
    # Updated for the alert-diet contract (Task 4): /analyze no longer sends
    # a per-bar text alert via send_alert. Instead, the active signal alone
    # may raise a pending proposal (default exec_mode is manual); shadow
    # signals are logged but never drive proposals.
    from app import main
    payload = _payload("BUY")
    payload["strategy_id"] = "halftrend_ema_v1"
    payload["shadows"] = [{"strategy_id": "stub", "signal": "SELL"},
                          {"strategy_id": "quiet", "signal": "NONE"}]
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    rows = main.app.state.db.conn.execute(
        "SELECT strategy_id, signal, is_active FROM signals ORDER BY id").fetchall()
    assert rows == [("halftrend_ema_v1", "BUY", 1), ("stub", "SELL", 0)]
    proposals = main.app.state.db.conn.execute(
        "SELECT strategy_id FROM proposals").fetchall()
    assert proposals == [("halftrend_ema_v1",)]   # active signal only; shadows never propose


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


@pytest.fixture
def analyze_payload():
    return _payload("NONE")


@pytest.fixture
def heartbeat_payload():
    return {"equity": 10000.0, "balance": 10000.0, "floating_pl": 0.0}


def test_ui_candles_empty_before_analyze(client):
    r = client.get("/ui/candles")
    assert r.status_code == 200
    body = r.json()
    assert body == {"symbol": "", "timeframe": "", "candles": []}


def test_ui_candles_returns_last_analyze_window(client, analyze_payload):
    # analyze_payload: reuse/extend the file's existing valid /analyze payload
    client.post("/analyze", json=analyze_payload)
    r = client.get("/ui/candles")
    body = r.json()
    assert body["symbol"] == analyze_payload["symbol"]
    assert body["timeframe"] == analyze_payload["timeframe"]
    assert len(body["candles"]) == min(len(analyze_payload["candles"]), 300)
    assert body["candles"][-1]["c"] == analyze_payload["candles"][-1]["c"]


def test_ui_candles_window_capped_at_300(client, analyze_payload):
    base = analyze_payload["candles"][0]
    analyze_payload["candles"] = [
        {**base, "t": base["t"] + i * 300} for i in range(350)
    ]
    client.post("/analyze", json=analyze_payload)
    r = client.get("/ui/candles")
    assert len(r.json()["candles"]) == 300
    assert r.json()["candles"][-1]["t"] == base["t"] + 349 * 300


def test_heartbeat_response_carries_mode_and_command(client, heartbeat_payload):
    r = client.post("/heartbeat", json=heartbeat_payload)
    body = r.json()
    assert body["mode"] in ("auto", "manual")
    assert "command" in body


def test_heartbeat_request_algo_trading_defaults_true():
    from app.models import HeartbeatRequest
    hb = HeartbeatRequest(equity=1, balance=1, floating_pl=0)
    assert hb.algo_trading is True


def test_heartbeat_accepts_algo_trading_false(client, heartbeat_payload):
    r = client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": False})
    assert r.status_code == 200
