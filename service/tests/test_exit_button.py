"""EXIT button on trade-open notifications: keyboard shape, sendPhoto
markup encoding, and the exitnow: callback lifecycle."""
import importlib
import json
import types

import pytest
from fastapi.testclient import TestClient

from app.telegram import EXIT_NOW_KB, TelegramClient, handle_callback


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return {"ok": True, "result": {"message_id": 42}}


def test_exit_now_kb_shape():
    m = EXIT_NOW_KB(7)
    assert m["inline_keyboard"][0][0]["callback_data"] == "exitnow:7"


def test_send_photo_serializes_reply_markup():
    t = FakeTransport()
    c = TelegramClient("tok", "123", transport=t)
    c.send_photo("open BUY", b"png", EXIT_NOW_KB(7))
    method, payload, files = t.calls[-1]
    assert method == "sendPhoto" and files is not None
    # multipart form field must be a JSON string, not a dict
    assert json.loads(payload["reply_markup"])["inline_keyboard"]
    c.send_photo("no markup", b"png")
    assert "reply_markup" not in t.calls[-1][1]


# ------------------------------------------------- exitnow: callback flow
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "exitbtn.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _hb_with_position():
    pos = types.SimpleNamespace(ticket=1, direction="BUY", lots=0.1,
                                open_price=2400.0, sl=2390.0, profit=1.0)
    return (1234567890.0, types.SimpleNamespace(
        positions=[pos], active_strategy="halftrend_ema_v1"))


def test_exitnow_flat_is_noop(client):
    client.app.state.latest_heartbeat = None
    edit, toast = handle_callback("exitnow:7", client.app)
    assert edit is None and toast == "already flat"
    assert client.app.state.db.pending_proposal(kind="exit", status="approved") is None


def test_exitnow_queues_approved_close(client):
    client.app.state.latest_heartbeat = _hb_with_position()
    edit, toast = handle_callback("exitnow:7", client.app)
    assert "closing" in toast
    row = client.app.state.db.pending_proposal(kind="exit", status="approved")
    assert row is not None and row["direction"] == "BUY"
    # second tap while in flight: guarded, no second proposal
    edit, toast = handle_callback("exitnow:7", client.app)
    assert toast == "close already approved"


# ------------------------------------------- brakereset: callback flow (2026-08-18)
class _RecTg:
    def __init__(self):
        self.calls = []

    def send_message(self, text, reply_markup=None):
        self.calls.append(("send", text))
        return {"ok": True, "result": {"message_id": 7}}

    def edit_message(self, mid, text, reply_markup=None):
        self.calls.append(("edit", mid, text))
        return {"ok": True}


def test_brakereset_round_trip(client):
    """tap -> pre-approved reset_brake proposal (remembers the tapped
    message) -> heartbeat delivers {"cmd":"reset_brake"} -> EA result edits
    the tapped message into the confirmation."""
    app = client.app
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0, "positions": [],
          "active_strategy": "halftrend_ema_v1"}
    client.post("/heartbeat", json=hb)
    tg = _RecTg()
    app.state.telegram = tg
    edit, toast = handle_callback("brakereset:1", app, message_id=99)
    assert edit is None and "resetting" in toast
    row = app.state.db.pending_proposal(kind="reset_brake", status="approved")
    assert row is not None and row["tg_message_id"] == 99
    assert row["strategy_id"] == "halftrend_ema_v1"
    # second tap while in flight: guarded
    edit, toast = handle_callback("brakereset:1", app, message_id=100)
    assert toast == "reset already approved"
    # heartbeat dispatches it (old EA payload shape — no new fields)
    body = client.post("/heartbeat", json=hb).json()
    assert body["command"] == {"cmd": "reset_brake", "proposal_id": row["id"]}
    r = client.post("/proposal-result",
                    json={"proposal_id": row["id"], "ok": True,
                          "detail": "brake reset for today"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert app.state.db.get_proposal(row["id"])["status"] == "executed"
    assert ("edit", 99, "🔓 Brake reset for today — re-arms after another 3%") in tg.calls
    # a fresh tap is allowed again once the previous one is settled
    edit, toast = handle_callback("brakereset:1", app, message_id=101)
    assert "resetting" in toast


def test_brakereset_failure_edits_tapped_message(client):
    app = client.app
    app.state.latest_heartbeat = None      # no heartbeat yet: still allowed
    tg = _RecTg()
    app.state.telegram = tg
    handle_callback("brakereset:1", app, message_id=55)
    row = app.state.db.pending_proposal(kind="reset_brake", status="approved")
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0, "positions": []}
    assert client.post("/heartbeat", json=hb).json()["command"]["cmd"] == "reset_brake"
    client.post("/proposal-result",
                json={"proposal_id": row["id"], "ok": False, "detail": "nope"})
    assert app.state.db.get_proposal(row["id"])["status"] == "blocked"
    assert ("edit", 55, "🚫 brake reset failed: nope") in tg.calls


def test_heartbeat_new_brake_fields_default_and_round_trip(client):
    from app.models import HeartbeatRequest
    old = HeartbeatRequest(equity=1.0, balance=1.0, floating_pl=0.0)
    assert old.daily_loss_pct == 0.0 and old.brake_reset is False
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0, "positions": [],
          "daily_loss_pct": 53.2, "brake_reset": True}
    assert client.post("/heartbeat", json=hb).status_code == 200
    latest = client.app.state.latest_heartbeat[1]
    assert latest.daily_loss_pct == 53.2 and latest.brake_reset is True
