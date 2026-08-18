"""Read-only mini-app feed service (port 9001) — the ONLY thing the
Phase 3 tunnel will expose. The Windows bridge POSTs batches to
/feed/push; browsers get history over REST and live deltas over one
WebSocket. No trading controls exist here by construction.

Runs as its own process: uvicorn app.miniapp:app --host 127.0.0.1
--port 9001. State is in-memory only — a restart just refills from the
bridge's next backfill push (fail-open).
"""
import asyncio
import bisect
import calendar
import datetime as _dt
import time
import hmac
import math
import sqlite3
import urllib.parse
from collections import deque
from pathlib import Path

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import miniapp_auth
from app.config import settings
from app.indicators import ema, halftrend
from app.models import Candle

STATIC_DIR = Path(__file__).parent / "static"

TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
MAX_CANDLES = 500
CANDLE_FIELDS = {"t", "o", "h", "l", "c", "v"}


class FeedState:
    """Ring-buffered candles per TF + latest tick/positions. apply_push
    validates loosely and returns the delta messages to broadcast —
    garbage in a batch is dropped, never raised (the bridge is trusted
    but the loop must survive anything)."""

    def __init__(self):
        self.candles = {tf: deque(maxlen=MAX_CANDLES) for tf in TFS}
        self.tick = None
        self.positions = []

    def apply_push(self, batch: dict) -> list[dict]:
        deltas = []
        if not isinstance(batch, dict):
            return deltas
        tick = batch.get("tick")
        if isinstance(tick, dict) and "bid" in tick and "ask" in tick:
            self.tick = tick
            deltas.append({"type": "tick", "tick": tick})
        candles = batch.get("candles")
        if isinstance(candles, dict):
            for tf, rows in candles.items():
                if tf not in TFS or not isinstance(rows, list):
                    continue
                buf = self.candles[tf]
                for row in rows:
                    if not isinstance(row, dict) or not CANDLE_FIELDS <= set(row):
                        continue
                    # Validate numeric types (reject bool, NaN, Infinity)
                    if not all(isinstance(row[k], (int, float)) and not isinstance(row[k], bool)
                               and math.isfinite(row[k])
                               for k in ["t", "o", "h", "l", "c", "v"]):
                        continue
                    if buf and row["t"] == buf[-1]["t"]:
                        buf[-1] = row                     # forming-bar update
                    elif buf and row["t"] < buf[-1]["t"]:
                        continue                          # stale, ignore
                    else:
                        buf.append(row)
                    deltas.append({"type": "candle", "tf": tf, "candle": row})
        positions = batch.get("positions")
        if isinstance(positions, list):
            self.positions = positions
            deltas.append({"type": "positions", "positions": positions})
        return deltas


_STARTED = time.time()
app = FastAPI(title="xau-miniapp", docs_url=None, redoc_url=None, openapi_url=None)
# docs_url/redoc_url/openapi_url off: Swagger UI pulls a CDN script and
# /docs, /redoc, /openapi.json are otherwise auth-free by FastAPI default
# (docs_url=None alone leaves /openapi.json registered) -- harmless on
# 127.0.0.1 but not once this process sits behind the public tunnel.
# Mount ONLY the vendor subdirectory, not the whole shared static/ dir —
# this process is the one Phase 3 tunnels publicly, and static/ also holds
# main.py's dashboard.html/onboarding.html (trading controls), which must
# never be reachable from here. The page itself is served by GET / below.
app.mount("/static/vendor", StaticFiles(directory=STATIC_DIR / "vendor"),
          name="static-vendor")


