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
