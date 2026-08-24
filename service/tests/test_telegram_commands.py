import asyncio
import contextlib
import importlib
import threading
import time
import types
from datetime import datetime
from zoneinfo import ZoneInfo

from app import main as app_main
from app.db import SignalDb
from app.telegram import (COMMANDS, PINNED_HELP_VERSION, TelegramClient,
                          _PINNED_EXTRA, format_pinned_help, format_proposal,
                          handle_callback, handle_command, market_session, pinned_tick)
from tests.test_proposals_flow import _post_signal, client, fake_tg  # noqa: F401

_PARIS = ZoneInfo("Europe/Paris")


def _paris(hour, minute):
    return datetime(2026, 6, 15, hour, minute, tzinfo=_PARIS)


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


def _hb(active="halftrend_ema_v1", equity=10250.5, ts=1234567890.0, algo_trading=True):
    position = types.SimpleNamespace(ticket=1, direction="BUY", lots=0.1,
                                     open_price=2400.0, sl=2390.0, profit=12.5)
    hb = types.SimpleNamespace(
        equity=equity, balance=10000.0, floating_pl=12.5, positions=[position],
        kill_switch=False, hwm=10300.0, exposure_min=5, window_open=True,
        spread_points=25.0, active_strategy=active, algo_trading=algo_trading)
    return (ts, hb)


class FakeDb:
    def __init__(self, stats=None, trades=None, pnl=None, strat_pnl=None,
                 mode="auto", entry="adr", htf="off", e200="off", strategies=None):
        self._stats = stats or {"total": 0, "resolved": 0, "ai_correct_pct": 0.0,
                                "by_strategy": {}}
        self._trades = trades or []
        # realized_pnl() returns this fixed (total, count) for ANY since_ts —
        # tests assert the same figure appears for both Today and Week.
        self._pnl = pnl
        self._strat_pnl = strat_pnl or {}
        self._mode, self._entry = mode, entry
        self._htf, self._e200 = htf, e200
        self._strategies = strategies or []

    def stats(self):
        return self._stats

    def recent_trades(self, limit=10):
        return self._trades[:limit]

    def realized_pnl(self, since_ts):
        return self._pnl if self._pnl is not None else (0.0, 0)

    def strategy_pnl(self):
        return self._strat_pnl

    def exec_mode(self):
        return self._mode

    def entry_mode(self):
        return self._entry

    def htf_enforce(self):
        return self._htf

    def ema200_enforce(self):
        return self._e200

    def strategy_ids(self):
        return self._strategies


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
# market_session
# ---------------------------------------------------------------------------

def test_market_session_asian():
    assert market_session(_paris(8, 59)) == "Asian session"


def test_market_session_london_open_lower_edge():
    assert market_session(_paris(9, 0)) == "London open"


def test_market_session_london_open_upper_edge_rolls_to_morning():
    assert market_session(_paris(10, 0)) == "London morning"


def test_market_session_overlap_us_data_window_before_1530():
    assert market_session(_paris(15, 29)) == "London+NY overlap · US data window"


def test_market_session_overlap_at_1530_boundary():
    assert market_session(_paris(15, 30)) == "London+NY overlap"


def test_market_session_new_york_afternoon():
    assert market_session(_paris(19, 0)) == "New York afternoon"


def test_market_session_late_new_york():
    assert market_session(_paris(21, 0)) == "Late New York"


def test_market_session_ny_close_pre_rollover():
    assert market_session(_paris(22, 30)) == "NY close / pre-rollover"


def test_market_session_rollover_thin_market_after_2300():
    assert market_session(_paris(23, 30)) == "Rollover — thin market"


def test_market_session_rollover_thin_market_before_0100():
    assert market_session(_paris(0, 30)) == "Rollover — thin market"


def test_market_session_converts_non_paris_tz_to_paris_local():
    # 07:00 UTC in summer (Paris = UTC+2) is 09:00 Paris local -> London open.
    utc_dt = datetime(2026, 6, 15, 7, 0, tzinfo=ZoneInfo("UTC"))
    assert market_session(utc_dt) == "London open"


