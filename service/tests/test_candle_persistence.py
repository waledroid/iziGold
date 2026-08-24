"""Task 2: /analyze persists candles; startup seeds the chart accumulator
from the persistent candles table; /static serves the vendored chart lib."""
import importlib

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import trend_candles


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def analyze_body(signal="NONE"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "candles": [c.model_dump() for c in trend_candles(200)]}


def make_client(db_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", db_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    client = TestClient(main.app)
    client.__enter__()
    return client


def test_analyze_persists_candles(client):
    body = analyze_body(signal="NONE")
    r = client.post("/analyze", json=body)
    assert r.status_code == 200
    db = client.app.state.db
    rows = db.get_candles(body["symbol"], body["timeframe"])
    assert len(rows) == len(body["candles"])
    assert rows[-1]["c"] == body["candles"][-1]["c"]


def test_startup_seeds_recent_candles(tmp_path, monkeypatch):
    # Arrange: a db file that already holds bars, then boot the app on it.
    from app.db import SignalDb
    db_path = str(tmp_path / "seed.db")
    pre = SignalDb(db_path)
    pre.upsert_candles("XAUUSD", "M5", [
        {"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0} for i in range(1, 6)])
    client = make_client(db_path, monkeypatch)
    rc = client.app.state.recent_candles
    assert rc is not None and rc["symbol"] == "XAUUSD"
    assert len(rc["candles"]) == 5
    assert rc["candles"][-1].t == 1500            # Candle objects, not dicts


def test_static_vendor_served(client):
    r = client.get("/static/vendor/lightweight-charts.standalone.production.js")
    assert r.status_code == 200