class _Hub:
    """WS clients + broadcast. Slow/dead clients are dropped, never
    awaited to death (send failures disconnect that client only)."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_json(msg), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


state = FeedState()
hub = _Hub()


def viewer_allowed(init_data: str | None) -> bool:
    """Check if viewer is allowed. Shared by REST and WS. Dev bypass is
    the first check, unchanged from Phase 1 (`settings.miniapp_dev_bypass`,
    short-circuits before `init_data` is even looked at). Past that,
    Phase 3's real Telegram initData validation + owner/channel-membership
    authorization lives in `app.miniapp_auth.viewer_ok`."""
    if settings.miniapp_dev_bypass:
        return True
    return miniapp_auth.viewer_ok(init_data)


def require_viewer(request: Request):
    """REST dependency (`GET /api/history`): initData comes from the
    `X-Telegram-Init-Data` header first (so it never lands in a reverse
    proxy's/tunnel's URL access log), falling back to the `?initData=`
    query param for parity with the WS path and manual testing."""
    init_data = request.headers.get("X-Telegram-Init-Data") \
        or request.query_params.get("initData")
    if viewer_allowed(init_data):
        return True
    raise HTTPException(status_code=403, detail="viewer auth required")


@app.post("/feed/push")
async def feed_push(request: Request):
    # Constant-time comparison (security-review fix, 2026-08-15): a plain
    # `!=` leaks timing proportional to the matching-prefix length, which
    # is a real side channel for a bearer-shaped secret sent over the
    # network. `not settings.feed_key` still guards first -- an
    # unconfigured key must fail closed even though
    # `hmac.compare_digest(b"", b"")` alone would return True.
    provided_key = request.headers.get("X-Feed-Key", "")
    if not settings.feed_key or not hmac.compare_digest(
            provided_key.encode("utf-8"), settings.feed_key.encode("utf-8")):
        raise HTTPException(status_code=403, detail="bad feed key")
    try:
        batch = await request.json()
    except Exception:
        return {"ok": False}
    try:
        deltas = state.apply_push(batch)
        state.last_push_ts = time.time()
    except Exception:
        return {"ok": False}
    for d in deltas:
        await hub.broadcast(d)
    # `depth` = the shallowest TF ring buffer. The bridge uses it to detect a
    # service that came back EMPTY (watchdog/deploy restart) and re-send its
    # 500-bar backfill — a restart faster than one push cycle would otherwise
    # leave the chart with 1-2 candles and no indicators (2026-08-17).
    depth = min((len(state.candles[tf]) for tf in TFS), default=0)
    return {"ok": True, "deltas": len(deltas), "depth": depth}


@app.get("/healthz")
def healthz():
    """Auth-free liveness probe (setup.sh's start/restart check uses this,
    not /openapi.json -- that route is gone now that docs are disabled)."""
    # feed_age_s: seconds since the last bridge push (None = never). The
    # watchdog reads this to decide the Windows bridge is dead — the earlier
    # "miniapp.log mtime" proxy was fooled by the watchdog's OWN probes.
    lp = getattr(state, "last_push_ts", None)
    return {"ok": True,
            "feed_age_s": (None if lp is None else round(time.time() - lp, 1)),
            "uptime_s": round(time.time() - _STARTED, 1)}


@app.get("/")
def page():
    """The live chart page itself. Deliberately NOT behind require_viewer —
    Telegram loads this URL directly inside the WebApp webview before any
    initData is available to check; the data endpoints (/api/history, /ws)
    stay gated. Matches Phase 1's auth seam."""
    return FileResponse(STATIC_DIR / "miniapp.html", media_type="text/html")


@app.get("/api/history")
def history(tf: str, _=Depends(require_viewer)):
    if tf not in TFS:
        raise HTTPException(status_code=400, detail=f"unknown tf {tf}")
    rows = list(state.candles[tf])
    return {"tf": tf, "candles": rows, **_indicator_series(rows)}


def _indicator_series(rows: list[dict]) -> dict:
    """EMA-9/21/55/200 + HalfTrend(amplitude=4), computed fresh from the ring
    buffer on every request (<=500 candles: cheap, no cache needed). Uses
    the exact same app.indicators math the render.py trade-chart PNGs use,
    so the mini-app overlays match the EA's live MT5 chart. ema()/halftrend()
    already degrade to all-None/empty for short input (see their
    docstrings), so a <2-candle TF naturally yields empty/null arrays here
    without any special-casing.
    """
    closes = [r["c"] for r in rows]
    candle_objs = [Candle(**r) for r in rows]
    ht = halftrend(candle_objs, amplitude=4)
    return {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema55": ema(closes, 55),
        "ema200": ema(closes, 200),
        "halftrend": [({"v": e[0], "trend": e[1]} if e else None) for e in ht],
    }


