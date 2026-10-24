"""Unit tests for Auto-Adaptive Hybrid Sizing ($50 - $5,000 balances)."""

import importlib
import pytest
from fastapi.testclient import TestClient
from app.config import calculate_adaptive_tier


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("AGENT_ENABLED", "false")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_micro_tier_200_and_below():
    info_50 = calculate_adaptive_tier(50.0)
    assert info_50["tier"] == "micro"
    assert info_50["max_lots"] == 0.01
    assert info_50["max_pos"] == 1

    info_200 = calculate_adaptive_tier(200.0)
    assert info_200["tier"] == "micro"
    assert info_200["max_lots"] == 0.01
    assert info_200["max_pos"] == 1


def test_mini_tier_200_to_500():
    info_201 = calculate_adaptive_tier(201.0)
    assert info_201["tier"] == "mini"
    assert info_201["max_lots"] == 0.02
    assert info_201["max_pos"] == 2

    info_500 = calculate_adaptive_tier(500.0)
    assert info_500["tier"] == "mini"
    assert info_500["max_lots"] == 0.02
    assert info_500["max_pos"] == 2


def test_core_tier_500_to_2000():
    info_501 = calculate_adaptive_tier(501.0)
    assert info_501["tier"] == "core"
    assert info_501["max_lots"] == 0.05
    assert info_501["max_pos"] == 3

    info_2000 = calculate_adaptive_tier(2000.0)
    assert info_2000["tier"] == "core"
    assert info_2000["max_lots"] == 0.05
    assert info_2000["max_pos"] == 3


def test_pro_tier_above_2000():
    info_2500 = calculate_adaptive_tier(2500.0)
    assert info_2500["tier"] == "pro"
    assert info_2500["max_lots"] == pytest.approx(0.06, abs=0.01)
    assert info_2500["max_pos"] == 3

    info_5000 = calculate_adaptive_tier(5000.0)
    assert info_5000["tier"] == "pro"
    assert info_5000["max_lots"] == pytest.approx(0.12, abs=0.01)
    assert info_5000["max_pos"] == 3


def test_api_state_includes_adaptive_tier(client):
    res = client.get("/api/state")
    assert res.status_code == 200
    data = res.json()
    assert "adaptive_tier" in data
    assert data["adaptive_tier"]["tier"] in ("micro", "mini", "core", "pro")
    assert "max_lots" in data["adaptive_tier"]
    assert "max_pos" in data["adaptive_tier"]


def test_api_config_includes_adaptive_tier(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "adaptive_tier" in data
    assert "tier" in data["adaptive_tier"]
