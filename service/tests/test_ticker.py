"""Live ticker state machine: flat→open posts once, open edits in place
(only on change, throttled), flat again freezes with CLOSED."""
import time
import types

from app.telegram import TelegramClient
from app.ticker import TICKER_MIN_EDIT_S, TickerState, format_ticker, ticker_tick


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.next_message_id = 100

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        if method == "sendMessage":
            self.next_message_id += 1
            return {"ok": True, "result": {"message_id": self.next_message_id}}
        return {"ok": True}

    def of(self, method):
        return [c for c in self.calls if c[0] == method]


class _Db:
    def __init__(self, channel_id=""):
        self._channel = channel_id

    def exec_mode(self):
        return "auto"

    def get_kv(self, key):
        return self._channel if key == "channel_id" else None


def _pos(direction="SELL", lots=0.02, price=4391.60, profit=54.02):
    return types.SimpleNamespace(ticket=1, direction=direction, lots=lots,
                                 open_price=price, sl=0.0, profit=profit)


def _hb(positions):
    return types.SimpleNamespace(equity=4785.18, floating_pl=65.40,
                                 positions=positions)


def _app(channel_id=""):
    transport = FakeTransport()
    tg = TelegramClient("tok", "555", transport=transport)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=tg, db=_Db(channel_id), ticker=TickerState()))
    return app, transport


def test_flat_heartbeats_send_nothing():
    app, t = _app()
    ticker_tick(app, _hb([]), now=1000.0)
    assert t.calls == []


def test_open_posts_one_live_message_and_remembers_id():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 1
    assert "LIVE" in sends[0][1]["text"]
    assert "SELL 0.02 @ 4391.6" in sends[0][1]["text"]
    assert "reply_markup" not in sends[0][1]
    assert app.state.ticker.owner_msg_id == 101


def test_open_again_same_text_does_not_edit():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([_pos()]), now=1000.0 + TICKER_MIN_EDIT_S + 1)
    assert t.of("editMessageText") == []


def test_open_again_changed_text_edits_in_place():
    app, t = _app()
    ticker_tick(app, _hb([_pos(profit=54.02)]), now=1000.0)
    ticker_tick(app, _hb([_pos(profit=60.00)]),
                now=1000.0 + TICKER_MIN_EDIT_S + 1)
    edits = t.of("editMessageText")
    assert len(edits) == 1
    assert edits[0][1]["message_id"] == 101
    assert "60.00" in edits[0][1]["text"]


def test_edit_throttled_below_min_interval():
    app, t = _app()
    ticker_tick(app, _hb([_pos(profit=54.02)]), now=1000.0)
    ticker_tick(app, _hb([_pos(profit=60.00)]), now=1000.0 + 1)
    assert t.of("editMessageText") == []


def test_close_freezes_message_and_resets_state():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([]), now=1000.0 + TICKER_MIN_EDIT_S + 1)
    edits = t.of("editMessageText")
    assert len(edits) == 1
    assert "CLOSED" in edits[0][1]["text"]
    assert app.state.ticker.owner_msg_id is None
    # a fresh cycle later posts a brand-new message
    ticker_tick(app, _hb([_pos()]), now=2000.0)
    assert len(t.of("sendMessage")) == 2


def test_close_edit_is_never_throttled():
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    ticker_tick(app, _hb([]), now=1000.5)     # < TICKER_MIN_EDIT_S later
    assert len(t.of("editMessageText")) == 1


def test_channel_gets_redacted_variant():
    app, t = _app(channel_id="-1001234")
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 2
    assert sends[0][1]["chat_id"] == "555"        # owner first
    assert sends[1][1]["chat_id"] == "-1001234"
    assert "Equity" in sends[0][1]["text"]
    assert "Equity" not in sends[1][1]["text"]    # privacy filter
    assert "4785.18" not in sends[1][1]["text"]
    assert "+$65.40" in sends[1][1]["text"]       # floating stays


def test_telegram_none_is_safe():
    app, _ = _app()
    app.state.telegram = None
    ticker_tick(app, _hb([_pos()]), now=1000.0)   # must not raise


def test_send_failure_keeps_state_clean_for_retry():
    app, t = _app()
    t.__call__ = None  # not used; replace transport wholesale below
    app.state.telegram = TelegramClient(
        "tok", "555", transport=lambda m, p, f=None: None)
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    assert app.state.ticker.owner_msg_id is None  # next tick retries open


def test_format_ticker_closed_footer():
    text = format_ticker(_hb([_pos()]), "auto", "14:32:05", closed=True)
    assert text.startswith("📊 CLOSED")
    assert "final P/L in the close report" in text
