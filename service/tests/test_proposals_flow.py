import importlib

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import trend_candles


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "proposals.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def make_analyze_payload(signal="BUY", strategy_id="halftrend_ema_v1"):
    return {"symbol": "XAUUSD", "timeframe": "M15", "signal": signal,
            "strategy_id": strategy_id,
            "candles": [c.model_dump() for c in trend_candles(200)]}


def _post_signal(client, signal, strategy_id="halftrend_ema_v1"):
    payload = make_analyze_payload()
    payload["signal"] = signal
    payload["strategy_id"] = strategy_id
    return client.post("/analyze", json=payload)


class RecordingTelegram:
    """Records send_message/edit_message calls; never makes a real request."""

    def __init__(self):
        self.calls = []

    def send_message(self, text, reply_markup=None):
        self.calls.append(("send", text, reply_markup))
        return {"ok": True, "result": {"message_id": 7}}

    def edit_message(self, mid, text, reply_markup=None):
        self.calls.append(("edit", mid, text))
        return {"ok": True}


@pytest.fixture
def fake_tg(client):
    from app import main
    from app.telegram import set_active_client

    recorder = RecordingTelegram()
    previous = getattr(main.app.state, "telegram", None)
    main.app.state.telegram = recorder
    set_active_client(recorder)
    yield recorder
    main.app.state.telegram = previous
    set_active_client(previous)


def test_manual_entry_creates_pending_proposal_and_sends_buttons(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    p = client.app.state.db.pending_proposal()
    assert p["kind"] == "entry" and p["direction"] == "BUY"
    assert p["tg_message_id"] == 7
    assert any("reply_markup" in str(c) or c[0] == "send" for c in fake_tg.calls)


def test_auto_mode_creates_no_proposal(client, fake_tg):
    client.app.state.db.set_exec_mode("auto")
    _post_signal(client, "BUY")
    assert client.app.state.db.pending_proposal() is None


def test_plain_none_signal_sends_nothing(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "NONE")
    assert fake_tg.calls == []          # alert diet: no per-bar noise


def test_opposite_signal_expires_pending_entry(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "SELL")
    assert client.app.state.db.get_proposal(pid)["status"] == "expired"
    # and the SELL raised its own new proposal
    assert client.app.state.db.pending_proposal()["direction"] == "SELL"


def test_exit_signal_expires_pending_entry(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "EXIT")
    assert client.app.state.db.get_proposal(pid)["status"] == "expired"


def test_duplicate_same_direction_signal_keeps_single_pending(client, fake_tg):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    first = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "BUY")
    assert client.app.state.db.pending_proposal()["id"] == first


def test_shadow_strategy_signal_never_proposes(client, fake_tg):
    # req.signal is the ACTIVE strategy's signal by contract; shadow-only
    # signals arrive with signal=NONE + shadows list -- covered by the NONE
    # test. This guards the contract: a NONE post with shadows creates
    # nothing.
    client.app.state.db.set_exec_mode("manual")
    payload = make_analyze_payload()
    payload["signal"] = "NONE"
    payload["shadows"] = [{"strategy_id": "boll_stochrsi_v1", "signal": "BUY"}]
    client.post("/analyze", json=payload)
    assert client.app.state.db.pending_proposal() is None