def test_market_session_default_now_returns_a_string():
    assert isinstance(market_session(), str)


# ---------------------------------------------------------------------------
# format_proposal — session line on entry only
# ---------------------------------------------------------------------------

class _FakeAnalyzeResp:
    def __init__(self):
        self.direction = "long"
        self.confidence = 0.72
        self.verdict = "confirm"
        self.ai_available = True
        self.regime = "trend"


def test_format_proposal_entry_starts_with_session_line():
    text = format_proposal("entry", "BUY", 2400.0, _FakeAnalyzeResp())
    assert text.splitlines()[0].startswith("🕒 ")
    assert "📥 Entry proposal" in text


def test_format_proposal_exit_has_no_session_line():
    text = format_proposal("exit", "SELL", 2400.0, _FakeAnalyzeResp())
    assert not text.splitlines()[0].startswith("🕒 ")
    assert text.splitlines()[0].startswith("📤 Exit proposal")


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

def test_status_no_heartbeat_yet():
    app = _app(latest_heartbeat=None)
    reply = handle_command("/status", app)
    assert reply is not None
    assert "no heartbeat yet" in reply
    assert "EA: 🔴 never connected" in reply


def test_status_with_heartbeat_contains_equity_and_strategy():
    app = _app(latest_heartbeat=_hb(active="halftrend_ema_v1", equity=10250.5))
    reply = handle_command("/status", app)
    assert "10250.5" in reply
    assert "halftrend_ema_v1" in reply


def test_status_ea_connection_fresh_heartbeat_shows_connected():
    app = _app(latest_heartbeat=_hb(ts=time.time() - 5))
    reply = handle_command("/status", app)
    assert reply.splitlines()[1].startswith("EA: 🟢 connected")
    assert "5s ago" in reply.splitlines()[1]


def test_status_ea_connection_stale_heartbeat_shows_disconnected():
    app = _app(latest_heartbeat=_hb(ts=time.time() - 300))
    reply = handle_command("/status", app)
    assert reply.splitlines()[1].startswith("EA: 🔴 disconnected")
    assert "5m ago" in reply.splitlines()[1]


def test_status_ea_connection_never_connected_when_no_heartbeat():
    app = _app(latest_heartbeat=None)
    reply = handle_command("/status", app)
    assert reply.splitlines()[1] == "EA: 🔴 never connected"


def test_status_first_line_is_market_session():
    app = _app(latest_heartbeat=None)
    reply = handle_command("/status", app)
    assert reply.splitlines()[0].startswith("🕒 ")


def test_status_shows_algo_trading_off_warning():
    app = _app(latest_heartbeat=_hb(algo_trading=False))
    reply = handle_command("/status", app)
    # line 0 session, 1 EA, 2 Mini app (added 2026-08-18), 3 the algo warning
    assert "⚠️ ALGO TRADING OFF — MT5 cannot execute trades" in reply.splitlines()[3]


def test_status_no_algo_trading_warning_when_on():
    app = _app(latest_heartbeat=_hb(algo_trading=True))
    reply = handle_command("/status", app)
    assert "ALGO TRADING OFF" not in reply


def test_bal_with_heartbeat_reports_balance_equity_floating():
    app = _app(latest_heartbeat=_hb())
    reply = handle_command("/bal", app)
    assert reply == "💰 Balance: $10000.00 | Equity: $10250.50 | Floating: +$12.50"


def test_bal_no_heartbeat_reports_placeholder():
    app = _app(latest_heartbeat=None)
    reply = handle_command("/bal", app)
    assert reply == "no EA heartbeat yet"


def test_bal_negative_floating_shows_minus_sign():
    hb = types.SimpleNamespace(equity=4331.90, balance=4302.24, floating_pl=-29.66)
    app = _app(latest_heartbeat=(time.time(), hb))
    reply = handle_command("/bal", app)
    assert reply == "💰 Balance: $4302.24 | Equity: $4331.90 | Floating: -$29.66"


