import copy
import sqlite3
import threading
import time

from app.repos.agent import AgentRepoMixin
from app.repos.backtest import BacktestRepoMixin
from app.repos.base import row_to_dict
from app.repos.candles import CandleRepoMixin
from app.repos.kv_profile import KvProfileRepoMixin, PROFILE_FIELDS, profile_completion
from app.repos.proposals import ProposalRepoMixin
from app.repos.signals import SignalRepoMixin
from app.repos.trades import TradeRepoMixin

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
  symbol TEXT DEFAULT 'XAUUSD',
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
  final INTEGER DEFAULT 1,
  slippage_pts REAL DEFAULT 0,
  exec_ms INTEGER DEFAULT 0,
  req_price REAL DEFAULT 0
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
  created_ts INTEGER, updated_ts INTEGER, risk_ack_ts INTEGER,
  ngrok_authtoken TEXT, ngrok_domain TEXT, miniapp_direct_link TEXT,
  btc_symbol TEXT, btc_risk_pct REAL, btc_lane TEXT, btc_enabled INTEGER DEFAULT 1
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

_CANDLES_SCHEMA = """CREATE TABLE IF NOT EXISTS candles (
  symbol    TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_time  INTEGER NOT NULL,
  o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
  v REAL DEFAULT 0,
  PRIMARY KEY (symbol, timeframe, bar_time)
)"""

_BACKTEST_SCHEMA = """CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  params_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  error TEXT,
  stats_json TEXT,
  report_path TEXT
)"""

_AI_GRADES_SCHEMA = """CREATE TABLE IF NOT EXISTS ai_grades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  bar_time INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  strategy_id TEXT,
  signal TEXT,
  model TEXT,
  prompt_version TEXT,
  direction TEXT,
  confidence REAL,
  regime_agreement TEXT,
  risk_flags TEXT,
  action TEXT,
  rationale TEXT,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  latency_ms INTEGER DEFAULT 0,
  ai_correct INTEGER,
  entry_quality INTEGER,
  expected_r REAL,
  invalidation REAL,
  window_state TEXT,
  regime TEXT,
  session TEXT,
  outcome_move REAL
)"""

_AGENT_LESSONS_SCHEMA = """CREATE TABLE IF NOT EXISTS agent_lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  grade_id INTEGER,
  bar_time INTEGER,
  symbol TEXT,
  timeframe TEXT,
  direction TEXT,
  window_state TEXT,
  regime TEXT,
  session TEXT,
  entry_quality INTEGER,
  outcome_move REAL,
  lesson TEXT
)"""

_AGENT_ACTIONS_SCHEMA = """CREATE TABLE IF NOT EXISTS agent_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  level TEXT,
  intent TEXT NOT NULL,
  payload TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  reason TEXT,
  proposal_id INTEGER,
  decided_ts INTEGER
)"""


class _SerializedCursor:
    """Cursor whose fetches take the connection lock (see _SerializedConnection).
    Fetching steps the underlying statement, so it needs the same exclusion
    as execute(); iteration materializes under the lock for the same reason."""
    __slots__ = ("_cur", "_lock")

    def __init__(self, cur, lock):
        self._cur = cur
        self._lock = lock

    def fetchone(self):
        with self._lock:
            return self._cur.fetchone()

    def fetchall(self):
        with self._lock:
            return self._cur.fetchall()

    def fetchmany(self, size=None):
        with self._lock:
            return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    def __iter__(self):
        with self._lock:
            return iter(self._cur.fetchall())

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount


class _SerializedConnection:
    """Serializes every use of the ONE shared sqlite3 connection."""
    __slots__ = ("_conn", "_lock")

    def __init__(self, conn, lock):
        self._conn = conn
        self._lock = lock

    def execute(self, sql, params=()):
        with self._lock:
            return _SerializedCursor(self._conn.execute(sql, params), self._lock)

    def executemany(self, sql, seq_of_params):
        with self._lock:
            return _SerializedCursor(self._conn.executemany(sql, seq_of_params), self._lock)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def rollback(self):
        with self._lock:
            self._conn.rollback()

    def close(self):
        with self._lock:
            self._conn.close()

    def __enter__(self):
        self._lock.acquire()
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()

    def __getattr__(self, name):
        return getattr(self._conn, name)


