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


# ---- /api/trades: past-trade markers -------------------------------------

# Copied verbatim from app.db._TRADES_SCHEMA -- miniapp_auth.py's docstring
# explains why this module opens the SAME db read-only rather than
# importing app.db.SignalDb (that class's __init__ needs a writable
# connection to run CREATE TABLE IF NOT EXISTS).
_TRADES_SCHEMA = """CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  event TEXT NOT NULL,
  strategy_id TEXT,
  direction TEXT,
  lots REAL,
  price REAL,
  sl REAL,
  reason TEXT,
  ticket INTEGER,
  screenshot_path TEXT,
  profit REAL DEFAULT 0,
  render_path TEXT,
  tp REAL DEFAULT 0,
  final INTEGER DEFAULT 1
)"""


def _make_trades_db(path, rows):
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute(_TRADES_SCHEMA)
    for r in rows:
        conn.execute(
            "INSERT INTO trades (ts, event, direction, lots, price, profit, final) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["ts"], r["event"], r.get("direction"), r.get("lots"), r.get("price"),
             r.get("profit", 0), r.get("final", 1)))
    conn.commit()
    conn.close()


@pytest.fixture()
def trades_client(tmp_path, monkeypatch):
    """Returns a builder: pass trade rows, get back a TestClient (dev
    bypass on) wired to a fresh sqlite db populated with those rows via
    the exact trades schema app.db uses."""
    def _build(rows, dev_bypass=True):
        db_path = tmp_path / "trades.db"
        _make_trades_db(db_path, rows)
        monkeypatch.setenv("FEED_KEY", "sekret")
        monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true" if dev_bypass else "false")
        monkeypatch.setenv("DB_PATH", str(db_path))
        from app import config, miniapp, miniapp_auth
        importlib.reload(config)
        importlib.reload(miniapp_auth)
        importlib.reload(miniapp)
        return TestClient(miniapp.app)
    return _build


def test_trades_requires_viewer_auth(trades_client):
    c = trades_client([], dev_bypass=False)
    assert c.get("/api/trades").status_code == 403


def test_trades_missing_db_returns_empty_200(monkeypatch, tmp_path):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "does_not_exist.db"))
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        resp = c.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == {"trades": [], "baskets": []}


def test_trades_broken_db_returns_empty_200(monkeypatch, tmp_path):
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite file at all")
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    monkeypatch.setenv("DB_PATH", str(db_path))
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        resp = c.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == {"trades": [], "baskets": []}


def test_trades_groups_baskets(trades_client):
    rows = [
        {"ts": 100, "event": "open", "direction": "BUY", "lots": 0.05, "price": 4000.0},
        {"ts": 200, "event": "add", "direction": "BUY", "lots": 0.05, "price": 4005.0},
        # non-final close: a leg stops out but the basket survives
        {"ts": 250, "event": "close", "direction": "BUY", "lots": 0.05,
         "price": 4002.0, "profit": -5.0, "final": 0},
        # final close ends the basket
        {"ts": 300, "event": "close", "direction": "BUY", "lots": 0.1,
         "price": 4010.0, "profit": 50.0, "final": 1},
        # a following open starts a second, still-open basket
        {"ts": 400, "event": "open", "direction": "SELL", "lots": 0.05, "price": 4020.0},
    ]
    c = trades_client(rows)
    resp = c.get("/api/trades")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["trades"]) == 5
    baskets = body["baskets"]
    assert len(baskets) == 2

    first = baskets[0]
    assert first["direction"] == "BUY"
    assert len(first["entries"]) == 2
    # legs also carry the M15/EMA200 verdicts (None when the source row has
    # no htf_agree/ema200_agree) so the report can render them -- dropping
    # a field here was the bug that made every M15 cell a dash
    assert first["entries"][0] == {"ts": 100, "price": 4000.0, "lots": 0.05,
                                   "htf_agree": None, "ema200_agree": None}
    assert first["entries"][1] == {"ts": 200, "price": 4005.0, "lots": 0.05,
                                   "htf_agree": None, "ema200_agree": None}
    assert first["exit"] == {"ts": 300, "price": 4010.0, "profit": 50.0}

    second = baskets[1]
    assert second["direction"] == "SELL"
    assert len(second["entries"]) == 1
    assert second["exit"] is None