def test_status_includes_today_and_week_pnl_when_db_supports_it():
    app = _app(latest_heartbeat=_hb(), db=FakeDb(pnl=(12.5, 3)))
    reply = handle_command("/status", app)
    assert "Today: +$12.50 (3 trades)" in reply
    assert "Week: +$12.50" in reply


def test_status_redacted_hides_pnl():
    app = _app(latest_heartbeat=_hb(), db=FakeDb(pnl=(12.5, 3)))
    reply = handle_command("/status", app, redacted=True)
    assert "Today:" not in reply


def test_bal_includes_today_and_week_pnl():
    app = _app(latest_heartbeat=_hb(), db=FakeDb(pnl=(-5.0, 2)))
    reply = handle_command("/bal", app)
    assert "Today: -$5.00 (2 trades)" in reply
    assert "Week: -$5.00" in reply


def test_bal_without_db_keeps_single_line():
    app = _app(latest_heartbeat=_hb())
    reply = handle_command("/bal", app)
    assert "Today" not in reply


def test_stats_includes_per_strategy_realized_pnl():
    db = FakeDb(stats={"total": 3, "resolved": 2, "ai_correct_pct": 50.0,
                       "by_strategy": {"halftrend_ema_v1": {
                           "signals": 3, "resolved": 2, "hit_pct": 66.7,
                           "avg_move": 1.2}}},
                strat_pnl={"halftrend_ema_v1": (102.0, 4)})
    app = _app(db=db)
    reply = handle_command("/stats", app)
    assert "+$102.00" in reply
    assert "4 trades" in reply


def test_config_shows_confirmation_gates():
    app = _app(latest_heartbeat=_hb(), db=FakeDb(htf="M15", e200="on"))
    reply = handle_command("/config", app)
    assert "HTF: M15" in reply
    assert "EMA200: on" in reply


def test_mode_marks_active_buttons():
    app = _app(db=FakeDb(mode="auto", entry="fixed"))
    text, keyboard = handle_command("/mode", app)
    rows = keyboard["inline_keyboard"]
    labels = {b["text"] for row in rows for b in row}
    assert any(l.startswith("● ") and "AUTO" in l for l in labels)
    assert any(l.startswith("● ") and "FIXED" in l for l in labels)
    assert not any(l.startswith("● ") and "MANUAL" in l for l in labels)


def test_strategy_lists_pending_switch():
    app = _app(latest_heartbeat=_hb(), pending_switch="boll_stochrsi",
               db=FakeDb(strategies=["halftrend_ema_v1", "boll_stochrsi"]))
    text, _ = handle_command("/strategy", app)
    assert "pending: boll_stochrsi" in text


def test_history_close_rows_get_direction_emoji_and_sum_line():
    db = FakeDb(trades=[
        {"id": 3, "ts": 1234567890, "event": "close", "strategy_id": "s",
         "direction": "BUY", "lots": 0.1, "price": 2400.0, "sl": 0,
         "reason": "tp", "ticket": 1, "screenshot_path": None,
         "profit": 10.5, "render_path": None},
        {"id": 2, "ts": 1234567890, "event": "close", "strategy_id": "s",
         "direction": "SELL", "lots": 0.1, "price": 2400.0, "sl": 0,
         "reason": "sl hit", "ticket": 2, "screenshot_path": None,
         "profit": -3.2, "render_path": None}])
    app = _app(db=db)
    reply = handle_command("/history", app)
    assert "🟢" in reply and "🔴" in reply
    assert "closed shown: +$7.30" in reply


def test_help_replies_with_pinned_reference():
    app = _app()
    reply = handle_command("/help", app)
    assert reply is not None
    assert "Command reference" in reply
    assert "/status" in reply


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


