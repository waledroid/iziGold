import asyncio
import contextlib
import importlib
import threading
import types

from app import main as app_main
from app.db import SignalDb
from app.telegram import TelegramClient, format_live_status, handle_command, pinned_tick


class FakeTransport:
    """Records (method, payload, files) calls; returns a canned result."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, method, payload, files=None):
        self.calls.append((method, payload, files))
        return self.result


def _app(latest_heartbeat=None, pending_switch=None, db=None):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        latest_heartbeat=latest_heartbeat, pending_switch=pending_switch, db=db))


def _hb(active="halftrend_ema_v1", equity=10250.5):
    position = types.SimpleNamespace(ticket=1, direction="BUY", lots=0.1,
                                     open_price=2400.0, sl=2390.0, profit=12.5)
    hb = types.SimpleNamespace(
        equity=equity, balance=10000.0, floating_pl=12.5, positions=[position],
        kill_switch=False, hwm=10300.0, exposure_min=5, window_open=True,
        spread_points=25.0, active_strategy=active)
    return (1234567890.0, hb)


class FakeDb:
    def __init__(self, stats=None, trades=None):
        self._stats = stats or {"total": 0, "resolved": 0, "ai_correct_pct": 0.0,
                                "by_strategy": {}}
        self._trades = trades or []

    def stats(self):
        return self._stats

    def recent_trades(self, limit=10):
        return self._trades[:limit]


# ---------------------------------------------------------------------------
# TelegramClient transport wiring
# ---------------------------------------------------------------------------

def test_send_message_hits_sendmessage_with_text_and_chat_id():
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)
    client.send_message("hello there")
    assert len(ft.calls) == 1
    method, payload, files = ft.calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "555"
    assert payload["text"] == "hello there"
    assert files is None


def test_send_photo_hits_sendphoto_with_caption_and_files():
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)
    client.send_photo("chart caption", b"\x89PNGDATA")
    method, payload, files = ft.calls[0]
    assert method == "sendPhoto"
    assert payload["chat_id"] == "555"
    assert payload["caption"] == "chart caption"
    assert files is not None


def test_edit_message_hits_editmessagetext_with_message_id_and_text():
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)
    client.edit_message(42, "updated text")
    method, payload, files = ft.calls[0]
    assert method == "editMessageText"
    assert payload["chat_id"] == "555"
    assert payload["message_id"] == 42
    assert payload["text"] == "updated text"
    assert files is None


def test_pin_message_hits_pinchatmessage_and_returns_bool():
    ft = FakeTransport(result={"ok": True})
    client = TelegramClient("tok", "555", transport=ft)
    assert client.pin_message(42) is True
    method, payload, files = ft.calls[0]
    assert method == "pinChatMessage"
    assert payload["message_id"] == 42


def test_pin_message_false_when_transport_returns_none():
    ft = FakeTransport()
    ft.result = None
    client = TelegramClient("tok", "555", transport=ft)
    assert client.pin_message(42) is False


def test_get_updates_hits_getupdates_and_returns_list():
    ft = FakeTransport(result={"ok": True, "result": [{"update_id": 1}]})
    client = TelegramClient("tok", "555", transport=ft)
    updates = client.get_updates(0)
    assert updates == [{"update_id": 1}]
    method, payload, files = ft.calls[0]
    assert method == "getUpdates"
    assert payload["offset"] == 0
    assert payload["timeout"] == 25


def test_get_updates_empty_list_when_transport_returns_none():
    ft = FakeTransport()
    ft.result = None
    client = TelegramClient("tok", "555", transport=ft)
    assert client.get_updates(0) == []


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

def test_status_no_heartbeat_yet():
    app = _app(latest_heartbeat=None)
    reply = handle_command("/status", app)
    assert reply is not None
    assert "no heartbeat yet" in reply


def test_status_with_heartbeat_contains_equity_and_strategy():
    app = _app(latest_heartbeat=_hb(active="halftrend_ema_v1", equity=10250.5))
    reply = handle_command("/status", app)
    assert "10250.5" in reply
    assert "halftrend_ema_v1" in reply


def test_switch_with_id_sets_pending_and_names_it_in_reply():
    app = _app()
    reply = handle_command("/switch boll_stochrsi_v1", app)
    assert app.state.pending_switch == "boll_stochrsi_v1"
    assert "boll_stochrsi_v1" in reply


def test_switch_cancel_clears_pending():
    app = _app(pending_switch="boll_stochrsi_v1")
    reply = handle_command("/switch cancel", app)
    assert app.state.pending_switch is None
    assert "cleared" in reply


def test_switch_bare_reports_current_pending():
    app = _app(pending_switch="boll_stochrsi_v1")
    reply = handle_command("/switch", app)
    assert "boll_stochrsi_v1" in reply
    assert "/switch cancel" in reply


def test_stats_includes_by_strategy_id():
    db = FakeDb(stats={"total": 3, "resolved": 2, "ai_correct_pct": 50.0,
                       "by_strategy": {"halftrend_ema_v1": {
                           "signals": 3, "resolved": 2, "hit_pct": 66.7,
                           "avg_move": 1.2}}})
    app = _app(db=db)
    reply = handle_command("/stats", app)
    assert "halftrend_ema_v1" in reply


def test_history_lists_recent_trades():
    db = FakeDb(trades=[{"id": 1, "ts": 1234567890, "event": "open",
                         "strategy_id": "halftrend_ema_v1", "direction": "BUY",
                         "lots": 0.1, "price": 2400.0, "sl": 2390.0,
                         "reason": "signal", "ticket": 1,
                         "screenshot_path": None, "profit": 0.0,
                         "render_path": None}])
    app = _app(db=db)
    reply = handle_command("/history", app)
    assert "halftrend_ema_v1" in reply
    assert "BUY" in reply


def test_history_open_trade_omits_pl():
    """insert_trade always stores a profit (default 0.0), so an open/add
    row must not print a misleading "P/L 0.0" -- only close events carry
    a real P/L."""
    db = FakeDb(trades=[{"id": 1, "ts": 1234567890, "event": "open",
                         "strategy_id": "halftrend_ema_v1", "direction": "BUY",
                         "lots": 0.1, "price": 2400.0, "sl": 2390.0,
                         "reason": "signal", "ticket": 1,
                         "screenshot_path": None, "profit": 0.0,
                         "render_path": None}])
    app = _app(db=db)
    reply = handle_command("/history", app)
    assert "P/L" not in reply


def test_history_close_trade_includes_pl_even_when_negative():
    """A break-even or losing close (profit 0.0 or negative) is still a
    real close outcome and must be shown, not treated as falsy/missing."""
    db = FakeDb(trades=[{"id": 2, "ts": 1234567890, "event": "close",
                         "strategy_id": "halftrend_ema_v1", "direction": "BUY",
                         "lots": 0.1, "price": 2400.0, "sl": 2390.0,
                         "reason": "sl hit", "ticket": 1,
                         "screenshot_path": None, "profit": -3.2,
                         "render_path": None}])
    app = _app(db=db)
    reply = handle_command("/history", app)
    assert "P/L -3.2" in reply


def test_unknown_command_returns_none():
    app = _app()
    assert handle_command("/foo", app) is None


# ---------------------------------------------------------------------------
# Lifespan wiring
# ---------------------------------------------------------------------------

def test_poller_task_not_created_when_telegram_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tg.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        assert main.app.state.telegram is None
        assert main.app.state.telegram_task is None


class _FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {"ok": True, "result": []}

    def json(self):
        return self._data


def test_poller_task_created_and_cancelled_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tg2.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResp())
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        assert main.app.state.telegram is not None
        assert main.app.state.telegram_task is not None
        assert not main.app.state.telegram_task.done()
    assert main.app.state.telegram_task.done()


# ---------------------------------------------------------------------------
# Poller must not block the event loop (regression: FastAPI + shutdown hangs)
# ---------------------------------------------------------------------------

def test_poller_dispatches_client_calls_off_event_loop(monkeypatch):
    """A blocking (sync, slow) TelegramClient must run in a worker thread,
    not directly on the event loop -- otherwise every /health, /analyze,
    /heartbeat request stalls for the duration of each long-poll, and
    task.cancel() can't interrupt an in-flight call because the block
    isn't at an await point."""
    monkeypatch.setattr(app_main.settings, "telegram_chat_id", "555")
    main_thread_name = threading.current_thread().name
    recorded = {"get_updates_thread": None, "send_message_thread": None,
                "sent": []}

    class StubClient:
        def __init__(self):
            self.calls = 0

        def get_updates(self, offset):
            recorded["get_updates_thread"] = threading.current_thread().name
            self.calls += 1
            if self.calls == 1:
                return [{"update_id": 1,
                        "message": {"text": "/status", "chat": {"id": 555}}}]
            return []

        def send_message(self, text):
            recorded["send_message_thread"] = threading.current_thread().name
            recorded["sent"].append(text)
            return {"ok": True}

    stub_app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=StubClient(), latest_heartbeat=None, pending_switch=None,
        db=None))

    async def run():
        task = asyncio.ensure_future(app_main.telegram_poller(stub_app))
        await asyncio.sleep(0.1)   # let one get_updates/send_message cycle run
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert recorded["get_updates_thread"] is not None
    assert recorded["get_updates_thread"] != main_thread_name
    assert recorded["send_message_thread"] is not None
    assert recorded["send_message_thread"] != main_thread_name
    assert recorded["sent"] == ["no heartbeat yet"]