def test_trades_respects_limit(trades_client):
    rows = [{"ts": 100 + i, "event": "open" if i % 2 == 0 else "close",
             "direction": "BUY", "lots": 0.01, "price": 4000.0 + i,
             "profit": 1.0, "final": 1}
            for i in range(10)]
    c = trades_client(rows)
    resp = c.get("/api/trades", params={"limit": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["trades"]) == 4
    # Ordered ascending by id (oldest of the last 4 first)
    assert [t["ts"] for t in body["trades"]] == [106, 107, 108, 109]


def test_trades_limit_is_capped_server_side(client, monkeypatch, tmp_path):
    """An authenticated viewer must not be able to force an unbounded scan
    via ?limit= — the server clamps to TRADES_MAX_LIMIT."""
    from app import miniapp
    calls = {}

    class _Cur:
        def execute(self, sql, params):
            calls["params"] = params
            return self

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

        def execute(self, sql, params=()):
            calls["params"] = params
            return _Cur()

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(miniapp.sqlite3, "connect", lambda *a, **k: _Conn())
    r = client.get("/api/trades", params={"limit": 999999})
    assert r.status_code == 200
    assert calls["params"] == (miniapp.TRADES_MAX_LIMIT,)


def test_push_response_reports_shallowest_buffer_depth(client):
    """The bridge re-backfills when `depth` is shallow — a service that
    restarted between two pushes must not stay at 1-2 candles forever."""
    r = _push(client, {"tick": None})
    assert r.json()["depth"] == 0                     # fresh service: empty
    _push(client, {"candles": {"M5": [_candle(1000 + 300 * i) for i in range(50)]}})
    r = _push(client, {"tick": None})
    assert r.json()["depth"] == 0                     # M1 etc still empty -> min is 0
    body = {"candles": {tf: [_candle(1000 + 60 * i) for i in range(20)] for tf in TFS}}
    r = _push(client, body)
    assert r.json()["depth"] == 20                    # every TF has 20 -> min 20


# ---- Trades report (/api/report) ------------------------------------------

_REPORT_SCHEMAS = [
    _TRADES_SCHEMA.replace("final INTEGER DEFAULT 1", "final INTEGER DEFAULT 1,\n  entry_mode TEXT DEFAULT ''"),
    """CREATE TABLE IF NOT EXISTS heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  equity REAL, balance REAL, floating_pl REAL, open_count INTEGER,
  kill_switch INTEGER, exposure_min INTEGER, active_strategy TEXT)""",
    """CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts INTEGER NOT NULL,
  bar_time INTEGER NOT NULL, symbol TEXT NOT NULL, signal TEXT NOT NULL,
  price REAL NOT NULL, direction TEXT, confidence REAL, regime TEXT, verdict TEXT,
  mode TEXT, ai_available INTEGER, outcome_price REAL, outcome_move REAL,
  ai_correct INTEGER, strategy_id TEXT, is_active INTEGER DEFAULT 1, timeframe TEXT)""",
]

OFF = 3 * 3600   # SERVER_UTC_OFFSET_H


def _utc(y, m, d, hh=0, mm=0):
    import calendar
    return calendar.timegm((y, m, d, hh, mm, 0))


def _make_report_db(path, trades=(), heartbeats=(), signals=()):
    import sqlite3
    conn = sqlite3.connect(str(path))
    for ddl in _REPORT_SCHEMAS:
        conn.execute(ddl)
    for r in trades:
        conn.execute(
            "INSERT INTO trades (ts, event, direction, lots, price, profit, final, reason, entry_mode)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (r["ts"], r["event"], r.get("direction"), r.get("lots", 0.01), r.get("price", 0.0),
             r.get("profit", 0), r.get("final", 1), r.get("reason", ""), r.get("entry_mode", "")))
    for h in heartbeats:
        conn.execute("INSERT INTO heartbeats (ts, balance, equity) VALUES (?,?,?)",
                     (h["ts"], h["balance"], h["balance"]))
    for s in signals:
        conn.execute(
            "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
            " confidence, regime, verdict, mode, ai_available, is_active)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["bar_time"] - OFF, s["bar_time"], "XAUUSD", s["signal"], 4000.0,
             s.get("direction"), s.get("confidence", 0.7), s.get("regime"), s.get("verdict", "neutral"),
             "grading", s.get("ai_available", 1), s.get("is_active", 1)))
    conn.commit()
    conn.close()