def test_every_registered_command_returns_something(client):
    """Regression guard for the COMMANDS registry: a typo'd dict entry (or a
    handler wired to the wrong key) would silently make handle_command()
    return None for a real command instead of raising -- this walks every
    entry and proves each one still produces a reply."""
    for cmd in COMMANDS:
        out = handle_command(cmd, client.app)
        assert out is not None, f"{cmd} returned None"


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
    # Stub the mini-app /healthz probe: these poller tests give the cycle
    # only 100 ms, while a probe against a port nothing answers on burns the
    # full 0.5 s urllib timeout (they used to pass by accident because an
    # unrelated local service answered on the old port). Threading is what
    # is under test here, not the chart probe.
    from app import telegram as tg
    monkeypatch.setattr(tg, "_miniapp_healthz", lambda: None)
    main_thread_name = threading.current_thread().name
    recorded = {"get_updates_thread": None, "send_message_thread": None,
                "sent": []}

    class StubClient:
        def __init__(self):
            self.chat_id = "555"  # matches settings.telegram_chat_id here too
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
    assert len(recorded["sent"]) == 1
    # status body: session / EA / Mini app (2026-08-18) / "no heartbeat yet"
    sent = recorded["sent"][0]
    assert "EA: 🔴 never connected" in sent and sent.endswith("no heartbeat yet")
    assert "Mini app:" in sent
    assert recorded["sent"][0].splitlines()[0].startswith("🕒 ")


def test_poller_filters_using_active_client_chat_id_not_settings(monkeypatch):
    """When Telegram credentials come from the profile (not .env), the
    poller must filter inbound commands using the *client's* chat_id, not
    settings.telegram_chat_id -- otherwise every profile-applied command is
    silently dropped. Regression for that bug: settings carries a stale/
    different chat id, and the active client carries the real one."""
    monkeypatch.setattr(app_main.settings, "telegram_chat_id", "999")  # stale .env value
    # Stub the mini-app /healthz probe: these poller tests give the cycle
    # only 100 ms, while a probe against a port nothing answers on burns the
    # full 0.5 s urllib timeout (they used to pass by accident because an
    # unrelated local service answered on the old port). Threading is what
    # is under test here, not the chart probe.
    from app import telegram as tg
    monkeypatch.setattr(tg, "_miniapp_healthz", lambda: None)
    recorded = {"sent": []}

    class StubClient:
        def __init__(self):
            self.chat_id = "555"  # the profile-applied chat id
            self.calls = 0

        def get_updates(self, offset):
            self.calls += 1
            if self.calls == 1:
                return [{"update_id": 1,
                        "message": {"text": "/status", "chat": {"id": 555}}}]
            return []

        def send_message(self, text):
            recorded["sent"].append(text)
            return {"ok": True}

    stub_app = types.SimpleNamespace(state=types.SimpleNamespace(
        telegram=StubClient(), latest_heartbeat=None, pending_switch=None,
        db=None))

    async def run():
        task = asyncio.ensure_future(app_main.telegram_poller(stub_app))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert len(recorded["sent"]) == 1
    # status body: session / EA / Mini app (2026-08-18) / "no heartbeat yet"
    sent = recorded["sent"][0]
    assert "EA: 🔴 never connected" in sent and sent.endswith("no heartbeat yet")
    assert "Mini app:" in sent
    assert recorded["sent"][0].splitlines()[0].startswith("🕒 ")


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
# format_pinned_help
# ---------------------------------------------------------------------------

def test_format_pinned_help_lists_commands_and_proposal_legend():
    text = format_pinned_help()
    for token in ("/status", "/bal", "/mode", "/strategy", "/config",
                  "/chart", "/stats", "/history", "/switch", "/channel",
                  "🟢 Take", "🔴 Skip", "Valid while the strategy holds"):
        assert token in text


def test_format_pinned_help_does_not_depend_on_heartbeat():
    """Static content -- calling it twice (no app/state involved at all)
    must produce identical text."""
    assert format_pinned_help() == format_pinned_help()


