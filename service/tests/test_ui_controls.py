"""Contract tests for the dashboard control endpoints (/api/mode,
/api/proposal/{pid}, /api/close-all) and the extended /api/state."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui_controls.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _db(client):
    return client.app.state.db


# ---------------------------------------------------------------- /api/mode
def test_mode_set_and_reflected_in_state(client):
    r = client.post("/api/mode", json={"mode": "auto"})
    assert r.status_code == 200 and r.json() == {"mode": "auto"}
    assert client.get("/api/state").json()["mode"] == "auto"
    r = client.post("/api/mode", json={"mode": "manual"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["mode"] == "manual"


def test_mode_invalid_rejected(client):
    assert client.post("/api/mode", json={"mode": "yolo"}).status_code == 400
    assert client.post("/api/mode", json={}).status_code == 400


# ---------------------------------------------------- /api/proposal/{pid}
def test_proposal_take_and_skip(client):
    db = _db(client)
    pid = db.create_proposal("entry", "BUY", "halftrend_ema_v1", 2400.0, None)
    r = client.post(f"/api/proposal/{pid}", json={"action": "take"})
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "approved"}
    assert db.get_proposal(pid)["status"] == "approved"

    pid2 = db.create_proposal("entry", "SELL", "halftrend_ema_v1", 2400.0, None)
    r = client.post(f"/api/proposal/{pid2}", json={"action": "skip"})
    assert r.json() == {"ok": True, "status": "skipped"}


def test_proposal_decided_race_reports_winner(client):
    db = _db(client)
    pid = db.create_proposal("entry", "BUY", "s", 2400.0, None)
    db.set_proposal_status(pid, "expired")  # e.g. stance changed first
    r = client.post(f"/api/proposal/{pid}", json={"action": "take"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "status": "expired"}


def test_proposal_bad_input(client):
    db = _db(client)
    pid = db.create_proposal("entry", "BUY", "s", 2400.0, None)
    assert client.post(f"/api/proposal/{pid}", json={"action": "yes"}).status_code == 400
    assert client.post("/api/proposal/999999", json={"action": "take"}).status_code == 404


# ------------------------------------------------------- /api/close-all
def test_close_all_creates_approved_exit(client):
    r = client.post("/api/close-all")
    assert r.status_code == 200 and r.json()["ok"] is True
    row = _db(client).get_proposal(r.json()["proposal_id"])
    assert row["kind"] == "exit" and row["status"] == "approved"


def test_close_all_conflict_while_in_flight(client):
    assert client.post("/api/close-all").status_code == 200
    assert client.post("/api/close-all").status_code == 409


def test_close_all_dispatched_via_heartbeat(client):
    pid = client.post("/api/close-all").json()["proposal_id"]
    hb = {"equity": 200.0, "balance": 200.0, "floating_pl": 0.0,
          "positions": [], "kill_switch": False, "hwm": 200.0,
          "exposure_min": 0, "window_open": True, "spread_points": 15.0,
          "active_strategy": "halftrend_ema_v1"}
    r = client.post("/heartbeat", json=hb)
    assert r.status_code == 200
    cmd = r.json()["command"]
    assert cmd == {"cmd": "close_all", "proposal_id": pid}


# ---------------------------------------------------------- /api/state
def test_state_carries_mode_and_proposal(client):
    s = client.get("/api/state").json()
    assert s["mode"] in ("auto", "manual") and s["proposal"] is None
    pid = _db(client).create_proposal("entry", "BUY", "s", 2400.0, None)
    s = client.get("/api/state").json()
    assert s["proposal"]["id"] == pid and s["proposal"]["status"] == "pending"