TRADES_DEFAULT_LIMIT = 50
TRADES_MAX_LIMIT = 500   # server-side ceiling: a viewer can never force a full-table scan
BASKETS_MAX = 30


def _open_trades_db_ro() -> sqlite3.Connection:
    """Same read-only open pattern as `miniapp_auth._resolve_credentials`:
    a `file:...?mode=ro` URI against `settings.db_path` with `timeout=1.0`
    so lock contention fails fast. This module must never write to the
    trading db (see miniapp_auth's module docstring) and must not import
    `app.main`/`app.db` (separate uvicorn processes, no shared writable
    SignalDb instance to reuse)."""
    uri = f"file:{urllib.parse.quote(settings.db_path, safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)


def _group_baskets(rows: list[dict], cap: int | None = BASKETS_MAX) -> list[dict]:
    """Mirrors `_basket_legs` in app/main.py: a basket is the run of
    'open'/'add' rows since the previous FINAL 'close' row, closed by the
    next FINAL 'close'. Non-final closes (a single leg stopping out while
    the rest of the basket survives) are ignored for boundary purposes --
    they neither end a basket nor count as an entry -- but their profit
    IS part of the basket's P/L (`pl` = sum of every close row's profit
    inside the basket; the EA posts one close row per deal, so a
    multi-leg exit lands as several rows, only the last flagged final).
    `rows` must be ordered by id ascending. The trailing basket (still
    open, no close row yet in the fetched window) gets `exit: None`.
    Capped to the last `cap` baskets (BASKETS_MAX for the chart markers;
    the Trades report passes None for "everything in the window")."""
    baskets: list[dict] = []
    current: dict | None = None
    for r in rows:
        event = r.get("event")
        if event in ("open", "add"):
            if current is None:
                current = {"direction": r.get("direction"), "entries": [], "exit": None,
                           "pl": 0.0, "entry_mode": (r.get("entry_mode") or "adr"),
                           "strategy_id": r.get("strategy_id"), "reason": None}
            current["entries"].append(
                {"ts": r.get("ts"), "price": r.get("price"), "lots": r.get("lots")})
        elif event == "close":
            if current is None:
                # a close with no open basket in the fetched window is a
                # stray boundary marker (the basket it closed started before
                # our window) -- nothing to attach it to, so it's dropped.
                continue
            p = r.get("profit")
            current["pl"] += p if isinstance(p, (int, float)) else 0.0
            if r.get("final"):
                current["exit"] = {"ts": r.get("ts"), "price": r.get("price"),
                                   "profit": r.get("profit")}
                current["reason"] = r.get("reason")
                baskets.append(current)
                current = None
    if current is not None:
        baskets.append(current)
    return baskets if cap is None else baskets[-cap:]


@app.get("/api/trades")
def trades(limit: int = TRADES_DEFAULT_LIMIT, _=Depends(require_viewer)):
    """Last `limit` trade-log rows + server-grouped baskets for the
    mini-app's past-trade markers. Fail-open like every other read here:
    ANY error (db missing, table missing, locked, garbage row) returns
    200 with empty lists rather than a 500 -- the chart must still render
    with no markers."""
    try:
        conn = _open_trades_db_ro()
        try:
            cur = conn.execute(
                "SELECT id, ts, event, direction, lots, price, profit, final "
                "FROM trades ORDER BY id DESC LIMIT ?",
                (min(max(int(limit), 0), TRADES_MAX_LIMIT),))
            raw = cur.fetchall()
        finally:
            conn.close()
        raw.reverse()  # ascending id order for basket grouping
        rows = [
            {"id": r[0], "ts": r[1], "event": r[2], "direction": r[3],
             "lots": r[4], "price": r[5], "profit": r[6], "final": r[7]}
            for r in raw
        ]
        baskets = _group_baskets(rows)
        return {"trades": rows, "baskets": baskets}
    except Exception:
        return {"trades": [], "baskets": []}


