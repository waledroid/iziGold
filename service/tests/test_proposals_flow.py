import importlib
import time

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


def _post_signal(client, signal, strategy_id="halftrend_ema_v1",
                 news_blackout=False):
    payload = make_analyze_payload()
    payload["signal"] = signal
    payload["strategy_id"] = strategy_id
    payload["news_blackout"] = news_blackout
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


@pytest.fixture
def heartbeat_payload():
    return {"equity": 1, "balance": 1, "floating_pl": 0}


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
    # Seed a heartbeat with an open position (M1): a manual EXIT proposal is
    # only raised when the EA's last-known heartbeat actually has something
    # open, so this test needs one to exercise the follow-on exit proposal
    # as well as the entry-expiry it was already asserting.
    client.post("/heartbeat", json={
        "equity": 1000, "balance": 1000, "floating_pl": 0,
        "positions": [{"ticket": 1, "direction": "BUY", "lots": 0.1,
                       "open_price": 4000.0, "sl": 3990.0, "profit": 5.0}],
    })
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    _post_signal(client, "EXIT")
    assert client.app.state.db.get_proposal(pid)["status"] == "expired"
    # and, since the heartbeat shows an open position, the EXIT also raised
    # its own exit proposal
    exit_p = client.app.state.db.pending_proposal()
    assert exit_p is not None and exit_p["kind"] == "exit"


def test_exit_signal_creates_no_proposal_without_known_open_position(client, fake_tg):
    """M1: without a heartbeat (or a heartbeat with no positions), maybe_propose
    must not raise an exit proposal -- there would be nothing for close_all
    to close."""
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "EXIT")
    assert client.app.state.db.pending_proposal() is None


def test_exit_signal_creates_no_proposal_when_heartbeat_has_no_positions(client, fake_tg):
    client.post("/heartbeat", json={"equity": 1000, "balance": 1000, "floating_pl": 0,
                                    "positions": []})
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "EXIT")
    assert client.app.state.db.pending_proposal() is None


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


def test_approved_proposal_delivered_once_via_heartbeat(client, fake_tg, heartbeat_payload):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    client.app.state.db.set_proposal_status(pid, "approved")
    b1 = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b1["command"] == {"cmd": "execute", "proposal_id": pid, "direction": "BUY"}
    b2 = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b2["command"] is None


def test_exit_proposal_delivers_close_all(client, fake_tg, heartbeat_payload):
    client.app.state.db.set_exec_mode("manual")
    pid = client.app.state.db.create_proposal("exit", "BUY", "s", 1.0, None)
    client.app.state.db.set_proposal_status(pid, "approved")
    b = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b["command"] == {"cmd": "close_all", "proposal_id": pid}


def test_proposal_result_updates_status_and_edits_message(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "BUY", "s", 1.0, None)
    client.app.state.db.set_proposal_message(pid, 7)
    client.app.state.db.set_proposal_status(pid, "approved")
    client.app.state.db.pop_approved_command()
    r = client.post("/proposal-result",
                    json={"proposal_id": pid, "ok": True, "detail": "filled @4067.1"})
    assert r.status_code == 200
    assert client.app.state.db.get_proposal(pid)["status"] == "executed"
    assert any(c[0] == "edit" for c in fake_tg.calls)


def test_proposal_result_blocked(client, fake_tg):
    pid = client.app.state.db.create_proposal("entry", "SELL", "s", 1.0, None)
    client.app.state.db.set_proposal_status(pid, "approved")
    client.app.state.db.pop_approved_command()
    client.post("/proposal-result",
                json={"proposal_id": pid, "ok": False, "detail": "spread too wide"})
    assert client.app.state.db.get_proposal(pid)["status"] == "blocked"


def test_proposal_result_guarded_against_already_reconciled_row(client, fake_tg):
    """I2: /proposal-result is guarded on the row still being 'dispatched'.
    If the /heartbeat TTL sweep already reconciled it (e.g. to 'blocked'),
    a late EA callback must not silently stomp that outcome."""
    db = client.app.state.db
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    db.set_proposal_status(pid, "approved")
    db.pop_approved_command()               # -> dispatched
    db.set_proposal_status(pid, "blocked", expected="dispatched")   # simulate the sweep winning
    r = client.post("/proposal-result",
                    json={"proposal_id": pid, "ok": True, "detail": "filled anyway"})
    assert r.json() == {"ok": False}
    assert db.get_proposal(pid)["status"] == "blocked"  # unchanged, not overwritten to executed


