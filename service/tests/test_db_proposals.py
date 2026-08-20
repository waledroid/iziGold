import pytest
import threading
import time
import sqlite3
from app.db import SignalDb


@pytest.fixture
def db(tmp_path):
    """Temp-file Database fixture matching test_db.py conventions."""
    return SignalDb(str(tmp_path / "t.db"))


def test_exec_mode_default_and_set(db):
    # A fresh install defaults to AUTO (owner decision 2026-08-20): the
    # service is the authority on mode, so "manual" here would override an
    # EA attached in AUTO on its very first heartbeat.
    assert db.exec_mode() == "auto"
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


def test_last_executed_entry_returns_newest_executed_entry(db):
    assert db.last_executed_entry() is None
    a = db.create_proposal("entry", "BUY", "s", 1.0, None)
    db.set_proposal_status(a, "executed")
    b = db.create_proposal("entry", "SELL", "s", 2.0, None)
    # b is still pending -- not executed -- so a remains the newest match
    assert db.last_executed_entry()["id"] == a
    db.set_proposal_status(b, "executed")
    assert db.last_executed_entry()["id"] == b
    assert db.last_executed_entry()["direction"] == "SELL"


def test_set_proposal_status_guarded_transition_succeeds_when_status_matches(db):
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    ok = db.set_proposal_status(pid, "approved", expected="pending")
    assert ok is True
    assert db.get_proposal(pid)["status"] == "approved"


def test_set_proposal_status_guarded_transition_fails_on_mismatch(db):
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    db.set_proposal_status(pid, "expired")   # unconditional -- row is now 'expired'
    ok = db.set_proposal_status(pid, "approved", expected="pending")
    assert ok is False
    # the row must be untouched by the failed guarded transition
    assert db.get_proposal(pid)["status"] == "expired"


def test_set_proposal_status_unconditional_when_no_expected(db):
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    ok = db.set_proposal_status(pid, "approved")
    assert ok is True
    assert db.get_proposal(pid)["status"] == "approved"


def test_pending_proposal_status_param_selects_approved(db):
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    assert db.pending_proposal(status="approved") is None
    db.set_proposal_status(pid, "approved")
    assert db.pending_proposal() is None            # no longer pending
    assert db.pending_proposal(status="approved")["id"] == pid


def test_stale_approved_and_stale_dispatched_use_decided_ts_cutoff(db):
    pid = db.create_proposal("entry", "BUY", "s", 1.0, None)
    db.set_proposal_status(pid, "approved")
    now = int(time.time())
    db.conn.execute("UPDATE proposals SET decided_ts=? WHERE id=?", (now - 121, pid))
    db.conn.commit()
    assert [r["id"] for r in db.stale_approved(120)] == [pid]
    assert db.stale_approved(130) == []              # not old enough for a wider window
    assert db.stale_dispatched(120) == []             # wrong status entirely
    # Move pid out of the 'approved' pool so pop_approved_command (oldest-first)
    # below picks pid2, not pid.
    db.set_proposal_status(pid, "expired", expected="approved")

    pid2 = db.create_proposal("entry", "SELL", "s", 2.0, None)
    db.set_proposal_status(pid2, "approved")
    db.pop_approved_command()                         # -> 'dispatched'; decided_ts untouched
    db.conn.execute("UPDATE proposals SET decided_ts=? WHERE id=?", (now - 181, pid2))
    db.conn.commit()
    assert [r["id"] for r in db.stale_dispatched(180)] == [pid2]
    assert db.stale_dispatched(200) == []


def test_pending_is_newest_and_single_query(db):
    a = db.create_proposal("entry", "BUY", "s", 1.0, None)
    b = db.create_proposal("entry", "SELL", "s", 2.0, None)
    assert db.pending_proposal()["id"] == b
    db.set_proposal_status(b, "expired")
    assert db.pending_proposal()["id"] == a


def test_pop_approved_command_concurrent_exactly_once(tmp_path):
    """Regression: two threads calling pop_approved_command on same DB must not both get the row.
    Without proper locking, both threads could SELECT the same approved row before either UPDATEs it.
    Uses threading.Barrier to synchronize concurrent access and verifies exactly-once delivery."""
    for iteration in range(10):  # Loop to catch race conditions that might be intermittent
        db = SignalDb(str(tmp_path / f"t_{iteration}.db"))
        pid = db.create_proposal("entry", "BUY", "halftrend_ema_v1", 4066.5, None)
        db.set_proposal_status(pid, "approved")

        results = {}
        barrier = threading.Barrier(2)

        def pop_in_thread(thread_id):
            barrier.wait()  # Synchronize both threads to start at same time
            results[thread_id] = db.pop_approved_command()

        t1 = threading.Thread(target=pop_in_thread, args=(1,))
        t2 = threading.Thread(target=pop_in_thread, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one thread should get a row, the other None
        got_row = [r for r in results.values() if r is not None]
        got_none = [r for r in results.values() if r is None]
        assert len(got_row) == 1, f"Expected 1 row got by thread, got {len(got_row)}"
        assert len(got_none) == 1, f"Expected 1 None got by thread, got {len(got_none)}"
        assert got_row[0]["id"] == pid
        assert got_row[0]["status"] == "dispatched"


def test_pop_approved_command_durability(tmp_path):
    """Durability check: pop_approved_command must commit to disk, not just memory.
    After a pop, a separate sqlite3 connection to the same file must see status='dispatched'.
    Without commit(), a crash/restart would revert the change and re-deliver the command."""
    db_path = str(tmp_path / "durability.db")
    db = SignalDb(db_path)
    pid = db.create_proposal("entry", "BUY", "halftrend_ema_v1", 4066.5, None)
    db.set_proposal_status(pid, "approved")

    # Pop the command (should update status to 'dispatched' and commit)
    cmd = db.pop_approved_command()
    assert cmd is not None
    assert cmd["status"] == "dispatched"

    # Open a NEW connection to verify the change is on disk
    verify_conn = sqlite3.connect(db_path)
    row = verify_conn.execute(
        "SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()
    verify_conn.close()

    # Assert the change is persisted (if not committed, this would still be 'approved')
    assert row[0] == "dispatched", \
        f"Status not durably committed to disk; got {row[0]}"