# ---- Trades report (mini-app "Trades" tab) ----------------------------------
# Broker server clock = UTC+3 (the same "GMT+3 summer" note as
# app/db.py::spread_stats). trades.ts / heartbeats.ts / signals.created_ts
# are UTC epoch seconds (service insert time); signals.bar_time is SERVER
# time (bar open on the broker clock). This constant is what turns a UTC ts
# into a broker-calendar day for the report's day boundaries and what
# aligns bar_time with trades.ts for the signal join. It MUST track the
# broker's DST switch (UTC+2 in winter for most GMT+3-summer brokers) --
# see izi §8 "Trades report" for the caveat.
SERVER_UTC_OFFSET_H = 3
REPORT_LOOKBACK_S = 45 * 86400   # rows fetched before the window start so
                                 # a basket opened earlier still groups
SIGNAL_JOIN_WINDOW_S = 4 * 3600  # signal bar open may precede the trade
                                 # by up to one H4 bar (EA runs on chart TF)
HB_AFTER_WINDOW_S = 600          # first heartbeat within 10 min after the
                                 # close = the account's "balance after"


def _server_offset_s() -> int:
    return SERVER_UTC_OFFSET_H * 3600


def _server_date(ts_utc: int) -> _dt.date:
    """Broker-calendar date of a UTC epoch second."""
    return _dt.datetime.fromtimestamp(ts_utc + _server_offset_s(), _dt.timezone.utc).date()


def _server_hhmm(ts_utc: int) -> str:
    return _dt.datetime.fromtimestamp(ts_utc + _server_offset_s(),
                                      _dt.timezone.utc).strftime("%H:%M")


def _server_day_bounds_utc(day: _dt.date) -> tuple[int, int]:
    """[start, end) UTC epoch of one broker-calendar day."""
    start = calendar.timegm((day.year, day.month, day.day, 0, 0, 0)) - _server_offset_s()
    return start, start + 86400


