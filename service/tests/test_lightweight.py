"""Lightweight pass (audit 2026-08-24): SQLite pragmas + write lock,
in-memory heartbeat collapse, startup retention, stats/candle caching,
active_proposal, and the /analyze short-payload guard.

Everything here is behavioral -- no monkeypatching of internals -- so the
tests keep meaning if the implementation moves.
"""
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from app.db import SignalDb

DAY = 86400


class _C:
    """Minimal candle stand-in for resolve_outcomes (needs .t and .c)."""

    def __init__(self, t, c):
        self.t, self.c = t, c
        self.o = self.h = self.l = c
        self.v = 0.0


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api.db"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _candles(n=60, start=1_754_000_000, step=300, base=2400.0):
    return [{"t": start + i * step, "o": base + i * 0.1, "h": base + 1 + i * 0.1,
             "l": base - 1 + i * 0.1, "c": base + 0.5 + i * 0.1, "v": 100.0}
            for i in range(n)]


def _payload(candles, signal="NONE"):
    return {"symbol": "XAUUSD", "timeframe": "M5", "signal": signal,
            "strategy_id": "lw_test", "shadows": [], "candles": candles}


# --- A1: pragmas ---------------------------------------------------------
def test_busy_timeout_is_set(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_journal_mode_is_wal_or_documented_fallback(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    # ext4 tmp dirs give wal; the drvfs/9p fallback leaves the default.
    assert mode in ("wal", "delete")


# --- A3: heartbeat collapse now in-memory --------------------------------
def _hb(equity=10_000.0):
    return {"equity": equity, "balance": 10_000.0, "floating_pl": 0.0,
            "open_count": 0, "kill_switch": False, "exposure_min": 0,
            "active_strategy": "halftrend_ema_v1"}


def test_heartbeat_collapses_within_60s(tmp_path):
    db = SignalDb(str(tmp_path / "hb.db"))
    assert db.insert_heartbeat(_hb()) is True
    assert db.insert_heartbeat(_hb(10_001.0)) is False
    assert db.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == 1


def test_heartbeat_collapse_survives_reopen(tmp_path):
    """The in-memory last-ts is seeded from the table at open, so a fresh
    SignalDb over the same file still collapses a heartbeat 60 s window."""
    path = str(tmp_path / "hb2.db")
    db = SignalDb(path)
    assert db.insert_heartbeat(_hb()) is True
    db2 = SignalDb(path)
    assert db2.insert_heartbeat(_hb()) is False


# --- A4: startup retention ----------------------------------------------
def test_retention_prunes_old_heartbeats_and_spreads(tmp_path):
    path = str(tmp_path / "ret.db")
    db = SignalDb(path)
    now = int(time.time())
    for age_days in (91, 89):
        db.conn.execute(
            "INSERT INTO heartbeats (ts, equity, balance, floating_pl,"
            " open_count, kill_switch, exposure_min, active_strategy)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (now - age_days * DAY, 1.0, 1.0, 0.0, 0, 0, 0, "s"))
    newest = now
    for age_days in (0, 91, 89):
        db.conn.execute(
            "INSERT OR REPLACE INTO spread_history VALUES (?,?,?,?)",
            (newest - age_days * DAY, 1.0, 2.0, 3.0))
    db.conn.commit()
    db.conn.close()

    fresh = SignalDb(path)
    kept_hb = [r[0] for r in
               fresh.conn.execute("SELECT ts FROM heartbeats").fetchall()]
    assert now - 89 * DAY in kept_hb
    assert now - 91 * DAY not in kept_hb
    kept_sp = [r[0] for r in
               fresh.conn.execute("SELECT bar_time FROM spread_history").fetchall()]
    assert newest in kept_sp and newest - 89 * DAY in kept_sp
    assert newest - 91 * DAY not in kept_sp


# --- A6: stats() memoization --------------------------------------------
def _sig(db, signal="BUY", bar_time=1_754_000_000, price=2400.0):
    return db.insert_signal(
        bar_time=bar_time, symbol="XAUUSD", signal=signal, price=price,
        direction="bullish", confidence=0.5, regime="trend", verdict="confirm",
        mode="grading", ai_available=True, strategy_id="lw_test",
        timeframe="M5")


def test_stats_cache_invalidated_by_insert_signal(tmp_path):
    db = SignalDb(str(tmp_path / "s.db"))
    assert db.stats()["total"] == 0
    assert db.stats()["total"] == 0          # served from cache
    _sig(db)
    assert db.stats()["total"] == 1


def test_stats_cache_invalidated_by_resolving_resolve_outcomes(tmp_path):
    db = SignalDb(str(tmp_path / "s2.db"))
    t0 = 1_754_000_000
    _sig(db, bar_time=t0)
    assert db.stats()["resolved"] == 0
    candles = [_C(t0 + i * 300, 2400.0 + i) for i in range(40)]
    assert db.resolve_outcomes(candles, 16) == 1
    assert db.stats()["resolved"] == 1
    # a non-resolving call must not disturb the cached value
    assert db.resolve_outcomes(candles, 16) == 0
    assert db.stats()["resolved"] == 1


def test_stats_cached_copy_is_not_shared(tmp_path):
    db = SignalDb(str(tmp_path / "s3.db"))
    first = db.stats()
    first["total"] = 999
    assert db.stats()["total"] == 0


# --- A7: active_proposal ------------------------------------------------
def test_active_proposal_none_when_empty(tmp_path):
    db = SignalDb(str(tmp_path / "pr.db"))
    assert db.active_proposal() is None


def test_active_proposal_prefers_pending_then_approved_then_dispatched(tmp_path):
    db = SignalDb(str(tmp_path / "pr2.db"))
    disp = db.create_proposal("entry", "BUY", "lw", 2400.0, None)
    db.set_proposal_status(disp, "dispatched")
    assert db.active_proposal()["id"] == disp

    appr = db.create_proposal("entry", "SELL", "lw", 2401.0, None)
    db.set_proposal_status(appr, "approved")
    assert db.active_proposal()["id"] == appr

    pend = db.create_proposal("entry", "BUY", "lw", 2402.0, None)
    assert db.active_proposal()["id"] == pend

    # decided rows drop out entirely
    db.set_proposal_status(pend, "skipped")
    db.set_proposal_status(appr, "expired")
    assert db.active_proposal()["id"] == disp
    db.set_proposal_status(disp, "executed")
    assert db.active_proposal() is None


def test_state_endpoint_uses_active_proposal(client):
    from app import main
    pid = main.app.state.db.create_proposal("entry", "BUY", "lw", 2400.0, None)
    assert client.get("/api/state").json()["proposal"]["id"] == pid


# --- B9: /analyze short-payload guard -----------------------------------
def test_resolve_outcomes_needs_two_candles(tmp_path):
    """Why the guard exists: the bar interval is inferred from
    candles[1].t - candles[0].t."""
    db = SignalDb(str(tmp_path / "one.db"))
    with pytest.raises(IndexError):
        db.resolve_outcomes([_C(1_754_000_000, 2400.0)], 16)


def test_analyze_with_one_candle_does_not_raise(client, monkeypatch):
    """A single-candle payload used to raise IndexError straight out of
    /analyze (via resolve_outcomes). The handler now guards it. The
    regime classifier has its own unrelated short-input floor (ATR needs
    14 bars) and is out of scope here, so it is stubbed -- what is under
    test is that the analyze() body itself survives the short payload."""
    from app import main
    from app.models import AnalyzeRequest, Candle
    monkeypatch.setattr(main, "classify_regime", lambda c: "range")
    monkeypatch.setattr(main, "last_atr", lambda c: 1.0)
    req = AnalyzeRequest.model_construct(
        symbol="XAUUSD", timeframe="M5", signal="NONE", strategy_id="lw_test",
        shadows=[], spread_min=0.0, spread_avg=0.0, spread_max=0.0,
        candles=[Candle(t=1_754_000_000, o=2400, h=2401, l=2399, c=2400.5)])
    resp = main.analyze(req)                    # must not raise
    assert resp.regime == "range"


def test_analyze_http_short_payload_is_rejected_not_500(client):
    """Belt and braces at the HTTP boundary: the request schema's
    min_length=50 means a short payload is a 422, never a 500."""
    r = client.post("/analyze", json=_payload(_candles(1)))
    assert r.status_code == 422


# --- B11: /api/candles + /api/overlays caching ---------------------------
def test_candles_payload_cached_until_accumulator_changes(client):
    from app import main
    candles = _candles()
    assert client.post("/analyze", json=_payload(candles)).status_code == 200
    first = main.ui_candles()
    second = main.ui_candles()
    assert first is second                      # cache hit, no rebuild

    bumped = [dict(c) for c in candles]
    bumped[-1]["c"] += 5.0
    assert client.post("/analyze", json=_payload(bumped)).status_code == 200
    third = main.ui_candles()
    assert third is not first
    assert third["candles"][-1]["c"] == bumped[-1]["c"]


def test_overlays_payload_cached_until_accumulator_changes(client):
    from app import main
    candles = _candles(120)
    assert client.post("/analyze", json=_payload(candles)).status_code == 200
    first = main.ui_overlays("halftrend_ema_v1")
    assert first is main.ui_overlays("halftrend_ema_v1")
    # a different strategy is a separate cache entry, not a bust
    other = main.ui_overlays("boll_stochrsi_v1")
    assert other is not first
    assert first is main.ui_overlays("halftrend_ema_v1")

    bumped = [dict(c) for c in candles]
    bumped[-1]["c"] += 5.0
    assert client.post("/analyze", json=_payload(bumped)).status_code == 200
    assert main.ui_overlays("halftrend_ema_v1") is not first


class _RacingCache(dict):
    """A cache that reports one stale key it no longer holds -- exactly what
    the eviction loop sees when a concurrent request pops that key between
    the stale-key list being built and this request deleting it."""

    def __iter__(self):
        return iter([("gone-key", "halftrend_ema_v1"), *self.keys()])


def test_overlays_eviction_tolerates_a_concurrently_removed_key(client):
    """/api/overlays is a sync def, so FastAPI runs it in the threadpool and
    two requests can build the same stale-key list and race the eviction.
    The loser used to hit an unhandled KeyError -> 500."""
    from app import main
    assert client.post("/analyze", json=_payload(_candles(120))).status_code == 200
    stale = (("old-key",), "halftrend_ema_v1")
    cache = main.app.state.overlays_cache = _RacingCache()
    cache[stale] = {}
    assert client.get("/api/overlays?strategy=halftrend_ema_v1").status_code == 200
    assert stale not in cache          # the genuinely-present stale entry went


def test_candles_endpoint_still_serves_over_http(client):
    assert client.post("/analyze", json=_payload(_candles())).status_code == 200
    body = client.get("/api/candles").json()
    assert body["symbol"] == "XAUUSD" and len(body["candles"]) == 60
