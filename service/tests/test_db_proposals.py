import pytest
from app.db import SignalDb


@pytest.fixture
def db(tmp_path):
    """Temp-file Database fixture matching test_db.py conventions."""
    return SignalDb(str(tmp_path / "t.db"))


def test_exec_mode_default_and_set(db):
    assert db.exec_mode() == "manual"
    db.set_exec_mode("auto")
    assert db.exec_mode() == "auto"
    with pytest.raises(ValueError):
        db.set_exec_mode("yolo")


def test_proposal_lifecycle(db):
    pid = db.create_proposal("entry", "BUY", "halftrend_ema_v1", 4066.5, None)
    row = db.get_proposal(pid)
    assert row["status"] == "pending" and row["direction"] == "BUY"
    assert db.pending_proposal()["id"] == pid
    assert db.pending_proposal(kind="exit") is None
    db.set_proposal_message(pid, 777)
    assert db.get_proposal(pid)["tg_message_id"] == 777
    db.set_proposal_status(pid, "approved")
    cmd = db.pop_approved_command()
    assert cmd["id"] == pid
    assert db.get_proposal(pid)["status"] == "dispatched"
    assert db.pop_approved_command() is None          # delivered exactly once
    db.set_proposal_status(pid, "executed")
    assert db.get_proposal(pid)["executed_ts"] is not None


def test_pending_is_newest_and_single_query(db):
    a = db.create_proposal("entry", "BUY", "s", 1.0, None)
    b = db.create_proposal("entry", "SELL", "s", 2.0, None)
    assert db.pending_proposal()["id"] == b
    db.set_proposal_status(b, "expired")
    assert db.pending_proposal()["id"] == a