def _server_month_bounds_utc(year: int, month: int) -> tuple[int, int]:
    start = calendar.timegm((year, month, 1, 0, 0, 0)) - _server_offset_s()
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    end = calendar.timegm((ny, nm, 1, 0, 0, 0)) - _server_offset_s()
    return start, end


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _fetch_closed_baskets(conn: sqlite3.Connection, start_utc: int, end_utc: int) -> list[dict]:
    """All baskets whose FINAL close falls in [start_utc, end_utc), with
    the entry-signal join (regime / AI direction) and balance-after
    already resolved. Rows are read from `start_utc - REPORT_LOOKBACK_S`
    so a basket opened before the window still groups (its close is what
    places it in the window)."""
    cols = _table_cols(conn, "trades")
    has_mode = "entry_mode" in cols
    has_reason = "reason" in cols
    has_strat = "strategy_id" in cols
    sel = ("SELECT id, ts, event, direction, lots, price, profit, final, "
           + ("entry_mode" if has_mode else "''") + ", "
           + ("reason" if has_reason else "''") + ", "
           + ("strategy_id" if has_strat else "''")
           + " FROM trades WHERE ts >= ? AND ts < ? ORDER BY id ASC")
    raw = conn.execute(sel, (start_utc - REPORT_LOOKBACK_S, end_utc)).fetchall()
    rows = [{"id": r[0], "ts": r[1], "event": r[2], "direction": r[3], "lots": r[4],
             "price": r[5], "profit": r[6], "final": r[7], "entry_mode": r[8],
             "reason": r[9], "strategy_id": r[10]} for r in raw]
    baskets = [b for b in _group_baskets(rows, cap=None)
               if b.get("exit") and isinstance(b["exit"].get("ts"), (int, float))
               and start_utc <= b["exit"]["ts"] < end_utc]
    if not baskets:
        return []
    first_open = min((e["ts"] for b in baskets for e in b["entries"]
                      if isinstance(e.get("ts"), (int, float))), default=start_utc)

    # -- entry-signal join: nearest active BUY/SELL signal (same direction)
    #    whose bar open (bar_time is server time -> minus offset) sits at or
    #    just before the basket's first entry.
    signals: list[tuple] = []
    try:
        scols = _table_cols(conn, "signals")
        active_expr = "COALESCE(is_active, 1)" if "is_active" in scols else "1"
        signals = conn.execute(
            "SELECT bar_time, signal, direction, confidence, regime, verdict, ai_available"
            " FROM signals WHERE signal IN ('BUY','SELL') AND " + active_expr + " = 1"
            " AND bar_time >= ? AND bar_time < ? ORDER BY bar_time ASC",
            (first_open - SIGNAL_JOIN_WINDOW_S + _server_offset_s(),
             end_utc + _server_offset_s() + 60)).fetchall()
    except Exception:
        signals = []
    sig_utc = [(int(s[0]) - _server_offset_s(),) + tuple(s[1:]) for s in signals]

    # -- heartbeats for balance-after
    hbs: list[tuple] = []
    try:
        hbs = conn.execute(
            "SELECT ts, balance FROM heartbeats WHERE ts >= ? AND ts < ?"
            " AND balance IS NOT NULL ORDER BY ts ASC",
            (start_utc - REPORT_LOOKBACK_S, end_utc + HB_AFTER_WINDOW_S)).fetchall()
    except Exception:
        hbs = []

    hb_ts = [h[0] for h in hbs]

    out = []
    # Running carry for the balance-after fallback: when several baskets
    # close inside ONE heartbeat gap (bridge/PC offline through consecutive
    # trades) each of them must add the CUMULATIVE pl since that heartbeat,
    # not just its own -- otherwise every basket after the first shows a
    # confidently wrong balance. Reset whenever a real post-close heartbeat
    # anchors again. Baskets are walked in close_ts order for this reason.
    carry_hb_idx = None      # index into hbs of the stale heartbeat in use
    carry_pl = 0.0           # cumulative pl of baskets closed since it
    for b in sorted(baskets, key=lambda x: x["exit"]["ts"]):
        entries = [e for e in b["entries"] if isinstance(e.get("ts"), (int, float))]
        open_ts = min((e["ts"] for e in entries), default=b["exit"]["ts"])
        close_ts = b["exit"]["ts"]
        direction = (b.get("direction") or "").upper()
        # lot-weighted average entry
        tot_l = sum(e["lots"] for e in entries if isinstance(e.get("lots"), (int, float)))
        if tot_l > 0:
            entry_px = sum(e["price"] * e["lots"] for e in entries
                           if isinstance(e.get("lots"), (int, float))
                           and isinstance(e.get("price"), (int, float))) / tot_l
        else:
            pxs = [e["price"] for e in entries if isinstance(e.get("price"), (int, float))]
            entry_px = (sum(pxs) / len(pxs)) if pxs else None
        # signal join
        sig = None
        for s in reversed(sig_utc):
            if s[0] > open_ts + 60:
                continue
            if s[0] < open_ts - SIGNAL_JOIN_WINDOW_S:
                break
            if s[1] == direction:
                sig = s
                break
        regime = sig[4] if sig else None
        ai_dir = sig[2] if sig else None
        ai_avail = bool(sig[6]) if (sig and sig[6] is not None) else bool(sig)
        ai = None
        if sig and ai_avail and ai_dir in ("bullish", "bearish"):
            want = "bullish" if direction == "BUY" else "bearish"
            ai = "agree" if ai_dir == want else "disagree"
        # balance after: first heartbeat within HB_AFTER_WINDOW_S after the
        # close (the account already reflects the deal); else the last one
        # before it plus this basket's P/L; else unknown.
        bal = None
        bal_src = None
        if hb_ts:
            i = bisect.bisect_left(hb_ts, close_ts)
            if i < len(hb_ts) and hb_ts[i] <= close_ts + HB_AFTER_WINDOW_S:
                bal, bal_src = hbs[i][1], "hb_after"
                carry_hb_idx, carry_pl = None, 0.0
            elif i > 0:
                if carry_hb_idx != i - 1:
                    carry_hb_idx, carry_pl = i - 1, 0.0
                carry_pl += (b.get("pl") or 0.0)
                bal, bal_src = hbs[i - 1][1] + carry_pl, "hb_before+pl"
        out.append({
            "open_ts": open_ts, "close_ts": close_ts,
            "day": _server_date(close_ts).isoformat(),
            "time": _server_hhmm(close_ts),
            "direction": direction or None,
            "mode": (b.get("entry_mode") or "adr").lower(),
            "entries": len(entries),
            "lots": round(tot_l, 2),
            "entry": (round(entry_px, 2) if isinstance(entry_px, (int, float)) else None),
            "exit": b["exit"].get("price"),
            "reason": b.get("reason") or "",
            "pl": round(b.get("pl") or 0.0, 2),
            "balance_after": bal, "balance_src": bal_src,
            "regime": regime,
            "ai": ai, "ai_direction": ai_dir,
            "ai_confidence": sig[3] if sig else None,
            "ai_verdict": sig[5] if sig else None,
            "strategy_id": b.get("strategy_id"),
        })
    return out