class SignalDb(
    SignalRepoMixin,
    TradeRepoMixin,
    ProposalRepoMixin,
    CandleRepoMixin,
    KvProfileRepoMixin,
    AgentRepoMixin,
    BacktestRepoMixin,
):
    def __init__(self, path: str):
        raw_conn = sqlite3.connect(path, check_same_thread=False)
        try:
            raw_conn.execute("PRAGMA journal_mode=WAL")
            raw_conn.execute("PRAGMA synchronous=NORMAL")
            raw_conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.OperationalError:
            pass
        self._lock = threading.RLock()
        self.conn = _SerializedConnection(raw_conn, self._lock)
        self.conn.execute(_SCHEMA)
        self.conn.execute(_HB_SCHEMA)
        self.conn.execute(_TRADES_SCHEMA)
        self.conn.execute(_KV_SCHEMA)
        self.conn.execute(_PROFILE_SCHEMA)
        self.conn.execute(_SPREAD_SCHEMA)
        self.conn.execute(_PROPOSALS_SCHEMA)
        self.conn.execute(_CANDLES_SCHEMA)
        self.conn.execute(_BACKTEST_SCHEMA)
        self.conn.execute(_AI_GRADES_SCHEMA)
        self.conn.execute(_AGENT_ACTIONS_SCHEMA)
        self.conn.execute(_AGENT_LESSONS_SCHEMA)
        self.conn.commit()
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signals)")}
        if "strategy_id" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN strategy_id TEXT")
        if "is_active" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN is_active INTEGER DEFAULT 1")
        if "timeframe" not in cols:
            self.conn.execute("ALTER TABLE signals ADD COLUMN timeframe TEXT")
        profile_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(profile)")}
        for col, col_def in (
            ("ngrok_authtoken", "TEXT"),
            ("ngrok_domain", "TEXT"),
            ("miniapp_direct_link", "TEXT"),
            ("btc_symbol", "TEXT DEFAULT 'BTCUSD'"),
            ("btc_risk_pct", "REAL DEFAULT 0.5"),
            ("btc_lane", "TEXT DEFAULT 'iziBit5'"),
            ("btc_enabled", "INTEGER DEFAULT 1"),
        ):
            if col not in profile_cols:
                try:
                    self.conn.execute(f"ALTER TABLE profile ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass
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
        if "htf_agree" not in trade_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN htf_agree INTEGER DEFAULT -1")
            except sqlite3.OperationalError:
                pass
        if "ema200_agree" not in trade_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN ema200_agree INTEGER DEFAULT -1")
            except sqlite3.OperationalError:
                pass
        if "rsi_agree" not in trade_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN rsi_agree INTEGER DEFAULT -1")
            except sqlite3.OperationalError:
                pass
        if "sr_agree" not in trade_cols:
            for ddl in ("ALTER TABLE trades ADD COLUMN sr_agree INTEGER DEFAULT -1",
                        "ALTER TABLE trades ADD COLUMN sr_tier TEXT DEFAULT ''"):
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        if "news_blackout" not in trade_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE trades ADD COLUMN news_blackout INTEGER DEFAULT -1")
            except sqlite3.OperationalError:
                pass
        for col, col_def in (
            ("slippage_pts", "REAL DEFAULT 0"),
            ("exec_ms", "INTEGER DEFAULT 0"),
            ("req_price", "REAL DEFAULT 0"),
        ):
            if col not in trade_cols:
                try:
                    self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass
        if "symbol" not in trade_cols:
            try:
                self.conn.execute("ALTER TABLE trades ADD COLUMN symbol TEXT DEFAULT 'XAUUSD'")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_unresolved"
            " ON signals(outcome_price) WHERE outcome_price IS NULL")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_series_time"
            " ON signals(symbol, timeframe, bar_time)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_heartbeats_ts"
            " ON heartbeats(ts)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposals_status_created"
            " ON proposals(status, created_ts)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_event_ticket"
            " ON trades(event, ticket)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol"
            " ON trades(symbol)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_grades_series_bar"
            " ON ai_grades(symbol, timeframe, bar_time)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_actions_created"
            " ON agent_actions(created_ts)")
        grade_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(ai_grades)")}
        for col, ddl in (("entry_quality", "ALTER TABLE ai_grades ADD COLUMN entry_quality INTEGER"),
                         ("expected_r", "ALTER TABLE ai_grades ADD COLUMN expected_r REAL"),
                         ("invalidation", "ALTER TABLE ai_grades ADD COLUMN invalidation REAL"),
                         ("window_state", "ALTER TABLE ai_grades ADD COLUMN window_state TEXT"),
                         ("regime", "ALTER TABLE ai_grades ADD COLUMN regime TEXT"),
                         ("session", "ALTER TABLE ai_grades ADD COLUMN session TEXT"),
                         ("outcome_move", "ALTER TABLE ai_grades ADD COLUMN outcome_move REAL")):
            if col not in grade_cols:
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        self.conn.execute("PRAGMA user_version = 2")
        self.conn.commit()
        self._migrate_strategy_ids()
        self._retain()
        self._last_hb_ts = self.conn.execute(
            "SELECT MAX(ts) FROM heartbeats").fetchone()[0]
        self._signals_version = 0
        self._stats_cache = None
        self._stats_cache_version = -1

    def _migrate_strategy_ids(self) -> None:
        renames = (
            ("halftrend_ema_v1", "iziGold5"),
            ("halftrend_m15_v1", "iziGold15"),
            ("izi5", "iziGold5"),
            ("izi15", "iziGold15"),
        )
        try:
            for old, new in renames:
                for table in ("signals", "trades", "ai_grades"):
                    self.conn.execute(
                        f"UPDATE {table} SET strategy_id=? WHERE strategy_id=?",
                        (new, old))
                self.conn.execute(
                    "UPDATE kv SET value=? WHERE key='active_strategy' AND value=?",
                    (new, old))
            self.conn.commit()
        except sqlite3.Error:
            pass

    _RETENTION_S = 90 * 86400

    def _retain(self) -> None:
        try:
            cutoff = int(time.time()) - self._RETENTION_S
            self.conn.execute("DELETE FROM heartbeats WHERE ts < ?", (cutoff,))
            self.conn.execute(
                "DELETE FROM spread_history WHERE bar_time <"
                " (SELECT MAX(bar_time) FROM spread_history) - ?",
                (self._RETENTION_S,))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def _row_to_dict(self, cur, row):
        return row_to_dict(cur, row)
