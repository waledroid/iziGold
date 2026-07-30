import sqlite3
import time

_SCHEMA = """CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  bar_time INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  signal TEXT NOT NULL,
  price REAL NOT NULL,
  direction TEXT, confidence REAL, regime TEXT, verdict TEXT,
  mode TEXT, ai_available INTEGER,
  outcome_price REAL, outcome_move REAL, ai_correct INTEGER,
  strategy_id TEXT, is_active INTEGER DEFAULT 1
)"""


class SignalDb:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signals)")}
        if "strategy_id" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN strategy_id TEXT")
        if "is_active" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN is_active INTEGER DEFAULT 1")
        self.conn.commit()

    def insert_signal(self, *, bar_time, symbol, signal, price, direction,
                      confidence, regime, verdict, mode, ai_available,
                      strategy_id="unknown", is_active=True) -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
            " confidence, regime, verdict, mode, ai_available, strategy_id, is_active)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), bar_time, symbol, signal, price, direction,
             confidence, regime, verdict, mode, int(ai_available),
             strategy_id, int(is_active)))
        self.conn.commit()
        return cur.lastrowid

    def resolve_outcomes(self, candles, horizon_bars: int = 16) -> int:
        bar_seconds = candles[1].t - candles[0].t
        resolved = 0
        rows = self.conn.execute(
            "SELECT id, bar_time, price, direction FROM signals"
            " WHERE outcome_price IS NULL").fetchall()
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
        return resolved

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        done = self.conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(ai_correct) * 100, 0) FROM signals"
            " WHERE outcome_price IS NOT NULL").fetchone()
        by_strategy = {}
        rows = self.conn.execute(
            "SELECT COALESCE(strategy_id, 'pre-framework'), COUNT(*),"
            " COUNT(outcome_price),"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN (signal='BUY' AND outcome_move > 0)"
            "          OR (signal='SELL' AND outcome_move < 0)"
            "   THEN 1.0 ELSE 0.0 END END) * 100,"
            " AVG(CASE WHEN outcome_price IS NOT NULL THEN"
            "   CASE WHEN signal='BUY' THEN outcome_move ELSE -outcome_move END END)"
            " FROM signals WHERE signal IN ('BUY','SELL')"
            " GROUP BY COALESCE(strategy_id, 'pre-framework')").fetchall()
        for sid, count, resolved, hit, avg in rows:
            by_strategy[sid] = {"signals": count, "resolved": resolved,
                                "hit_pct": round(hit or 0.0, 1),
                                "avg_move": round(avg or 0.0, 2)}
        return {"total": total, "resolved": done[0],
                "ai_correct_pct": round(done[1], 1), "by_strategy": by_strategy}
