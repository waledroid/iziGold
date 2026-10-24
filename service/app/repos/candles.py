"""Candles, Spread history, and WAL Maintenance repository mixin for SignalDb."""

from __future__ import annotations

import sqlite3
import time


class CandleRepoMixin:
    def upsert_spread(self, *, bar_time: int, spread_min: float,
                      spread_avg: float, spread_max: float) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO spread_history"
                " (bar_time, spread_min, spread_avg, spread_max) VALUES (?,?,?,?)",
                (bar_time, spread_min, spread_avg, spread_max))
            self.conn.commit()

    def spread_stats(self, hours: int = 24) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), MIN(spread_min), AVG(spread_avg), MAX(spread_max)"
            " FROM spread_history WHERE bar_time >="
            " (SELECT MAX(bar_time) FROM spread_history) - ?",
            (hours * 3600,)).fetchone()
        n = row[0] or 0
        if n == 0:
            return {"n": 0, "min": 0.0, "avg": 0.0, "max": 0.0}
        return {"n": n, "min": row[1], "avg": row[2], "max": row[3]}

    def upsert_candles(self, symbol: str, timeframe: str, candles) -> int:
        rows = []
        for c in candles:
            if isinstance(c, dict):
                rows.append((symbol, timeframe, int(c["t"]), c["o"], c["h"],
                             c["l"], c["c"], c.get("v", 0.0)))
            else:
                rows.append((symbol, timeframe, int(c.t), c.o, c.h,
                             c.l, c.c, getattr(c, "v", 0.0)))
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO candles (symbol, timeframe, bar_time,"
                " o, h, l, c, v) VALUES (?,?,?,?,?,?,?,?)", rows)
            self.conn.commit()
        return len(rows)

    def get_candles(self, symbol: str, timeframe: str, start_ts=None,
                    end_ts=None, limit=None) -> list:
        q = ("SELECT bar_time, o, h, l, c, v FROM candles"
             " WHERE symbol=? AND timeframe=?")
        args = [symbol, timeframe]
        if start_ts is not None:
            q += " AND bar_time >= ?"; args.append(int(start_ts))
        if end_ts is not None:
            q += " AND bar_time <= ?"; args.append(int(end_ts))
        if limit is not None:
            q = (f"SELECT * FROM ({q} ORDER BY bar_time DESC LIMIT ?)"
                 " ORDER BY bar_time ASC")
            args.append(int(limit))
        else:
            q += " ORDER BY bar_time ASC"
        rows = self.conn.execute(q, args).fetchall()
        return [{"t": t, "o": o, "h": h, "l": l, "c": c, "v": v or 0.0}
                for t, o, h, l, c, v in rows]

    def count_candles(self, symbol: str, timeframe: str,
                      start_ts=None, end_ts=None) -> int:
        q = "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?"
        args = [symbol, timeframe]
        if start_ts is not None:
            q += " AND bar_time >= ?"; args.append(int(start_ts))
        if end_ts is not None:
            q += " AND bar_time <= ?"; args.append(int(end_ts))
        return self.conn.execute(q, args).fetchone()[0]

    def candles_range(self, symbol: str, timeframe: str) -> dict | None:
        row = self.conn.execute(
            "SELECT MIN(bar_time), MAX(bar_time), COUNT(*) FROM candles"
            " WHERE symbol=? AND timeframe=?", (symbol, timeframe)).fetchone()
        if not row or not row[2]:
            return None
        return {"start": row[0], "end": row[1], "count": row[2]}

    def latest_candle_series(self) -> tuple | None:
        row = self.conn.execute(
            "SELECT symbol, timeframe FROM candles"
            " ORDER BY bar_time DESC LIMIT 1").fetchone()
        return tuple(row) if row else None

    def checkpoint_wal(self, mode: str = "PASSIVE") -> tuple[int, int, int]:
        with self._lock:
            try:
                row = self.conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
                return tuple(row) if row else (0, 0, 0)
            except sqlite3.OperationalError:
                return (0, 0, 0)

    def prune_old_data(self, *, heartbeat_days: int = 7,
                       candle_days: int = 60, spread_days: int = 30) -> dict:
        now = int(time.time())
        pruned = {}
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM heartbeats WHERE ts < ?", (now - heartbeat_days * 86400,))
            pruned["heartbeats"] = cur.rowcount

            max_st_row = self.conn.execute("SELECT MAX(bar_time) FROM spread_history").fetchone()
            max_st = max_st_row[0] if max_st_row else None
            if max_st:
                cur = self.conn.execute(
                    "DELETE FROM spread_history WHERE bar_time < ?",
                    (max_st - spread_days * 86400,))
                pruned["spread_history"] = cur.rowcount
            else:
                pruned["spread_history"] = 0

            max_ct_row = self.conn.execute("SELECT MAX(bar_time) FROM candles").fetchone()
            max_ct = max_ct_row[0] if max_ct_row else None
            if max_ct:
                cur = self.conn.execute(
                    "DELETE FROM candles WHERE bar_time < ?",
                    (max_ct - candle_days * 86400,))
                pruned["candles"] = cur.rowcount
            else:
                pruned["candles"] = 0

            self.conn.commit()
            return pruned
