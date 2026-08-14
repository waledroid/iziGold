"""Mini-app feed service: keyed push, ring buffers, history, WS deltas."""
import importlib

import pytest
from fastapi.testclient import TestClient

TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        yield c


def _candle(t, c=4000.0):
    return {"t": t, "o": c - 1, "h": c + 2, "l": c - 2, "c": c, "v": 10}


def _push(client, body, key="sekret"):
    return client.post("/feed/push", json=body, headers={"X-Feed-Key": key})


def test_push_requires_key(client):
    assert _push(client, {"tick": None}, key="wrong").status_code == 403
    assert _push(client, {"tick": None}).status_code == 200


def test_history_appends_and_replaces_forming_bar(client):
    _push(client, {"candles": {"M5": [_candle(1000, 4000.0)]}})
    _push(client, {"candles": {"M5": [_candle(1000, 4001.5)]}})   # same t -> replace
    _push(client, {"candles": {"M5": [_candle(1300, 4002.0)]}})   # newer -> append
    _push(client, {"candles": {"M5": [_candle(700, 3999.0)]}})    # older -> ignored
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert [c["t"] for c in body["candles"]] == [1000, 1300]
    assert body["candles"][0]["c"] == 4001.5


def test_history_ring_buffer_caps_at_500(client):
    _push(client, {"candles": {"M1": [_candle(1000 + 60 * i) for i in range(600)]}})
    body = client.get("/api/history", params={"tf": "M1"}).json()
    assert len(body["candles"]) == 500
    assert body["candles"][0]["t"] == 1000 + 60 * 100   # oldest 100 evicted


def test_history_unknown_tf_400(client):
    assert client.get("/api/history", params={"tf": "M7"}).status_code == 400


def test_history_requires_viewer_auth(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "false")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        assert c.get("/api/history", params={"tf": "M5"}).status_code == 403


def test_ws_snapshot_then_deltas(client):
    _push(client, {"tick": {"bid": 4000.1, "ask": 4000.4, "time": 111},
                   "candles": {"M5": [_candle(1000)]},
                   "positions": [{"ticket": 1, "direction": "SELL",
                                  "lots": 0.05, "entry": 4005.0, "sl": 4015.0,
                                  "tp": 0.0, "profit": 24.5, "magic": 990001}]})
    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["tick"]["bid"] == 4000.1
        assert snap["positions"][0]["ticket"] == 1
        assert snap["tfs"] == TFS
        _push(client, {"tick": {"bid": 4000.2, "ask": 4000.5, "time": 112}})
        msg = ws.receive_json()
        assert msg["type"] == "tick" and msg["tick"]["bid"] == 4000.2
        _push(client, {"candles": {"M5": [_candle(1300)]}})
        msg = ws.receive_json()
        assert msg["type"] == "candle" and msg["tf"] == "M5" \
            and msg["candle"]["t"] == 1300
        _push(client, {"positions": []})
        msg = ws.receive_json()
        assert msg["type"] == "positions" and msg["positions"] == []


def test_ws_requires_viewer_auth(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "false")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws"):
                pass


def test_push_never_500s_on_garbage(client):
    assert _push(client, {"candles": {"M5": [{"bad": 1}]}}).status_code == 200
    assert _push(client, {"tick": "nonsense"}).status_code == 200


def test_bad_t_in_empty_buffer_then_well_formed(client):
    """Critical fix: bad-t candle into empty buffer should be rejected,
    and later well-formed pushes should land without TypeError."""
    # Push a candle with non-numeric t into empty buffer
    resp = _push(client, {"candles": {"M5": [{"t": "oops", "o": 4000, "h": 4002, "l": 3999, "c": 4001, "v": 10}]}})
    assert resp.status_code == 200  # Never 500
    # Now push a well-formed candle
    resp = _push(client, {"candles": {"M5": [_candle(1000)]}})
    assert resp.status_code == 200
    # Verify the well-formed candle landed
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 1
    assert body["candles"][0]["t"] == 1000
    # Push another well-formed candle
    resp = _push(client, {"candles": {"M5": [_candle(1300)]}})
    assert resp.status_code == 200
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 2
    assert [c["t"] for c in body["candles"]] == [1000, 1300]


def test_type_garbage_candles_rejected(client):
    """Type validation: reject candles with non-numeric t/o/h/l/c
    (including booleans which are technically int subclass)."""
    # String t
    assert _push(client, {"candles": {"M5": [{"t": "oops", "o": 4000, "h": 4002, "l": 3999, "c": 4001, "v": 10}]}}).status_code == 200
    # Bool t (bool is int subclass in Python, must explicitly reject)
    assert _push(client, {"candles": {"M5": [{"t": True, "o": 4000, "h": 4002, "l": 3999, "c": 4001, "v": 10}]}}).status_code == 200
    # Non-numeric o
    assert _push(client, {"candles": {"M5": [{"t": 1000, "o": "bad", "h": 4002, "l": 3999, "c": 4001, "v": 10}]}}).status_code == 200
    # Non-numeric c
    assert _push(client, {"candles": {"M5": [{"t": 1000, "o": 4000, "h": 4002, "l": 3999, "c": None, "v": 10}]}}).status_code == 200
    # Verify nothing landed
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 0
