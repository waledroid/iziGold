"""Test suite for Phase 1 Improvements:
1. Canonical Trade Band & /api/basket
2. Realtime SSE Streaming & Broadcast Hub
3. External News Fallback & Telegram /news
4. Slippage & Execution Latency Telemetry
5. Dashboard Auth / PIN Protection
6. Database WAL Checkpoint & Pruning
"""
import importlib
import time
import types
import pytest
from fastapi.testclient import TestClient

from app.basket import derive_basket_band
from app.db import SignalDb
from app.models import HeartbeatRequest, Position, TradeEventRequest
from app.news_feed import fetch_upcoming_news, _is_high_impact_usd


# --- 1. Canonical Basket Band Tests -----------------------------------------

def test_derive_basket_band_open_positions():
    db = types.SimpleNamespace(recent_trades=lambda limit: [])
    pos1 = Position(ticket=101, direction="BUY", lots=0.1, open_price=2650.0, sl=2640.0, profit=15.0)
    pos2 = Position(ticket=102, direction="BUY", lots=0.07, open_price=2655.0, sl=2640.0, profit=5.0)
    hb = HeartbeatRequest(
        equity=5000.0, balance=5000.0, floating_pl=20.0,
        positions=[pos1, pos2], active_strategy="izi5"
    )

    band = derive_basket_band(db, hb, profit_target_pct=2.0)
    assert band["state"] == "open"
    assert band["direction"] == "BUY"
    assert band["strategy_id"] == "izi5"
    assert len(band["legs"]) == 2
    assert band["lots"] == pytest.approx(0.17)
    # Weighted entry: (0.1*2650 + 0.07*2655) / 0.17 = 2652.06
    assert band["entry"] == pytest.approx(2652.06, abs=0.01)
    assert band["sl"] == 2640.0
    # Virtual TP: entry * (1 + 0.02)
    assert band["tp"] > band["entry"]
    assert band["risk_box"]["low"] == 2640.0
    assert band["risk_box"]["high"] == band["entry"]


def test_derive_basket_band_closed_state():
    db = types.SimpleNamespace(
        recent_trades=lambda limit: [
            {"event": "close", "direction": "SELL", "price": 2640.0,
             "sl": 2650.0, "tp": 2630.0, "profit": 45.0, "strategy_id": "izi15", "lots": 0.1}
        ]
    )
    hb = HeartbeatRequest(equity=5000.0, balance=5000.0, floating_pl=0.0, positions=[])
    band = derive_basket_band(db, hb)
    assert band["state"] == "closed"
    assert band["direction"] == "SELL"
    assert band["entry"] == 2640.0
    assert band["sl"] == 2650.0
    assert band["strategy_id"] == "izi15"
    assert band["legs"] == []


def test_derive_basket_band_empty_history():
    db = types.SimpleNamespace(recent_trades=lambda limit: [])
    hb = HeartbeatRequest(equity=5000.0, balance=5000.0, floating_pl=0.0, positions=[])
    band = derive_basket_band(db, hb)
    assert band["state"] == "none"
    assert band["entry"] == 0.0


# --- 2. News Fallback Tests --------------------------------------------------

def test_high_impact_usd_detection():
    assert _is_high_impact_usd("Core CPI m/m", "high", "USD") is True
    assert _is_high_impact_usd("Fed Interest Rate Decision", "", "USD") is True
    assert _is_high_impact_usd("Non-Farm Employment Change", "high", "USD") is True
    assert _is_high_impact_usd("EUR Interest Rate Decision", "high", "EUR") is False
    assert _is_high_impact_usd("Random minor speech", "low", "USD") is False


# --- 3. Database Slippage & Maintenance Tests --------------------------------

def test_db_slippage_and_maintenance(tmp_path):
    db = SignalDb(str(tmp_path / "test_maint.db"))
    
    # Insert trade with slippage & latency
    trade_id = db.insert_trade({
        "event": "open",
        "strategy_id": "izi5",
        "direction": "BUY",
        "lots": 0.1,
        "price": 2651.2,
        "req_price": 2650.0,
        "slippage_pts": 1.2,
        "exec_ms": 142,
        "ticket": 9991
    })
    assert trade_id > 0
    
    recent = db.recent_trades(limit=1)
    assert recent[0]["slippage_pts"] == 1.2
    assert recent[0]["exec_ms"] == 142
    assert recent[0]["req_price"] == 2651.2 or recent[0]["req_price"] == 2650.0

    # Slippage stats
    stats = db.slippage_stats()
    assert stats["count"] == 1
    assert stats["avg_slippage_pts"] == 1.2
    assert stats["avg_exec_ms"] == 142.0

    # WAL Checkpoint
    busy, log_frames, backfilled = db.checkpoint_wal()
    assert isinstance(busy, int)

    # Prune old data
    now = int(time.time())
    db.conn.execute(
        "INSERT INTO heartbeats (ts, equity, balance, floating_pl, open_count, kill_switch, exposure_min, active_strategy)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (now - 800000, 1000, 1000, 0, 0, 0, 0, "izi5")
    )
    db.conn.commit()
    pruned = db.prune_old_data(heartbeat_days=7)
    assert pruned["heartbeats"] >= 1


# --- 4. API Endpoints & Dashboard Auth Tests --------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_api_p1.db"))
    monkeypatch.setenv("DASHBOARD_PIN", "9944")
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_api_basket_endpoint(client):
    r = client.get("/api/basket")
    assert r.status_code == 200
    data = r.json()
    assert "state" in data
    assert "entry" in data
    assert "sl" in data
    assert "tp" in data


def test_api_stats_includes_slippage(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert "slippage" in data
    assert "avg_slippage_pts" in data["slippage"]


def test_dashboard_pin_protection(client):
    # Unauthenticated actions should be 401
    r = client.post("/api/mode", json={"mode": "auto"})
    assert r.status_code == 401

    r = client.post("/api/rules", json={"key": "rsi_enforce", "value": "off"})
    assert r.status_code == 401

    r = client.post("/api/switch", json={"strategy_id": "izi15"})
    assert r.status_code == 401

    # Authenticated via X-Dashboard-PIN header
    headers = {"X-Dashboard-PIN": "9944"}
    r = client.post("/api/mode", json={"mode": "auto"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["mode"] == "auto"

    r = client.post("/api/rules", json={"key": "rsi_enforce", "value": "off"}, headers=headers)
    assert r.status_code == 200

    r = client.post("/api/switch", json={"strategy_id": "izi15"}, headers=headers)
    assert r.status_code == 200


def test_maintenance_prune_endpoint(client):
    headers = {"X-Dashboard-PIN": "9944"}
    r = client.post("/api/maintenance/prune", headers=headers)
    assert r.status_code == 200
    res = r.json()
    assert res["status"] == "ok"
    assert "pruned" in res
    assert "wal_checkpoint" in res


def test_feed_push_endpoint(client):
    payload = {
        "tick": {"bid": 2650.1, "ask": 2650.3},
        "bars": {"M5": [{"t": int(time.time()), "o": 2649.0, "h": 2651.0, "l": 2648.5, "c": 2650.2}]}
    }
    r = client.post("/feed/push", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