# ---------------------------------------------------------------------------
# kv store (SignalDb)
# ---------------------------------------------------------------------------

def test_kv_roundtrip(tmp_path):
    db = SignalDb(str(tmp_path / "kv.db"))
    assert db.get_kv("pinned_message_id") is None
    db.set_kv("pinned_message_id", "123")
    assert db.get_kv("pinned_message_id") == "123"
    db.set_kv("pinned_message_id", "456")
    assert db.get_kv("pinned_message_id") == "456"


# ---------------------------------------------------------------------------
# format_live_status
# ---------------------------------------------------------------------------

def test_format_live_status_contains_equity_and_strategy():
    app = _app(latest_heartbeat=_hb(active="halftrend_ema_v1", equity=10250.5))
    text = format_live_status(app)
    assert "10250.5" in text
    assert "halftrend_ema_v1" in text


def test_format_live_status_no_heartbeat_yet():
    app = _app(latest_heartbeat=None)
    text = format_live_status(app)
    assert "no heartbeat" in text


# ---------------------------------------------------------------------------
# pinned_tick
# ---------------------------------------------------------------------------

def test_pinned_tick_creates_and_pins_then_edits_on_next_call(tmp_path):
    db = SignalDb(str(tmp_path / "pin.db"))
    app = _app(latest_heartbeat=_hb(), db=db)
    ft = FakeTransport(result={"ok": True, "result": {"message_id": 999}})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    methods = [c[0] for c in ft.calls]
    assert methods == ["sendMessage", "pinChatMessage"]
    assert db.get_kv("pinned_message_id") == "999"
    pin_payload = ft.calls[1][1]
    assert pin_payload["message_id"] == 999

    ft.calls.clear()
    ft.result = {"ok": True}
    pinned_tick(app, client)

    assert len(ft.calls) == 1
    method, payload, files = ft.calls[0]
    assert method == "editMessageText"
    assert payload["message_id"] == 999
    assert isinstance(payload["message_id"], int)


