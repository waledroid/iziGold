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
  strategy_id TEXT, is_active INTEGER DEFAULT 1, timeframe TEXT
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
  screenshot_path TEXT,
  profit REAL DEFAULT 0,
  render_path TEXT,
  tp REAL DEFAULT 0,
  final INTEGER DEFAULT 1
)"""

_KV_SCHEMA = """CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
)"""

_PROFILE_SCHEMA = """CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT, email TEXT, phone TEXT,
  telegram_bot_token TEXT, telegram_chat_id TEXT,
  risk_per_trade_pct REAL, max_drawdown_pct REAL, profit_target_pct REAL,
  window_start_hour INTEGER, window_end_hour INTEGER,
  broker_name TEXT, account_login TEXT, account_type TEXT,
  experience_level TEXT, risk_ack INTEGER,
  created_ts INTEGER, updated_ts INTEGER, risk_ack_ts INTEGER
)"""

_SPREAD_SCHEMA = """CREATE TABLE IF NOT EXISTS spread_history (
  bar_time INTEGER PRIMARY KEY,
  spread_min REAL,
  spread_avg REAL,
  spread_max REAL
)"""

_PROPOSALS_SCHEMA = """CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  direction TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  price REAL NOT NULL,
  signal_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  tg_message_id INTEGER,
  decided_ts INTEGER,
  executed_ts INTEGER
)"""

PROFILE_FIELDS = ["name", "email", "phone", "telegram_bot_token",
                  "telegram_chat_id", "risk_per_trade_pct", "max_drawdown_pct",
                  "profit_target_pct", "broker_name", "account_login",
                  "account_type", "risk_ack"]


def profile_completion(profile) -> int:
    if not profile:
        return 0
    filled = sum(1 for f in PROFILE_FIELDS
                 if profile.get(f) not in (None, ""))
    return round(100 * filled / len(PROFILE_FIELDS))


class SignalDb:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.execute(_HB_SCHEMA)
        self.conn.execute(_TRADES_SCHEMA)
        self.conn.execute(_KV_SCHEMA)
        self.conn.execute(_PROFILE_SCHEMA)
        self.conn.execute(_SPREAD_SCHEMA)
        self.conn.execute(_PROPOSALS_SCHEMA)
        self.conn.commit()
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signals)")}
        if "strategy_id" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN strategy_id TEXT")
        if "is_active" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN is_active INTEGER DEFAULT 1")
        if "timeframe" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN timeframe TEXT")
        trade_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(trades)")}
        if "profit" not in trade_cols:
            self.conn.execute("ALTER TABLE trades ADD COLUMN profit REAL DEFAULT 0")
        if "render_path" not in trade_cols:
            self.conn.execute("ALTER TABLE trades ADD COLUMN render_path TEXT")
        if "tp" not in trade_cols:
            try:
                self.conn.execute("ALTER TABLE trades ADD COLUMN tp REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        if "final" not in trade_cols:
            try:
                self.conn.execute("ALTER TABLE trades ADD COLUMN final INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass
        if "entry_mode" not in trade_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN entry_mode TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def insert_signal(self, *, bar_time, symbol, signal, price, direction,
                      confidence, regime, verdict, mode, ai_available,
                      strategy_id="unknown", is_active=True, timeframe="") -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (created_ts, bar_time, symbol, signal, price, direction,"
            " confidence, regime, verdict, mode, ai_available, strategy_id, is_active,"
            " timeframe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), bar_time, symbol, signal, price, direction,
             confidence, regime, verdict, mode, int(ai_available),
             strategy_id, int(is_active), timeframe))
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
            key = f"{sid} @{tf}" if tf else sid   # timeframes never blend
            by_strategy[key] = {"signals": count, "resolved": resolved,
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

    def upsert_spread(self, *, bar_time: int, spread_min: float,
                      spread_avg: float, spread_max: float) -> None:
        """One row per closed bar; a re-post for the same bar replaces it."""
        self.conn.execute(
            "INSERT OR REPLACE INTO spread_history"
            " (bar_time, spread_min, spread_avg, spread_max) VALUES (?,?,?,?)",
            (bar_time, spread_min, spread_avg, spread_max))
        self.conn.commit()

    def spread_stats(self, hours: int = 24) -> dict:
        """Aggregate spread over the most recent window. The window is
        anchored to the newest bar_time in the table (broker server clock),
        not the wall clock -- bar_time is server time (GMT+3 summer) while
        time.time() is UTC, and anchoring to MAX(bar_time) sidesteps that
        offset entirely."""
        row = self.conn.execute(
            "SELECT COUNT(*), MIN(spread_min), AVG(spread_avg), MAX(spread_max)"
            " FROM spread_history WHERE bar_time >="
            " (SELECT MAX(bar_time) FROM spread_history) - ?",
            (hours * 3600,)).fetchone()
        n = row[0] or 0
        if n == 0:
            return {"n": 0, "min": 0.0, "avg": 0.0, "max": 0.0}
        return {"n": n, "min": row[1], "avg": row[2], "max": row[3]}

    def equity_series(self, limit: int = 1440) -> list:
        rows = self.conn.execute(
            "SELECT ts, equity, balance, floating_pl FROM heartbeats"
            " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": t, "equity": e, "balance": b, "floating_pl": f}
                for t, e, b, f in reversed(rows)]

    def recent_signals(self, limit: int = 50) -> list:
        cols = ["id", "created_ts", "bar_time", "strategy_id", "timeframe", "signal", "price",
                "direction", "confidence", "regime", "verdict", "is_active",
                "outcome_move", "ai_correct"]
        rows = self.conn.execute(
            f"SELECT {', '.join(cols)} FROM signals ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def insert_trade(self, ev: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO trades (ts, event, strategy_id, direction, lots, price,"
            " sl, reason, ticket, profit, tp, final, entry_mode)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), ev["event"], ev.get("strategy_id", "unknown"),
             ev["direction"], ev["lots"], ev["price"], ev.get("sl", 0.0),
             ev.get("reason", ""), ev.get("ticket", 0), ev.get("profit", 0.0),
             ev.get("tp", 0.0), int(ev.get("final", True)),
             ev.get("entry_mode", "")))
        self.conn.commit()
        return cur.lastrowid

    def recent_trades(self, limit: int = 50) -> list:
        cols = ["id", "ts", "event", "strategy_id", "direction", "lots", "price",
                "sl", "reason", "ticket", "screenshot_path", "profit", "render_path",
                "tp", "final"]
        rows = self.conn.execute(
            f"SELECT {', '.join(cols)} FROM trades ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def set_screenshot(self, trade_id: int, path: str) -> None:
        self.conn.execute(
            "UPDATE trades SET screenshot_path=? WHERE id=?", (path, trade_id))
        self.conn.commit()

    def set_render(self, trade_id: int, path: str) -> None:
        self.conn.execute(
            "UPDATE trades SET render_path=? WHERE id=?", (path, trade_id))
        self.conn.commit()

    def get_kv(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    def get_profile(self):
        row = self.conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute(
            "SELECT * FROM profile WHERE id = 1").description]
        return dict(zip(cols, row))

    def save_profile(self, partial: dict) -> dict:
        now = int(time.time())
        if self.get_profile() is None:
            self.conn.execute(
                "INSERT INTO profile (id, created_ts, updated_ts) VALUES (1, ?, ?)",
                (now, now))
        updates = {k: v for k, v in partial.items() if k in PROFILE_FIELDS}
        if updates.get("risk_ack") and not (self.get_profile() or {}).get("risk_ack_ts"):
            updates["risk_ack_ts"] = now
        updates["updated_ts"] = now
        sets = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE profile SET {sets} WHERE id = 1",
                          tuple(updates.values()))
        self.conn.commit()
        return self.get_profile()

    def _row_to_dict(self, cur, row):
        """Convert sqlite3 tuple row to dict using cursor description."""
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def strategy_ids(self) -> list:
        rows = self.conn.execute(
            "SELECT DISTINCT strategy_id FROM signals WHERE strategy_id IS NOT NULL"
            " ORDER BY strategy_id").fetchall()
        return [r[0] for r in rows]

    def exec_mode(self) -> str:
        val = self.get_kv("exec_mode")
        return val if val else "manual"

    def set_exec_mode(self, mode: str) -> None:
        if mode not in ("auto", "manual"):
            raise ValueError(f"invalid exec mode: {mode}")
        self.set_kv("exec_mode", mode)

    def entry_mode(self) -> str:
        val = self.get_kv("entry_mode")
        return val if val else "adr"

    def set_entry_mode(self, mode: str) -> None:
        if mode not in ("adr", "fixed"):
            raise ValueError(f"invalid entry mode: {mode}")
        self.set_kv("entry_mode", mode)

    def create_proposal(self, kind: str, direction: str, strategy_id: str,
                       price: float, signal_id: int | None) -> int:
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
        return self._row_to_dict(cur, row)

    def pending_proposal(self, kind: str | None = None, status: str = "pending") -> dict | None:
        q = "SELECT * FROM proposals WHERE status=?"
        args = [status]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        cur = self.conn.execute(q + " ORDER BY id DESC LIMIT 1", tuple(args))
        row = cur.fetchone()
        return self._row_to_dict(cur, row)

    def set_proposal_status(self, pid: int, status: str, expected: str | None = None) -> bool:
        """Unconditional UPDATE when `expected` is None (default) -- existing
        callers keep their old semantics unchanged. When `expected` is
        given, the UPDATE is guarded on the row's CURRENT status
        (WHERE id=? AND status=?) to close a TOCTOU race -- e.g. a Telegram
        approve tap landing after /analyze already expired the same row, or
        the /heartbeat TTL sweep racing the EA's /proposal-result callback.
        Returns whether the transition actually applied (always True when
        expected is None, since that path is unconditional by design)."""
        now = int(time.time())
        with self.conn:
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
        self.conn.execute(
            "UPDATE proposals SET tg_message_id=? WHERE id=?", (tg_message_id, pid))
        self.conn.commit()

    def last_executed_entry(self) -> dict | None:
        """Newest executed entry proposal -- used to resolve an exit
        proposal's (informational-only) direction."""
        cur = self.conn.execute(
            "SELECT * FROM proposals WHERE kind='entry' AND status='executed'"
            " ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return self._row_to_dict(cur, row)

    def pop_approved_command(self) -> dict | None:
        # UPDATE...RETURNING atomically updates the oldest approved row and returns it.
        # Explicit commit() ensures durability under concurrent access (check_same_thread=False).
        # Without commit, the transaction remains open and a crash/restart could revert to 'approved'.
        cur = self.conn.execute(
            "UPDATE proposals SET status='dispatched' "
            "WHERE id = (SELECT id FROM proposals WHERE status='approved' ORDER BY id ASC LIMIT 1) "
            "RETURNING *")
        row = cur.fetchone()
        row_dict = self._row_to_dict(cur, row) if row else None
        # Commit to persist. sqlite3 may auto-commit UPDATE...RETURNING that finds 0 rows,
        # so wrap in try/except; if already committed, error is benign.
        try:
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # "no transaction is active" means UPDATE found 0 rows and auto-committed
        return row_dict

    def _rows_older_than(self, status: str, older_than_s: int) -> list:
        """Rows in `status` whose decided_ts is more than `older_than_s`
        seconds old. Used by the /heartbeat TTL sweeps (I1/I4): 'approved'
        rows use decided_ts as the approval time; 'dispatched' rows have no
        dedicated dispatch timestamp, so decided_ts (set once, at approval,
        and never touched again by pop_approved_command) is reused as a
        lower bound -- see the COMMAND_RESULT_TTL_S comment in main.py."""
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
