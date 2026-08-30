# Regression test for the live sqlite3.InterfaceError ("bad parameter or
# other API misuse", 1458 occurrences in service.log by 2026-08-30): every
# thread shares the ONE SignalDb connection (check_same_thread=False), and
# reads used to run unlocked on the premise that "SQLite readers don't need
# it". That premise holds across connections, not on the same connection
# object — two threads running the SAME SQL concurrently race on pysqlite's
# per-connection statement cache and die with SQLITE_MISUSE. In production
# the collision is /heartbeat's get_kv/exec_mode against the ticker and
# poller threads reading the same keys.
import threading

from app.db import SignalDb


def _hammer(db, errors, n=400):
    try:
        for i in range(n):
            # Same SQL from every thread on purpose: the statement-cache
            # race needs identical query text. Mix in writes so the
            # read-vs-write interleave is exercised too.
            db.get_kv("exec_mode")
            db.exec_mode()
            if i % 20 == 0:
                db.set_kv("exec_mode", "auto")
    except Exception as e:  # noqa: BLE001 — any exception is the failure
        errors.append(e)


def test_shared_connection_survives_concurrent_readers(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    db.set_kv("exec_mode", "auto")
    errors: list[Exception] = []
    threads = [threading.Thread(target=_hammer, args=(db, errors))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrent db access raised: {errors[:3]}"