@pytest.fixture()
def report_client(tmp_path, monkeypatch):
    def _build(trades=(), heartbeats=(), signals=(), dev_bypass=True):
        db_path = tmp_path / "report.db"
        _make_report_db(db_path, trades, heartbeats, signals)
        monkeypatch.setenv("FEED_KEY", "sekret")
        monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true" if dev_bypass else "false")
        monkeypatch.setenv("DB_PATH", str(db_path))
        from app import config, miniapp, miniapp_auth
        importlib.reload(config)
        importlib.reload(miniapp_auth)
        importlib.reload(miniapp)
        return TestClient(miniapp.app)
    return _build


def _seed():
    """Two broker days in July 2026 (server = UTC+3):
    - Jul 6: BUY basket (open+add, 2 close legs: -5 non-final, +50 final) at
      07:00 server; SELL basket 12:00 server, -20.
    - Jul 7: SELL basket that opened late Jul 6 UTC (23:30 UTC = 02:30 Jul 7
      server) and closed 03:00 server -> lands on Jul 7 by SERVER day.
    Heartbeats bracket each close; signals give regime + AI direction."""
    t_open1 = _utc(2026, 7, 6, 4, 0)      # 07:00 server
    t_close1 = _utc(2026, 7, 6, 4, 30)
    t_open2 = _utc(2026, 7, 6, 9, 0)      # 12:00 server
    t_close2 = _utc(2026, 7, 6, 9, 20)
    t_open3 = _utc(2026, 7, 6, 23, 30)    # 02:30 server Jul 7
    t_close3 = _utc(2026, 7, 7, 0, 0)     # 03:00 server Jul 7
    trades = [
        {"ts": t_open1, "event": "open", "direction": "BUY", "lots": 0.05, "price": 4000.0,
         "reason": "signal BUY", "entry_mode": "fixed"},
        {"ts": t_open1 + 300, "event": "add", "direction": "BUY", "lots": 0.05, "price": 4004.0,
         "reason": "pyramid add"},
        {"ts": t_close1, "event": "close", "direction": "BUY", "lots": 0.05, "price": 4010.0,
         "profit": -5.0, "final": 0, "reason": "stop-loss"},
        {"ts": t_close1, "event": "close", "direction": "BUY", "lots": 0.05, "price": 4010.0,
         "profit": 50.0, "final": 1, "reason": "profit target"},
        {"ts": t_open2, "event": "open", "direction": "SELL", "lots": 0.05, "price": 4020.0,
         "reason": "signal SELL"},
        {"ts": t_close2, "event": "close", "direction": "SELL", "lots": 0.05, "price": 4025.0,
         "profit": -20.0, "final": 1, "reason": "stop-loss"},
        {"ts": t_open3, "event": "open", "direction": "SELL", "lots": 0.05, "price": 4030.0,
         "reason": "signal SELL"},
        {"ts": t_close3, "event": "close", "direction": "SELL", "lots": 0.05, "price": 4020.0,
         "profit": 30.0, "final": 1, "reason": "profit lock"},
    ]
    heartbeats = [
        {"ts": t_open1 - 60, "balance": 1000.0},
        {"ts": t_close1 + 30, "balance": 1045.0},   # first hb after close 1 (1000-5+50)
        {"ts": t_close1 + 90, "balance": 1045.0},
        {"ts": t_close2 - 60, "balance": 1045.0},   # before close 2, no hb after within 10 min
        {"ts": t_close3 + 5, "balance": 1055.0},
    ]
    # bar_time is SERVER time; bar open 5 min before the trade (M5 close)
    signals = [
        {"bar_time": t_open1 + OFF - 300, "signal": "BUY", "direction": "bullish",
         "regime": "trend"},
        {"bar_time": t_open2 + OFF - 300, "signal": "SELL", "direction": "bullish",
         "regime": "range"},                                        # AI disagrees
        {"bar_time": t_open3 + OFF - 300, "signal": "SELL", "direction": "neutral",
         "regime": "high_volatility"},                              # AI neutral -> null
        {"bar_time": t_open3 + OFF - 300, "signal": "SELL", "direction": "bearish",
         "regime": "trend", "is_active": 0},                        # shadow row: ignored
    ]
    return trades, heartbeats, signals


