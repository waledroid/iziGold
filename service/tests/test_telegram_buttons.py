from app.telegram import TelegramClient, kb


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 42}}


def make_client(t):
    # match TelegramClient's real constructor signature
    return TelegramClient("tok", "123", transport=t)


def test_kb_builds_inline_keyboard():
    m = kb([[("A", "a"), ("B", "b")]])
    assert m == {"inline_keyboard": [[{"text": "A", "callback_data": "a"},
                                      {"text": "B", "callback_data": "b"}]]}


def test_send_with_markup_and_edit_and_answer():
    t = FakeTransport()
    c = make_client(t)
    c.send_message("hi", reply_markup=kb([[("X", "x")]]))
    method, payload = t.calls[-1]
    assert method == "sendMessage" and "reply_markup" in payload
    c.edit_message(42, "new", reply_markup=None)
    method, payload = t.calls[-1]
    assert method == "editMessageText" and payload["message_id"] == 42
    c.answer_callback("cb1", "done")
    method, payload = t.calls[-1]
    assert method == "answerCallbackQuery" and payload["callback_query_id"] == "cb1"