def _fmt_day_label(day: _dt.date) -> str:
    return day.strftime("%b %d").replace(" 0", " ")


def _report_month(conn: sqlite3.Connection, year: int, month: int) -> dict:
    start, end = _server_month_bounds_utc(year, month)
    baskets = _fetch_closed_baskets(conn, start, end)
    by_day: dict[str, list[dict]] = {}
    for b in baskets:
        by_day.setdefault(b["day"], []).append(b)
    days = []
    for day in sorted(by_day):
        rows = by_day[day]
        wins = sum(1 for r in rows if r["pl"] > 0)
        losses = sum(1 for r in rows if r["pl"] < 0)
        regimes: dict[str, int] = {}
        for r in rows:
            regimes[r["regime"] or "unknown"] = regimes.get(r["regime"] or "unknown", 0) + 1
        last = max(rows, key=lambda r: r["close_ts"])
        days.append({
            "date": day,
            "label": _fmt_day_label(_dt.date.fromisoformat(day)),
            "trades": len(rows), "wins": wins, "losses": losses,
            "pl": round(sum(r["pl"] for r in rows), 2),
            "balance_end": last["balance_after"],
            "regimes": regimes,
        })
    n = len(baskets)
    wins = sum(1 for b in baskets if b["pl"] > 0)
    rw: dict[str, dict] = {}
    for b in baskets:
        k = b["regime"] or "unknown"
        d = rw.setdefault(k, {"trades": 0, "wins": 0})
        d["trades"] += 1
        d["wins"] += 1 if b["pl"] > 0 else 0
    for d in rw.values():
        d["win_pct"] = round(100.0 * d["wins"] / d["trades"], 1) if d["trades"] else None
    best = max(days, key=lambda d: d["pl"]) if days else None
    worst = min(days, key=lambda d: d["pl"]) if days else None
    return {
        "view": "month", "month": f"{year:04d}-{month:02d}",
        "server_utc_offset_h": SERVER_UTC_OFFSET_H,
        "days": days,
        "footer": {
            "pl": round(sum(b["pl"] for b in baskets), 2),
            "trades": n, "wins": wins,
            "win_pct": round(100.0 * wins / n, 1) if n else None,
            "best_day": ({"date": best["date"], "label": best["label"], "pl": best["pl"]}
                         if best else None),
            "worst_day": ({"date": worst["date"], "label": worst["label"], "pl": worst["pl"]}
                          if worst else None),
        },
        "regime_winrates": rw,
        "equity": [d["balance_end"] for d in days],
    }