def test_report_requires_viewer_auth(report_client):
    c = report_client(dev_bypass=False)
    assert c.get("/api/report", params={"view": "month", "month": "2026-07"}).status_code == 403
    assert c.get("/api/report", params={"view": "day", "date": "2026-07-06"}).status_code == 403


def test_report_bad_params_400(report_client):
    c = report_client()
    assert c.get("/api/report", params={"view": "week"}).status_code == 400
    assert c.get("/api/report", params={"view": "month", "month": "07-2026"}).status_code == 400
    assert c.get("/api/report", params={"view": "day", "date": "yesterday"}).status_code == 400


def test_report_month_aggregates_by_server_day(report_client):
    trades, hbs, sigs = _seed()
    c = report_client(trades, hbs, sigs)
    r = c.get("/api/report", params={"view": "month", "month": "2026-07"})
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == "month" and body["month"] == "2026-07"
    assert body["server_utc_offset_h"] == 3
    days = body["days"]
    assert [d["date"] for d in days] == ["2026-07-06", "2026-07-07"]
    d6, d7 = days
    assert d6["label"] == "Jul 6"
    assert d6["trades"] == 2 and d6["wins"] == 1 and d6["losses"] == 1
    assert d6["pl"] == pytest.approx(25.0)          # (50 - 5) + (-20)
    assert d6["regimes"] == {"trend": 1, "range": 1}
    assert d6["balance_end"] == pytest.approx(1025.0)   # hb_before(1045) + pl(-20)
    assert d7["trades"] == 1 and d7["pl"] == pytest.approx(30.0)
    assert d7["regimes"] == {"high_volatility": 1}
    assert d7["balance_end"] == pytest.approx(1055.0)
    f = body["footer"]
    assert f["pl"] == pytest.approx(55.0)
    assert f["trades"] == 3 and f["wins"] == 2
    assert f["win_pct"] == pytest.approx(66.7)
    assert f["best_day"]["date"] == "2026-07-07"
    assert f["worst_day"]["date"] == "2026-07-06"
    rw = body["regime_winrates"]
    assert rw["trend"] == {"trades": 1, "wins": 1, "win_pct": 100.0}
    assert rw["range"] == {"trades": 1, "wins": 0, "win_pct": 0.0}
    assert rw["high_volatility"]["win_pct"] == 100.0
    assert body["equity"] == [pytest.approx(1025.0), pytest.approx(1055.0)]