def test_stance_change_expires_approved_undispatched_proposal(client, fake_tg):
    """I1a: a stance-broke expiry check also applies to APPROVED-but-not-yet-
    dispatched proposals, not just pending ones."""
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    pid = client.app.state.db.pending_proposal()["id"]
    client.app.state.db.set_proposal_status(pid, "approved")
    _post_signal(client, "SELL")
    row = client.app.state.db.get_proposal(pid)
    assert row["status"] == "expired"
    assert any(c[0] == "edit" and "stance changed" in c[2] for c in fake_tg.calls)


def test_heartbeat_sweep_expires_121s_old_approved_row_and_pop_returns_none(
        client, fake_tg, heartbeat_payload):
    """I1b: an approved row past the 120s approval TTL is expired by the
    /heartbeat sweep before pop_approved_command runs, so it's never
    delivered as a command."""
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    db = client.app.state.db
    pid = db.pending_proposal()["id"]
    db.set_proposal_status(pid, "approved")
    db.conn.execute("UPDATE proposals SET decided_ts=? WHERE id=?",
                    (int(time.time()) - 121, pid))
    db.conn.commit()
    b = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b["command"] is None
    assert db.get_proposal(pid)["status"] == "expired"
    assert any(c[0] == "edit" and "timed out" in c[2] for c in fake_tg.calls)


def test_heartbeat_sweep_leaves_fresh_approved_row_alone(client, fake_tg, heartbeat_payload):
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    db = client.app.state.db
    pid = db.pending_proposal()["id"]
    db.set_proposal_status(pid, "approved")
    b = client.post("/heartbeat", json=heartbeat_payload).json()
    assert b["command"] == {"cmd": "execute", "proposal_id": pid, "direction": "BUY"}


def test_heartbeat_sweep_blocks_dispatched_command_without_result_after_180s(
        client, fake_tg, heartbeat_payload):
    """I4: a dispatched command the EA never confirmed is reconciled to
    'blocked' once it's older than the 180s command-result TTL."""
    db = client.app.state.db
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    db.set_proposal_message(pid, 7)
    db.set_proposal_status(pid, "approved")
    db.pop_approved_command()   # -> dispatched
    db.conn.execute("UPDATE proposals SET decided_ts=? WHERE id=?",
                    (int(time.time()) - 181, pid))
    db.conn.commit()
    client.post("/heartbeat", json=heartbeat_payload)
    row = db.get_proposal(pid)
    assert row["status"] == "blocked"
    assert any(c[0] == "edit" and "no confirmation" in c[2] for c in fake_tg.calls)


def test_expire_after_concurrent_approve_skips_edit(client, fake_tg, monkeypatch):
    """Guarded-transition race: maybe_propose's section-1 stance-expiry
    check reads a 'pending' row and decides to expire it, but a concurrent
    Telegram approve wins the guarded UPDATE first. Section 1's own
    "expired (strategy stance changed)" edit must be skipped for that lost
    race -- it must not double-message a row it no longer owns. (Section
    1b/I1a may go on to legitimately re-expire the very same row, since it
    re-reads as 'approved' with the same broken stance -- that's a separate,
    correct transition with its own distinct wording, not the bug under
    test here.)"""
    client.app.state.db.set_exec_mode("manual")
    _post_signal(client, "BUY")
    db = client.app.state.db
    pid = db.pending_proposal()["id"]
    real_set = db.set_proposal_status
    section1 = {"called": False, "returned": None}

    def racing_set(pid_, status, expected=None):
        if pid_ == pid and status == "expired" and expected == "pending":
            # Simulate a concurrent Telegram "Take trade" tap landing first,
            # then let the real guarded UPDATE run and observe it lose.
            db.conn.execute(
                "UPDATE proposals SET status='approved', decided_ts=? WHERE id=?",
                (int(time.time()), pid_))
            db.conn.commit()
            section1["called"] = True
            section1["returned"] = real_set(pid_, status, expected=expected)
            return section1["returned"]
        return real_set(pid_, status, expected=expected)

    monkeypatch.setattr(db, "set_proposal_status", racing_set)
    _post_signal(client, "SELL")   # opposite direction -> stance-expiry path
    assert section1["called"] is True
    assert section1["returned"] is False   # section 1's guarded UPDATE lost the race
    assert not any(c[0] == "edit" and c[2].endswith("expired (strategy stance changed)")
                  for c in fake_tg.calls)


