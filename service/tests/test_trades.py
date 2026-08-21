import importlib
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.db import SignalDb
from app.telegram import TelegramClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tr.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "screenshots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _trade(**overrides):
    ev = {"event": "open", "strategy_id": "halftrend_ema_v1", "direction": "BUY",
          "lots": 0.1, "price": 2400.0, "sl": 2390.0, "reason": "signal confirmed",
          "ticket": 12345}
    ev.update(overrides)
    return ev


def test_trade_event_returns_id_and_persists_row(client):
    r = client.post("/trade-event", json=_trade())
    assert r.status_code == 200
    trade_id = r.json()["id"]
    assert isinstance(trade_id, int)

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT event, direction, lots, price, reason FROM trades WHERE id=?",
        (trade_id,)).fetchone()
    assert row == ("open", "BUY", 0.1, 2400.0, "signal confirmed")


def test_screenshot_upload_saves_file_and_records_path(client):
    from app import main

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200
    saved_path = r.json()["saved"]
    assert os.path.exists(saved_path)
    with open(saved_path, "rb") as f:
        assert f.read() == b"\x89PNG fake"

    row = main.app.state.db.conn.execute(
        "SELECT screenshot_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] == saved_path


def test_ui_trades_returns_recent_rows(client):
    client.post("/trade-event", json=_trade(event="open"))
    client.post("/trade-event", json=_trade(event="close"))
    body = client.get("/ui/trades").json()
    assert len(body["trades"]) == 2
    assert body["trades"][0]["event"] == "close"   # newest first


def test_ui_screenshot_serves_bytes(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    r = client.get(f"/ui/screenshot/{trade_id}")
    assert r.status_code == 200
    assert r.content == b"\x89PNG fake"


def test_ui_screenshot_404_when_missing(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.get(f"/ui/screenshot/{trade_id}")
    assert r.status_code == 404
    assert r.json()["detail"]


def test_screenshot_retention_keeps_newest_500(client, tmp_path):
    shot_dir = tmp_path / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    for i in range(502):
        f = shot_dir / f"fake_{i}.png"
        f.write_bytes(b"x")
        # oldest first: increasing mtimes so later files are "newer"
        os.utime(f, (now - 1000 + i, now - 1000 + i))

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200

    remaining = list(shot_dir.glob("*.png"))
    assert len(remaining) == 500
    # the newly uploaded screenshot (newest) must survive
    assert (shot_dir / f"{trade_id}.png") in remaining
    # oldest fakes should have been pruned
    assert not (shot_dir / "fake_0.png").exists()
    assert (shot_dir / "fake_501.png").exists()


# ---------------------------------------------------------------------------
# Trade P/L + render_path migration
# ---------------------------------------------------------------------------

def test_migration_adds_profit_and_render_path_columns_to_legacy_trades_table(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE trades (
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
      screenshot_path TEXT
    )""")
    conn.commit()
    conn.close()

    db = SignalDb(path)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(trades)")}
    assert "profit" in cols
    assert "render_path" in cols


def test_migration_adds_tp_and_final_columns_to_legacy_trades_table(tmp_path):
    path = str(tmp_path / "legacy2.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE trades (
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
      render_path TEXT
    )""")
    conn.execute(
        "INSERT INTO trades (ts, event, direction, lots, price, sl)"
        " VALUES (1, 'open', 'BUY', 0.1, 2400.0, 2390.0)")
    conn.commit()
    conn.close()

    db = SignalDb(path)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(trades)")}
    assert "tp" in cols
    assert "final" in cols
    # Pre-existing rows must default sanely (tp=0, final=1/True) so old
    # payloads keep behaving exactly as they did before this migration.
    row = db.conn.execute("SELECT tp, final FROM trades").fetchone()
    assert row == (0, 1)


def test_trade_event_profit_persists_and_returned_by_ui_trades(client):
    r = client.post("/trade-event", json=_trade(event="close", profit=42.5))
    trade_id = r.json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT profit FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] == 42.5

    body = client.get("/ui/trades").json()
    trade = next(t for t in body["trades"] if t["id"] == trade_id)
    assert trade["profit"] == 42.5


def test_trade_event_sl_persists_and_returned_by_ui_trades(client):
    r = client.post("/trade-event", json=_trade(event="open", sl=2385.5))
    trade_id = r.json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT sl FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] == 2385.5

    body = client.get("/ui/trades").json()
    trade = next(t for t in body["trades"] if t["id"] == trade_id)
    assert trade["sl"] == 2385.5


# ---------------------------------------------------------------------------
# _basket_legs — basket-boundary inference for enriched renders
# ---------------------------------------------------------------------------

def test_basket_legs_returns_open_and_add_rows_after_last_close(client):
    from app.main import _basket_legs

    id1 = client.post(
        "/trade-event", json=_trade(event="open", price=2400.0)).json()["id"]
    id2 = client.post(
        "/trade-event", json=_trade(event="add", price=2405.0)).json()["id"]
    id3 = client.post(
        "/trade-event", json=_trade(event="close", price=2415.0)).json()["id"]

    from app import main
    legs = _basket_legs(main.app.state.db, id3)
    assert legs == [
        {"price": 2400.0, "lots": 0.1, "event": "open", "sl": 2390.0, "tp": 0.0},
        {"price": 2405.0, "lots": 0.1, "event": "add", "sl": 2390.0, "tp": 0.0},
    ]
    assert id1 < id2 < id3  # sanity: ids really are in basket order


def test_basket_legs_excludes_prior_baskets(client):
    from app.main import _basket_legs
    from app import main

    client.post("/trade-event", json=_trade(event="open", price=2300.0))
    # distinct tickets per close: real deal tickets are unique, and the
    # idempotent close receiver collapses same-ticket re-deliveries
    id_close1 = client.post(
        "/trade-event",
        json=_trade(event="close", price=2310.0, ticket=101)).json()["id"]

    id_open2 = client.post(
        "/trade-event", json=_trade(event="open", price=2400.0)).json()["id"]
    id_close2 = client.post(
        "/trade-event",
        json=_trade(event="close", price=2415.0, ticket=102)).json()["id"]

    legs = _basket_legs(main.app.state.db, id_close2)
    assert legs == [
        {"price": 2400.0, "lots": 0.1, "event": "open", "sl": 2390.0, "tp": 0.0}]
    assert id_close1 < id_open2 < id_close2


def test_basket_legs_includes_just_inserted_open_row(client):
    from app.main import _basket_legs
    from app import main

    trade_id = client.post(
        "/trade-event", json=_trade(event="open", price=2400.0)).json()["id"]
    legs = _basket_legs(main.app.state.db, trade_id)
    assert legs == [
        {"price": 2400.0, "lots": 0.1, "event": "open", "sl": 2390.0, "tp": 0.0}]


# ---------------------------------------------------------------------------
# Screenshot photo alerts
# ---------------------------------------------------------------------------

class _FakeTransport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def test_screenshot_upload_sends_photo_alert_with_caption(client):
    from app import main
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    trade_id = client.post(
        "/trade-event",
        json=_trade(event="close", reason="tp hit", profit=10.0)).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200

    photo_calls = [c for c in ft.calls if c[0] == "sendPhoto"]
    assert len(photo_calls) == 1
    method, payload, files = photo_calls[0]
    assert "tp hit" in payload["caption"]
    assert files is not None


def test_screenshot_upload_no_telegram_configured_does_not_crash(client):
    from app import main
    assert main.app.state.telegram is None

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200


def test_screenshot_upload_swallows_photo_alert_failure(client):
    from app import main

    class _BoomTransport:
        def __call__(self, method, payload, files=None):
            raise RuntimeError("boom")

    main.app.state.telegram = TelegramClient("tok", "555", transport=_BoomTransport())

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# at-least-once delivery: idempotent close receiver + fast response
# ---------------------------------------------------------------------------

def _wait_calls(ft, method, n, timeout=3.0):
    """Poll for n calls of `method` on a fake transport (report work runs in
    a background task now, not on the response path)."""
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if len([c for c in ft.calls if c[0] == method]) >= n:
            break
        _t.sleep(0.05)
    return [c for c in ft.calls if c[0] == method]


def test_duplicate_close_same_ticket_is_idempotent(client):
    """The EA delivers closes at-least-once (it retries when it times out
    before seeing our response). A re-delivered close with the same nonzero
    deal ticket must return the ORIGINAL row id and not re-insert or
    re-report."""
    from app import main
    from app.telegram import TelegramClient

    class FT:
        def __init__(self):
            self.calls = []

        def __call__(self, method, payload, files=None):
            self.calls.append((method, payload, files))
            return {"ok": True, "result": {"message_id": 1}}

    ft = FT()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)
    ev = {"event": "close", "strategy_id": "halftrend_ema_v1",
          "direction": "SELL", "lots": 0.02, "price": 4378.55,
          "reason": "closed offline (reconciled)", "ticket": 1497330178,
          "profit": 68.06, "final": True}
    first = client.post("/trade-event", json=ev).json()["id"]
    msgs = _wait_calls(ft, "sendMessage", 1)
    assert len(msgs) == 1                      # reported exactly once
    second = client.post("/trade-event", json=ev).json()["id"]
    assert second == first                     # same id -> EA advances watermark
    rows = main.app.state.db.conn.execute(
        "SELECT COUNT(*) FROM trades WHERE ticket=1497330178").fetchone()[0]
    assert rows == 1                           # no duplicate row
    import time as _t
    _t.sleep(0.3)                              # grace: no second report either
    assert len([c for c in ft.calls if c[0] == "sendMessage"]) == 1