def _report_day(conn: sqlite3.Connection, day: _dt.date) -> dict:
    start, end = _server_day_bounds_utc(day)
    # today: up to now (rows can't be in the future anyway; the bound is
    # documented rather than enforced)
    rows = _fetch_closed_baskets(conn, start, end)
    n = len(rows)
    wins = sum(1 for r in rows if r["pl"] > 0)
    return {
        "view": "day", "date": day.isoformat(),
        "label": _fmt_day_label(day),
        "server_utc_offset_h": SERVER_UTC_OFFSET_H,
        "rows": rows,
        "footer": {"pl": round(sum(r["pl"] for r in rows), 2), "trades": n, "wins": wins,
                   "losses": sum(1 for r in rows if r["pl"] < 0)},
    }


def _empty_report(view: str, key: str) -> dict:
    if view == "day":
        return {"view": "day", "date": key, "label": "", "rows": [],
                "server_utc_offset_h": SERVER_UTC_OFFSET_H,
                "footer": {"pl": 0.0, "trades": 0, "wins": 0, "losses": 0}}
    return {"view": "month", "month": key, "days": [],
            "server_utc_offset_h": SERVER_UTC_OFFSET_H,
            "footer": {"pl": 0.0, "trades": 0, "wins": 0, "win_pct": None,
                       "best_day": None, "worst_day": None},
            "regime_winrates": {}, "equity": []}


@app.get("/api/report")
def report(view: str = "month", month: str | None = None, date: str | None = None,
           _=Depends(require_viewer)):
    """Trades report for the mini-app's Trades tab. `view=month&month=YYYY-MM`
    (default: the current broker-calendar month) -> one row per trading
    day; `view=day&date=YYYY-MM-DD` -> one row per CLOSED basket that day.
    Day boundaries are broker server days (SERVER_UTC_OFFSET_H). Malformed
    params -> 400; every db-side failure (missing db/table, lock, garbage)
    is fail-open: 200 with empty rows, never a 500."""
    if view not in ("month", "day"):
        raise HTTPException(status_code=400, detail="view must be month|day")
    now_server = _server_date(int(time.time()))
    if view == "month":
        key = month or now_server.strftime("%Y-%m")
        try:
            y, m = key.split("-")
            y, m = int(y), int(m)
            if not (1 <= m <= 12 and 2000 <= y <= 2100):
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="month must be YYYY-MM")
        key = f"{y:04d}-{m:02d}"
    else:
        key = date or now_server.isoformat()
        try:
            d = _dt.date.fromisoformat(key)
        except Exception:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        key = d.isoformat()
    try:
        conn = _open_trades_db_ro()
        try:
            return _report_month(conn, y, m) if view == "month" else _report_day(conn, d)
        finally:
            conn.close()
    except Exception:
        return _empty_report(view, key)


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    # Browsers can't set custom headers on a WS handshake, so initData
    # rides the connection URL's query string here -- no header option
    # exists for this call site (see require_viewer for the REST path).
    # viewer_allowed() is sync and can do real blocking work on a
    # membership-cache miss (sqlite open + a 5 s httpx.get) -- run it off
    # the event loop so it can't freeze every other client's broadcasts
    # and handshakes while it waits. Also defensively caught: an
    # unhandled exception here must still produce the mandated 4403
    # close, never a bare crash of the handshake.
    try:
        allowed = await asyncio.to_thread(viewer_allowed, ws.query_params.get("initData"))
    except Exception:
        allowed = False
    if not allowed:
        await ws.close(code=4403)
        return
    await ws.accept()
    hub.clients.add(ws)
    try:
        await ws.send_json({"type": "snapshot", "tick": state.tick,
                            "positions": state.positions, "tfs": TFS})
        while True:
            # Keep the socket open; inbound messages are ignored (read-only
            # feed). Disconnect surfaces here.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.clients.discard(ws)
