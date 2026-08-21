import importlib

import pytest
from fastapi.testclient import TestClient

from app.render import _favorable, render_trade_chart
from tests.fixtures import trend_candles


def _trade(**overrides):
    ev = {"event": "open", "strategy_id": "halftrend_ema_v1", "direction": "BUY",
          "lots": 0.1, "price": 2400.0, "sl": 2390.0, "reason": "signal confirmed"}
    ev.update(overrides)
    return ev


def _drain(timeout=6.0):
    """Wait for the background trade-event report tasks (render + Telegram
    now run OFF the response path so the EA's 1 s timeout can't fire)."""
    import time as _t
    from app import main
    deadline = _t.time() + timeout
    while _t.time() < deadline and getattr(main.app.state, "report_tasks", None):
        _t.sleep(0.05)



# ---------------------------------------------------------------------------
# render_trade_chart unit tests
# ---------------------------------------------------------------------------

def test_render_trade_chart_writes_nonempty_png(tmp_path):
    out_path = str(tmp_path / "render_1.png")
    ok = render_trade_chart(trend_candles(200), _trade(), out_path)
    assert ok is True

    import os
    assert os.path.exists(out_path)
    with open(out_path, "rb") as f:
        data = f.read()
    assert len(data) > 0
    assert data.startswith(b"\x89PNG")


def test_render_trade_chart_close_event(tmp_path):
    out_path = str(tmp_path / "render_close.png")
    ok = render_trade_chart(
        trend_candles(200), _trade(event="close", reason="tp hit"), out_path)
    assert ok is True

    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_trade_chart_returns_false_on_empty_candles(tmp_path):
    out_path = str(tmp_path / "render_empty.png")
    ok = render_trade_chart([], _trade(), out_path)
    assert ok is False


def test_render_trade_chart_returns_false_on_bad_out_path(tmp_path):
    # a directory that does not exist -> matplotlib savefig raises -> False,
    # no exception propagates
    bad_path = str(tmp_path / "does" / "not" / "exist" / "render.png")
    ok = render_trade_chart(trend_candles(200), _trade(), bad_path)
    assert ok is False


def test_render_trade_chart_with_overlays_and_sl_writes_nonempty_png(tmp_path):
    # 200 candles (full EA payload size) + a trade with sl>0 exercises the
    # HalfTrend/EMA overlay computation over the full series plus the
    # SL line/label path.
    out_path = str(tmp_path / "render_overlays.png")
    ok = render_trade_chart(trend_candles(200), _trade(sl=2390.0), out_path)
    assert ok is True

    import os
    assert os.path.exists(out_path)
    with open(out_path, "rb") as f:
        data = f.read()
    assert len(data) > 0
    assert data.startswith(b"\x89PNG")