def test_pinned_help_and_command_registry_cannot_drift():
    """format_pinned_help() is generated from COMMANDS (plus _PINNED_EXTRA
    for the one command -- /chart -- that bypasses handle_command entirely,
    see its docstring in telegram.py). This is the whole point of the
    registry: a command can no longer exist without being documented, or be
    documented without existing, because both come from the same table."""
    listed = {line.split()[0] for line in format_pinned_help().splitlines()
              if line.startswith("/")}
    registered = set(COMMANDS)
    known_external = {line.split()[0] for lines in _PINNED_EXTRA.values()
                      for line in lines if line.startswith("/")}
    # Every registered command is documented.
    assert registered <= listed
    # Nothing is documented that isn't either dispatched via COMMANDS or a
    # known, explained exception (_PINNED_EXTRA).
    assert listed == registered | known_external


# ---------------------------------------------------------------------------
# pinned_tick
# ---------------------------------------------------------------------------

def test_pinned_tick_creates_pins_and_stores_version(tmp_path):
    db = SignalDb(str(tmp_path / "pin.db"))
    app = _app(db=db)
    ft = FakeTransport(result={"ok": True, "result": {"message_id": 999}})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    methods = [c[0] for c in ft.calls]
    assert methods == ["sendMessage", "pinChatMessage"]
    assert db.get_kv("pinned_message_id") == "999"
    assert db.get_kv("pinned_help_version") == PINNED_HELP_VERSION
    pin_payload = ft.calls[1][1]
    assert pin_payload["message_id"] == 999
    sent_text = ft.calls[0][1]["text"]
    assert sent_text == format_pinned_help()


def test_pinned_tick_noop_without_heartbeat(tmp_path):
    """The pinned message is static command reference, not live status --
    it must be created/maintained even with no heartbeat ever received."""
    db = SignalDb(str(tmp_path / "pin_noheartbeat.db"))
    app = _app(latest_heartbeat=None, db=db)
    ft = FakeTransport(result={"ok": True, "result": {"message_id": 42}})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert [c[0] for c in ft.calls] == ["sendMessage", "pinChatMessage"]
    assert db.get_kv("pinned_message_id") == "42"


def test_pinned_tick_noop_when_pinned_and_version_matches(tmp_path):
    """Once the pin exists and its stored version is current, subsequent
    ticks make no Telegram calls at all -- content is static."""
    db = SignalDb(str(tmp_path / "pin_match.db"))
    db.set_kv("pinned_message_id", "999")
    db.set_kv("pinned_help_version", PINNED_HELP_VERSION)
    app = _app(db=db)
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert ft.calls == []


def test_pinned_tick_edits_when_stored_version_differs(tmp_path):
    """A version bump (or an upgrade from before pinned_help_version
    existed, i.e. no stored version at all) triggers a rewrite."""
    db = SignalDb(str(tmp_path / "pin_stale_version.db"))
    db.set_kv("pinned_message_id", "999")
    db.set_kv("pinned_help_version", "0")
    app = _app(db=db)
    ft = FakeTransport(result={"ok": True})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    methods = [c[0] for c in ft.calls]
    # Re-pin after the edit: the message may have been manually unpinned,
    # and once the version matches again pinned_tick no-ops forever — the
    # version-bump edit is the only chance to restore the pin.
    assert methods == ["editMessageText", "pinChatMessage"]
    payload = ft.calls[0][1]
    assert payload["message_id"] == 999
    assert payload["text"] == format_pinned_help()
    assert ft.calls[1][1]["message_id"] == 999
    assert db.get_kv("pinned_help_version") == PINNED_HELP_VERSION


def test_pinned_tick_self_heals_when_edit_fails(tmp_path):
    """If the pinned message was deleted server-side, editMessageText comes
    back None/error. That tick must clear the stale kv id (not retry it
    forever); the *next* tick then falls through to create+pin again and
    stores the new id. Version is left stale (not "0") so the next tick
    still takes the edit-worthy path once a new message exists."""
    db = SignalDb(str(tmp_path / "pin_heal.db"))
    db.set_kv("pinned_message_id", "999")
    db.set_kv("pinned_help_version", "0")
    app = _app(db=db)
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
    assert db.get_kv("pinned_help_version") == PINNED_HELP_VERSION


