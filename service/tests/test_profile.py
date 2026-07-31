import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from app.db import SignalDb, profile_completion


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    assert profile_completion(row) == 25            # 3 of 12
    assert profile_completion(db.save_profile({"name": ""})) == 17  # empty string unsets → 2/12


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
    assert body["completion_pct"] == 17          # 2 of 12


def test_telegram_live_apply(client, monkeypatch):
    from app import main, telegram
    # No-op transport: the poller/pinned-editor tasks spawned by
    # _apply_telegram must never touch api.telegram.org during tests. This
    # replaces the module-level default transport factory so every
    # TelegramClient built during this test (real token/chat_id, fake wire)
    # returns instantly instead of long-polling the real network.
    monkeypatch.setattr(telegram, "_default_transport",
                        lambda token: (lambda *a, **k: None))
    assert main.app.state.telegram is None       # test env has no credentials
    client.post("/ui/profile", json={"telegram_bot_token": "T", "telegram_chat_id": "C"})
    assert main.app.state.telegram is not None
    assert main.app.state.telegram_task is not None
    client.post("/ui/profile", json={"telegram_bot_token": "", "telegram_chat_id": ""})
    assert main.app.state.telegram is None       # cleared back to .env fallback (empty)


def test_telegram_token_masked_on_get(client, monkeypatch):
    from app import telegram
    monkeypatch.setattr(telegram, "_default_transport",
                        lambda token: (lambda *a, **k: None))
    client.post("/ui/profile",
               json={"telegram_bot_token": "123456:ABC-DEF7890", "telegram_chat_id": "42"})
    profile = client.get("/ui/profile").json()["profile"]
    token = profile["telegram_bot_token"]
    assert "123456:ABC-DEF7890" not in token
    assert token.endswith("7890") and token.startswith("•")
    assert profile["telegram_chat_id"] == "42"   # chat id is not a secret, stays plain


def test_masked_token_post_does_not_overwrite_stored_token(client, monkeypatch):
    from app import main, telegram
    monkeypatch.setattr(telegram, "_default_transport",
                        lambda token: (lambda *a, **k: None))
    client.post("/ui/profile",
               json={"telegram_bot_token": "SECRETTOKEN1", "telegram_chat_id": "C"})
    masked = client.get("/ui/profile").json()["profile"]["telegram_bot_token"]
    assert masked.startswith("•")
    # Simulate the onboarding page re-submitting the form unchanged: the
    # masked placeholder round-trips back in the POST body. The server-side
    # guard must ignore it rather than clobbering the real stored token.
    client.post("/ui/profile", json={"telegram_bot_token": masked, "name": "Wale"})
    stored = main.app.state.db.get_profile()["telegram_bot_token"]
    assert stored == "SECRETTOKEN1"


@pytest.mark.anyio
async def test_apply_telegram_concurrent_calls_leave_no_orphan_tasks(monkeypatch, tmp_path):
    """Two overlapping _apply_telegram calls (e.g. two rapid POST
    /ui/profile requests) must be serialized by app.state.telegram_lock:
    only one telegram_task/pinned_task pair should be live afterwards, and
    whichever pair got superseded must be actually cancelled -- not leaked
    as an orphan background task."""
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "concurrent.db"))
    from app import config, main, telegram
    importlib.reload(config)
    importlib.reload(main)
    monkeypatch.setattr(telegram, "_default_transport",
                        lambda token: (lambda *a, **k: None))

    async with main.app.router.lifespan_context(main.app):
        main.app.state.db.save_profile(
            {"telegram_bot_token": "T", "telegram_chat_id": "C"})

        created = []
        real_create_task = asyncio.create_task

        def counting_create_task(coro, *a, **kw):
            task = real_create_task(coro, *a, **kw)
            created.append(task)
            return task

        monkeypatch.setattr(asyncio, "create_task", counting_create_task)

        await asyncio.gather(main._apply_telegram(main.app),
                             main._apply_telegram(main.app))

        live = {main.app.state.telegram_task, main.app.state.pinned_task}
        assert None not in live
        assert len(live) == 2                    # exactly one live pair

        orphans = [t for t in created if t not in live]
        assert orphans                            # one call really did supersede the other
        await asyncio.sleep(0)                    # let cancellation land
        for task in orphans:
            assert task.cancelled() or task.done()  # no leaked running task


def test_onboarding_page_served(client):
    r = client.get("/ui/onboarding")
    assert r.status_code == 200
    for needle in ("Identity", "Telegram", "Risk profile", "Account", "/ui/profile"):
        assert needle in r.text
