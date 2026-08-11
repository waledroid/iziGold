"""Channel-addressed sends: explicit chat_id, structurally no reply_markup."""
import json

from app.telegram import TelegramClient


class FakeTransport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def _client(result=None):
    t = FakeTransport(result)
    return TelegramClient("tok", "555", transport=t), t


def test_send_message_to_overrides_chat_id():
    client, t = _client()
    client.send_message_to("-1001234", "hello channel")
    assert t.calls == [("sendMessage",
                        {"chat_id": "-1001234", "text": "hello channel"}, None)]


def test_send_message_to_never_has_reply_markup():
    client, t = _client()
    client.send_message_to("-1001234", "x")
    assert "reply_markup" not in t.calls[0][1]


def test_send_photo_to_overrides_chat_id():
    client, t = _client()
    client.send_photo_to("-1001234", "cap", b"png")
    method, payload, files = t.calls[0]
    assert method == "sendPhoto"
    assert payload == {"chat_id": "-1001234", "caption": "cap"}
    assert files == {"photo": ("chart.png", b"png", "image/png")}


def test_edit_message_to_overrides_chat_id():
    client, t = _client()
    client.edit_message_to("-1001234", 42, "new text")
    assert t.calls == [("editMessageText",
                        {"chat_id": "-1001234", "message_id": 42,
                         "text": "new text"}, None)]


def test_owner_methods_unchanged():
    client, t = _client()
    client.send_message("owner text")
    assert t.calls[0][1]["chat_id"] == "555"


import time
import types

from app.telegram import REDACTED, handle_command


class _KvDb:
    """Minimal db stub: exec_mode + kv store, enough for handle_command."""

    def __init__(self):
        self.kv = {}

    def exec_mode(self):
        return "auto"

    def get_kv(self, key):
        return self.kv.get(key)

    def set_kv(self, key, value):
        self.kv[key] = value

    def strategy_ids(self):
        return ["halftrend_ema_v1"]


def _hb_ns(**over):
    base = dict(equity=4785.18, balance=4719.78, floating_pl=65.40,
                positions=[], kill_switch=False, hwm=4800.0, exposure_min=5,
                window_open=True, spread_points=25.0,
                active_strategy="halftrend_ema_v1", algo_trading=True)
    base.update(over)
    return types.SimpleNamespace(**base)


def _cmd_app():
    return types.SimpleNamespace(state=types.SimpleNamespace(
        latest_heartbeat=(time.time(), _hb_ns()), pending_switch=None,
        db=_KvDb(), pending_channel=None))


def test_status_redacted_hides_account_figures():
    app = _cmd_app()
    text = handle_command("/status", app, redacted=True)
    for figure in ("4785.18", "4719.78", "4800", "drawdown"):
        assert figure not in text
    assert "halftrend_ema_v1" in text          # strategy stays
    assert "Protection armed" in text           # state stays, number goes


def test_status_redacted_keeps_position_pl():
    app = _cmd_app()
    app.state.latest_heartbeat[1].positions = [types.SimpleNamespace(
        ticket=7, direction="SELL", lots=0.02, open_price=4391.60,
        sl=4400.0, profit=54.02)]
    text = handle_command("/status", app, redacted=True)
    assert "54.02" in text and "4391.6" in text


def test_bal_redacted_masks_balance_and_equity():
    text = handle_command("/bal", app=_cmd_app(), redacted=True)
    assert REDACTED in text
    assert "4719.78" not in text and "4785.18" not in text
    assert "+$65.40" in text                     # floating is trade-level


def test_config_redacted_masks_account_line():
    text = handle_command("/config", app=_cmd_app(), redacted=True)
    assert "4719.78" not in text and "4785.18" not in text
    assert REDACTED in text


def test_default_is_unredacted():
    text = handle_command("/bal", app=_cmd_app())
    assert "4719.78" in text


from app.telegram import (PINNED_HELP_VERSION, format_pinned_help,
                          handle_callback, handle_channel_post)


def _post(chat_id="-1001234", title="XAU Signals"):
    return {"chat": {"id": chat_id, "title": title, "type": "channel"},
            "text": "hello"}


def test_channel_post_offers_link_to_owner():
    app = _cmd_app()
    result = handle_channel_post(_post(), app)
    assert result is not None
    text, keyboard = result
    assert "XAU Signals" in text
    flat = [b for row in keyboard["inline_keyboard"] for b in row]
    assert [b["callback_data"] for b in flat] == \
        ["chan:link:-1001234", "chan:ignore:-1001234"]
    assert app.state.pending_channel == "-1001234"