def test_render_trade_chart_no_sl_still_renders(tmp_path):
    out_path = str(tmp_path / "render_no_sl.png")
    ok = render_trade_chart(trend_candles(200), _trade(sl=0.0), out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_trade_chart_short_candle_series_does_not_raise(tmp_path):
    # Fewer candles than the HalfTrend warm-up window (amplitude=4) --
    # indicators should degrade to all-None overlays, not crash the render.
    out_path = str(tmp_path / "render_short.png")
    ok = render_trade_chart(trend_candles(3), _trade(), out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# _favorable — pure helper, no plotting
# ---------------------------------------------------------------------------

def test_favorable_buy_true_when_exit_above_entry():
    assert _favorable("BUY", 2400.0, 2450.0) is True


def test_favorable_buy_false_when_exit_at_or_below_entry():
    assert _favorable("BUY", 2400.0, 2400.0) is False
    assert _favorable("BUY", 2400.0, 2350.0) is False


def test_favorable_sell_true_when_exit_below_entry():
    assert _favorable("SELL", 2400.0, 2350.0) is True


def test_favorable_sell_false_when_exit_at_or_above_entry():
    assert _favorable("SELL", 2400.0, 2400.0) is False
    assert _favorable("SELL", 2400.0, 2450.0) is False


def test_favorable_unknown_direction_is_false():
    assert _favorable("", 2400.0, 2450.0) is False


# ---------------------------------------------------------------------------
# Enriched renders: legs, tp, risk/profit boxes
# ---------------------------------------------------------------------------

def _legs(*entries):
    return [{"price": p, "lots": lots, "event": ev} for p, lots, ev in entries]


def test_render_close_with_legs_tp_sl_writes_nonempty_png(tmp_path):
    out_path = str(tmp_path / "render_close_legs.png")
    trade = _trade(
        event="close", direction="BUY", price=2415.0, sl=2390.0,
        tp=2420.0, reason="tp hit",
        legs=_legs((2400.0, 0.1, "open"), (2405.0, 0.1, "add")))
    ok = render_trade_chart(trend_candles(200), trade, out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_open_with_tp_does_not_raise(tmp_path):
    out_path = str(tmp_path / "render_open_tp.png")
    trade = _trade(event="open", tp=2420.0,
                   legs=_legs((2400.0, 0.1, "open")))
    ok = render_trade_chart(trend_candles(200), trade, out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_add_event_renders(tmp_path):
    out_path = str(tmp_path / "render_add.png")
    trade = _trade(event="add", price=2405.0,
                   legs=_legs((2400.0, 0.1, "open"), (2405.0, 0.1, "add")))
    ok = render_trade_chart(trend_candles(200), trade, out_path)
    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read().startswith(b"\x89PNG")


def test_render_close_favorable_returns_true(tmp_path):
    out_path = str(tmp_path / "render_favorable.png")
    trade = _trade(event="close", direction="BUY", price=2450.0, sl=2390.0,
                   legs=_legs((2400.0, 0.1, "open")))
    ok = render_trade_chart(trend_candles(200), trade, out_path)
    assert ok is True


def test_render_close_unfavorable_returns_true(tmp_path):
    out_path = str(tmp_path / "render_unfavorable.png")
    trade = _trade(event="close", direction="BUY", price=2350.0, sl=2390.0,
                   legs=_legs((2400.0, 0.1, "open")))
    ok = render_trade_chart(trend_candles(200), trade, out_path)
    assert ok is True


def test_render_close_without_legs_still_renders(tmp_path):
    # Old-shape trade dict (no "legs"/"tp" keys) must still render exactly
    # as before -- backward compatibility for callers/tests that predate
    # this feature.
    out_path = str(tmp_path / "render_no_legs.png")
    ok = render_trade_chart(trend_candles(200), _trade(event="close"), out_path)
    assert ok is True


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "render.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "screenshots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _analyze_payload(signal="BUY"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "candles": [c.model_dump() for c in trend_candles(200)]}


def test_trade_event_close_renders_chart_after_analyze(client):
    r = client.post("/analyze", json=_analyze_payload())
    assert r.status_code == 200

    trade_id = client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit")).json()["id"]
    _drain()

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is not None

    import os
    assert os.path.exists(row[0])
    with open(row[0], "rb") as f:
        assert f.read().startswith(b"\x89PNG")

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_trade_event_open_renders_chart_after_analyze(client):
    client.post("/analyze", json=_analyze_payload())

    trade_id = client.post("/trade-event", json=_trade(event="open")).json()["id"]
    _drain()

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is not None

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 200


def test_trade_event_without_prior_candles_still_200_no_render(client):
    trade_id = client.post(
        "/trade-event", json=_trade(event="close")).json()["id"]

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is None

    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 404


def test_trade_event_render_prunes_to_retention_cap(client, tmp_path):
    import os
    import time

    shot_dir = tmp_path / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    for i in range(502):
        f = shot_dir / f"fake_{i}.png"
        f.write_bytes(b"x")
        os.utime(f, (now - 1000 + i, now - 1000 + i))

    client.post("/analyze", json=_analyze_payload())
    trade_id = client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit")).json()["id"]
    _drain()

    remaining = list(shot_dir.glob("*.png"))
    assert len(remaining) == 500
    # the newly rendered chart (newest) must survive
    assert (shot_dir / f"render_{trade_id}.png") in remaining
    assert not (shot_dir / "fake_0.png").exists()
    assert (shot_dir / "fake_501.png").exists()


def test_ui_render_404_when_missing(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.get(f"/ui/render/{trade_id}")
    assert r.status_code == 404
    assert r.json()["detail"]


# ---------------------------------------------------------------------------
# Telegram send_photo of render
# ---------------------------------------------------------------------------

class _FakeTransport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def test_trade_event_close_sends_no_photo_but_still_renders_to_disk(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    trade_id = client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit")).json()["id"]
    _drain()

    # Owner request 2026-08-17: no more "render:" photos to Telegram — the
    # PNG is still written for the dashboard, but only the P/L text goes out.
    assert [c for c in ft.calls if c[0] == "sendPhoto"] == []
    row = main.app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] is not None


def test_trade_event_close_sends_profit_message(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post(
        "/trade-event", json=_trade(event="close", reason="tp hit", profit=102.82))
    _drain()

    msg_calls = [c for c in ft.calls if c[0] == "sendMessage"]
    assert len(msg_calls) == 1
    assert msg_calls[0][1]["text"] == "💰 Trade closed: +$102.82 profit"


def test_trade_event_close_sends_loss_message(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post(
        "/trade-event", json=_trade(event="close", reason="sl hit", profit=-21.40))
    _drain()

    msg_calls = [c for c in ft.calls if c[0] == "sendMessage"]
    assert len(msg_calls) == 1
    assert msg_calls[0][1]["text"] == "🔻 Trade closed: -$21.40 loss"


def test_trade_event_close_sends_breakeven_message(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="close", profit=0.0))
    _drain()

    msg_calls = [c for c in ft.calls if c[0] == "sendMessage"]
    assert len(msg_calls) == 1
    assert msg_calls[0][1]["text"] == "⚖️ Trade closed: breakeven"


def test_trade_event_open_sends_no_profit_message(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="open"))

    assert [c for c in ft.calls if c[0] == "sendMessage"] == []


def test_trade_event_close_profit_message_swallows_failure(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())

    class _BoomTransport:
        def __call__(self, method, payload, files=None):
            raise RuntimeError("boom")

    main.app.state.telegram = TelegramClient("tok", "555", transport=_BoomTransport())

    r = client.post("/trade-event", json=_trade(event="close", profit=5.0))
    assert r.status_code == 200


def test_trade_event_swallows_telegram_render_failure(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())

    class _BoomTransport:
        def __call__(self, method, payload, files=None):
            raise RuntimeError("boom")

    main.app.state.telegram = TelegramClient("tok", "555", transport=_BoomTransport())

    r = client.post("/trade-event", json=_trade(event="close"))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Finding 1: real close events (sl=0/tp=0) inherit sl/tp from stored legs
# ---------------------------------------------------------------------------

def test_close_render_inherits_open_legs_sl_and_tp_when_own_are_zero(client, monkeypatch):
    from app import main

    client.post("/analyze", json=_analyze_payload())
    captured = {}

    def _fake_render(candles, trade, out_path):
        captured["trade"] = trade
        return True

    from app import trade_report
    monkeypatch.setattr(trade_report, "render_trade_chart", _fake_render)

    client.post(
        "/trade-event",
        json=_trade(event="open", price=2400.0, sl=2390.0, tp=2420.0))
    # A real broker-side SL/TP close carries sl=0/tp=0 -- the EA has no
    # per-position snapshot to resend at close time.
    client.post(
        "/trade-event",
        json=_trade(event="close", price=2415.0, sl=0.0, tp=0.0,
                    reason="stop-loss"))

    trade = captured["trade"]
    assert trade["sl"] == 2390.0   # inherited from the basket's first leg
    assert trade["tp"] == 2420.0   # inherited from the basket's latest tp


def test_close_render_keeps_own_sl_tp_when_nonzero(client, monkeypatch):
    from app import main

    client.post("/analyze", json=_analyze_payload())
    captured = {}

    def _fake_render(candles, trade, out_path):
        captured["trade"] = trade
        return True

    from app import trade_report
    monkeypatch.setattr(trade_report, "render_trade_chart", _fake_render)

    client.post(
        "/trade-event",
        json=_trade(event="open", price=2400.0, sl=2390.0, tp=2420.0))
    _drain()
    client.post(
        "/trade-event",
        json=_trade(event="close", price=2415.0, sl=2391.0, tp=2421.0,
                    reason="strategy EXIT"))
    _drain()

    trade = captured["trade"]
    assert trade["sl"] == 2391.0   # the event's own nonzero value wins
    assert trade["tp"] == 2421.0


# ---------------------------------------------------------------------------
# Finding 2: 'add' events record legs but never render/send a photo
# ---------------------------------------------------------------------------

def test_add_event_sends_no_photo_close_render_still_carries_add_leg(client):
    from app import main
    from app.trade_report import _basket_legs
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="open", price=2400.0))
    _drain()
    # /trade-event sends NOTHING to Telegram for an open: the EA's own
    # /screenshot post (with the EXIT button) is the single chart per entry.
    assert [c for c in ft.calls if c[0] in ("sendPhoto", "sendMessage")] == []

    client.post("/trade-event", json=_trade(event="add", price=2405.0))
    _drain()
    # The add must NOT trigger any Telegram traffic of its own.
    assert [c for c in ft.calls if c[0] in ("sendPhoto", "sendMessage")] == []

    close_id = client.post(
        "/trade-event", json=_trade(event="close", price=2415.0)).json()["id"]
    _drain()
    assert [c for c in ft.calls if c[0] == "sendPhoto"] == []

    legs = _basket_legs(main.app.state.db, close_id)
    assert [leg["event"] for leg in legs] == ["open", "add"]


# ---------------------------------------------------------------------------
# Finding 3: non-final (partial single-leg) closes are telemetry-only
# ---------------------------------------------------------------------------

def test_non_final_close_sends_no_pl_message_and_no_photo(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="open", price=2400.0))
    _drain()
    msgs_after_open = len([c for c in ft.calls if c[0] == "sendMessage"])
    assert msgs_after_open == 0          # opens are silent on /trade-event

    r = client.post(
        "/trade-event",
        json=_trade(event="close", price=2395.0, sl=0.0, tp=0.0,
                    reason="stop-loss", profit=-10.0, final=False))
    _drain()
    assert r.status_code == 200
    trade_id = r.json()["id"]

    # non-final close: no P/L message, no photo, nothing new at all
    assert len([c for c in ft.calls if c[0] == "sendMessage"]) == msgs_after_open
    assert [c for c in ft.calls if c[0] == "sendPhoto"] == []

    row = main.app.state.db.conn.execute(
        "SELECT event, final FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row == ("close", 0)   # telemetry row still persisted


def test_basket_legs_span_a_non_final_close_row(client):
    from app.trade_report import _basket_legs
    from app import main

    id_open = client.post(
        "/trade-event", json=_trade(event="open", price=2400.0)).json()["id"]
    client.post(
        "/trade-event",
        json=_trade(event="close", price=2395.0, final=False))
    id_add = client.post(
        "/trade-event", json=_trade(event="add", price=2405.0)).json()["id"]
    id_close_final = client.post(
        "/trade-event", json=_trade(event="close", price=2415.0)).json()["id"]

    legs = _basket_legs(main.app.state.db, id_close_final)
    assert [leg["event"] for leg in legs] == ["open", "add"]
    assert id_open < id_add < id_close_final


def test_final_close_after_non_final_leg_sends_pl_message_no_photos(client):
    from app import main
    from app.telegram import TelegramClient

    client.post("/analyze", json=_analyze_payload())
    ft = _FakeTransport()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)

    client.post("/trade-event", json=_trade(event="open", price=2400.0))
    _drain()
    client.post(
        "/trade-event",
        json=_trade(event="close", price=2395.0, final=False, profit=-5.0))
    _drain()
    client.post(
        "/trade-event",
        json=_trade(event="close", price=2415.0, final=True, profit=15.0,
                    reason="profit target"))
    _drain()

    pl_msgs = [c for c in ft.calls if c[0] == "sendMessage"
               and "Trade closed" in c[1].get("text", "")]
    assert len(pl_msgs) == 1
    assert "profit" in pl_msgs[0][1]["text"]
    # no render photos at all any more (open, non-final close, final close)
    assert [c for c in ft.calls if c[0] == "sendPhoto"] == []