def test_trade_event_responds_before_slow_telegram(client):
    """A FINAL close's render+Telegram work can take seconds — far beyond
    the EA's 1 s WebRequest timeout. The response must not wait for it."""
    import time as _t

    from app import main
    from app.telegram import TelegramClient

    class SlowFT:
        def __init__(self):
            self.calls = []

        def __call__(self, method, payload, files=None):
            self.calls.append((method, payload, files))
            _t.sleep(1.5)
            return {"ok": True, "result": {"message_id": 1}}

    ft = SlowFT()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)
    ev = {"event": "close", "strategy_id": "halftrend_ema_v1",
          "direction": "SELL", "lots": 0.02, "price": 4378.55,
          "reason": "stop-loss", "ticket": 42, "profit": -10.0, "final": True}
    t0 = _t.time()
    r = client.post("/trade-event", json=ev)
    elapsed = _t.time() - t0
    assert r.status_code == 200 and r.json()["id"] > 0
    assert elapsed < 1.0, f"response took {elapsed:.2f}s — EA would time out"
    assert _wait_calls(ft, "sendMessage", 1, timeout=5.0)  # report still happens


def test_trade_event_carries_and_stores_the_m15_verdict(tmp_path):
    """The EA records what the higher-timeframe filter decided at ENTRY, so
    the report can show it per trade rather than reconstructing it."""
    from app.db import SignalDb
    db = SignalDb(str(tmp_path / "t.db"))
    agreed = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.1,
                              "price": 4500.0, "htf_agree": 1})
    refused = db.insert_trade({"event": "open", "direction": "SELL", "lots": 0.1,
                               "price": 4500.0, "htf_agree": 0})
    legacy = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.1,
                              "price": 4500.0})
    got = {r[0]: r[1] for r in
           db.conn.execute("SELECT id, htf_agree FROM trades")}
    assert got[agreed] == 1
    assert got[refused] == 0
    assert got[legacy] == -1, "an EA that does not send it must read as unknown"


