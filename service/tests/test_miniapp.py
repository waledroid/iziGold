"""Mini-app feed service: keyed push, ring buffers, history, WS deltas."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)  # keep its `settings` in sync -- see test_miniapp_auth.py
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


def test_push_rejects_when_no_key_configured(monkeypatch):
    """An unconfigured FEED_KEY must fail closed. This is the case
    hmac.compare_digest's constant-time comparison can't protect on its
    own -- compare_digest(b"", b"") is True, so an empty configured key
    plus an empty (or absent) header would otherwise "match"."""
    monkeypatch.setenv("FEED_KEY", "")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        assert c.post("/feed/push", json={"tick": None},
                       headers={"X-Feed-Key": ""}).status_code == 403
        assert c.post("/feed/push", json={"tick": None}).status_code == 403


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
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)  # keep its `settings` in sync -- see test_miniapp_auth.py
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
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)  # keep its `settings` in sync -- see test_miniapp_auth.py
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


def test_nan_candle_rejected_then_well_formed_land(client):
    """Important fix: NaN-o candle in empty buffer should be rejected,
    later well-formed pushes should land, /api/history stays 200."""
    # Push a candle with NaN-o into empty buffer using raw JSON (json.loads accepts NaN)
    resp = client.post("/feed/push",
                       content=json.dumps({"candles": {"M5": [{"t": 1000, "o": float("nan"), "h": 4002, "l": 3999, "c": 4001, "v": 10}]}}, allow_nan=True),
                       headers={"X-Feed-Key": "sekret", "Content-Type": "application/json"})
    assert resp.status_code == 200  # Never 500
    # Now push a well-formed candle
    resp = _push(client, {"candles": {"M5": [_candle(1000)]}})
    assert resp.status_code == 200
    # Verify the well-formed candle landed
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 1
    assert body["candles"][0]["t"] == 1000
    # Verify /api/history doesn't 500
    resp = client.get("/api/history", params={"tf": "M5"})
    assert resp.status_code == 200


def test_infinity_candle_rejected(client):
    """Infinity values (inf/-inf) should be rejected, not land in buffer."""
    # Test both positive and negative infinity using raw JSON
    resp = client.post("/feed/push",
                       content=json.dumps({"candles": {"M5": [{"t": 1000, "o": float("inf"), "h": 4002, "l": 3999, "c": 4001, "v": 10}]}}, allow_nan=True),
                       headers={"X-Feed-Key": "sekret", "Content-Type": "application/json"})
    assert resp.status_code == 200
    resp = client.post("/feed/push",
                       content=json.dumps({"candles": {"M5": [{"t": 1001, "o": 4000, "h": float("-inf"), "l": 3999, "c": 4001, "v": 10}]}}, allow_nan=True),
                       headers={"X-Feed-Key": "sekret", "Content-Type": "application/json"})
    assert resp.status_code == 200
    # Verify nothing landed
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 0


def test_page_route_returns_html_with_chart_div(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="chart"' in resp.text


def test_page_route_does_not_require_viewer_auth(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "false")
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)  # keep its `settings` in sync -- see test_miniapp_auth.py
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/history", params={"tf": "M5"}).status_code == 403


def test_vendor_lightweight_charts_served(client):
    resp = client.get("/static/vendor/lightweight-charts.standalone.production.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "LightweightCharts" in resp.text


def test_shared_static_dir_not_exposed(client):
    """The miniapp process is the one Phase 3 tunnels publicly — it must
    only ever serve static/vendor, never the rest of static/ (which holds
    main.py's dashboard.html/onboarding.html, i.e. trading controls)."""
    assert client.get("/static/dashboard.html").status_code == 404
    assert client.get("/static/onboarding.html").status_code == 404


def test_history_includes_indicator_arrays_aligned_to_candles(client):
    n = 60
    _push(client, {"candles": {"M5": [_candle(1000 + 300 * i, 4000.0 + i)
                                       for i in range(n)]}})
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == n
    for key in ("ema9", "ema21", "ema55", "ema200", "halftrend"):
        assert key in body
        assert len(body[key]) == n
    # ema9 (period 9) has settled by bar 60; ema55/200 (periods 55/200)
    # are still warming up (55 <= 60 so ema55 just settled, ema200 not).
    assert body["ema9"][-1] is not None
    assert body["ema55"][-1] is not None
    assert body["ema200"][-1] is None
    # halftrend entries are either null or {"v": ..., "trend": 0|1}.
    non_null_ht = [e for e in body["halftrend"] if e is not None]
    assert non_null_ht
    for e in non_null_ht:
        assert set(e) == {"v", "trend"}
        assert e["trend"] in (0, 1)


def test_history_short_buffer_has_warmup_nulls(client):
    _push(client, {"candles": {"M5": [_candle(1000, 4000.0), _candle(1300, 4001.0)]}})
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 2
    assert body["ema9"] == [None, None]
    assert body["ema200"] == [None, None]
    assert body["halftrend"] == [None, None]


def test_history_ema55_matches_indicators_module_directly(client):
    """Parity check: the served ema55 must equal app.indicators.ema computed
    directly over the same closes -- the server must not be re-deriving the
    math, just calling the shared module."""
    from app import indicators
    closes = [4000.0 + i * 0.7 for i in range(80)]
    rows = [_candle(1000 + 300 * i, closes[i]) for i in range(80)]
    _push(client, {"candles": {"M5": rows}})
    body = client.get("/api/history", params={"tf": "M5"}).json()
    expected = indicators.ema(closes, 55)
    assert body["ema55"] == expected
    assert body["ema55"][-1] == pytest.approx(expected[-1])


def test_history_empty_tf_arrays_empty_200(client):
    body = client.get("/api/history", params={"tf": "M15"}).json()
    assert body["candles"] == []
    for key in ("ema9", "ema21", "ema55", "ema200", "halftrend"):
        assert body[key] == []


def test_string_v_rejected(client):
    """Volume (v) must be numeric too — string-v should be rejected."""
    resp = _push(client, {"candles": {"M5": [{"t": 1000, "o": 4000, "h": 4002, "l": 3999, "c": 4001, "v": "NOT_A_NUMBER"}]}})
    assert resp.status_code == 200
    # Verify nothing landed
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 0
    # Push a well-formed candle to verify the buffer still works
    resp = _push(client, {"candles": {"M5": [_candle(1000)]}})
    assert resp.status_code == 200
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert len(body["candles"]) == 1
