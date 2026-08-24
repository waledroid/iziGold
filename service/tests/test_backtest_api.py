"""Task 7: /api/backtest run lifecycle endpoints (range, start, status,
runs, report)."""
import importlib
import json

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


def seed_candles(client, n=500):
    client.app.state.db.upsert_candles("XAUUSD", "M5", [
        {"t": 1700000000 + 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}
        for i in range(n)])


def test_range_lists_strategies(client):
    seed_candles(client)
    r = client.get("/api/backtest/range").json()
    assert r["symbol"] == "XAUUSD"
    assert r["range"]["count"] == 500
    ids = {s["id"]: s["supported"] for s in r["strategies"]}
    assert ids == {"halftrend_ema_v1": True, "halftrend_m15_v1": True,
                   "boll_stochrsi": False}


def test_start_validates(client, monkeypatch):
    seed_candles(client)
    bad = [
        ({"strategy": "boll_stochrsi", "start": "2023-11-14", "end": "2023-11-16"},
         "not yet supported"),
        ({"strategy": "halftrend_ema_v1", "start": "2030-01-01", "end": "2030-02-01"},
         "no candles"),
        ({"strategy": "halftrend_ema_v1", "start": "2023-11-16", "end": "2023-11-14"},
         "start must be before end"),
        ({"strategy": "halftrend_ema_v1", "start": "2023-11-14",
          "end": "2023-11-16", "balance": -5}, "balance"),
        ({"strategy": "halftrend_ema_v1", "start": "2023-11-14",
          "end": "2023-11-16", "balance": 100}, "balance"),
        ({"strategy": "nope", "start": "2023-11-14", "end": "2023-11-16"},
         "unknown strategy"),
    ]
    for body, frag in bad:
        r = client.post("/api/backtest", json=body)
        assert r.status_code == 400, body
        assert frag in r.json()["detail"]


def test_start_rejects_nan_balance(client):
    seed_candles(client)
    raw = json.dumps({"strategy": "halftrend_ema_v1", "start": "2023-11-14",
                      "end": "2023-11-16", "balance": float("nan")}, allow_nan=True)
    r = client.post("/api/backtest", content=raw,
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert "balance" in r.json()["detail"] or "finite" in r.json()["detail"]


def test_start_rejects_sparse_range(client):
    # Only ~50 bars on a single day -- below the runner's own 300-bar floor.
    # This must be caught up front (400), not left to create a doomed run.
    client.app.state.db.upsert_candles("XAUUSD", "M5", [
        {"t": 1704067200 + 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}
        for i in range(50)])
    r = client.post("/api/backtest", json={
        "strategy": "halftrend_ema_v1", "start": "2024-01-01", "end": "2024-01-01"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "50" in detail and "300" in detail


def test_start_and_status_roundtrip(client, monkeypatch):
    seed_candles(client)
    captured = {}

    def fake_start(db, params):
        captured.update(params)
        return db.insert_backtest_run(json.dumps(params))

    from app import backtest_runner
    monkeypatch.setattr(backtest_runner, "start_run", fake_start)
    r = client.post("/api/backtest", json={
        "strategy": "halftrend_m15_v1", "start": "2023-11-14",
        "end": "2023-11-16", "balance": 5000, "risk_pct": 2.0})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert captured["strategy"] == "halftrend_m15_v1"
    assert captured["entry_mode"] == "adr"            # default applied
    assert captured["start_ts"] < captured["end_ts"]
    row = client.get(f"/api/backtest/{rid}").json()
    assert row["status"] == "running"
    assert row["params"]["balance"] == 5000
    runs = client.get("/api/backtest/runs").json()["runs"]
    assert runs[0]["id"] == rid


def test_busy_returns_409(client, monkeypatch):
    seed_candles(client)
    from app import backtest_runner

    def busy(db, params):
        raise RuntimeError("a backtest is already running")

    monkeypatch.setattr(backtest_runner, "start_run", busy)
    r = client.post("/api/backtest", json={
        "strategy": "halftrend_ema_v1", "start": "2023-11-14", "end": "2023-11-16"})
    assert r.status_code == 409


def test_report_404s(client):
    assert client.get("/api/backtest/12345").status_code == 404
    assert client.get("/api/backtest/12345/report").status_code == 404


def make_client(db_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", db_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    c = TestClient(main.app)
    c.__enter__()
    return c


def test_startup_reconciles_orphaned_running_run(tmp_path, monkeypatch):
    # A service restart kills the runner's daemon thread, but a 'running'
    # row it left behind is process-local state that would otherwise never
    # move -- see backtest_runner._busy. lifespan() must reconcile it to
    # 'failed' on boot instead of leaving the page poller spinning forever.
    from app.db import SignalDb
    db_path = str(tmp_path / "orphan.db")
    pre = SignalDb(db_path)
    rid = pre.insert_backtest_run(json.dumps({"strategy": "halftrend_ema_v1"}))
    assert pre.get_backtest_run(rid)["status"] == "running"
    client = make_client(db_path, monkeypatch)
    row = client.app.state.db.get_backtest_run(rid)
    assert row["status"] == "failed"
    assert row["error"] == "interrupted by service restart"