def test_notify_sends_text_verbatim_via_active_telegram_client(client, fake_tg):
    resp = client.post("/notify", json={"text": "AUTO BUY not executed: kill switch active"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert fake_tg.calls == [("send", "AUTO BUY not executed: kill switch active", None)]


def test_notify_empty_text_sends_nothing_and_reports_not_ok(client, fake_tg):
    resp = client.post("/notify", json={"text": "   "})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False}
    assert fake_tg.calls == []


def test_notify_oversize_text_rejected(client, fake_tg):
    resp = client.post("/notify", json={"text": "x" * 501})
    assert resp.status_code == 422
    assert fake_tg.calls == []


def test_notify_no_telegram_client_fails_open(client):
    client.app.state.telegram = None
    resp = client.post("/notify", json={"text": "AUTO SELL not executed: no client"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# /heartbeat algo_trading transition notice
# ---------------------------------------------------------------------------

def test_heartbeat_algo_trading_true_to_false_sends_warning_once(
        client, fake_tg, heartbeat_payload):
    client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": True})
    client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": False})
    assert fake_tg.calls == [("send", "⚠️ MT5 Algo Trading is OFF — trades cannot execute "
                              "until you enable it", None)]
    # A second False heartbeat (still-off streak) must not spam another notice.
    client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": False})
    assert fake_tg.calls == [("send", "⚠️ MT5 Algo Trading is OFF — trades cannot execute "
                              "until you enable it", None)]


def test_heartbeat_algo_trading_false_to_true_sends_recovery_notice(
        client, fake_tg, heartbeat_payload):
    client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": False})
    fake_tg.calls.clear()
    client.post("/heartbeat", json={**heartbeat_payload, "algo_trading": True})
    assert fake_tg.calls == [("send", "✅ Algo Trading back ON", None)]


def test_heartbeat_algo_trading_default_true_sends_no_notice(client, fake_tg, heartbeat_payload):
    client.post("/heartbeat", json=heartbeat_payload)
    client.post("/heartbeat", json=heartbeat_payload)
    assert fake_tg.calls == []


def test_messageless_exit_failure_sends_new_telegram_message(client, fake_tg):
    # quick-exit proposals (dashboard close-all / exitnow) have no tg message;
    # a failed result must still notify the user with a fresh message
    db = client.app.state.db
    pid = db.create_proposal("exit", "SELL", "s", 0.0, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    db.pop_approved_command()
    fake_tg.calls.clear()
    client.post("/proposal-result",
                json={"proposal_id": pid, "ok": False,
                      "detail": "partial close - 2 of 3 legs still open"})
    sends = [c for c in fake_tg.calls if c[0] == "send"]
    assert sends and "partial close" in sends[0][1]
    assert db.get_proposal(pid)["status"] == "blocked"


def test_pl_message_includes_avg_entry_and_exit(client, fake_tg):
    # basket: open 0.01 @ 4084.55 + add 0.03 @ 4075.50 -> weighted avg 4077.76
    base = {"strategy_id": "s", "lots": 0.01, "price": 4084.55, "sl": 4080.0}
    client.post("/trade-event", json={**base, "event": "open", "direction": "SELL"})
    client.post("/trade-event", json={**base, "event": "add", "direction": "SELL",
                                      "lots": 0.03, "price": 4075.50})
    fake_tg.calls.clear()
    client.post("/trade-event", json={**base, "event": "close", "direction": "SELL",
                                      "lots": 0.04, "price": 4078.65,
                                      "profit": -8.24, "final": True})
    # report work runs in a background task now (fast /trade-event response)
    deadline = time.time() + 5.0
    sends = []
    while time.time() < deadline and not sends:
        sends = [c for c in fake_tg.calls if c[0] == "send"]
        time.sleep(0.05)
    assert sends, "P/L message not sent"
    msg = sends[-1][1]
    assert "-$8.24 loss" in msg and "SELL 4077.76" in msg and "4078.65" in msg


# --- blackout entries propose even in AUTO (owner request 2026-09-01) ------
# During a news blackout the EA does not auto-execute; a BUY/SELL /analyze
# arriving with news_blackout=true raises a Telegram proposal (with the
# blackout warning prefixed) so the owner decides the trade themselves.
# Auto behavior resumes when the window ends — no exec-mode state is flipped.

def test_blackout_entry_creates_proposal_in_auto_mode(client, fake_tg):
    client.app.state.db.set_exec_mode("auto")
    _post_signal(client, "BUY", news_blackout=True)
    p = client.app.state.db.pending_proposal()
    assert p is not None and p["kind"] == "entry" and p["direction"] == "BUY"
    texts = [str(c) for c in fake_tg.calls]
    assert any("NEWS BLACKOUT" in t for t in texts), texts


def test_blackout_none_or_exit_does_not_propose_in_auto(client, fake_tg):
    client.app.state.db.set_exec_mode("auto")
    _post_signal(client, "NONE", news_blackout=True)
    assert client.app.state.db.pending_proposal() is None
    _post_signal(client, "EXIT", news_blackout=True)   # exits stay auto
    assert client.app.state.db.pending_proposal() is None
