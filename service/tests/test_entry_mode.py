"""Entry mode (ADR/FIXED): kv round-trip, /mode four buttons, tmode
callbacks, heartbeat contract, trades column."""
import time
import types

import pytest

from app.db import SignalDb
from app.telegram import handle_callback, handle_command


def _db(tmp_path):
    return SignalDb(str(tmp_path / "em.db"))


def _app(db):
    hb = types.SimpleNamespace(
        equity=1000.0, balance=1000.0, floating_pl=0.0, positions=[],
        kill_switch=False, hwm=0.0, exposure_min=0, window_open=True,
        spread_points=0.0, active_strategy="halftrend_ema_v1",
        algo_trading=True)
    return types.SimpleNamespace(state=types.SimpleNamespace(
        db=db, latest_heartbeat=(time.time(), hb), pending_switch=None,
        pending_channel=None))


def test_entry_mode_kv_roundtrip_defaults_adr(tmp_path):
    db = _db(tmp_path)
    assert db.entry_mode() == "adr"
    db.set_entry_mode("fixed")
    assert db.entry_mode() == "fixed"
    with pytest.raises(ValueError):
        db.set_entry_mode("yolo")


def test_mode_command_shows_both_states_and_six_buttons(tmp_path):
    app = _app(_db(tmp_path))
    text, keyboard = handle_command("/mode", app)
    assert "Execution mode" in text and "Entry mode" in text
    flat = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert flat == ["mode:auto", "mode:manual", "tmode:adr", "tmode:fixed",
                    "strat:halftrend_ema_v1", "strat:halftrend_m15_v1"]


def test_tmode_callback_sets_kv_and_names_next_trade(tmp_path):
    db = _db(tmp_path)
    app = _app(db)
    edit_text, toast = handle_callback("tmode:fixed", app)
    assert db.entry_mode() == "fixed"
    assert "FIXED" in edit_text and "next" in edit_text.lower()
    edit_text, _ = handle_callback("tmode:adr", app)
    assert db.entry_mode() == "adr"


def test_tmode_callback_rejects_unknown_value(tmp_path):
    db = _db(tmp_path)
    _, toast = handle_callback("tmode:yolo", _app(db))
    assert db.entry_mode() == "adr"


def test_config_shows_entry_mode(tmp_path):
    app = _app(_db(tmp_path))
    assert "entry mode: adr" in handle_command("/config", app)


def test_heartbeat_response_carries_entry_mode(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "hb_em.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0}
    with TestClient(main.app) as client:
        assert client.post("/heartbeat", json=hb).json()["entry_mode"] == "adr"
        main.app.state.db.set_entry_mode("fixed")
        assert client.post("/heartbeat", json=hb).json()["entry_mode"] == "fixed"
        # old EA payload (no entry_mode field) still validates
        assert client.post("/heartbeat", json=hb).status_code == 200


def test_trades_table_stores_entry_mode(tmp_path):
    db = _db(tmp_path)
    tid = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.05,
                           "price": 4000.0, "entry_mode": "fixed"})
    row = db.conn.execute(
        "SELECT entry_mode FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == "fixed"
    tid2 = db.insert_trade({"event": "open", "direction": "BUY", "lots": 0.05,
                            "price": 4000.0})
    assert db.conn.execute("SELECT entry_mode FROM trades WHERE id=?",
                           (tid2,)).fetchone()[0] == ""


# ---------------------------------------------------------------------------
# FIXED-mode target alert: /notify with an EXIT button
# ---------------------------------------------------------------------------

def _notify_client(tmp_path, monkeypatch, positions):
    import importlib

    from fastapi.testclient import TestClient
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "notify_em.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    client = TestClient(main.app)
    client.__enter__()

    class FT:
        def __init__(self):
            self.calls = []

        def __call__(self, method, payload, files=None):
            self.calls.append((method, payload, files))
            return {"ok": True, "result": {"message_id": 1}}

    from app.telegram import TelegramClient
    ft = FT()
    main.app.state.telegram = TelegramClient("tok", "555", transport=ft)
    hb = {"equity": 1.0, "balance": 1.0, "floating_pl": 0.0,
          "positions": positions}
    client.post("/heartbeat", json=hb)
    ft.calls.clear()
    return client, ft, main


_POS = [{"ticket": 1, "direction": "SELL", "lots": 0.05,
         "open_price": 4388.0, "sl": 4400.0, "profit": 95.0}]


def test_notify_exit_button_attaches_exit_keyboard(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, _POS)
    r = client.post("/notify", json={"text": "🎯 target hit", "exit_button": True})
    assert r.status_code == 200 and r.json()["ok"] is True
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "🎯 target hit"]   # exclude ticker sends
    assert len(owner) == 1
    markup = owner[0][1].get("reply_markup")
    assert markup is not None
    flat = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert any(cb.startswith("exitnow:") for cb in flat)


def test_notify_exit_button_skipped_when_flat(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, [])
    client.post("/notify", json={"text": "🎯 target hit", "exit_button": True})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "🎯 target hit"]
    assert len(owner) == 1
    assert "reply_markup" not in owner[0][1]


def test_notify_default_has_no_button(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, _POS)
    client.post("/notify", json={"text": "plain notice"})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "plain notice"]
    assert len(owner) == 1
    assert "reply_markup" not in owner[0][1]


# ------------------------------------------- button selector (2026-08-18)
def test_notify_button_reset_brake_attaches_reset_keyboard(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, [])   # flat is fine
    client.post("/notify", json={"text": "⚠️ Daily loss brake at 70%",
                                 "button": "reset_brake"})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "⚠️ Daily loss brake at 70%"]
    assert len(owner) == 1
    markup = owner[0][1]["reply_markup"]
    assert markup == {"inline_keyboard": [[{"text": "🔓 Reset brake for today",
                                            "callback_data": "brakereset:1"}]]}


def test_notify_button_exit_selector_attaches_exit_keyboard(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, _POS)
    client.post("/notify", json={"text": "🎯 target hit", "button": "exit"})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "🎯 target hit"]
    flat = [b["callback_data"] for row in owner[0][1]["reply_markup"]["inline_keyboard"]
            for b in row]
    assert any(cb.startswith("exitnow:") for cb in flat)


def test_notify_button_exit_selector_skipped_when_flat(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, [])
    client.post("/notify", json={"text": "🎯 target hit", "button": "exit"})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555"
             and c[1].get("text") == "🎯 target hit"]
    assert len(owner) == 1 and "reply_markup" not in owner[0][1]


def test_notify_button_empty_has_no_button(tmp_path, monkeypatch):
    client, ft, _ = _notify_client(tmp_path, monkeypatch, _POS)
    client.post("/notify", json={"text": "plain", "button": ""})
    owner = [c for c in ft.calls if c[0] == "sendMessage"
             and c[1].get("chat_id") == "555" and c[1].get("text") == "plain"]
    assert len(owner) == 1 and "reply_markup" not in owner[0][1]
