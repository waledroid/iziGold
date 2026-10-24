"""Backtest runs repository mixin for SignalDb."""

from __future__ import annotations

import time

from app.repos.base import row_to_dict


class BacktestRepoMixin:
    def insert_backtest_run(self, params_json: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO backtest_runs (created_ts, params_json, status)"
                " VALUES (?,?, 'running')", (int(time.time()), params_json))
            self.conn.commit()
            return cur.lastrowid

    def finish_backtest_run(self, run_id: int, *, status: str, error=None,
                            stats_json=None, report_path=None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE backtest_runs SET status=?, error=?, stats_json=?,"
                " report_path=? WHERE id=?",
                (status, error, stats_json, report_path, run_id))
            self.conn.commit()

    def get_backtest_run(self, run_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM backtest_runs WHERE id=?", (run_id,))
        return row_to_dict(cur, cur.fetchone())

    def recent_backtest_runs(self, limit: int = 20) -> list:
        cur = self.conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
