"""Proposals and Commands repository mixin for SignalDb."""

from __future__ import annotations

import sqlite3
import time

from app.repos.base import row_to_dict


class ProposalRepoMixin:
    def create_proposal(self, kind: str, direction: str, strategy_id: str,
                        price: float, signal_id: int | None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO proposals(created_ts, kind, direction, strategy_id,"
                " price, signal_id) VALUES(?,?,?,?,?,?)",
                (int(time.time()), kind, direction, strategy_id, price, signal_id))
            self.conn.commit()
            return cur.lastrowid

    def get_proposal(self, pid: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM proposals WHERE id=?", (pid,))
        row = cur.fetchone()
        return row_to_dict(cur, row)

    def pending_proposal(self, kind: str | None = None, status: str = "pending") -> dict | None:
        q = "SELECT * FROM proposals WHERE status=?"
        args = [status]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        cur = self.conn.execute(q + " ORDER BY id DESC LIMIT 1", tuple(args))
        row = cur.fetchone()
        return row_to_dict(cur, row)

    def active_proposal(self) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM proposals WHERE status IN ('pending','approved','dispatched')"
            " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1"
            " ELSE 2 END, id DESC LIMIT 1")
        return row_to_dict(cur, cur.fetchone())

    def set_proposal_status(self, pid: int, status: str, expected: str | None = None) -> bool:
        now = int(time.time())
        with self._lock, self.conn:
            if expected is not None:
                cur = self.conn.execute(
                    "UPDATE proposals SET status=? WHERE id=? AND status=?",
                    (status, pid, expected))
                if cur.rowcount != 1:
                    return False
            else:
                self.conn.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))
            if status in ("approved", "skipped", "expired", "blocked"):
                self.conn.execute(
                    "UPDATE proposals SET decided_ts=? WHERE id=? AND decided_ts IS NULL",
                    (now, pid))
            if status == "executed":
                self.conn.execute(
                    "UPDATE proposals SET executed_ts=? WHERE id=?", (now, pid))
        return True

    def set_proposal_message(self, pid: int, tg_message_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE proposals SET tg_message_id=? WHERE id=?", (tg_message_id, pid))
            self.conn.commit()

    def last_executed_entry(self) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM proposals WHERE kind='entry' AND status='executed'"
            " ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row_to_dict(cur, row)

    def pop_approved_command(self) -> dict | None:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE proposals SET status='dispatched' "
                "WHERE id = (SELECT id FROM proposals WHERE status='approved' ORDER BY id ASC LIMIT 1) "
                "RETURNING *")
            row = cur.fetchone()
            row_dict = row_to_dict(cur, row) if row else None
            try:
                self.conn.commit()
            except sqlite3.OperationalError:
                pass
            return row_dict

    def _rows_older_than(self, status: str, older_than_s: int) -> list:
        cutoff = int(time.time()) - older_than_s
        cur = self.conn.execute(
            "SELECT * FROM proposals WHERE status=? AND decided_ts IS NOT NULL"
            " AND decided_ts < ? ORDER BY id ASC", (status, cutoff))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def stale_approved(self, older_than_s: int) -> list:
        return self._rows_older_than("approved", older_than_s)

    def stale_dispatched(self, older_than_s: int) -> list:
        return self._rows_older_than("dispatched", older_than_s)
