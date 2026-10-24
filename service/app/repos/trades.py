"""Trades and Heartbeats repository mixin for SignalDb."""

from __future__ import annotations

import time


class TradeRepoMixin:
    def insert_heartbeat(self, hb: dict) -> bool:
        with self._lock:
            now = int(time.time())
            last = self._last_hb_ts
            if last is not None and now - last < 60:
                return False
            self.conn.execute(
                "INSERT INTO heartbeats (ts, equity, balance, floating_pl, open_count,"
                " kill_switch, exposure_min, active_strategy) VALUES (?,?,?,?,?,?,?,?)",
                (now, hb["equity"], hb["balance"], hb["floating_pl"], hb["open_count"],
                 int(hb["kill_switch"]), hb["exposure_min"], hb["active_strategy"]))
            self.conn.commit()
            self._last_hb_ts = now
            return True

    def equity_series(self, limit: int = 1440) -> list:
        rows = self.conn.execute(
            "SELECT ts, equity, balance, floating_pl FROM heartbeats"
            " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": t, "equity": e, "balance": b, "floating_pl": f}
                for t, e, b, f in reversed(rows)]

    def insert_trade(self, ev: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO trades (ts, event, symbol, strategy_id, direction, lots, price,"
                " sl, reason, ticket, profit, tp, final, entry_mode, htf_agree,"
                " ema200_agree, news_blackout, rsi_agree, sr_agree, sr_tier,"
                " slippage_pts, exec_ms, req_price)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), ev["event"], ev.get("symbol", "XAUUSD"), ev.get("strategy_id", "unknown"),
                 ev["direction"], ev["lots"], ev["price"], ev.get("sl", 0.0),
                 ev.get("reason", ""), ev.get("ticket", 0), ev.get("profit", 0.0),
                 ev.get("tp", 0.0), int(ev.get("final", True)),
                 ev.get("entry_mode", ""), int(ev.get("htf_agree", -1)),
                 int(ev.get("ema200_agree", -1)), int(ev.get("news_blackout", -1)),
                 int(ev.get("rsi_agree", -1)),
                 int(ev.get("sr_agree", -1)), str(ev.get("sr_tier", "") or ""),
                 float(ev.get("slippage_pts", 0.0)), int(ev.get("exec_ms", 0)),
                 float(ev.get("req_price", 0.0))))
            self.conn.commit()
            return cur.lastrowid

    def recent_trades(self, limit: int = 50, symbol: str | None = None) -> list:
        cols = ["id", "ts", "event", "symbol", "strategy_id", "direction", "lots", "price",
                "sl", "reason", "ticket", "screenshot_path", "profit", "render_path",
                "tp", "final", "entry_mode", "htf_agree", "ema200_agree",
                "news_blackout", "rsi_agree", "sr_agree", "sr_tier",
                "slippage_pts", "exec_ms", "req_price"]
        if symbol:
            rows = self.conn.execute(
                f"SELECT {', '.join(cols)} FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit)).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT {', '.join(cols)} FROM trades ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def slippage_stats(self) -> dict:
        """Aggregate slippage and execution latency stats."""
        row = self.conn.execute(
            "SELECT COUNT(*), AVG(slippage_pts), MAX(slippage_pts), AVG(exec_ms), MAX(exec_ms)"
            " FROM trades WHERE event IN ('open', 'add') AND (slippage_pts != 0 OR exec_ms > 0)"
        ).fetchone()
        if not row or not row[0]:
            return {"count": 0, "avg_slippage_pts": 0.0, "max_slippage_pts": 0.0,
                    "avg_exec_ms": 0, "max_exec_ms": 0}
        return {
            "count": row[0],
            "avg_slippage_pts": round(row[1] or 0.0, 2),
            "max_slippage_pts": round(row[2] or 0.0, 2),
            "avg_exec_ms": round(row[3] or 0, 1),
            "max_exec_ms": round(row[4] or 0, 1),
        }

    def realized_pnl(self, since_ts: int) -> tuple:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(profit), 0), COUNT(*) FROM trades"
            " WHERE event='close' AND ts >= ?", (int(since_ts),)).fetchone()
        return (row[0] or 0.0, row[1] or 0)

    def strategy_pnl(self) -> dict:
        out = {}
        for sid, total, count in self.conn.execute(
                "SELECT strategy_id, COALESCE(SUM(profit), 0), COUNT(*)"
                " FROM trades WHERE event='close' GROUP BY strategy_id"):
            out[sid or "unknown"] = (total or 0.0, count)
        return out

    def set_screenshot(self, trade_id: int, path: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE trades SET screenshot_path=? WHERE id=?", (path, trade_id))
            self.conn.commit()

    def set_render(self, trade_id: int, path: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE trades SET render_path=? WHERE id=?", (path, trade_id))
            self.conn.commit()