def test_pinned_tick_self_heals_when_edit_returns_error(tmp_path):
    """Same self-heal path, but the transport returns an explicit error
    response (ok: False) rather than None -- e.g. Telegram's "message to
    edit not found"."""
    db = SignalDb(str(tmp_path / "pin_heal2.db"))
    db.set_kv("pinned_message_id", "999")
    db.set_kv("pinned_help_version", "0")
    app = _app(db=db)
    ft = FakeTransport(result={"ok": False, "description": "message not found"})
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert [c[0] for c in ft.calls] == ["editMessageText"]
    assert db.get_kv("pinned_message_id") in (None, "")


def test_pinned_tick_self_heals_when_stored_id_not_numeric(tmp_path):
    db = SignalDb(str(tmp_path / "pin_heal3.db"))
    db.set_kv("pinned_message_id", "not-a-number")
    app = _app(db=db)
    ft = FakeTransport()
    client = TelegramClient("tok", "555", transport=ft)

    pinned_tick(app, client)

    assert ft.calls == []
    assert db.get_kv("pinned_message_id") in (None, "")


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


# ---------------------------------------------------------------------------
# /mode, /strategy, /config commands + callback_query handling
# ---------------------------------------------------------------------------

def test_mode_command_returns_buttons(client):
    out = handle_command("/mode", client.app)
    assert isinstance(out, tuple)
    text, markup = out
    assert "manual" in text.lower()
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert set(datas) == {"mode:auto", "mode:manual", "tmode:adr", "tmode:fixed"}


def test_mode_callback_switches(client):
    edit, toast = handle_callback("mode:auto", client.app)
    assert client.app.state.db.exec_mode() == "auto"
    assert "auto" in (edit or "").lower() or "auto" in toast.lower()


def test_strategy_command_lists_known_strategies(client):
    # NB: /analyze only inserts into `signals` for non-NONE signals (see
    # app/main.py::analyze), so a real BUY/SELL is needed to seed strategy_ids().
    _post_signal(client, "BUY")
    out = handle_command("/strategy", client.app)
    text, markup = out
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert any(d.startswith("strat:") for d in datas)


def test_strategy_command_empty_signals_table_has_no_buttons(client):
    out = handle_command("/strategy", client.app)
    text, markup = out
    assert markup is None


def test_strategy_callback_sets_pending_switch(client):
    edit, toast = handle_callback("strat:boll_stochrsi_v1", client.app)
    assert client.app.state.pending_switch == "boll_stochrsi_v1"


def test_config_command_reports_mode_and_settings(client):
    out = handle_command("/config", client.app)
    text = out if isinstance(out, str) else out[0]
    assert "mode" in text.lower() and "strategy" in text.lower()


def test_proposal_callback_take_and_skip(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "BUY", "s", 1.0, None)
    edit, toast = handle_callback(f"prop:{pid}:take", client.app)
    assert client.app.state.db.get_proposal(pid)["status"] == "approved"
    pid2 = client.app.state.db.create_proposal("entry", "SELL", "s", 1.0, None)
    edit, toast = handle_callback(f"prop:{pid2}:skip", client.app)
    assert client.app.state.db.get_proposal(pid2)["status"] == "skipped"
    # acting on a decided proposal is a no-op with an informative toast
    edit, toast = handle_callback(f"prop:{pid}:take", client.app)
    assert "already" in toast.lower()
    assert edit is None


def test_proposal_callback_race_after_expiry_returns_already_toast(client, fake_tg, monkeypatch):
    """Guarded-transition race (I2): handle_callback reads the row as
    'pending', but between that read and the guarded UPDATE, a concurrent
    /analyze stance-expiry (or the /heartbeat TTL sweep) flips it first.
    The guarded UPDATE must lose gracefully and report the row's real
    current status instead of claiming "approved"."""
    db = client.app.state.db
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    real_set = db.set_proposal_status

    def racing_set(pid_, status, expected=None):
        if pid_ == pid and status == "approved" and expected == "pending":
            db.conn.execute("UPDATE proposals SET status='expired' WHERE id=?", (pid_,))
            db.conn.commit()
        return real_set(pid_, status, expected=expected)

    monkeypatch.setattr(db, "set_proposal_status", racing_set)
    edit, toast = handle_callback(f"prop:{pid}:take", client.app)
    assert edit is None
    assert toast == "already expired"
    assert db.get_proposal(pid)["status"] == "expired"   # the race's winner stands