def test_report_rows_carry_m15_and_session():
    """Both new report columns come off the same row the table renders."""
    import datetime as dt
    from app.miniapp import _htf_flag, market_session_short
    assert _htf_flag([{"ts": 1, "htf_agree": 1}]) is True
    assert _htf_flag([{"ts": 1, "htf_agree": 0}]) is False
    assert _htf_flag([{"ts": 1, "htf_agree": -1}]) is None, "unknown is not False"
    assert _htf_flag([]) is None
    # the first leg decides -- a later add must not overwrite the entry verdict
    assert _htf_flag([{"ts": 2, "htf_agree": 0}, {"ts": 1, "htf_agree": 1}]) is True
    # session labels are short enough for a table column
    for h in (2, 8, 12, 16, 20, 22):
        lab = market_session_short(dt.datetime(2026, 8, 20, h, 0, tzinfo=dt.UTC))
        assert lab and lab != "—" and len(lab) <= 12


def test_basket_grouping_preserves_the_m15_verdict():
    """Regression: _group_baskets rebuilt each leg with only ts/price/lots,
    so htf_agree was dropped between the DB and the report and EVERY row
    rendered a dash even with the column fully populated."""
    from app.miniapp import _group_baskets, _htf_flag
    rows = [
        {"id": 1, "ts": 100, "event": "open", "direction": "BUY", "lots": 0.1,
         "price": 4500.0, "profit": 0.0, "final": 1, "entry_mode": "adr",
         "reason": "signal BUY", "strategy_id": "x", "htf_agree": 1},
        {"id": 2, "ts": 200, "event": "add", "direction": "BUY", "lots": 0.05,
         "price": 4510.0, "profit": 0.0, "final": 1, "entry_mode": "adr",
         "reason": "pyramid add", "strategy_id": "x", "htf_agree": -1},
        {"id": 3, "ts": 300, "event": "close", "direction": "BUY", "lots": 0.15,
         "price": 4520.0, "profit": 30.0, "final": 1, "entry_mode": "adr",
         "reason": "profit target", "strategy_id": "x", "htf_agree": -1},
    ]
    baskets = _group_baskets(rows, cap=None)
    assert baskets, "the fixture must group into one basket"
    assert _htf_flag(baskets[0]["entries"]) is True


def test_entry_caption_reports_the_m15_verdict():
    """The verdict is evaluated on every entry and reported even when the
    tape was trending and it was not allowed to block."""
    from app.main import _trade_caption
    agree = _trade_caption("open", "BUY", 0.1, 4500.0, "signal BUY", 0.0, 1)
    refuse = _trade_caption("open", "SELL", 0.1, 4500.0, "signal SELL", 0.0, 0)
    unknown = _trade_caption("open", "BUY", 0.1, 4500.0, "signal BUY", 0.0, -1)
    closed = _trade_caption("close", "BUY", 0.1, 4500.0, "stop-loss", -20.0, 1)
    assert "M15: agrees" in agree
    assert "M15: DISAGREES" in refuse
    assert "M15" not in unknown, "an unknown verdict must not be asserted"
    assert "M15" not in closed, "the verdict belongs to the entry, not the close"
