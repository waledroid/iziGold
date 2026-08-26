"""[🔒 Move SL] on the FIXED-ride target alert: /notify 'target' keyboard,
movesl: callback lifecycle, heartbeat dispatch, and the reusable-button
edit on /proposal-result."""
import importlib
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from app.telegram import TARGET_KB, handle_callback


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "movesl.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


class _RecTg:
    def __init__(self):
        self.sends = []
        self.edits = []

    def send_message(self, text, reply_markup=None):
        self.sends.append((text, reply_markup))
        return {"ok": True, "result": {"message_id": 42}}

    def edit_message(self, message_id, text, reply_markup=None):
        self.edits.append((message_id, text, reply_markup))
        return {"ok": True}


def _pos(direction="SELL"):
    return types.SimpleNamespace(ticket=1, direction=direction, lots=0.05,
                                 open_price=4642.56, sl=4658.32, profit=95.0)


def _hb(positions=(), ts=None):
    return (ts if ts is not None else time.time(), types.SimpleNamespace(
        positions=list(positions), active_strategy="halftrend_m15_v1",
        bar_c=4623.50, entry_mode="fixed", algo_trading=True))


def _kb_data(markup):
    return [btn["callback_data"]
            for row in markup["inline_keyboard"] for btn in row]


def test_target_kb_has_exit_and_move_sl():
    data = _kb_data(TARGET_KB())
    assert any(d.startswith("exitnow:") for d in data)
    assert any(d.startswith("movesl:") for d in data)


def test_notify_target_button_attaches_dual_keyboard(client):
    client.app.state.telegram = tg = _RecTg()
    client.app.state.latest_heartbeat = _hb(positions=[_pos()])
    r = client.post("/notify", json={"text": "🎯 target hit", "button": "target"})
    assert r.json()["ok"] is True
    text, markup = tg.sends[0]
    assert markup is not None
    data = _kb_data(markup)
    assert any(d.startswith("exitnow:") for d in data)
    assert any(d.startswith("movesl:") for d in data)


def test_notify_target_degrades_to_plain_when_flat(client):
    client.app.state.telegram = tg = _RecTg()
    client.app.state.latest_heartbeat = _hb(positions=[])
    client.post("/notify", json={"text": "🎯 target hit", "button": "target"})
    assert tg.sends[0][1] is None


# ---------------------------------------------------------- movesl: callback

def test_movesl_tap_queues_approved_proposal(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos("SELL")])
    edit, toast = handle_callback("movesl:1", client.app, message_id=88)
    assert "SL" in toast
    row = client.app.state.db.pending_proposal(kind="move_sl", status="approved")
    assert row is not None
    assert row["direction"] == "SELL"
    assert row["tg_message_id"] == 88


def test_movesl_second_tap_is_guarded(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos()])
    handle_callback("movesl:1", client.app, message_id=88)
    edit, toast = handle_callback("movesl:1", client.app, message_id=88)
    assert edit is None and "already" in toast
    cur = client.app.state.db.conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE kind='move_sl'")
    assert cur.fetchone()[0] == 1


def test_movesl_refused_when_flat(client):
    client.app.state.latest_heartbeat = _hb(positions=[])
    edit, toast = handle_callback("movesl:1", client.app)
    assert edit is None and "nothing open" in toast
    assert client.app.state.db.pending_proposal(
        kind="move_sl", status="approved") is None


def test_movesl_refused_on_stale_heartbeat(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos()], ts=time.time() - 999)
    edit, toast = handle_callback("movesl:1", client.app)
    assert edit is None and "EA not connected" in toast


def test_movesl_rides_next_heartbeat_as_move_sl_cmd(client):
    client.app.state.latest_heartbeat = _hb(positions=[_pos()])
    handle_callback("movesl:1", client.app, message_id=88)
    r = client.post("/heartbeat", json={
        "equity": 4756.0, "balance": 4756.0, "floating_pl": 95.0})
    cmd = r.json()["command"]
    assert cmd is not None and cmd["cmd"] == "move_sl"


# ----------------------------------------------- /proposal-result reusability

def _queued_move_sl(client, message_id=88):
    client.app.state.latest_heartbeat = _hb(positions=[_pos()])
    handle_callback("movesl:1", client.app, message_id=message_id)
    client.post("/heartbeat", json={
        "equity": 4756.0, "balance": 4756.0, "floating_pl": 95.0})
    return client.app.state.db.pending_proposal(kind="move_sl",
                                                status="dispatched")


def test_move_sl_result_edits_message_and_keeps_buttons(client):
    client.app.state.telegram = tg = _RecTg()
    row = _queued_move_sl(client)
    client.post("/proposal-result", json={
        "proposal_id": row["id"], "ok": True,
        "detail": "SL → 4623.80 (1 leg)"})
    message_id, text, markup = tg.edits[-1]
    assert message_id == 88
    assert "🔒" in text and "4623.80" in text
    # buttons re-attached so [Move SL] stays reusable for further gains
    assert markup is not None
    data = _kb_data(markup)
    assert any(d.startswith("movesl:") for d in data)


def test_move_sl_failure_reports_without_lock_mark(client):
    client.app.state.telegram = tg = _RecTg()
    row = _queued_move_sl(client)
    client.post("/proposal-result", json={
        "proposal_id": row["id"], "ok": False, "detail": "nothing open"})
    _, text, _ = tg.edits[-1]
    assert "🚫" in text and "nothing open" in text