def test_unknown_callback_returns_none_edit_and_unknown_toast(client):
    edit, toast = handle_callback("bogus:data", client.app)
    assert edit is None
    assert toast == "unknown"


def test_malformed_prop_callback_does_not_raise(client):
    edit, toast = handle_callback("prop:abc:take", client.app)
    assert (edit, toast) == (None, "unknown")
    edit, toast = handle_callback("prop::take", client.app)
    assert (edit, toast) == (None, "unknown")


# ---------------------------------------------------------------------------
# /status: "Mini app" line right under the EA line (owner request 2026-08-18)
# ---------------------------------------------------------------------------

def _status_lines(monkeypatch, healthz):
    """Run /status with the mini-app probe stubbed to return `healthz`
    (dict = reachable, None = unreachable)."""
    from app import telegram as tg
    monkeypatch.setattr(tg, "_miniapp_healthz", lambda: healthz)
    app = _app(latest_heartbeat=_hb(), db=FakeDb())
    app.state.pending_switch = None
    return handle_command("/status", app).splitlines()


def test_miniapp_healthz_url_follows_configured_port():
    """The /status probe must target MINIAPP_PORT, never a hard-coded 9001
    (incident 2026-08-19: the mini-app moved port and every hard-coded
    probe silently reported it down)."""
    from app import telegram as tg
    from app.config import settings
    assert tg._MINIAPP_HEALTHZ_URL == f"http://127.0.0.1:{settings.miniapp_port}/healthz"
    assert tg._miniapp_healthz_url().endswith(f":{settings.miniapp_port}/healthz")


def test_status_miniapp_line_follows_ea_line(monkeypatch):
    lines = _status_lines(monkeypatch, {"ok": True, "feed_age_s": 0.4, "uptime_s": 500})
    ea_idx = next(i for i, l in enumerate(lines) if l.startswith("EA:"))
    assert lines[ea_idx + 1].startswith("Mini app:")
    assert "🟢" in lines[ea_idx + 1] and "connected" in lines[ea_idx + 1]


def test_status_miniapp_stale_feed_is_yellow(monkeypatch):
    lines = _status_lines(monkeypatch, {"ok": True, "feed_age_s": 240.0, "uptime_s": 900})
    line = next(l for l in lines if l.startswith("Mini app:"))
    assert "🟡" in line and "no data" in line and "4m" in line


def test_status_miniapp_unreachable_is_red(monkeypatch):
    lines = _status_lines(monkeypatch, None)
    line = next(l for l in lines if l.startswith("Mini app:"))
    assert "🔴" in line and "down" in line


def test_status_miniapp_line_survives_redaction(monkeypatch):
    from app import telegram as tg
    monkeypatch.setattr(tg, "_miniapp_healthz", lambda: {"ok": True, "feed_age_s": 1.0, "uptime_s": 10})
    app = _app(latest_heartbeat=_hb(), db=FakeDb())
    app.state.pending_switch = None
    text = handle_command("/status", app, redacted=True)
    assert "Mini app: 🟢" in text     # infra state is not an account figure


# ------------------------------------------- brake awareness /status (2026-08-18)
def test_status_protection_line_shows_daily_loss_pct():
    ts, hb = _hb()
    hb.daily_loss_pct = 53.0
    hb.brake_reset = False
    reply = handle_command("/status", _app(latest_heartbeat=(ts, hb)))
    line = [l for l in reply.splitlines() if l.startswith("🛡 Protection armed")][0]
    assert line.endswith(" · daily loss 53%")
    assert "brake reset today" not in line


