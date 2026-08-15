"""Live ticker state machine: flat→open posts once, open edits in place
(only on change, throttled), flat again freezes with CLOSED."""
import time
import types

import pytest

from app.telegram import TelegramClient
from app.ticker import TICKER_MIN_EDIT_S, TickerState, format_ticker, ticker_tick


@pytest.fixture(autouse=True)
def _no_miniapp_url_by_default(monkeypatch):
    """MINIAPP_PUBLIC_URL may be set in the developer's real .env (it drives
    the live ngrok tunnel) -- tests must not depend on that machine-local
    state. Default it off; tests that specifically cover the button-present
    path opt back in with their own monkeypatch.setattr."""
    from app.config import settings
    monkeypatch.setattr(settings, "miniapp_public_url", "")


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


def test_close_with_previous_parameter_snapshots_prior_open():
    """Close branch must use prior open snapshot when previous parameter provided."""
    app, t = _app()
    prior_pos = _pos(profit=100.00)
    prior_hb = _hb([prior_pos])
    prior_ts = 1000.0

    # Open the trade with initial state
    ticker_tick(app, prior_hb, now=prior_ts)
    assert app.state.ticker.owner_msg_id == 101

    # Close the trade, passing the prior state via previous parameter
    flat_hb = _hb([])  # now flat
    ticker_tick(app, flat_hb, now=prior_ts + 100,
                previous=(prior_ts, prior_hb))

    # Verify the frozen CLOSED message contains the prior position data
    edits = t.of("editMessageText")
    assert len(edits) == 1
    closed_text = edits[0][1]["text"]
    assert "CLOSED" in closed_text
    assert "SELL 0.02 @ 4391.6" in closed_text  # prior position visible
    assert "100.00" in closed_text  # prior profit visible


def test_open_to_open_edit_failure_retries():
    """Open→open: failed edit leaves state unchanged so next tick retries."""
    app, t = _app()

    # First open posts successfully
    ticker_tick(app, _hb([_pos(profit=54.02)]), now=1000.0)
    assert app.state.ticker.owner_msg_id == 101
    initial_text = app.state.ticker.owner_text

    # Second call: change data but make edit fail (transport returns None/falsy ok)
    class FailingTransport:
        def __init__(self):
            self.calls = []
            self.next_message_id = 101

        def __call__(self, method, payload, files=None):
            self.calls.append((method, payload, files))
            if method == "sendMessage":
                self.next_message_id += 1
                return {"ok": True, "result": {"message_id": self.next_message_id}}
            elif method == "editMessageText":
                # Simulate failure: return None or {"ok": False}
                return None
            return {"ok": True}

    failing_transport = FailingTransport()
    app.state.telegram = TelegramClient("tok", "555", transport=failing_transport)

    ticker_tick(app, _hb([_pos(profit=60.00)]), now=1000.0 + TICKER_MIN_EDIT_S + 1)

    # State should be unchanged after failed edit
    assert app.state.ticker.owner_text == initial_text
    # Try again: this time edit succeeds (new app/transport)
    success_transport = FakeTransport()
    app.state.telegram = TelegramClient("tok", "555", transport=success_transport)
    ticker_tick(app, _hb([_pos(profit=60.00)]), now=1000.0 + TICKER_MIN_EDIT_S * 2 + 2)

    # Now the edit should have succeeded and text updated
    assert "60.00" in app.state.ticker.owner_text


def test_open_posts_live_chart_button_when_url_set(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "miniapp_public_url",
                        "https://tribute-obscurity-monday.ngrok-free.dev")
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 1
    markup = sends[0][1]["reply_markup"]
    assert markup == {"inline_keyboard": [[
        {"text": "📈 Live Chart",
         "web_app": {"url": "https://tribute-obscurity-monday.ngrok-free.dev"}}
    ]]}


def test_open_posts_no_button_when_url_unset(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "miniapp_public_url", "")
    app, t = _app()
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert "reply_markup" not in sends[0][1]


def test_channel_ticker_copy_never_has_markup_even_when_url_set(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "miniapp_public_url",
                        "https://tribute-obscurity-monday.ngrok-free.dev")
    app, t = _app(channel_id="-1001234")
    ticker_tick(app, _hb([_pos()]), now=1000.0)
    sends = t.of("sendMessage")
    assert len(sends) == 2
    assert sends[0][1]["chat_id"] == "555"        # owner: has the button
    assert "reply_markup" in sends[0][1]
    assert sends[1][1]["chat_id"] == "-1001234"   # channel: never a button
    assert "reply_markup" not in sends[1][1]


def test_heartbeat_endpoint_triggers_ticker(tmp_path, monkeypatch):
    """Integration: /heartbeat with positions posts a LIVE message without
    delaying the response; flat heartbeats post nothing."""
    import importlib

    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tick.db"))
    from fastapi.testclient import TestClient

    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as client:
        transport = FakeTransport()
        main.app.state.telegram = TelegramClient("tok", "555",
                                                 transport=transport)
        hb = {"equity": 4785.18, "balance": 4719.78, "floating_pl": 65.40,
              "positions": [{"ticket": 1, "direction": "SELL", "lots": 0.02,
                             "open_price": 4391.60, "sl": 4400.0,
                             "profit": 54.02}],
              "kill_switch": False, "hwm": 4800.0, "exposure_min": 5,
              "window_open": True, "spread_points": 25.0,
              "active_strategy": "halftrend_ema_v1"}
        r = client.post("/heartbeat", json=hb)
        assert r.status_code == 200
        assert r.json()["command"] is None      # response shape unchanged
        for _ in range(40):                      # ticker runs in background
            if transport.of("sendMessage"):
                break
            time.sleep(0.05)
        sends = transport.of("sendMessage")
        assert len(sends) == 1 and "LIVE" in sends[0][1]["text"]


def test_channel_ticker_carries_direct_link_line_when_configured(monkeypatch):
    """Channel copies can't carry web_app buttons — they get a tap link line
    (BotFather direct link preferred), placed above the timestamp so the
    unchanged-body check keeps working; owner text does not get the line."""
    from app import config
    monkeypatch.setattr(config.settings, "miniapp_direct_link",
                        "https://t.me/bot/chart", raising=False)
    monkeypatch.setattr(config.settings, "miniapp_public_url",
                        "https://x.example", raising=False)
    from app.ticker import format_ticker
    hb = types.SimpleNamespace(equity=1.0, floating_pl=0.0, positions=[
        types.SimpleNamespace(direction="SELL", lots=0.05, open_price=4000.0,
                              profit=1.0)])
    chan = format_ticker(hb, "auto", "10:00:00", redacted=True)
    owner = format_ticker(hb, "auto", "10:00:00")
    assert "📈 Live chart: https://t.me/bot/chart" in chan
    assert chan.rstrip().endswith("updated 10:00:00")     # link above timestamp
    assert "Live chart" not in owner
    closed = format_ticker(hb, "auto", "10:00:00", closed=True, redacted=True)
    assert "Live chart" not in closed