def test_channel_post_ignored_when_already_linked_or_pending():
    app = _cmd_app()
    app.state.db.set_kv("channel_id", "-1009999")
    assert handle_channel_post(_post(), app) is None
    app2 = _cmd_app()
    app2.state.pending_channel = "-1008888"
    assert handle_channel_post(_post(), app2) is None


def test_chan_link_callback_stores_kv_and_clears_pending():
    app = _cmd_app()
    app.state.pending_channel = "-1001234"
    edit_text, toast = handle_callback("chan:link:-1001234", app)
    assert app.state.db.get_kv("channel_id") == "-1001234"
    assert app.state.pending_channel is None
    assert "linked" in edit_text.lower()


def test_chan_ignore_callback_stores_nothing():
    app = _cmd_app()
    app.state.pending_channel = "-1001234"
    edit_text, toast = handle_callback("chan:ignore:-1001234", app)
    assert not app.state.db.get_kv("channel_id")
    assert app.state.pending_channel is None


def test_chan_link_callback_ignored_when_no_pending_offer():
    app = _cmd_app()
    app.state.pending_channel = None
    edit_text, toast = handle_callback("chan:link:-1001234", app)
    assert not app.state.db.get_kv("channel_id")
    assert toast == "offer expired"


def test_chan_link_callback_ignored_when_id_does_not_match_pending():
    app = _cmd_app()
    app.state.pending_channel = "-100NEW"
    edit_text, toast = handle_callback("chan:link:-100OLD", app)
    assert not app.state.db.get_kv("channel_id")
    assert app.state.pending_channel == "-100NEW"
    assert toast == "offer expired"


def test_channel_command_states_and_unlink():
    app = _cmd_app()
    assert "no channel linked" in handle_command("/channel", app)
    app.state.db.set_kv("channel_id", "-1001234")
    assert "-1001234" in handle_command("/channel", app)
    reply = handle_command("/channel unlink", app)
    assert "unlinked" in reply
    assert not app.state.db.get_kv("channel_id")


def test_pinned_help_mentions_channel_and_version_bumped():
    assert "/channel" in format_pinned_help()
    assert PINNED_HELP_VERSION == "5"


import importlib

import pytest
from fastapi.testclient import TestClient


class _RecordingTransport:
    def __init__(self, fail_chat_ids=()):
        self.calls = []
        self.fail_chat_ids = set(fail_chat_ids)
        self._mid = 200

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        if str(payload.get("chat_id")) in self.fail_chat_ids:
            return None
        if method in ("sendMessage", "sendPhoto"):
            self._mid += 1
            return {"ok": True, "result": {"message_id": self._mid}}
        return {"ok": True}

    def sends(self):
        return [(p.get("chat_id"), p.get("text") or p.get("caption"))
                for m, p, f in self.calls if m in ("sendMessage", "sendPhoto")]


@pytest.fixture()
def linked_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mirror.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = _RecordingTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        main.app.state.db.set_kv("channel_id", "-1001234")
        yield main, client, transport


def test_notify_mirrors_owner_first(linked_app):
    main, client, transport = linked_app
    r = client.post("/notify", json={"text": "🚫 entry not executed: spread"})
    assert r.status_code == 200
    sends = transport.sends()
    assert sends[0][0] == "555"
    assert sends[1] == ("-1001234", "🚫 entry not executed: spread")


def test_channel_failure_leaves_owner_delivery_intact(linked_app):
    main, client, transport = linked_app
    transport.fail_chat_ids = {"-1001234"}
    r = client.post("/notify", json={"text": "hello"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert transport.sends()[0][0] == "555"


def test_unlinked_channel_sends_nothing_extra(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "nolink.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = _RecordingTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        client.post("/notify", json={"text": "hello"})
        assert [c for c, _ in transport.sends()] == ["555"]


def test_channel_payloads_never_carry_reply_markup(linked_app):
    main, client, transport = linked_app
    client.post("/notify", json={"text": "hi"})
    for method, payload, files in transport.calls:
        if str(payload.get("chat_id")) == "-1001234":
            assert "reply_markup" not in payload


def test_mirror_helper_redacts_command_replies(linked_app):
    """Poller-level mirroring is driven by _mirror_command; verify the
    composed channel text: '👤 /bal' header + redacted reply."""
    main, client, transport = linked_app
    hb = {"equity": 4785.18, "balance": 4719.78, "floating_pl": 65.40,
          "positions": [], "kill_switch": False, "hwm": 4800.0,
          "exposure_min": 5, "window_open": True, "spread_points": 25.0,
          "active_strategy": "halftrend_ema_v1"}
    client.post("/heartbeat", json=hb)
    text = main._mirror_command_text("/bal", main.app)
    assert text.startswith("👤 /bal")
    assert "4719.78" not in text and "•••" in text