def test_status_protection_line_shows_brake_reset_and_survives_redaction():
    ts, hb = _hb()
    hb.daily_loss_pct = 12.4
    hb.brake_reset = True
    reply = handle_command("/status", _app(latest_heartbeat=(ts, hb)), redacted=True)
    line = [l for l in reply.splitlines() if l.startswith("🛡 Protection armed")][0]
    assert "drawdown" not in line                       # account figure: redacted
    assert line.endswith(" · daily loss 12% since reset")   # infra state: kept


def test_status_protection_line_unchanged_for_old_heartbeat():
    reply = handle_command("/status", _app(latest_heartbeat=_hb()))
    line = [l for l in reply.splitlines() if l.startswith("🛡 Protection armed")][0]
    assert "daily loss" not in line


def test_agree_command_offers_the_module_toggle(client):
    """/agree exposes the higher-timeframe module: off, or enforce on a TF."""
    from app.telegram import handle_command
    app = client.app
    app.state.db.set_htf_enforce("off")
    text, markup = handle_command("/agree", app)
    assert "CHECK ONLY" in text
    labels = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    for choice in ("agree:off", "agree:M15", "agree:M30", "agree:H1"):
        assert choice in labels
    # the current setting is marked
    marked = [b["text"] for row in markup["inline_keyboard"] for b in row
              if b["text"].startswith("●")]
    assert marked and "Off" in marked[0]


def test_agree_callback_sets_and_clears_enforcement(client):
    from app.telegram import handle_callback
    app = client.app
    reply, toast = handle_callback("agree:M15", app)
    assert app.state.db.htf_enforce() == "M15"
    assert "ENFORCING on M15" in reply and "choppy" in reply
    reply, toast = handle_callback("agree:off", app)
    assert app.state.db.htf_enforce() == "off"
    assert "CHECK ONLY" in reply
    assert "reported" in reply, "off must still promise the check is reported"


def test_heartbeat_carries_the_agree_setting(client):
    """The EA obeys the service, so the setting must ride every heartbeat."""
    client.app.state.db.set_htf_enforce("M15")
    body = client.post("/heartbeat", json={
        "equity": 10000.0, "balance": 10000.0, "floating_pl": 0.0}).json()
    assert body["htf_enforce"] == "M15"
    client.app.state.db.set_htf_enforce("off")
    body = client.post("/heartbeat", json={
        "equity": 10000.0, "balance": 10000.0, "floating_pl": 0.0}).json()
    assert body["htf_enforce"] == "off"


def test_agree_command_offers_the_ema200_toggle_too(client):
    """/agree is the single 'what confirms a trade' menu -- it must expose
    the EMA200 module alongside the (unchanged) HTF one, default off."""
    from app.telegram import handle_command
    app = client.app
    app.state.db.set_htf_enforce("off")
    app.state.db.set_ema200_enforce("off")
    text, markup = handle_command("/agree", app)
    assert "EMA-200" in text and "CHECK ONLY" in text
    labels = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "e200:off" in labels and "e200:on" in labels
    # the existing HTF buttons are unchanged
    for choice in ("agree:off", "agree:M15", "agree:M30", "agree:H1"):
        assert choice in labels


def test_e200_callback_sets_and_clears_enforcement(client):
    from app.telegram import handle_callback
    app = client.app
    reply, toast = handle_callback("e200:on", app)
    assert app.state.db.ema200_enforce() == "on"
    assert "ENFORCING" in reply
    reply, toast = handle_callback("e200:off", app)
    assert app.state.db.ema200_enforce() == "off"
    assert "CHECK ONLY" in reply
    assert "reported" in reply, "off must still promise the check is reported"
    # unrelated to the HTF toggle
    assert app.state.db.htf_enforce() == "off"


def test_heartbeat_carries_the_ema200_agree_setting(client):
    client.app.state.db.set_ema200_enforce("on")
    body = client.post("/heartbeat", json={
        "equity": 10000.0, "balance": 10000.0, "floating_pl": 0.0}).json()
    assert body["ema200_enforce"] == "on"
    client.app.state.db.set_ema200_enforce("off")
    body = client.post("/heartbeat", json={
        "equity": 10000.0, "balance": 10000.0, "floating_pl": 0.0}).json()
    assert body["ema200_enforce"] == "off"
