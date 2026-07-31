import importlib

import pytest
from fastapi.testclient import TestClient

from app.db import SignalDb, profile_completion


def test_profile_absent_then_created(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.get_profile() is None
    row = db.save_profile({})               # Skip: creates empty row
    assert row["id"] == 1 and db.get_profile() is not None


def test_partial_update_only_touches_sent_fields(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    db.save_profile({"name": "Wale", "email": "w@x.com"})
    row = db.save_profile({"phone": "+33 6 00"})
    assert row["name"] == "Wale" and row["email"] == "w@x.com"
    assert row["phone"] == "+33 6 00"
    assert db.save_profile({"bogus_key": 1})["name"] == "Wale"  # unknown ignored


def test_risk_ack_ts_set_once(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.save_profile({})["risk_ack_ts"] is None
    first = db.save_profile({"risk_ack": 1})["risk_ack_ts"]
    assert first is not None
    assert db.save_profile({"risk_ack": 1})["risk_ack_ts"] == first


def test_completion_percent(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert profile_completion(None) == 0
    assert profile_completion(db.save_profile({})) == 0
    row = db.save_profile({"name": "W", "email": "e", "phone": "p"})
    assert profile_completion(row) == 20            # 3 of 15
    assert profile_completion(db.save_profile({"name": ""})) == 13  # empty string unsets → 2/15


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ob.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_ui_redirects_once(client):
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/onboarding"
    client.post("/ui/profile", json={})          # Skip creates the row
    assert client.get("/ui", follow_redirects=False).status_code == 200


def test_profile_roundtrip_and_completion(client):
    assert client.get("/ui/profile").json() == {"profile": None, "completion_pct": 0}
    body = client.post("/ui/profile", json={"name": "Wale", "risk_ack": 1}).json()
    assert body["profile"]["name"] == "Wale"
    assert body["completion_pct"] == 13          # 2 of 15


def test_telegram_live_apply(client):
    from app import main
    assert main.app.state.telegram is None       # test env has no credentials
    client.post("/ui/profile", json={"telegram_bot_token": "T", "telegram_chat_id": "C"})
    assert main.app.state.telegram is not None
    assert main.app.state.telegram_task is not None
    client.post("/ui/profile", json={"telegram_bot_token": "", "telegram_chat_id": ""})
    assert main.app.state.telegram is None       # cleared back to .env fallback (empty)


def test_onboarding_page_served(client):
    r = client.get("/ui/onboarding")
    assert r.status_code == 200
    for needle in ("Identity", "Telegram", "Risk profile", "Account", "/ui/profile"):
        assert needle in r.text
