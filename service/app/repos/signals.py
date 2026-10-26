"""Signals repository mixin for SignalDb."""

from __future__ import annotations

import copy
import time


class SignalRepoMixin:
    def insert_signal(self, *, bar_time, symbol, signal, price, direction,
                      confidence, regime, verdict, mode, ai_available,
                      strategy_id="unknown", is_active=True, timeframe="",
                      refusal_reason="") -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
                " confidence, regime, verdict, mode, ai_available, strategy_id, is_active,"
                " timeframe, refusal_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), bar_time, symbol, signal, price, direction,
                 confidence, regime, verdict, mode, int(ai_available),
                 strategy_id, int(is_active), timeframe, refusal_reason))
            self.conn.commit()
            self._signals_version += 1
            return cur.lastrowid

    def resolve_outcomes(self, candles, horizon_bars: int = 16,
                         symbol: str | None = None,
                         timeframe: str | None = None) -> int:
        with self._lock:
            bar_seconds = candles[1].t - candles[0].t
            resolved = 0
            sql = ("SELECT id, bar_time, price, direction FROM signals"
                   " WHERE outcome_price IS NULL")
            params: list = []
            if symbol is not None:
                sql += " AND symbol = ?"
                params.append(symbol)
            if timeframe is not None:
                sql += " AND timeframe = ?"
                params.append(timeframe)
            rows = self.conn.execute(sql, params).fetchall()
            for rid, bar_time, price, direction in rows:
                target = bar_time + horizon_bars * bar_seconds
                hit = next((x for x in candles if x.t >= target), None)
                if hit is None:
                    continue
                move = hit.c - price
                correct = None
                if direction == "bullish":
                    correct = int(move > 0)
                elif direction == "bearish":
                    correct = int(move < 0)
                self.conn.execute(
                    "UPDATE signals SET outcome_price=?, outcome_move=?, ai_correct=? WHERE id=?",
                    (hit.c, move, correct, rid))
                resolved += 1
            self.conn.commit()
            if resolved:
                self._signals_version += 1
            return resolved

    def stats(self) -> dict:
        if self._stats_cache is not None \
           and self._stats_cache_version == self._signals_version:
            return copy.deepcopy(self._stats_cache)
        stats = self._stats_uncached()
        self._stats_cache = stats
        self._stats_cache_version = self._signals_version
        return copy.deepcopy(stats)

    def _stats_uncached(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        done = self.conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(ai_correct) * 100, 0) FROM signals"
            " WHERE outcome_price IS NOT NULL").fetchone()
        by_strategy = {}
        rows = self.conn.execute(
            "SELECT COALESCE(strategy_id, 'pre-framework'), COALESCE(timeframe, ''),"
            " COUNT(*), COUNT(outcome_price),"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN (signal='BUY' AND outcome_move > 0)"
            "          OR (signal='SELL' AND outcome_move < 0)"
            "   THEN 1.0 ELSE 0.0 END END) * 100,"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN signal='BUY' THEN outcome_move ELSE -outcome_move END END)"
            " FROM signals WHERE signal IN ('BUY','SELL')"
            " GROUP BY COALESCE(strategy_id, 'pre-framework'),"
            " COALESCE(timeframe, '')").fetchall()
        for sid, tf, count, resolved, hit, avg in rows:
            key = f"{sid} @{tf}" if tf else sid
            by_strategy[key] = {"signals": count, "resolved": resolved,
                                "hit_pct": round(hit or 0.0, 1),
                                "avg_move": round(avg or 0.0, 2)}
        return {"total": total, "resolved": done[0],
                "ai_correct_pct": round(done[1], 1), "by_strategy": by_strategy}

    def recent_signals(self, limit: int = 50) -> list:
        cols = ["id", "created_ts", "bar_time", "strategy_id", "timeframe", "signal", "price",
                "direction", "confidence", "regime", "verdict", "is_active",
                "outcome_move", "ai_correct", "refusal_reason"]
        rows = self.conn.execute(
            f"SELECT {', '.join(cols)} FROM signals ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def strategy_ids(self) -> list:
        rows = self.conn.execute(
            "SELECT DISTINCT strategy_id FROM signals WHERE strategy_id IS NOT NULL"
            " ORDER BY strategy_id").fetchall()
        return [r[0] for r in rows]
