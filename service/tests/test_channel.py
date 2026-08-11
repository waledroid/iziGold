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
