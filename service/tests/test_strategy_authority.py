"""Strategy lane authority: the owner's last explicit choice (Telegram
strat: button / dashboard /api/switch) is persisted in kv and re-asserted
on every heartbeat, so an EA re-init (recompile, restart, chart change —
which resets active to the ActiveStrategy INPUT) can revert the lane for
at most one bar before being pushed back."""
import importlib

import pytest
from fastapi.testclient import TestClient

from app.telegram import handle_callback


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _hb(client, active):
    r = client.post("/heartbeat", json={
        "equity": 1000.0, "balance": 1000.0, "floating_pl": 0.0,
        "active_strategy": active})
    assert r.status_code == 200
    return r.json()


def test_reverted_ea_is_pushed_back_to_stored_lane(client):
    client.app.state.db.set_kv("active_strategy", "halftrend_m15_v1")
    # EA re-initialized and reverted to its input default:
    resp = _hb(client, "halftrend_ema_v1")
    assert resp["switch_to"] == "halftrend_m15_v1"
    # and keeps being pushed until the EA complies
    resp = _hb(client, "halftrend_ema_v1")
    assert resp["switch_to"] == "halftrend_m15_v1"


def test_matching_lane_gets_no_switch(client):
    client.app.state.db.set_kv("active_strategy", "halftrend_m15_v1")
    resp = _hb(client, "halftrend_m15_v1")
    assert resp["switch_to"] is None


def test_no_stored_choice_means_ea_input_rules(client):
    resp = _hb(client, "halftrend_ema_v1")
    assert resp["switch_to"] is None


def test_api_switch_persists_choice(client):
    r = client.post("/api/switch", json={"strategy_id": "halftrend_m15_v1"})
    assert r.json()["pending"] == "halftrend_m15_v1"
    assert client.app.state.db.get_kv("active_strategy") == "halftrend_m15_v1"
    # clearing hands authority back to the EA input
    client.post("/api/switch", json={"strategy_id": ""})
    assert not client.app.state.db.get_kv("active_strategy")


def test_strat_button_persists_choice(client):
    edit, toast = handle_callback("strat:halftrend_m15_v1", client.app)
    assert client.app.state.db.get_kv("active_strategy") == "halftrend_m15_v1"


def test_pending_display_follows_stored_choice(client):
    client.app.state.db.set_kv("active_strategy", "halftrend_m15_v1")
    _hb(client, "halftrend_ema_v1")
    assert client.app.state.pending_switch == "halftrend_m15_v1"
    _hb(client, "halftrend_m15_v1")     # EA complied
    assert client.app.state.pending_switch is None
