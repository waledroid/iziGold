"""AI Supervisory Agent grades, actions, lessons, and scorecard repository mixin for SignalDb."""

from __future__ import annotations

import time

from app.repos.base import row_to_dict


class AgentRepoMixin:
    def insert_ai_grade(self, *, bar_time, symbol, timeframe, strategy_id="",
                        signal="", model="", prompt_version="", direction="neutral",
                        confidence=0.0, regime_agreement="", risk_flags="",
                        action="", rationale="", tokens_in=0, tokens_out=0,
                        latency_ms=0, entry_quality=None, expected_r=None,
                        invalidation=None, window_state="", regime="",
                        session="") -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO ai_grades (created_ts, bar_time, symbol, timeframe,"
                " strategy_id, signal, model, prompt_version, direction, confidence,"
                " regime_agreement, risk_flags, action, rationale, tokens_in,"
                " tokens_out, latency_ms, entry_quality, expected_r, invalidation,"
                " window_state, regime, session)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), bar_time, symbol, timeframe, strategy_id,
                 signal, model, prompt_version, direction, float(confidence),
                 regime_agreement, risk_flags, action, rationale, int(tokens_in),
                 int(tokens_out), int(latency_ms),
                 None if entry_quality is None else int(entry_quality),
                 None if expected_r is None else float(expected_r),
                 None if invalidation is None else float(invalidation),
                 window_state, regime, session))
            self.conn.commit()
            return cur.lastrowid

    def agent_accuracy(self) -> dict:
        try:
            row = self.conn.execute(
                "SELECT COUNT(*), AVG(ai_correct) * 100 FROM ai_grades"
                " WHERE ai_correct IS NOT NULL").fetchone()
            by_dir = {}
            for d, n, acc in self.conn.execute(
                    "SELECT direction, COUNT(*), AVG(ai_correct) * 100 FROM ai_grades"
                    " WHERE ai_correct IS NOT NULL GROUP BY direction").fetchall():
                by_dir[d] = {"n": n, "acc_pct": round(acc or 0.0, 1)}
            return {"resolved": row[0] or 0,
                    "acc_pct": round(row[1] or 0.0, 1), "by_direction": by_dir}
        except Exception:
            return {"resolved": 0, "acc_pct": 0.0, "by_direction": {}}

    def ungraded_signal(self, symbol: str, timeframe: str):
        cur = self.conn.execute(
            "SELECT s.bar_time, s.signal, s.strategy_id, s.price"
            " FROM signals s WHERE s.symbol=? AND s.timeframe=?"
            " AND s.signal IN ('BUY','SELL','EXIT')"
            " AND NOT EXISTS (SELECT 1 FROM ai_grades g WHERE g.symbol=s.symbol"
            " AND g.timeframe=s.timeframe AND g.bar_time=s.bar_time)"
            " ORDER BY s.id DESC LIMIT 1", (symbol, timeframe))
        row = cur.fetchone()
        return row_to_dict(cur, row)

    def latest_ai_grade(self, symbol: str, timeframe: str,
                        bar_time: int | None = None):
        sql = ("SELECT * FROM ai_grades WHERE symbol=? AND timeframe=?")
        params: list = [symbol, timeframe]
        if bar_time is not None:
            sql += " AND bar_time=?"
            params.append(bar_time)
        sql += " ORDER BY id DESC LIMIT 1"
        cur = self.conn.execute(sql, params)
        row = cur.fetchone()
        return row_to_dict(cur, row)

    def recent_ai_grades(self, limit: int = 50) -> list:
        cur = self.conn.execute(
            "SELECT * FROM ai_grades ORDER BY id DESC LIMIT ?", (int(limit),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def similar_historical_setups(self, regime: str = "", signal: str = "", limit: int = 3) -> list[dict]:
        rows = []
        if regime and signal:
            cur = self.conn.execute(
                "SELECT signal, regime, session, window_state, direction, confidence, "
                "ai_correct, outcome_move, entry_quality, rationale "
                "FROM ai_grades "
                "WHERE regime = ? AND signal = ? AND ai_correct IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (regime, signal, int(limit))
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        if len(rows) < limit and (regime or signal):
            needed = limit - len(rows)
            cur = self.conn.execute(
                "SELECT signal, regime, session, window_state, direction, confidence, "
                "ai_correct, outcome_move, entry_quality, rationale "
                "FROM ai_grades "
                "WHERE (regime = ? OR signal = ?) AND ai_correct IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (regime, signal, int(needed + len(rows)))
            )
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                if d not in rows:
                    rows.append(d)
                if len(rows) >= limit:
                    break

        if len(rows) < limit:
            needed = limit - len(rows)
            cur = self.conn.execute(
                "SELECT signal, regime, session, window_state, direction, confidence, "
                "ai_correct, outcome_move, entry_quality, rationale "
                "FROM ai_grades "
                "WHERE ai_correct IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (int(needed),)
            )
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                if d not in rows:
                    rows.append(d)
                if len(rows) >= limit:
                    break

        return rows[:limit]

    def resolve_grade_outcomes(self, candles, horizon_bars: int = 16,
                               symbol: str | None = None,
                               timeframe: str | None = None) -> int:
        if not candles or len(candles) < 2:
            return 0
        with self._lock:
            bar_seconds = candles[1].t - candles[0].t
            if bar_seconds <= 0:
                return 0
            sql = ("SELECT id, bar_time, direction FROM ai_grades"
                   " WHERE ai_correct IS NOT NULL"
                   " AND direction IN ('bullish','bearish')")
            # Score un-scored grades
            sql = ("SELECT id, bar_time, direction FROM ai_grades"
                   " WHERE ai_correct IS NULL"
                   " AND direction IN ('bullish','bearish')")
            params: list = []
            if symbol is not None:
                sql += " AND symbol = ?"
                params.append(symbol)
            if timeframe is not None:
                sql += " AND timeframe = ?"
                params.append(timeframe)
            by_time = {int(c.t): c for c in candles}
            resolved = 0
            for gid, bar_time, direction in self.conn.execute(sql, params).fetchall():
                base = by_time.get(int(bar_time))
                if base is None:
                    continue
                target = int(bar_time) + horizon_bars * bar_seconds
                hit = next((x for x in candles if x.t >= target), None)
                if hit is None:
                    continue
                move = hit.c - base.c
                correct = int(move > 0) if direction == "bullish" else int(move < 0)
                self.conn.execute(
                    "UPDATE ai_grades SET ai_correct=?, outcome_move=? WHERE id=?",
                    (correct, move, gid))
                resolved += 1
            self.conn.commit()
            return resolved

    def insert_agent_action(self, *, intent: str, level: str = "L3",
                            payload: str = "", status: str = "pending",
                            proposal_id: int | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO agent_actions (created_ts, level, intent, payload,"
                " status, proposal_id) VALUES (?,?,?,?,?,?)",
                (int(time.time()), level, intent, payload, status, proposal_id))
            self.conn.commit()
            return cur.lastrowid

    def finish_agent_action(self, action_id: int, *, status: str,
                            result: str = "", reason: str = "",
                            proposal_id: int | None = None) -> None:
        with self._lock:
            if proposal_id is None:
                self.conn.execute(
                    "UPDATE agent_actions SET status=?, result=?, reason=?,"
                    " decided_ts=? WHERE id=?",
                    (status, result, reason, int(time.time()), action_id))
            else:
                self.conn.execute(
                    "UPDATE agent_actions SET status=?, result=?, reason=?,"
                    " proposal_id=?, decided_ts=? WHERE id=?",
                    (status, result, reason, proposal_id, int(time.time()),
                     action_id))
            self.conn.commit()

    def agent_actions_since(self, since_ts: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM agent_actions WHERE created_ts >= ?",
            (int(since_ts),)).fetchone()[0]

    def recent_agent_actions(self, limit: int = 50) -> list:
        cur = self.conn.execute(
            "SELECT * FROM agent_actions ORDER BY id DESC LIMIT ?", (int(limit),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def unreflected_grades(self, limit: int = 1) -> list:
        cur = self.conn.execute(
            "SELECT * FROM ai_grades g WHERE g.ai_correct = 0"
            " AND g.direction IN ('bullish','bearish')"
            " AND NOT EXISTS (SELECT 1 FROM agent_lessons l WHERE l.grade_id = g.id)"
            " ORDER BY g.bar_time ASC LIMIT ?", (int(limit),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def insert_lesson(self, *, grade_id=None, bar_time=None, symbol="",
                      timeframe="", direction="", window_state="", regime="",
                      session="", entry_quality=None, outcome_move=None,
                      lesson="") -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO agent_lessons (created_ts, grade_id, bar_time, symbol,"
                " timeframe, direction, window_state, regime, session, entry_quality,"
                " outcome_move, lesson) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(time.time()), grade_id, bar_time, symbol, timeframe,
                 direction, window_state, regime, session,
                 None if entry_quality is None else int(entry_quality),
                 None if outcome_move is None else float(outcome_move), lesson))
            self.conn.commit()
            return cur.lastrowid

    def recent_lessons(self, limit: int = 5) -> list:
        cur = self.conn.execute(
            "SELECT * FROM agent_lessons ORDER BY id DESC LIMIT ?", (int(limit),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _group_accuracy(self, column: str) -> dict:
        out = {}
        try:
            rows = self.conn.execute(
                f"SELECT COALESCE(NULLIF({column}, ''), 'unknown'), COUNT(*),"
                " AVG(ai_correct) * 100 FROM ai_grades WHERE ai_correct IS NOT NULL"
                f" GROUP BY COALESCE(NULLIF({column}, ''), 'unknown')").fetchall()
            for key, n, acc in rows:
                out[str(key)] = {"n": n, "acc_pct": round(acc or 0.0, 1)}
        except Exception:
            pass
        return out

    def agent_scorecard(self) -> dict:
        base = self.agent_accuracy()
        bands = {}
        try:
            for lo, hi, label in ((0, 39, "0-39"), (40, 69, "40-69"),
                                  (70, 100, "70-100")):
                row = self.conn.execute(
                    "SELECT COUNT(*), AVG(ai_correct) * 100 FROM ai_grades"
                    " WHERE ai_correct IS NOT NULL AND entry_quality BETWEEN ? AND ?",
                    (lo, hi)).fetchone()
                if row and row[0]:
                    bands[label] = {"n": row[0], "acc_pct": round(row[1] or 0.0, 1)}
        except Exception:
            pass
        try:
            er = self.conn.execute(
                "SELECT AVG(CASE WHEN ai_correct=1 THEN expected_r END),"
                " AVG(CASE WHEN ai_correct=0 THEN expected_r END)"
                " FROM ai_grades WHERE ai_correct IS NOT NULL"
                " AND expected_r IS NOT NULL").fetchone()
            expected_r = {"avg_on_win": round(er[0], 2) if er and er[0] is not None else None,
                          "avg_on_loss": round(er[1], 2) if er and er[1] is not None else None}
        except Exception:
            expected_r = {"avg_on_win": None, "avg_on_loss": None}
        return {
            "overall": {"resolved": base["resolved"], "acc_pct": base["acc_pct"]},
            "by_direction": base["by_direction"],
            "by_window_state": self._group_accuracy("window_state"),
            "by_regime": self._group_accuracy("regime"),
            "by_session": self._group_accuracy("session"),
            "by_entry_quality": bands,
            "expected_r": expected_r,
        }
