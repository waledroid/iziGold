"""Clean-URLs migration: old /ui/* addresses (bookmarks, Telegram links, the
backtest CLI's default --source) must keep working via a 307 redirect to the
new /, /backtest, /onboarding pages and /api/* JSON routes."""
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


def test_ui_root_redirects_to_dashboard(client):
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/"


def test_ui_backtest_redirects_to_backtest_page(client):
    r = client.get("/ui/backtest", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/backtest"


def test_ui_onboarding_redirects_to_onboarding_page(client):
    r = client.get("/ui/onboarding", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/onboarding"


def test_ui_state_redirects_to_api_state(client):
    r = client.get("/ui/state", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/state"


def test_ui_overlays_redirects_with_query_preserved(client):
    r = client.get("/ui/overlays?strategy=x", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/overlays?strategy=x"


def test_ui_rules_post_redirects_and_still_works(client):
    r = client.post("/ui/rules", json={"key": "htf_enforce", "value": "M15"},
                    follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/api/rules"
    # Following it (client default) preserves method + body (307) and still
    # lands a working response -- a valid rule update succeeds (200)...
    r2 = client.post("/ui/rules", json={"key": "htf_enforce", "value": "M15"})
    assert r2.status_code == 200
    assert r2.json() == {"htf_enforce": "M15"}
    # ...and an invalid one still 400s through the redirect, same as calling
    # /api/rules directly.
    r3 = client.post("/ui/rules", json={"key": "bogus", "value": "x"})
    assert r3.status_code == 400


def test_ui_candles_end_to_end_matches_api_candles(client):
    legacy = client.get("/ui/candles", follow_redirects=True)
    fresh = client.get("/api/candles")
    assert legacy.status_code == 200 and fresh.status_code == 200
    assert legacy.json() == fresh.json()
    assert set(legacy.json().keys()) == {"symbol", "timeframe", "candles"}
