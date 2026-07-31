import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tr.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "screenshots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _trade(**overrides):
    ev = {"event": "open", "strategy_id": "halftrend_ema_v1", "direction": "BUY",
          "lots": 0.1, "price": 2400.0, "sl": 2390.0, "reason": "signal confirmed",
          "ticket": 12345}
    ev.update(overrides)
    return ev


def test_trade_event_returns_id_and_persists_row(client):
    r = client.post("/trade-event", json=_trade())
    assert r.status_code == 200
    trade_id = r.json()["id"]
    assert isinstance(trade_id, int)

    from app import main
    row = main.app.state.db.conn.execute(
        "SELECT event, direction, lots, price, reason FROM trades WHERE id=?",
        (trade_id,)).fetchone()
    assert row == ("open", "BUY", 0.1, 2400.0, "signal confirmed")


def test_screenshot_upload_saves_file_and_records_path(client):
    from app import main

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200
    saved_path = r.json()["saved"]
    assert os.path.exists(saved_path)
    with open(saved_path, "rb") as f:
        assert f.read() == b"\x89PNG fake"

    row = main.app.state.db.conn.execute(
        "SELECT screenshot_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row[0] == saved_path


def test_ui_trades_returns_recent_rows(client):
    client.post("/trade-event", json=_trade(event="open"))
    client.post("/trade-event", json=_trade(event="close"))
    body = client.get("/ui/trades").json()
    assert len(body["trades"]) == 2
    assert body["trades"][0]["event"] == "close"   # newest first


def test_ui_screenshot_serves_bytes(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    r = client.get(f"/ui/screenshot/{trade_id}")
    assert r.status_code == 200
    assert r.content == b"\x89PNG fake"


def test_ui_screenshot_404_when_missing(client):
    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.get(f"/ui/screenshot/{trade_id}")
    assert r.status_code == 404
    assert r.json()["detail"]


def test_screenshot_retention_keeps_newest_500(client, tmp_path):
    shot_dir = tmp_path / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    for i in range(502):
        f = shot_dir / f"fake_{i}.png"
        f.write_bytes(b"x")
        # oldest first: increasing mtimes so later files are "newer"
        os.utime(f, (now - 1000 + i, now - 1000 + i))

    trade_id = client.post("/trade-event", json=_trade()).json()["id"]
    r = client.post(f"/screenshot?event={trade_id}", content=b"\x89PNG fake")
    assert r.status_code == 200

    remaining = list(shot_dir.glob("*.png"))
    assert len(remaining) == 500
    # the newly uploaded screenshot (newest) must survive
    assert (shot_dir / f"{trade_id}.png") in remaining
    # oldest fakes should have been pruned
    assert not (shot_dir / "fake_0.png").exists()
    assert (shot_dir / "fake_501.png").exists()
