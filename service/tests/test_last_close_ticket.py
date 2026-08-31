# /api/last-close-ticket — the reconciler's replay guard (incident
# 2026-08-31: a hard power cut rolled the EA's MT5-global watermark back a
# week, and the reconciler re-reported all 25 of the prior week's closes; 10
# aggregate-reported legs weren't ticket-dedupable and 4 fired duplicate
# Telegram profit alerts). The EA now asks the service for the newest close
# ticket it has recorded and takes max(local watermark, this) before
# replaying, so a rolled-back watermark can't resurrect closes the service
# already knows about.
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _close(client, ticket, profit=1.0):
    return client.post("/trade-event", json={
        "event": "close", "strategy_id": "t", "direction": "BUY",
        "lots": 0.01, "price": 4000.0, "sl": 0.0, "reason": "stop-loss",
        "ticket": ticket, "profit": profit, "tp": 0.0, "final": True})


def test_empty_db_returns_zero(client):
    r = client.get("/api/last-close-ticket")
    assert r.status_code == 200
    assert r.json() == {"ticket": 0}


def test_returns_max_close_ticket_ignoring_aggregates_and_opens(client):
    client.post("/trade-event", json={
        "event": "open", "strategy_id": "t", "direction": "BUY",
        "lots": 0.01, "price": 4000.0, "sl": 0.0, "reason": "signal BUY",
        "ticket": 9_999_999_999, "profit": 0.0, "tp": 0.0, "final": True})
    _close(client, 1516121563)
    _close(client, 0)            # aggregate close (profit lock) — no ticket
    _close(client, 1522440869)
    _close(client, 1520195809)
    assert client.get("/api/last-close-ticket").json() == {"ticket": 1522440869}
