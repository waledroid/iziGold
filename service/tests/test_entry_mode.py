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


def test_mode_command_shows_both_states_and_four_buttons(tmp_path):
    app = _app(_db(tmp_path))
    text, keyboard = handle_command("/mode", app)
    assert "Execution mode" in text and "Entry mode" in text
    flat = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert flat == ["mode:auto", "mode:manual", "tmode:adr", "tmode:fixed"]


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