def test_report_day_rows_balance_regime_ai(report_client):
    trades, hbs, sigs = _seed()
    c = report_client(trades, hbs, sigs)
    body = c.get("/api/report", params={"view": "day", "date": "2026-07-06"}).json()
    assert body["view"] == "day" and body["date"] == "2026-07-06"
    rows = body["rows"]
    assert len(rows) == 2
    a, b = rows
    assert a["time"] == "07:30" and a["direction"] == "BUY" and a["mode"] == "fixed"
    assert a["entries"] == 2 and a["entry"] == pytest.approx(4002.0)   # lot-weighted
    assert a["exit"] == 4010.0 and a["reason"] == "profit target"
    assert a["pl"] == pytest.approx(45.0)                # both close legs summed
    assert a["balance_after"] == pytest.approx(1045.0) and a["balance_src"] == "hb_after"
    assert a["regime"] == "trend" and a["ai"] == "agree" and a["ai_direction"] == "bullish"
    assert b["time"] == "12:20" and b["direction"] == "SELL" and b["mode"] == "adr"
    assert b["pl"] == pytest.approx(-20.0)
    assert b["balance_after"] == pytest.approx(1025.0) and b["balance_src"] == "hb_before+pl"
    assert b["regime"] == "range" and b["ai"] == "disagree"
    assert body["footer"] == {"pl": 25.0, "trades": 2, "wins": 1, "losses": 1}

    body7 = c.get("/api/report", params={"view": "day", "date": "2026-07-07"}).json()
    assert len(body7["rows"]) == 1
    r7 = body7["rows"][0]
    assert r7["time"] == "03:00" and r7["regime"] == "high_volatility"
    assert r7["ai"] is None            # neutral AI -> no agree/disagree
    assert r7["balance_after"] == pytest.approx(1055.0)


def test_report_empty_month_and_day_200(report_client):
    trades, hbs, sigs = _seed()
    c = report_client(trades, hbs, sigs)
    body = c.get("/api/report", params={"view": "month", "month": "2025-01"}).json()
    assert body["days"] == [] and body["footer"]["trades"] == 0
    assert body["footer"]["best_day"] is None and body["regime_winrates"] == {}
    body = c.get("/api/report", params={"view": "day", "date": "2026-07-08"}).json()
    assert body["rows"] == [] and body["footer"]["trades"] == 0


def test_report_missing_db_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nope.db"))
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        r = c.get("/api/report", params={"view": "month", "month": "2026-07"})
        assert r.status_code == 200 and r.json()["days"] == []
        r = c.get("/api/report")            # defaults: current month
        assert r.status_code == 200 and r.json()["view"] == "month"


def test_page_contains_trades_tab(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="tabTrades"' in html and "Trades" in html
    assert 'id="tradesPanel"' in html and "/api/report" in html
    assert 'id="repCsv"' in html and 'id="repCopy"' in html


def test_report_balance_fallback_carries_cumulative_pl_across_a_heartbeat_gap(report_client):
    """Two baskets closing inside ONE heartbeat gap: the second must show
    hb + pl1 + pl2, not hb + pl2 (per-basket independent fallback bug)."""
    t0 = _utc(2026, 7, 8, 6, 0)
    trades = [
        {"ts": t0, "event": "open", "direction": "BUY", "price": 4000.0},
        {"ts": t0 + 600, "event": "close", "direction": "BUY", "price": 4010.0,
         "profit": 40.0, "final": 1, "reason": "profit target"},
        {"ts": t0 + 1200, "event": "open", "direction": "SELL", "price": 4010.0},
        {"ts": t0 + 1800, "event": "close", "direction": "SELL", "price": 4015.0,
         "profit": -15.0, "final": 1, "reason": "stop-loss"},
        # third basket: a real heartbeat lands after it -> carry resets
        {"ts": t0 + 2400, "event": "open", "direction": "BUY", "price": 4015.0},
        {"ts": t0 + 3000, "event": "close", "direction": "BUY", "price": 4020.0,
         "profit": 10.0, "final": 1, "reason": "profit lock"},
    ]
    heartbeats = [{"ts": t0 - 60, "balance": 1000.0},          # stale anchor
                  {"ts": t0 + 3005, "balance": 1035.0}]        # after basket 3
    c = report_client(trades, heartbeats, [])
    rows = c.get("/api/report", params={"view": "day", "date": "2026-07-08"}).json()["rows"]
    assert [r["balance_src"] for r in rows] == ["hb_before+pl", "hb_before+pl", "hb_after"]
    assert rows[0]["balance_after"] == pytest.approx(1040.0)   # 1000 + 40
    assert rows[1]["balance_after"] == pytest.approx(1025.0)   # 1000 + 40 - 15
    assert rows[2]["balance_after"] == pytest.approx(1035.0)   # real heartbeat
