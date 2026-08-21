"""Trades-report engine for the mini-app's "Trades" tab (`GET /api/report`
in app/miniapp.py) and the basket-grouping it shares with `/api/trades`'
chart markers.

Pure functions only: every function here takes an already-open
`sqlite3.Connection` (read-only, opened by the caller) rather than owning
its own connection or any FastAPI coupling. This mirrors app/miniapp.py's
`_open_trades_db_ro` doc: this module must never write to the trading db
and must not import `app.main`/`app.db` (separate uvicorn processes, no
shared writable SignalDb instance to reuse)."""
import bisect
import calendar
import datetime as _dt
import sqlite3

from app.telegram import market_session_short


BASKETS_MAX = 30


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
    the Trades report passes None for "everything in the window").

    TWIN WARNING: `app/main.py`'s `_basket_legs` implements this same
    basket-boundary rule independently (it walks backward in SQL from one
    just-inserted row id instead of grouping a whole fetched window, because
    it only ever needs the ONE basket around a fresh trade-event). Both MUST
    agree on which rows are legs of a basket, in what order -- that
    agreement is pinned by `tests/test_basket_twins.py::
    test_basket_legs_and_group_baskets_agree_on_the_same_legs`. They
    deliberately return different SHAPES: this function's entries carry
    `ts`/`htf_agree` (needed for the report) and no `sl`/`tp` (the mini-app's
    SQL never selects them), plus basket-level `entry_mode`/`strategy_id`/
    `reason`/`direction` that `_basket_legs` has no reason to carry (it feeds
    a single render/Telegram call, not a report table). If you change the
    boundary rule here, change it there too, and vice versa."""
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
                {"ts": r.get("ts"), "price": r.get("price"),
                 "lots": r.get("lots"), "htf_agree": r.get("htf_agree")})
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


def _htf_flag(entries):
    """The higher-timeframe verdict recorded on a basket's FIRST leg.
    None when unknown (-1, or no entry row carried the column)."""
    for e in sorted(entries, key=lambda x: x.get("ts") or 0):
        v = e.get("htf_agree")
        if v is None or int(v) < 0:
            continue
        return bool(int(v))
    return None


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
    has_htf = "htf_agree" in cols
    sel = ("SELECT id, ts, event, direction, lots, price, profit, final, "
           + ("entry_mode" if has_mode else "''") + ", "
           + ("reason" if has_reason else "''") + ", "
           + ("strategy_id" if has_strat else "''") + ", "
           + ("htf_agree" if has_htf else "-1")
           + " FROM trades WHERE ts >= ? AND ts < ? ORDER BY id ASC")
    raw = conn.execute(sel, (start_utc - REPORT_LOOKBACK_S, end_utc)).fetchall()
    rows = [{"id": r[0], "ts": r[1], "event": r[2], "direction": r[3], "lots": r[4],
             "price": r[5], "profit": r[6], "final": r[7], "entry_mode": r[8],
             "reason": r[9], "strategy_id": r[10], "htf_agree": r[11]}
            for r in raw]
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
            # M15 agreement as the EA judged it at ENTRY (the first leg):
            # True / False / None when unknown. Older rows are backfilled by
            # scripts/backfill_htf_agree.py.
            "m15": _htf_flag(entries),
            # Which market session the trade was OPENED in -- entry time is
            # what the session describes, not the exit.
            "session": market_session_short(
                _dt.datetime.fromtimestamp(open_ts, _dt.UTC)),
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
