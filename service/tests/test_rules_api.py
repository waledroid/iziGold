"""Task 4: trade agree-flags in recent_trades; rule state in /ui/state;
new POST /ui/rules."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_recent_trades_carries_agree_flags(tmp_path):
    from app.db import SignalDb
    db = SignalDb(str(tmp_path / "t.db"))
    db.insert_trade({"event": "open", "strategy_id": "halftrend_ema_v1",
                     "direction": "BUY", "lots": 0.1, "price": 4000.0,
                     "entry_mode": "adr", "htf_agree": 1, "ema200_agree": 0})
    row = db.recent_trades(1)[0]
    assert row["htf_agree"] == 1
    assert row["ema200_agree"] == 0
    assert row["entry_mode"] == "adr"


def test_state_exposes_rules(client):
    s = client.get("/ui/state").json()
    assert s["rules"] == {"entry_mode": "adr", "htf_enforce": "off",
                          "ema200_enforce": "off"}


def test_post_rules_roundtrip(client):
    r = client.post("/ui/rules", json={"key": "htf_enforce", "value": "M15"})
    assert r.status_code == 200 and r.json() == {"htf_enforce": "M15"}
    assert client.get("/ui/state").json()["rules"]["htf_enforce"] == "M15"
    assert client.post("/ui/rules",
                       json={"key": "ema200_enforce", "value": "on"}).status_code == 200
    assert client.post("/ui/rules",
                       json={"key": "entry_mode", "value": "fixed"}).status_code == 200


def test_post_rules_rejects_bad_input(client):
    assert client.post("/ui/rules",
                       json={"key": "htf_enforce", "value": "H4"}).status_code == 400
    assert client.post("/ui/rules",
                       json={"key": "exec_mode", "value": "auto"}).status_code == 400
    assert client.post("/ui/rules", json={}).status_code == 400
