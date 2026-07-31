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

_HB_SCHEMA = """CREATE TABLE IF NOT EXISTS heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  equity REAL, balance REAL, floating_pl REAL,
  open_count INTEGER, kill_switch INTEGER,
  exposure_min INTEGER, active_strategy TEXT
)"""

_TRADES_SCHEMA = """CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  event TEXT NOT NULL,
  strategy_id TEXT,
  direction TEXT,
  lots REAL,
  price REAL,
  sl REAL,
  reason TEXT,
  ticket INTEGER,
  screenshot_path TEXT
)"""


class SignalDb:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.execute(_HB_SCHEMA)
        self.conn.execute(_TRADES_SCHEMA)
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

    def insert_heartbeat(self, hb: dict) -> bool:
        now = int(time.time())
        last = self.conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
        if last is not None and now - last < 60:
            return False
        self.conn.execute(
            "INSERT INTO heartbeats (ts, equity, balance, floating_pl, open_count,"
            " kill_switch, exposure_min, active_strategy) VALUES (?,?,?,?,?,?,?,?)",
            (now, hb["equity"], hb["balance"], hb["floating_pl"], hb["open_count"],
             int(hb["kill_switch"]), hb["exposure_min"], hb["active_strategy"]))
        self.conn.commit()
        return True

    def equity_series(self, limit: int = 1440) -> list:
        rows = self.conn.execute(
            "SELECT ts, equity, balance, floating_pl FROM heartbeats"
            " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": t, "equity": e, "balance": b, "floating_pl": f}
                for t, e, b, f in reversed(rows)]

    def recent_signals(self, limit: int = 50) -> list:
        cols = ["id", "created_ts", "bar_time", "strategy_id", "signal", "price",
                "direction", "confidence", "regime", "verdict", "is_active",
                "outcome_move", "ai_correct"]
        rows = self.conn.execute(
            f"SELECT {', '.join(cols)} FROM signals ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def insert_trade(self, ev: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO trades (ts, event, strategy_id, direction, lots, price,"
            " sl, reason, ticket) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(time.time()), ev["event"], ev.get("strategy_id", "unknown"),
             ev["direction"], ev["lots"], ev["price"], ev.get("sl", 0.0),
             ev.get("reason", ""), ev.get("ticket", 0)))
        self.conn.commit()
        return cur.lastrowid

    def recent_trades(self, limit: int = 50) -> list:
        cols = ["id", "ts", "event", "strategy_id", "direction", "lots", "price",
                "sl", "reason", "ticket", "screenshot_path"]
        rows = self.conn.execute(
            f"SELECT {', '.join(cols)} FROM trades ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def set_screenshot(self, trade_id: int, path: str) -> None:
        self.conn.execute(
            "UPDATE trades SET screenshot_path=? WHERE id=?", (path, trade_id))
        self.conn.commit()
