"""/trade manual entry: command reply with BUY/SELL buttons, mtrade:
callback lifecycle, and the clash guards that keep a manual tap from
fighting the EA (open basket, in-flight entry, stale EA)."""
import importlib
import time
import types

import pytest
from fastapi.testclient import TestClient

from app.telegram import format_pinned_help, handle_callback, handle_command


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "trade_cmd.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _hb(positions=(), ts=None, bar_c=4640.55, active="halftrend_m15_v1",
        entry_mode="fixed"):
    return (ts if ts is not None else time.time(), types.SimpleNamespace(
        positions=list(positions), active_strategy=active,
        bar_c=bar_c, entry_mode=entry_mode, algo_trading=True))


def _pos(direction="SELL"):
    return types.SimpleNamespace(ticket=1, direction=direction, lots=0.05,
                                 open_price=4642.56, sl=4658.32, profit=1.0)


# ------------------------------------------------------------- /trade command

def test_trade_replies_with_buy_and_sell_buttons(client):
    client.app.state.latest_heartbeat = _hb()
    reply = handle_command("/trade", client.app)
    assert isinstance(reply, tuple)
    text, keyboard = reply
    assert "4640.55" in text
    data = [btn["callback_data"]
            for row in keyboard["inline_keyboard"] for btn in row]
    assert data == ["mtrade:BUY", "mtrade:SELL"]
    labels = [btn["text"]
              for row in keyboard["inline_keyboard"] for btn in row]
    assert any("🔵" in l and "BUY" in l for l in labels)
    assert any("🔴" in l and "SELL" in l for l in labels)


def test_trade_refuses_when_ea_never_connected(client):
    client.app.state.latest_heartbeat = None
    reply = handle_command("/trade", client.app)
    assert isinstance(reply, str) and "EA not connected" in reply


def test_trade_refuses_when_ea_stale(client):
    client.app.state.latest_heartbeat = _hb(ts=time.time() - 999)
    reply = handle_command("/trade", client.app)
    assert isinstance(reply, str) and "EA not connected" in reply


def test_trade_refuses_when_basket_open(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos("SELL")])
    reply = handle_command("/trade", client.app)
    assert isinstance(reply, str) and "already in a trade" in reply


def test_trade_refuses_when_entry_already_in_flight(client):
    client.app.state.latest_heartbeat = _hb()
    db = client.app.state.db
    pid = db.create_proposal("entry", "BUY", "halftrend_m15_v1", 4640.0, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    reply = handle_command("/trade", client.app)
    assert isinstance(reply, str) and "already" in reply


def test_trade_listed_in_pinned_help(client):
    assert "/trade" in format_pinned_help()


# --------------------------------------------------------- mtrade: callback

def test_mtrade_tap_queues_approved_entry(client):
    client.app.state.latest_heartbeat = _hb()
    edit, toast = handle_callback("mtrade:BUY", client.app, message_id=77)
    assert "BUY" in toast
    row = client.app.state.db.pending_proposal(kind="entry", status="approved")
    assert row is not None
    assert row["direction"] == "BUY"
    assert row["price"] == 4640.55
    assert row["strategy_id"] == "halftrend_m15_v1"
    assert row["tg_message_id"] == 77
    assert edit is not None and "BUY" in edit


def test_mtrade_second_tap_is_guarded(client):
    client.app.state.latest_heartbeat = _hb()
    handle_callback("mtrade:SELL", client.app, message_id=77)
    edit, toast = handle_callback("mtrade:SELL", client.app, message_id=77)
    assert edit is None and "already" in toast
    cur = client.app.state.db.conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE kind='entry'")
    assert cur.fetchone()[0] == 1


def test_mtrade_refuses_when_basket_open(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos("BUY")])
    edit, toast = handle_callback("mtrade:SELL", client.app)
    assert edit is None and "already in a trade" in toast
    assert client.app.state.db.pending_proposal(
        kind="entry", status="approved") is None


def test_mtrade_refuses_on_stale_heartbeat(client):
    client.app.state.latest_heartbeat = _hb(ts=time.time() - 999)
    edit, toast = handle_callback("mtrade:BUY", client.app)
    assert edit is None and "EA not connected" in toast


def test_mtrade_unknown_direction_rejected(client):
    client.app.state.latest_heartbeat = _hb()
    edit, toast = handle_callback("mtrade:SIDEWAYS", client.app)
    assert edit is None and toast == "unknown"
    assert client.app.state.db.pending_proposal(
        kind="entry", status="approved") is None


def test_queued_manual_entry_rides_next_heartbeat(client):
    # End-to-end: tap -> approved proposal -> /heartbeat returns execute cmd.
    client.app.state.latest_heartbeat = _hb()
    handle_callback("mtrade:BUY", client.app, message_id=5)
    r = client.post("/heartbeat", json={
        "equity": 4756.0, "balance": 4756.0, "floating_pl": 0.0})
    cmd = r.json()["command"]
    assert cmd is not None
    assert cmd["cmd"] == "execute" and cmd["direction"] == "BUY"