def test_pinned_tick_self_heals_when_edit_fails(tmp_path):
    """If the pinned message was deleted server-side, editMessageText comes
    back None/error. That tick must clear the stale kv id (not retry it
    forever); the *next* tick then falls through to create+pin again and
    stores the new id."""
    db = SignalDb(str(tmp_path / "pin_heal.db"))
    db.set_kv("pinned_message_id", "999")
    app = _app(latest_heartbeat=_hb(), db=db)
    ft = FakeTransport()
    ft.result = None  # editMessageText fails
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert [c[0] for c in ft.calls] == ["editMessageText"]
    assert db.get_kv("pinned_message_id") in (None, "")

    ft.calls.clear()
    ft.result = {"ok": True, "result": {"message_id": 1000}}
    pinned_tick(app, client)

    assert [c[0] for c in ft.calls] == ["sendMessage", "pinChatMessage"]
    assert db.get_kv("pinned_message_id") == "1000"


def test_pinned_tick_self_heals_when_edit_returns_error(tmp_path):
    """Same self-heal path, but the transport returns an explicit error
    response (ok: False) rather than None -- e.g. Telegram's "message to
    edit not found"."""
    db = SignalDb(str(tmp_path / "pin_heal2.db"))
    db.set_kv("pinned_message_id", "999")
    app = _app(latest_heartbeat=_hb(), db=db)
    ft = FakeTransport(result={"ok": False, "description": "message not found"})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert [c[0] for c in ft.calls] == ["editMessageText"]
    assert db.get_kv("pinned_message_id") in (None, "")


def test_pinned_tick_noop_without_heartbeat(tmp_path):
    db = SignalDb(str(tmp_path / "pin2.db"))
    app = _app(latest_heartbeat=None, db=db)
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert ft.calls == []
    assert db.get_kv("pinned_message_id") is None


# ---------------------------------------------------------------------------
# pinned_editor must not block the event loop (same reasoning as the poller)
# ---------------------------------------------------------------------------

def test_pinned_editor_dispatches_client_calls_off_event_loop(tmp_path):
    db = SignalDb(str(tmp_path / "pin3.db"))
    main_thread_name = threading.current_thread().name
    recorded = {"send_message_thread": None}

    class StubClient:
        def send_message(self, text):
            recorded["send_message_thread"] = threading.current_thread().name
            return {"ok": True, "result": {"message_id": 42}}

        def edit_message(self, message_id, text):
            pass

        def pin_message(self, message_id):
            return True

    stub_app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=StubClient(), latest_heartbeat=_hb(), db=db))

    async def run():
        task = asyncio.ensure_future(app_main.pinned_editor(stub_app))
        await asyncio.sleep(0.1)  # let one tick run before it sleeps 60s
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert recorded["send_message_thread"] is not None
    assert recorded["send_message_thread"] != main_thread_name
    assert db.get_kv("pinned_message_id") == "42"
