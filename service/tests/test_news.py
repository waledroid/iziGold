"""/news + pre-blackout notices: heartbeat news contract, the command
rendering, and the one-shot heads-up before a high-impact USD event."""
import importlib
import time
import types

import pytest
from fastapi.testclient import TestClient

from app.models import HeartbeatRequest
from app.telegram import format_pinned_help, handle_command


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "news.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


class _RecTg:
    def __init__(self):
        self.texts = []

    def send_message(self, text, reply_markup=None):
        self.texts.append(text)
        return {"ok": True}


def _hb_payload(news=(), blackout=30):
    return {"equity": 4756.0, "balance": 4756.0, "floating_pl": 0.0,
            "news": list(news), "news_blackout_min": blackout}


def _app_hb(news=(), blackout=30, ts=None):
    hb = types.SimpleNamespace(
        positions=[], active_strategy="halftrend_m15_v1", algo_trading=True,
        news=[types.SimpleNamespace(**e) for e in news],
        news_blackout_min=blackout)
    return (ts if ts is not None else time.time(), hb)


# ------------------------------------------------------------- contract

def test_heartbeat_accepts_news_events():
    hb = HeartbeatRequest(equity=1, balance=1, floating_pl=0,
                          news=[{"in_s": 1920, "name": "CPI m/m"}],
                          news_blackout_min=30)
    assert hb.news[0].in_s == 1920
    assert hb.news[0].name == "CPI m/m"
    assert hb.news_blackout_min == 30


def test_heartbeat_news_defaults_empty_for_old_ea():
    hb = HeartbeatRequest(equity=1, balance=1, floating_pl=0)
    assert hb.news == [] and hb.news_blackout_min == 30


# ------------------------------------------------------------- /news command

def test_news_lists_events_with_countdown_and_blackout(client):
    client.app.state.latest_heartbeat = _app_hb(news=[
        {"in_s": 5100, "name": "CPI m/m"},
        {"in_s": 60 * 60 * 5, "name": "FOMC Statement"}])
    reply = handle_command("/news", client.app)
    assert "CPI m/m" in reply and "FOMC Statement" in reply
    assert "1h 25m" in reply          # 5100 s
    assert "±30" in reply


def test_news_empty_says_no_events(client):
    client.app.state.latest_heartbeat = _app_hb(news=[])
    reply = handle_command("/news", client.app)
    assert "no high-impact" in reply.lower()


def test_news_without_heartbeat_reports_ea_down(client):
    client.app.state.latest_heartbeat = None
    reply = handle_command("/news", client.app)
    assert "EA" in reply


def test_news_listed_in_pinned_help(client):
    assert "/news" in format_pinned_help()


# -------------------------------------------------- pre-blackout heads-up

def test_heads_up_fires_once_before_blackout(client):
    client.app.state.telegram = tg = _RecTg()
    # 32 min out, 30 min radius -> inside the (radius + 5 min) notice window
    client.post("/heartbeat", json=_hb_payload(
        news=[{"in_s": 32 * 60, "name": "ISM Manufacturing PMI"}]))
    notices = [t for t in tg.texts if "blackout" in t.lower()]
    assert len(notices) == 1
    assert "ISM Manufacturing PMI" in notices[0]
    # next heartbeat, ~5 s later in event-relative terms: latched, no repeat
    client.post("/heartbeat", json=_hb_payload(
        news=[{"in_s": 32 * 60 - 5, "name": "ISM Manufacturing PMI"}]))
    assert len([t for t in tg.texts if "blackout" in t.lower()]) == 1


def test_no_heads_up_for_far_events(client):
    client.app.state.telegram = tg = _RecTg()
    client.post("/heartbeat", json=_hb_payload(
        news=[{"in_s": 3 * 3600, "name": "GDP q/q"}]))
    assert [t for t in tg.texts if "blackout" in t.lower()] == []
