# UI + Backtest Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist candles in SQLite, rebuild the dashboard chart on Lightweight Charts with past trades drawn on it, surface every recent behavior change in the UI, add a Backtest page that drives the existing `scripts/backtest.py` engine, and restyle onboarding into a menu-reachable Settings page.

**Architecture:** All service-side (zero MQL5 changes). A new `candles` table is fed by `/analyze` and a backfill script; the dashboard swaps its hand-drawn canvas for the vendored TradingView Lightweight Charts library; the backtest API runs the untouched CLI engine as a subprocess over candles exported from SQLite (`halftrend_ema_v1` → `--tf M5 --confirm 2`, `halftrend_m15_v1` → `--tf M15 --confirm 3` — the M15 lane needs **no engine changes**, resampling and `--tf M15` already exist).

**Tech Stack:** FastAPI + SQLite (stdlib `sqlite3`), vanilla-JS static HTML (no build step, no npm), vendored `lightweight-charts.standalone.production.js`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-ui-backtest-revamp-design.md`

## Deliberate deviations from the spec (all simplifications, none change the user-visible result)

1. **No new lane in `LANES`.** The spec assumed the M15 lane needed engine work; in fact `--tf M15` + `--confirm 3` reproduces `halftrend_m15_v1` (EA inputs at `mt5/Experts/XauAssistant.mq5:76-86`: only ConfirmCloses differs from M5; amplitude 4 / EMA 55 / stop buffer 0.75 are identical). The engine file is not touched at all.
2. **Engine runs as a subprocess**, not an import — `scripts/backtest.py` configures itself through module globals in `main()` (lines 1746-1810), which is unsafe to drive in-process from a threaded service. Subprocess isolation also means an engine crash can never take the service down.
3. **No `progress` percent** on runs — a subprocess can't report it. Status is `running/done/failed` (+ error text). The page shows elapsed time instead.
4. **M15 candles are always resampled from M5 by the engine** (its existing `resample()`); we never store or prefer M15 rows. The backfill stores M5 only.
5. **Chop filter dropped from the v1 backtest form** — there is no validated parameter set (`--chop-flips` default 0 = off) and guessing one would mislead. The CLI keeps the full flag surface for power use.
6. **Trade P/L on marker text, not hover tooltip** — Lightweight Charts markers have no native hover; the close marker carries `+12.34` as its label and the trade table stays the detail view.
7. **`setup.sh` prints a hint** when the candles table is empty instead of auto-running the backfill (the backfill needs Windows Python + a running MT5 terminal, which setup.sh cannot assume).
8. **No per-run delete button in v1** — runs are small rows + on-disk artifacts under `service/data/backtests/`; the list caps at 20 recent runs and disk cleanup is an `rm -r` away. A DELETE endpoint can come later if run clutter becomes real.

## Global Constraints

- **Fail-open everywhere**: candle persistence, rule toggles, chart, and backtest failures must never break `/analyze`, `/heartbeat`, or trading. Wrap persistence in `try/except` that swallows.
- **Golden pins untouched**: `service/tests/test_backtest_golden.py` (LOOSE and STRICT) must pass byte-identical — `scripts/backtest.py` and `service/app/static/backtest_report.html` are read-only in this plan.
- **No MQL5 changes.** Nothing in `mt5/` is edited.
- **No build step, no npm, no CDN**: static HTML with inline JS/CSS; the only external asset is the already-vendored Lightweight Charts file and the Google Fonts link the pages already carry.
- All tests run from `service/`: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_<file>.py -v` (the fast suite excludes `slow` by default). Full-suite check: `.venv/bin/python -m pytest`.
- Commits on branch `feat/ui-backtest-revamp`. Commit messages end with the repo's standard trailer (see Bash tool git guidance).
- **izi.md law** (CLAUDE.md): behavior-changing commits update `.claude/agents/izi.md` in the same commit or immediate follow-up. Tasks 1-11 may defer to Task 12's consolidated izi.md update **only if pushed together**; each such commit message must say `izi.md: updated in the final task of this plan`.
- Known flake: `test_pop_approved_command_concurrent_exactly_once` — re-run before treating a failure there as yours.

---

### Task 1: `candles` table + accessors in `db.py`

**Files:**
- Modify: `service/app/db.py` (schema block ~line 60, `SignalDb.__init__` ~line 96, new methods after `recent_signals` ~line 280)
- Test: `service/tests/test_candles_db.py` (new)

**Interfaces:**
- Produces: `SignalDb.upsert_candles(symbol: str, timeframe: str, candles) -> int` (accepts objects with `.t/.o/.h/.l/.c/.v` **or** dicts with those keys; INSERT OR REPLACE; returns row count), `SignalDb.get_candles(symbol, timeframe, start_ts=None, end_ts=None, limit=None) -> list[dict]` (ascending `bar_time`; dict keys `t,o,h,l,c,v`; `limit` keeps the **newest** N, still ascending), `SignalDb.candles_range(symbol, timeframe) -> dict | None` (`{"start","end","count"}`), `SignalDb.latest_candle_series() -> tuple[str, str] | None` (`(symbol, timeframe)` of the newest bar).

- [ ] **Step 1: Write the failing tests**

```python
# service/tests/test_candles_db.py
from app.db import SignalDb


def _mk(tmp_path):
    return SignalDb(str(tmp_path / "t.db"))


def bar(t, c=100.0):
    return {"t": t, "o": c - 1, "h": c + 2, "l": c - 2, "c": c, "v": 10.0}


def test_upsert_and_get_roundtrip(tmp_path):
    db = _mk(tmp_path)
    n = db.upsert_candles("XAUUSD", "M5", [bar(300), bar(600, 101.0)])
    assert n == 2
    rows = db.get_candles("XAUUSD", "M5")
    assert [r["t"] for r in rows] == [300, 600]
    assert rows[1]["c"] == 101.0


def test_upsert_replaces_same_bar_time(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(300, 100.0)])
    db.upsert_candles("XAUUSD", "M5", [bar(300, 105.0)])   # forming bar re-sent
    rows = db.get_candles("XAUUSD", "M5")
    assert len(rows) == 1 and rows[0]["c"] == 105.0


def test_upsert_accepts_objects(tmp_path):
    class C:
        t, o, h, l, c, v = 900, 1.0, 2.0, 0.5, 1.5, 3.0
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [C()])
    assert db.get_candles("XAUUSD", "M5")[0]["c"] == 1.5


def test_get_candles_range_and_limit(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(t) for t in range(300, 3300, 300)])
    rows = db.get_candles("XAUUSD", "M5", start_ts=600, end_ts=1200)
    assert [r["t"] for r in rows] == [600, 900, 1200]
    newest3 = db.get_candles("XAUUSD", "M5", limit=3)
    assert [r["t"] for r in newest3] == [2400, 2700, 3000]   # newest N, ascending


def test_series_are_isolated_by_symbol_and_tf(tmp_path):
    db = _mk(tmp_path)
    db.upsert_candles("XAUUSD", "M5", [bar(300)])
    db.upsert_candles("XAUUSD", "M15", [bar(900)])
    assert len(db.get_candles("XAUUSD", "M5")) == 1
    assert db.candles_range("XAUUSD", "M15") == {"start": 900, "end": 900, "count": 1}
    assert db.candles_range("EURUSD", "M5") is None
    assert db.latest_candle_series() == ("XAUUSD", "M15")


def test_latest_candle_series_empty(tmp_path):
    assert _mk(tmp_path).latest_candle_series() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_candles_db.py -v`
Expected: FAIL with `AttributeError: 'SignalDb' object has no attribute 'upsert_candles'`

- [ ] **Step 3: Implement**

In `db.py`, after `_PROPOSALS_SCHEMA` add:

```python
_CANDLES_SCHEMA = """CREATE TABLE IF NOT EXISTS candles (
  symbol    TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_time  INTEGER NOT NULL,
  o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
  v REAL DEFAULT 0,
  PRIMARY KEY (symbol, timeframe, bar_time)
)"""
```

In `SignalDb.__init__`, next to the other `self.conn.execute(_*_SCHEMA)` calls add `self.conn.execute(_CANDLES_SCHEMA)`.

Add methods (after `recent_signals`):

```python
    def upsert_candles(self, symbol: str, timeframe: str, candles) -> int:
        """Persist bars; a re-sent bar_time (the EA re-posts the forming bar
        until it closes) replaces the stored row, so the final close wins."""
        rows = []
        for c in candles:
            if isinstance(c, dict):
                rows.append((symbol, timeframe, int(c["t"]), c["o"], c["h"],
                             c["l"], c["c"], c.get("v", 0.0)))
            else:
                rows.append((symbol, timeframe, int(c.t), c.o, c.h,
                             c.l, c.c, getattr(c, "v", 0.0)))
        self.conn.executemany(
            "INSERT OR REPLACE INTO candles (symbol, timeframe, bar_time,"
            " o, h, l, c, v) VALUES (?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def get_candles(self, symbol: str, timeframe: str, start_ts=None,
                    end_ts=None, limit=None) -> list:
        q = ("SELECT bar_time, o, h, l, c, v FROM candles"
             " WHERE symbol=? AND timeframe=?")
        args = [symbol, timeframe]
        if start_ts is not None:
            q += " AND bar_time >= ?"; args.append(int(start_ts))
        if end_ts is not None:
            q += " AND bar_time <= ?"; args.append(int(end_ts))
        if limit is not None:
            # newest N, returned ascending like every other path
            q = (f"SELECT * FROM ({q} ORDER BY bar_time DESC LIMIT ?)"
                 " ORDER BY bar_time ASC")
            args.append(int(limit))
        else:
            q += " ORDER BY bar_time ASC"
        rows = self.conn.execute(q, args).fetchall()
        return [{"t": t, "o": o, "h": h, "l": l, "c": c, "v": v or 0.0}
                for t, o, h, l, c, v in rows]

    def candles_range(self, symbol: str, timeframe: str) -> dict | None:
        row = self.conn.execute(
            "SELECT MIN(bar_time), MAX(bar_time), COUNT(*) FROM candles"
            " WHERE symbol=? AND timeframe=?", (symbol, timeframe)).fetchone()
        if not row or not row[2]:
            return None
        return {"start": row[0], "end": row[1], "count": row[2]}

    def latest_candle_series(self) -> tuple | None:
        row = self.conn.execute(
            "SELECT symbol, timeframe FROM candles"
            " ORDER BY bar_time DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_candles_db.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run the full fast suite (schema change touches every SignalDb user)**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add service/app/db.py service/tests/test_candles_db.py
git commit -m "feat(db): persistent candles table with upsert/range accessors

izi.md: updated in the final task of this plan."
```

---

### Task 2: `/analyze` persists candles; startup seeds the chart; `/static` mount

**Files:**
- Modify: `service/app/main.py` (lifespan ~line 295-330, `analyze` ~line 427-442, app wiring ~line 331)
- Test: `service/tests/test_candle_persistence.py` (new)

**Interfaces:**
- Consumes: Task 1's `upsert_candles`, `get_candles`, `latest_candle_series`.
- Produces: `/analyze` side effect (candles rows appear); on startup `app.state.recent_candles` is pre-seeded from the DB (same `{"symbol","timeframe","candles":[Candle...]}` shape the accumulator uses); `GET /static/vendor/lightweight-charts.standalone.production.js` serves the vendored lib.

- [ ] **Step 1: Write the failing tests**

Look at an existing `/analyze` test in `service/tests/test_api.py` first and copy its client/fixture pattern (it builds a `TestClient` with `FORECASTER=fake`); reuse the same request-body helper the contract tests use. Then:

```python
# service/tests/test_candle_persistence.py
# Reuse the TestClient fixture pattern from tests/test_api.py -- same env
# (FORECASTER=fake, tmp db path), same /analyze payload builder.


def test_analyze_persists_candles(client):
    body = analyze_body(signal="NONE")            # helper per test_api.py
    r = client.post("/analyze", json=body)
    assert r.status_code == 200
    db = client.app.state.db
    rows = db.get_candles(body["symbol"], body["timeframe"])
    assert len(rows) == len(body["candles"])
    assert rows[-1]["c"] == body["candles"][-1]["c"]


def test_startup_seeds_recent_candles(tmp_path):
    # Arrange: a db file that already holds bars, then boot the app on it.
    from app.db import SignalDb
    db_path = str(tmp_path / "seed.db")
    pre = SignalDb(db_path)
    pre.upsert_candles("XAUUSD", "M5", [
        {"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0} for i in range(1, 6)])
    client = make_client(db_path=db_path)         # per test_api.py pattern
    rc = client.app.state.recent_candles
    assert rc is not None and rc["symbol"] == "XAUUSD"
    assert len(rc["candles"]) == 5
    assert rc["candles"][-1].t == 1500            # Candle objects, not dicts


def test_static_vendor_served(client):
    r = client.get("/static/vendor/lightweight-charts.standalone.production.js")
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_candle_persistence.py -v`
Expected: FAIL (no candles rows; `recent_candles` is None; /static 404)

- [ ] **Step 3: Implement in `main.py`**

(a) Static mount, right after `app = FastAPI(...)`:

```python
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"),
          name="static")
```

(b) In `analyze()`, right after the `app.state.recent_candles = {...}` assignment:

```python
    # Persist the merged window: the forming bar's re-posts overwrite the
    # same bar_time until the close is final. Fail-open -- persistence must
    # never break grading.
    try:
        app.state.db.upsert_candles(req.symbol, req.timeframe, req.candles)
    except Exception:
        pass
```

(c) In `lifespan()`, replace `app.state.recent_candles = None` with:

```python
    app.state.recent_candles = None
    # Seed the chart accumulator from the persistent candles table so the
    # dashboard survives a service restart instead of starting empty.
    try:
        series = app.state.db.latest_candle_series()
        if series is not None:
            from app.models import Candle
            sym, tf = series
            rows = app.state.db.get_candles(sym, tf, limit=_CANDLE_WINDOW_CAP)
            if rows:
                app.state.recent_candles = {
                    "symbol": sym, "timeframe": tf,
                    "candles": [Candle(**r) for r in rows]}
    except Exception:
        pass
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_candle_persistence.py tests/test_api.py -v`
Expected: PASS (including the existing /analyze contract tests)

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_candle_persistence.py
git commit -m "feat(service): persist /analyze candles, seed chart from db on startup, mount /static

izi.md: updated in the final task of this plan."
```

---

### Task 3: backfill script `scripts/backfill_candles.py`

**Files:**
- Create: `scripts/backfill_candles.py`
- Test: `service/tests/test_backfill_candles.py` (new)

**Interfaces:**
- Consumes: Task 1's `upsert_candles` / `candles_range`; the `dump_bars.py` JSON shape `{"symbol","timeframe","candles":[{t,o,h,l,c,v}...]}`.
- Produces: `backfill_candles.load_dump(db, path) -> int` (importable, tested) + CLI `python3 scripts/backfill_candles.py [--db PATH] dump1.json [dump2.json ...]`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_backfill_candles.py
import importlib.util
import json
from pathlib import Path

from app.db import SignalDb

_SPEC = importlib.util.spec_from_file_location(
    "backfill_candles",
    Path(__file__).resolve().parents[2] / "scripts" / "backfill_candles.py")
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


def test_load_dump_idempotent(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    dump = tmp_path / "week.json"
    dump.write_text(json.dumps({
        "symbol": "XAUUSD", "timeframe": "M5",
        "candles": [{"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}
                    for i in range(1, 11)]}))
    assert backfill.load_dump(db, str(dump)) == 10
    assert backfill.load_dump(db, str(dump)) == 10        # re-run: no dupes
    assert db.candles_range("XAUUSD", "M5")["count"] == 10
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backfill_candles.py -v`
Expected: FAIL (`FileNotFoundError` — script doesn't exist)

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""Load dump_bars.py JSON dumps into the service's persistent candles table.

Two-step backfill (the MT5 python package only runs under WINDOWS python,
so the pull and the load are separate steps):

  1. pull from the running terminal (Windows python, from the repo root):
       python.exe scripts/dump_bars.py 75000 bars_max.json    # ~12 months of M5
  2. load into SQLite (WSL, from service/ so the default db path matches):
       cd service && python3 ../scripts/backfill_candles.py ../bars_max.json

Idempotent: bars are keyed (symbol, timeframe, bar_time); re-running a load
replaces identical rows and never duplicates.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.db import SignalDb  # noqa: E402


def load_dump(db: SignalDb, path: str) -> int:
    data = json.load(open(path))
    return db.upsert_candles(data["symbol"], data["timeframe"], data["candles"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dumps", nargs="+", help="dump_bars.py JSON file(s)")
    ap.add_argument("--db", default="xau_assistant.db",
                    help="SQLite db path (default: xau_assistant.db in CWD"
                         " -- run from service/)")
    args = ap.parse_args()
    db = SignalDb(args.db)
    for p in args.dumps:
        n = load_dump(db, p)
        data = json.load(open(p))
        rng = db.candles_range(data["symbol"], data["timeframe"])
        print(f"{p}: loaded {n} bars -> table holds {rng['count']} "
              f"({rng['start']} .. {rng['end']})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backfill_candles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_candles.py service/tests/test_backfill_candles.py
git commit -m "feat(backfill): scripts/backfill_candles.py loads bar dumps into the candles table

Behavior-neutral ops tooling; izi.md: updated in the final task of this plan."
```

---

### Task 4: trade agree-flags in `recent_trades`; rule state + `POST /ui/rules`

**Files:**
- Modify: `service/app/db.py` (`recent_trades` ~line 296), `service/app/main.py` (`ui_state` ~line 742, new endpoint after `ui_mode` ~line 703)
- Test: `service/tests/test_rules_api.py` (new); extend `service/tests/test_candles_db.py` is NOT needed — trades assertions go in the new file too.

**Interfaces:**
- Consumes: existing `db.entry_mode()/set_entry_mode()`, `db.htf_enforce()/set_htf_enforce()`, `db.ema200_enforce()/set_ema200_enforce()` (validation lives in `set_choice`, raising `ValueError`).
- Produces: `recent_trades()` rows additionally carry `entry_mode`, `htf_agree`, `ema200_agree`; `GET /ui/state` response gains `"rules": {"entry_mode","htf_enforce","ema200_enforce"}`; `POST /ui/rules` body `{"key","value"}` → `{key: value}` or 400.

- [ ] **Step 1: Write the failing tests**

```python
# service/tests/test_rules_api.py
# Reuse the TestClient fixture pattern from tests/test_api.py.


def test_recent_trades_carries_agree_flags(tmp_path):
    from app.db import SignalDb
    db = SignalDb(str(tmp_path / "t.db"))
    db.insert_trade({"event": "open", "strategy_id": "halftrend_ema_v1",
                     "direction": "BUY", "lots": 0.1, "price": 4000.0,
                     "entry_mode": "adr", "htf_agree": 1, "ema200_agree": 0})
    row = db.recent_trades(1)[0]
    assert row["htf_agree"] == 1
    assert row["ema200_agree"] == 0
    assert row["entry_mode"] == "adr"


def test_state_exposes_rules(client):
    s = client.get("/ui/state").json()
    assert s["rules"] == {"entry_mode": "adr", "htf_enforce": "off",
                          "ema200_enforce": "off"}


def test_post_rules_roundtrip(client):
    r = client.post("/ui/rules", json={"key": "htf_enforce", "value": "M15"})
    assert r.status_code == 200 and r.json() == {"htf_enforce": "M15"}
    assert client.get("/ui/state").json()["rules"]["htf_enforce"] == "M15"
    assert client.post("/ui/rules",
                       json={"key": "ema200_enforce", "value": "on"}).status_code == 200
    assert client.post("/ui/rules",
                       json={"key": "entry_mode", "value": "fixed"}).status_code == 200


def test_post_rules_rejects_bad_input(client):
    assert client.post("/ui/rules",
                       json={"key": "htf_enforce", "value": "H4"}).status_code == 400
    assert client.post("/ui/rules",
                       json={"key": "exec_mode", "value": "auto"}).status_code == 400
    assert client.post("/ui/rules", json={}).status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_rules_api.py -v`
Expected: FAIL (missing keys / 404 on /ui/rules)

- [ ] **Step 3: Implement**

(a) `db.py` `recent_trades`: extend the column list:

```python
        cols = ["id", "ts", "event", "strategy_id", "direction", "lots", "price",
                "sl", "reason", "ticket", "screenshot_path", "profit", "render_path",
                "tp", "final", "entry_mode", "htf_agree", "ema200_agree"]
```

(b) `main.py` `ui_state`: add to the returned dict:

```python
            "rules": {"entry_mode": app.state.db.entry_mode(),
                      "htf_enforce": app.state.db.htf_enforce(),
                      "ema200_enforce": app.state.db.ema200_enforce()},
```

(c) `main.py`, after `ui_mode`:

```python
@app.post("/ui/rules")
def ui_rules(body: dict):
    """Dashboard mirror of the Telegram rule commands (/agree etc.). Writes
    the same kv keys the EA reads back on every heartbeat -- last writer
    (dashboard or Telegram) wins, both stay live."""
    key = str(body.get("key", "")).strip()
    value = str(body.get("value", "")).strip()
    db = app.state.db
    setters = {"entry_mode": db.set_entry_mode,
               "htf_enforce": db.set_htf_enforce,
               "ema200_enforce": db.set_ema200_enforce}
    if key not in setters:
        raise HTTPException(status_code=400, detail="unknown rule key")
    try:
        setters[key](value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {key: value}
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_rules_api.py tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/db.py service/app/main.py service/tests/test_rules_api.py
git commit -m "feat(service): agree flags in /ui/trades, rules in /ui/state, POST /ui/rules toggle endpoint

izi.md: updated in the final task of this plan."
```

---

### Task 5: M15 overlays for `halftrend_m15_v1` (+ BB alias)

**Files:**
- Modify: `service/app/main.py` (overlay builders ~line 785-811)
- Test: `service/tests/test_overlays_m15.py` (new)

**Interfaces:**
- Consumes: `app.indicators.ema/halftrend`; the `/ui/overlays` contract (arrays aligned 1:1 with `/ui/candles`, `None` for warmup).
- Produces: `_resample_m15(candles) -> list[dict]` (module-level, 900-second buckets, forming bucket included); `_OVERLAY_BUILDERS` gains `"halftrend_m15_v1"` (keys `halftrend`, `ema55`, `ema200` — M15-computed, expanded back to M5 alignment) and `"boll_stochrsi"` as an alias of the existing BB builder (the EA-side id vs the dashboard's historical `boll_stochrsi_v1` — registering both is cheaper than chasing which spelling each consumer uses).

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_overlays_m15.py
from app.main import _resample_m15, _OVERLAY_BUILDERS
from app.models import Candle


def m5(t, c):
    return Candle(t=t, o=c - 1, h=c + 1, l=c - 2, c=c, v=1)


def test_resample_m15_buckets_and_ohlc():
    # 09:00, 09:05, 09:10 -> one M15 bucket; 09:15 starts the next
    candles = [m5(900, 10.0), m5(1200, 12.0), m5(1500, 11.0), m5(1800, 13.0)]
    out = _resample_m15(candles)
    assert [b["t"] for b in out] == [900, 1800]
    b0 = out[0]
    assert b0["o"] == candles[0].o
    assert b0["c"] == 11.0                       # last M5 close in the bucket
    assert b0["h"] == max(c.h for c in candles[:3])
    assert b0["l"] == min(c.l for c in candles[:3])


def test_m15_overlays_align_with_m5_list():
    candles = [m5(900 * (i // 3) + 300 * (i % 3) + 900, 100.0 + i)
               for i in range(600)]              # 600 M5 bars = 200 M15 bars
    closes = [c.c for c in candles]
    out = _OVERLAY_BUILDERS["halftrend_m15_v1"](candles, closes)
    assert set(out) == {"halftrend", "ema55", "ema200"}
    for arr in out.values():
        assert len(arr) == len(candles)          # 1:1 with /ui/candles
    # three consecutive M5 bars share their M15 bucket's value
    i = 450
    j = i - (i % 3)
    assert out["ema55"][j] == out["ema55"][j + 1] == out["ema55"][j + 2]


def test_bb_alias_registered():
    assert _OVERLAY_BUILDERS["boll_stochrsi"] is _OVERLAY_BUILDERS["boll_stochrsi_v1"]
```

(Note: the timestamp arithmetic in the 600-bar generator must produce strictly increasing M5 times — `t = 900 + 300*i` is simpler and correct; use that instead if the bucketed expression confuses.)

Use `t = 900 + 300 * i` in the generator. Bucket membership then follows from `t % 900`.

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_overlays_m15.py -v`
Expected: FAIL (`ImportError: cannot import name '_resample_m15'`)

- [ ] **Step 3: Implement in `main.py`** (above `_OVERLAY_BUILDERS`)

```python
def _resample_m15(candles: list) -> list:
    """3xM5 -> 1xM15 dict bars aligned to 900 s boundaries. The trailing
    (possibly incomplete) bucket is kept -- on a live chart the forming M15
    bar is real information, and the EA's own M15 lane sees the same thing."""
    out = []
    for c in candles:
        bucket = c.t - (c.t % 900)
        if out and out[-1]["t"] == bucket:
            b = out[-1]
            b["h"] = max(b["h"], c.h)
            b["l"] = min(b["l"], c.l)
            b["c"] = c.c
        else:
            out.append({"t": bucket, "o": c.o, "h": c.h, "l": c.l, "c": c.c})
    return out


def _overlays_halftrend_m15_v1(candles: list, closes: list) -> dict:
    """halftrend_m15_v1's indicators computed on M15 bars, expanded back to
    the M5 candle list (each M5 bar takes its M15 bucket's value) so the
    arrays stay 1:1 with /ui/candles like every other overlay."""
    m15 = _resample_m15(candles)
    if len(m15) < 3:
        return {}
    m15_objs = [type("C", (), b)() for b in m15]
    closes15 = [b["c"] for b in m15]
    ht15 = halftrend(m15_objs, amplitude=4)
    e55, e200 = ema(closes15, 55), ema(closes15, 200)
    idx = {b["t"]: i for i, b in enumerate(m15)}
    out_ht, out55, out200 = [], [], []
    for c in candles:
        i = idx[c.t - (c.t % 900)]
        v = ht15[i]
        out_ht.append(list(v) if v is not None else None)
        out55.append(e55[i])
        out200.append(e200[i])
    return {"halftrend": out_ht, "ema55": out55, "ema200": out200}
```

And extend the registry:

```python
_OVERLAY_BUILDERS = {
    "halftrend_ema_v1": _overlays_halftrend_ema_v1,
    "halftrend_m15_v1": _overlays_halftrend_m15_v1,
    "boll_stochrsi_v1": _overlays_boll_stochrsi_v1,
    "boll_stochrsi": _overlays_boll_stochrsi_v1,     # EA-side id alias
}
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_overlays_m15.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_overlays_m15.py
git commit -m "feat(service): /ui/overlays for halftrend_m15_v1 (M15 resample) + boll_stochrsi alias

izi.md: updated in the final task of this plan."
```

---

### Task 6: `backtest_runs` table + subprocess runner

**Files:**
- Modify: `service/app/db.py` (new schema + 4 methods)
- Create: `service/app/backtest_runner.py`
- Test: `service/tests/test_backtest_runner.py` (new)

**Interfaces:**
- Consumes: Task 1's `get_candles`.
- Produces:
  - `SignalDb.insert_backtest_run(params_json: str) -> int` (status `'running'`), `SignalDb.finish_backtest_run(run_id, *, status, error=None, stats_json=None, report_path=None)`, `SignalDb.get_backtest_run(run_id) -> dict | None`, `SignalDb.recent_backtest_runs(limit=20) -> list[dict]` (newest first; each row's `params_json`/`stats_json` are raw strings — callers decode).
  - `backtest_runner.STRATEGIES: dict` — `{"halftrend_ema_v1": {"label": "HalfTrend M5", "flags": ["--tf","M5","--confirm","2"]}, "halftrend_m15_v1": {"label": "HalfTrend M15", "flags": ["--tf","M15","--confirm","3"]}}` (ConfirmCloses per EA inputs: M5=2, M15=3).
  - `backtest_runner.build_cli(params: dict, source: Path, json_out: Path, web_out: Path) -> list[str]`
  - `backtest_runner.start_run(db, params: dict) -> int` — inserts the row, spawns a daemon thread, returns `run_id`; raises `RuntimeError("a backtest is already running")` when busy.
  - `params` dict keys (validated by the endpoint in Task 7): `strategy, symbol, start_ts, end_ts, balance, risk_pct, entry_mode, exit_scheme, ema200_confirm, m15_bias`.

- [ ] **Step 1: Write the failing tests**

```python
# service/tests/test_backtest_runner.py
import json
import sys
from pathlib import Path

from app import backtest_runner
from app.db import SignalDb


def _params(**over):
    p = {"strategy": "halftrend_ema_v1", "symbol": "XAUUSD",
         "start_ts": 0, "end_ts": 10**10, "balance": 10000.0,
         "risk_pct": 1.0, "entry_mode": "adr", "exit_scheme": "target-exit",
         "ema200_confirm": "off", "m15_bias": "off"}
    p.update(over)
    return p


def test_run_rows_lifecycle(tmp_path):
    db = SignalDb(str(tmp_path / "t.db"))
    rid = db.insert_backtest_run(json.dumps(_params()))
    assert db.get_backtest_run(rid)["status"] == "running"
    db.finish_backtest_run(rid, status="done", stats_json='{"net": 5}',
                           report_path="/x/report.html")
    row = db.get_backtest_run(rid)
    assert row["status"] == "done" and row["report_path"] == "/x/report.html"
    assert db.recent_backtest_runs()[0]["id"] == rid
    assert db.get_backtest_run(999) is None


def test_build_cli_maps_strategies_and_flags(tmp_path):
    src, jout, wout = tmp_path / "b.json", tmp_path / "r.json", tmp_path / "r.html"
    cmd = backtest_runner.build_cli(_params(), src, jout, wout)
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("scripts/backtest.py")
    s = " ".join(cmd)
    assert "--tf M5" in s and "--confirm 2" in s
    assert "--balance 10000.0" in s and "--risk 1.0" in s
    assert "--entry-mode adr" in s and "--exit-scheme target-exit" in s
    assert "--ema200-confirm off" in s and "--bias-ema" not in s

    cmd15 = backtest_runner.build_cli(_params(strategy="halftrend_m15_v1"),
                                      src, jout, wout)
    s15 = " ".join(cmd15)
    assert "--tf M15" in s15 and "--confirm 3" in s15

    biased = " ".join(backtest_runner.build_cli(_params(m15_bias="on"),
                                                src, jout, wout))
    assert "--bias-ema 200" in biased and "--bias-tf M15" in biased \
        and "--bias-mode target" in biased
    # bias is an M5-lane concept: the M15 lane never gets the flags
    b15 = " ".join(backtest_runner.build_cli(
        _params(strategy="halftrend_m15_v1", m15_bias="on"), src, jout, wout))
    assert "--bias-ema" not in b15


def test_execute_happy_path_with_fake_engine(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    db.upsert_candles("XAUUSD", "M5", [
        {"t": 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}
        for i in range(1, 400)])
    monkeypatch.setattr(backtest_runner, "RUNS_DIR", tmp_path / "runs")

    def fake_run(cmd, **kw):
        # the engine writes --json and --web outputs; fake both
        jout = Path(cmd[cmd.index("--json") + 1])
        jout.write_text(json.dumps({"stats": {"net": 42.0, "trades": 3}}))
        Path(cmd[cmd.index("--web") + 1]).write_text("<html></html>")
        class P:
            returncode, stdout, stderr = 0, "", ""
        return P()

    monkeypatch.setattr(backtest_runner.subprocess, "run", fake_run)
    rid = db.insert_backtest_run(json.dumps(_params()))
    backtest_runner._execute(db, rid, _params())
    row = db.get_backtest_run(rid)
    assert row["status"] == "done"
    assert json.loads(row["stats_json"])["net"] == 42.0
    assert Path(row["report_path"]).exists()
    # the exported source file holds the db's bars in dump_bars shape
    src = json.loads((tmp_path / "runs" / str(rid) / "bars.json").read_text())
    assert src["timeframe"] == "M5" and len(src["candles"]) == 399


def test_execute_too_few_bars_fails_cleanly(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    monkeypatch.setattr(backtest_runner, "RUNS_DIR", tmp_path / "runs")
    rid = db.insert_backtest_run(json.dumps(_params()))
    backtest_runner._execute(db, rid, _params())
    row = db.get_backtest_run(rid)
    assert row["status"] == "failed" and "bars" in row["error"]


def test_start_run_serializes(tmp_path, monkeypatch):
    db = SignalDb(str(tmp_path / "t.db"))
    # hold the busy lock as if a run were in flight
    assert backtest_runner._busy.acquire(blocking=False)
    try:
        import pytest
        with pytest.raises(RuntimeError):
            backtest_runner.start_run(db, _params())
    finally:
        backtest_runner._busy.release()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_runner.py -v`
Expected: FAIL (`ModuleNotFoundError: app.backtest_runner`, missing db methods)

- [ ] **Step 3: Implement**

(a) `db.py` — schema next to the others:

```python
_BACKTEST_SCHEMA = """CREATE TABLE IF NOT EXISTS backtest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts INTEGER NOT NULL,
  params_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  error TEXT,
  stats_json TEXT,
  report_path TEXT
)"""
```

`self.conn.execute(_BACKTEST_SCHEMA)` in `__init__`, then methods:

```python
    def insert_backtest_run(self, params_json: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO backtest_runs (created_ts, params_json, status)"
            " VALUES (?,?, 'running')", (int(time.time()), params_json))
        self.conn.commit()
        return cur.lastrowid

    def finish_backtest_run(self, run_id: int, *, status: str, error=None,
                            stats_json=None, report_path=None) -> None:
        self.conn.execute(
            "UPDATE backtest_runs SET status=?, error=?, stats_json=?,"
            " report_path=? WHERE id=?",
            (status, error, stats_json, report_path, run_id))
        self.conn.commit()

    def get_backtest_run(self, run_id: int) -> dict | None:
        cur = self.conn.execute(
            "SELECT * FROM backtest_runs WHERE id=?", (run_id,))
        return self._row_to_dict(cur, cur.fetchone())

    def recent_backtest_runs(self, limit: int = 20) -> list:
        cur = self.conn.execute(
            "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
```

(b) `service/app/backtest_runner.py`:

```python
"""Drive scripts/backtest.py (UNCHANGED -- the golden pins guard it) as a
subprocess over candles exported from the persistent SQLite table.

Subprocess, not import: the engine configures itself through module globals
in its main() and is not safe to re-enter from a threaded service; isolation
also means an engine crash can never take the service down. One run at a
time (_busy): the engine is CPU-bound and the account of record is a single
run artifact anyway.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # repo root
ENGINE = ROOT / "scripts" / "backtest.py"
RUNS_DIR = ROOT / "service" / "data" / "backtests"
RUN_TIMEOUT_S = 1800

# ConfirmCloses per the EA's registrations (XauAssistant.mq5): M5 lane
# ConfirmCloses=2, M15 lane M15ConfirmCloses=3; every other HalfTrend
# parameter matches the engine's live defaults (amplitude 4, EMA 55,
# stop buffer 0.75). --tf M15 makes the engine resample its M5 source.
STRATEGIES = {
    "halftrend_ema_v1": {"label": "HalfTrend M5",
                         "flags": ["--tf", "M5", "--confirm", "2"]},
    "halftrend_m15_v1": {"label": "HalfTrend M15",
                         "flags": ["--tf", "M15", "--confirm", "3"]},
}

_busy = threading.Lock()


def build_cli(params: dict, source: Path, json_out: Path, web_out: Path) -> list:
    cmd = [sys.executable, str(ENGINE),
           "--source", str(source),
           "--balance", str(params["balance"]),
           "--risk", str(params["risk_pct"]),
           "--entry-mode", params["entry_mode"],
           "--exit-scheme", params["exit_scheme"],
           "--ema200-confirm", params["ema200_confirm"],
           "--json", str(json_out),
           "--web", str(web_out)]
    cmd += STRATEGIES[params["strategy"]]["flags"]
    # M15 bias is the M5 lane's HTF-agreement replay; the M15 lane has no
    # HTF module (EA: "the only confirmation is the ema 200").
    if params.get("m15_bias") == "on" and params["strategy"] == "halftrend_ema_v1":
        cmd += ["--bias-ema", "200", "--bias-tf", "M15", "--bias-mode", "target"]
    return cmd


def start_run(db, params: dict) -> int:
    """Insert the run row and launch the engine in a daemon thread.
    Raises RuntimeError when a run is already in flight (the API maps it
    to 409)."""
    if not _busy.acquire(blocking=False):
        raise RuntimeError("a backtest is already running")
    try:
        run_id = db.insert_backtest_run(json.dumps(params))
        thread = threading.Thread(target=_execute_locked,
                                  args=(db, run_id, params), daemon=True)
        thread.start()
    except BaseException:
        _busy.release()
        raise
    return run_id


def _execute_locked(db, run_id: int, params: dict) -> None:
    try:
        _execute(db, run_id, params)
    finally:
        _busy.release()


def _execute(db, run_id: int, params: dict) -> None:
    """Thread body. Every failure path lands in a 'failed' row -- the
    service itself must never see an exception from here."""
    try:
        run_dir = RUNS_DIR / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        rows = db.get_candles(params["symbol"], "M5",
                              start_ts=params["start_ts"],
                              end_ts=params["end_ts"])
        if len(rows) < 300:
            raise RuntimeError(
                f"only {len(rows)} M5 bars in that range -- need at least 300"
                " (run the backfill: see scripts/backfill_candles.py)")
        source = run_dir / "bars.json"
        source.write_text(json.dumps(
            {"symbol": params["symbol"], "timeframe": "M5", "candles": rows},
            separators=(",", ":")))
        json_out = run_dir / "result.json"
        web_out = run_dir / "report.html"
        proc = subprocess.run(build_cli(params, source, json_out, web_out),
                              cwd=str(ROOT), capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S)
        if proc.returncode != 0 or not json_out.exists():
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"engine exited {proc.returncode}: {tail}")
        stats = json.loads(json_out.read_text()).get("stats", {})
        db.finish_backtest_run(run_id, status="done",
                               stats_json=json.dumps(stats),
                               report_path=str(web_out))
    except Exception as exc:
        try:
            db.finish_backtest_run(run_id, status="failed",
                                   error=str(exc)[:500])
        except Exception:
            pass
```

Note `test_start_run_serializes` and the `_execute` tests call `_execute` directly (not `_execute_locked`) — no lock involvement.

(c) Add `data/backtests/` to `service/.gitignore` (or the repo `.gitignore`) — run artifacts are not tracked:

```bash
grep -q "data/backtests" .gitignore || echo "service/data/backtests/" >> .gitignore
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_runner.py -v`
Expected: 5 PASS

- [ ] **Step 5: Run golden pins to prove the engine is untouched**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_golden.py -v`
Expected: PASS, zero diffs

- [ ] **Step 6: Commit**

```bash
git add service/app/db.py service/app/backtest_runner.py service/tests/test_backtest_runner.py .gitignore
git commit -m "feat(backtest): run rows + subprocess runner driving the untouched CLI engine

izi.md: updated in the final task of this plan."
```

---

### Task 7: backtest API endpoints

**Files:**
- Modify: `service/app/main.py` (import + 6 routes; **declare `/ui/backtest/runs` and `/ui/backtest/range` BEFORE `/ui/backtest/{run_id}`**)
- Test: `service/tests/test_backtest_api.py` (new)

**Interfaces:**
- Consumes: Task 6's `backtest_runner.start_run/STRATEGIES`, db run-row methods, Task 1's `candles_range/latest_candle_series`.
- Produces:
  - `GET /ui/backtest` → `static/backtest.html` (page lands in Task 10; FileResponse of a not-yet-existing file is fine to wire now — the route 404s until the file exists, and the API tests below don't touch it).
  - `GET /ui/backtest/range` → `{"symbol", "range": {start,end,count} | null, "strategies": [{"id","label","supported"}...]}` (both HalfTrend lanes supported:true; `boll_stochrsi` supported:false).
  - `POST /ui/backtest` body `{strategy, start: "YYYY-MM-DD", end: "YYYY-MM-DD", balance?, risk_pct?, entry_mode?, exit_scheme?, ema200_confirm?, m15_bias?}` → `{"run_id"}`; 400 on bad params/unsupported strategy/range outside data; 409 when busy.
  - `GET /ui/backtest/runs` → `{"runs": [...]}` with `params`/`stats` JSON-decoded per row.
  - `GET /ui/backtest/{run_id}` → the row (decoded) or 404.
  - `GET /ui/backtest/{run_id}/report` → the report HTML or 404.

- [ ] **Step 1: Write the failing tests**

```python
# service/tests/test_backtest_api.py
# Reuse the TestClient fixture pattern from tests/test_api.py.
import json


def seed_candles(client, n=500):
    client.app.state.db.upsert_candles("XAUUSD", "M5", [
        {"t": 1700000000 + 300 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}
        for i in range(n)])


def test_range_lists_strategies(client):
    seed_candles(client)
    r = client.get("/ui/backtest/range").json()
    assert r["symbol"] == "XAUUSD"
    assert r["range"]["count"] == 500
    ids = {s["id"]: s["supported"] for s in r["strategies"]}
    assert ids == {"halftrend_ema_v1": True, "halftrend_m15_v1": True,
                   "boll_stochrsi": False}


def test_start_validates(client, monkeypatch):
    seed_candles(client)
    bad = [
        ({"strategy": "boll_stochrsi", "start": "2023-11-14", "end": "2023-11-16"},
         "not yet supported"),
        ({"strategy": "halftrend_ema_v1", "start": "2030-01-01", "end": "2030-02-01"},
         "no candles"),
        ({"strategy": "halftrend_ema_v1", "start": "2023-11-16", "end": "2023-11-14"},
         "start must be before end"),
        ({"strategy": "halftrend_ema_v1", "start": "2023-11-14",
          "end": "2023-11-16", "balance": -5}, "balance"),
        ({"strategy": "nope", "start": "2023-11-14", "end": "2023-11-16"},
         "unknown strategy"),
    ]
    for body, frag in bad:
        r = client.post("/ui/backtest", json=body)
        assert r.status_code == 400, body
        assert frag in r.json()["detail"]


def test_start_and_status_roundtrip(client, monkeypatch):
    seed_candles(client)
    captured = {}

    def fake_start(db, params):
        captured.update(params)
        return db.insert_backtest_run(json.dumps(params))

    from app import backtest_runner
    monkeypatch.setattr(backtest_runner, "start_run", fake_start)
    r = client.post("/ui/backtest", json={
        "strategy": "halftrend_m15_v1", "start": "2023-11-14",
        "end": "2023-11-16", "balance": 5000, "risk_pct": 2.0})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert captured["strategy"] == "halftrend_m15_v1"
    assert captured["entry_mode"] == "adr"            # default applied
    assert captured["start_ts"] < captured["end_ts"]
    row = client.get(f"/ui/backtest/{rid}").json()
    assert row["status"] == "running"
    assert row["params"]["balance"] == 5000
    runs = client.get("/ui/backtest/runs").json()["runs"]
    assert runs[0]["id"] == rid


def test_busy_returns_409(client, monkeypatch):
    seed_candles(client)
    from app import backtest_runner

    def busy(db, params):
        raise RuntimeError("a backtest is already running")

    monkeypatch.setattr(backtest_runner, "start_run", busy)
    r = client.post("/ui/backtest", json={
        "strategy": "halftrend_ema_v1", "start": "2023-11-14", "end": "2023-11-16"})
    assert r.status_code == 409


def test_report_404s(client):
    assert client.get("/ui/backtest/12345").status_code == 404
    assert client.get("/ui/backtest/12345/report").status_code == 404
```

(The seeded range 1700000000.. covers 2023-11-14..16 UTC — dates in tests must overlap it.)

- [ ] **Step 2: Run to verify failure**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_api.py -v`
Expected: FAIL with 404s

- [ ] **Step 3: Implement in `main.py`**

Top of file: `import datetime as dt` and `from app import backtest_runner`.

Add (order matters — page, range, runs, then `{run_id}` routes last):

```python
def _day_ts(s: str, end: bool = False) -> int:
    """Date-picker day -> epoch seconds, midnight (or end-of-day) UTC --
    the same convention the CLI's --start/--end use. Candle bar_time is
    broker server time; a day boundary being ~3 h off is acceptable for a
    date-range filter and matches every existing replay."""
    d = dt.date.fromisoformat(s)
    t = dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time.min,
                            tzinfo=dt.timezone.utc)
    return int(t.timestamp())


@app.get("/ui/backtest")
def ui_backtest_page():
    return FileResponse(Path(__file__).parent / "static" / "backtest.html",
                        media_type="text/html")


@app.get("/ui/backtest/range")
def ui_backtest_range():
    series = app.state.db.latest_candle_series()
    symbol = series[0] if series else "XAUUSD"
    strategies = [{"id": sid, "label": s["label"], "supported": True}
                  for sid, s in backtest_runner.STRATEGIES.items()]
    strategies.append({"id": "boll_stochrsi", "label": "BollStochRsi",
                       "supported": False})
    return {"symbol": symbol,
            "range": app.state.db.candles_range(symbol, "M5"),
            "strategies": strategies}


@app.get("/ui/backtest/runs")
def ui_backtest_runs(limit: int = 20):
    runs = []
    for row in app.state.db.recent_backtest_runs(limit):
        row = dict(row)
        row["params"] = json.loads(row.pop("params_json") or "{}")
        row["stats"] = json.loads(row.pop("stats_json") or "null")
        runs.append(row)
    return {"runs": runs}


@app.post("/ui/backtest")
def ui_backtest_start(body: dict):
    strategy = str(body.get("strategy", "")).strip()
    if strategy == "boll_stochrsi":
        raise HTTPException(status_code=400,
                            detail="boll_stochrsi is not yet supported by the replay engine")
    if strategy not in backtest_runner.STRATEGIES:
        raise HTTPException(status_code=400, detail="unknown strategy")
    try:
        start_ts = _day_ts(str(body.get("start", "")))
        end_ts = _day_ts(str(body.get("end", "")), end=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD")
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start must be before end")
    try:
        balance = float(body.get("balance", 10000))
        risk_pct = float(body.get("risk_pct", 1.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="balance/risk_pct must be numbers")
    if balance <= 0:
        raise HTTPException(status_code=400, detail="balance must be > 0")
    if not 0 < risk_pct <= 10:
        raise HTTPException(status_code=400, detail="risk_pct must be in (0, 10]")
    entry_mode = str(body.get("entry_mode", "adr"))
    exit_scheme = str(body.get("exit_scheme", "target-exit"))
    ema200_confirm = str(body.get("ema200_confirm", "off"))
    m15_bias = str(body.get("m15_bias", "off"))
    if entry_mode not in ("adr", "fixed"):
        raise HTTPException(status_code=400, detail="entry_mode must be adr|fixed")
    if exit_scheme not in ("target-exit", "floor-a", "floor-b", "floor-a-adds"):
        raise HTTPException(status_code=400, detail="bad exit_scheme")
    if ema200_confirm not in ("off", "on") or m15_bias not in ("off", "on"):
        raise HTTPException(status_code=400,
                            detail="ema200_confirm/m15_bias must be off|on")
    series = app.state.db.latest_candle_series()
    symbol = series[0] if series else "XAUUSD"
    rng = app.state.db.candles_range(symbol, "M5")
    if rng is None or end_ts < rng["start"] or start_ts > rng["end"]:
        avail = (f"{dt.datetime.utcfromtimestamp(rng['start']):%Y-%m-%d} .. "
                 f"{dt.datetime.utcfromtimestamp(rng['end']):%Y-%m-%d}"
                 if rng else "none -- run the backfill first")
        raise HTTPException(status_code=400,
                            detail=f"no candles in that range (available: {avail})")
    params = {"strategy": strategy, "symbol": symbol,
              "start_ts": start_ts, "end_ts": end_ts,
              "balance": balance, "risk_pct": risk_pct,
              "entry_mode": entry_mode, "exit_scheme": exit_scheme,
              "ema200_confirm": ema200_confirm, "m15_bias": m15_bias}
    try:
        run_id = backtest_runner.start_run(app.state.db, params)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run_id}


@app.get("/ui/backtest/{run_id}")
def ui_backtest_status(run_id: int):
    row = app.state.db.get_backtest_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such run")
    row = dict(row)
    row["params"] = json.loads(row.pop("params_json") or "{}")
    row["stats"] = json.loads(row.pop("stats_json") or "null")
    return row


@app.get("/ui/backtest/{run_id}/report")
def ui_backtest_report(run_id: int):
    row = app.state.db.get_backtest_run(run_id)
    if not row or not row["report_path"] or not Path(row["report_path"]).exists():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(row["report_path"], media_type="text/html")
```

Also add `import json` to main.py's imports if not present.

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/app/main.py service/tests/test_backtest_api.py
git commit -m "feat(api): /ui/backtest run lifecycle endpoints (range, start, status, report)

izi.md: updated in the final task of this plan."
```

---

### Task 8: dashboard chart → Lightweight Charts with trade markers

**Files:**
- Modify: `service/app/static/dashboard.html` (head; chart panel markup ~line 458-470; the whole canvas-chart JS block ~lines 590-848 is REMOVED and replaced; `trades()` gains a 📍 zoom control; the boot lines ~945-947 change)

**Interfaces:**
- Consumes: `/ui/candles`, `/ui/overlays?strategy=`, `/ui/trades` (unchanged shapes); `/static/vendor/lightweight-charts.standalone.production.js` (Task 2's mount).
- Produces: global JS `priceChart()` (fetch + redraw), `setTab(id)`, `toggleExpand()`, `zoomToTrade(ts)` — used by Task 9's dynamic tabs and by the trade table.

- [ ] **Step 1: Swap the panel markup**

Replace the `canvas-wrap` div (keep the surrounding panel, tabs and expand button):

```html
    <div class="canvas-wrap" id="chartWrap">
      <div id="chartEl" style="width:100%;height:100%"></div>
    </div>
```

In `<head>`, after the fonts link:

```html
<script src="/static/vendor/lightweight-charts.standalone.production.js"></script>
```

- [ ] **Step 2: Delete the canvas engine, add the LWC engine**

Delete: `_pcTrades/_tip/_tipLastIdx/_panBars/_pcData/_pcDrag/_pcLastMoved/_liveBtn` declarations, `_clampPan`, `tipEl`, the old `priceChart`, `_drawChart`, `_onPcDragMove`, `_onPcDragEnd`, and the `window.addEventListener('resize', ...)` boot line (autoSize replaces it). KEEP `setTab`/`toggleExpand` (bodies below) and `openLightbox`/`closeLightbox`.

Add:

```js
let chart=null,candleSeries=null,S={};        // S: overlay line series by key
let _activeTab='halftrend_ema_v1',_expanded=false,_pcData=null;

function initChart(){
 chart=LightweightCharts.createChart($('chartEl'),{
  layout:{background:{type:'solid',color:'rgba(9,13,20,0)'},textColor:'#94a3b8',
          fontFamily:'"Plus Jakarta Sans", system-ui, sans-serif'},
  grid:{vertLines:{color:'rgba(255,255,255,0.05)'},horzLines:{color:'rgba(255,255,255,0.05)'}},
  rightPriceScale:{borderColor:'rgba(255,255,255,0.08)'},
  timeScale:{borderColor:'rgba(255,255,255,0.08)',timeVisible:true,secondsVisible:false},
  crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
  autoSize:true});
 // overlays first: later-added series paint on top, so candles stay above
 const noLbl={priceLineVisible:false,lastValueVisible:false};
 S.ema9=chart.addLineSeries(Object.assign({color:'#666',lineWidth:1},noLbl));
 S.ema21=chart.addLineSeries(Object.assign({color:'#666',lineWidth:1},noLbl));
 S.ema55=chart.addLineSeries(Object.assign({color:'#f5c542',lineWidth:2},noLbl));
 S.ema200=chart.addLineSeries(Object.assign({color:'mediumpurple',lineWidth:2},noLbl));
 S.ht=chart.addLineSeries(Object.assign({color:'dodgerblue',lineWidth:2},noLbl));
 S.bb_upper=chart.addLineSeries(Object.assign({color:'#7f8c9a',lineWidth:1},noLbl));
 S.bb_mid=chart.addLineSeries(Object.assign({color:'#7f8c9a',lineWidth:1,
   lineStyle:LightweightCharts.LineStyle.Dashed},noLbl));
 S.bb_lower=chart.addLineSeries(Object.assign({color:'#7f8c9a',lineWidth:1},noLbl));
 candleSeries=chart.addCandlestickSeries({upColor:'#2ecc71',downColor:'#e74c3c',
  borderUpColor:'#2ecc71',borderDownColor:'#e74c3c',
  wickUpColor:'#2ecc71',wickDownColor:'#e74c3c'});
}

function setLine(s,arr){
 if(!arr||!_pcData){s.setData([]);return;}
 s.setData(_pcData.candles.map((c,i)=>arr[i]==null?null:{time:c.t,value:arr[i]})
   .filter(Boolean));
}
function setHt(arr){
 if(!arr||!_pcData){S.ht.setData([]);return;}
 S.ht.setData(_pcData.candles.map((c,i)=>{
  const e=arr[i]; if(e==null) return null;
  return {time:c.t,value:e[0],color:e[1]===0?'dodgerblue':'orangered'};
 }).filter(Boolean));
}
function barSec(){const c=_pcData&&_pcData.candles;return c&&c.length>1?c[1].t-c[0].t:300;}
function barTime(candles,ts){
 // trade ts is service wall-clock; candle t is broker server time -- same
 // snap rule the old canvas chart used: first bar at/after ts, else last.
 const hit=candles.find(c=>c.t>=ts);
 return (hit||candles[candles.length-1]).t;
}
function buildMarkers(candles,tr){
 if(!candles.length) return [];
 const m=[];
 for(const t of tr){
  if(t.event==='open'||t.event==='add'){
   m.push({time:barTime(candles,t.ts),
    position:t.direction==='BUY'?'belowBar':'aboveBar',
    color:t.direction==='BUY'?'#2ecc71':'#e74c3c',
    shape:t.direction==='BUY'?'arrowUp':'arrowDown',
    text:(t.event==='add'?'add ':'')+fmt(t.lots,2)});
  }else if(t.event==='close'){
   m.push({time:barTime(candles,t.ts),position:'aboveBar',
    color:t.profit>=0?'#2ecc71':'#e74c3c',shape:'circle',
    text:(t.profit>=0?'+':'')+fmt(t.profit)});
  }
 }
 return m.sort((a,b)=>a.time-b.time);
}

async function priceChart(){
 if(!window.LightweightCharts){
  $('chartWrap').innerHTML='<div style="padding:20px;color:var(--text-muted)">chart library failed to load — the rest of the dashboard still works</div>';
  return;
 }
 if(!chart) initChart();
 const cd=await j('/ui/candles');
 let ov={},tr=[];
 try{ ov=await j('/ui/overlays?strategy='+encodeURIComponent(_activeTab)); }catch(e){}
 try{ tr=(await j('/ui/trades?limit=100')).trades||[]; }catch(e){}
 _pcData={candles:cd.candles,ov,tr};
 candleSeries.setData(cd.candles.map(c=>({time:c.t,open:c.o,high:c.h,low:c.l,close:c.c})));
 setLine(S.ema9,ov.ema9); setLine(S.ema21,ov.ema21);
 setLine(S.ema55,ov.ema55); setLine(S.ema200,ov.ema200);
 setHt(ov.halftrend);
 setLine(S.bb_upper,ov.bb_upper); setLine(S.bb_mid,ov.bb_mid);
 setLine(S.bb_lower,ov.bb_lower);
 candleSeries.setMarkers(buildMarkers(cd.candles,tr));
}

function setTab(t){
 _activeTab=t;
 document.querySelectorAll('#stratTabs .tab-btn').forEach(b=>
   b.classList.toggle('active', b.dataset.tab===t));
 priceChart();
}
function toggleExpand(){
 _expanded=!_expanded;
 $('pricePanel').classList.toggle('expanded', _expanded);   // autoSize refits
}
function zoomToTrade(ts){
 if(!chart||!_pcData||!_pcData.candles.length) return;
 const t=barTime(_pcData.candles,ts), span=90*barSec();
 chart.timeScale().setVisibleRange({from:t-span,to:t+span});
 $('pricePanel').scrollIntoView({behavior:'smooth',block:'start'});
}
```

- [ ] **Step 3: Wire the trade table to the chart**

In `trades()`, add a first cell to each row (before the time cell):

```js
`<td><button class="btn-sm" onclick="zoomToTrade(${Number(t.ts)})" title="show on chart">📍</button></td>`
```

and add the matching empty `<th></th>` at the front of the header row.

- [ ] **Step 4: Verify by launch**

Run: `cd /mnt/c/Users/aatanda/Desktop/xau/service && timeout 20 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9100 & sleep 6 && curl -s http://127.0.0.1:9100/ui | grep -c "chartEl" && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9100/static/vendor/lightweight-charts.standalone.production.js`
Expected: `1` and `200` (if `/ui` 307-redirects to onboarding on a fresh profile-less test db, POST `{}` to `/ui/profile` first or check `/ui` with `-L`)

Manual (flag for the user, cannot be automated here): open `http://127.0.0.1:9000/ui` after restart — candles render in the TradingView look, EMA/HalfTrend overlays on the halftrend tab, BB on the boll tab, arrow markers where trades happened, 📍 in the trade table zooms the chart.

- [ ] **Step 5: Commit**

```bash
git add service/app/static/dashboard.html
git commit -m "feat(dashboard): TradingView Lightweight Charts price panel with trade markers and row-click zoom

izi.md: updated in the final task of this plan."
```

---

### Task 9: dashboard catch-up — nav, rule toggles, dynamic tabs, agree columns, TF column, shadow filter

**Files:**
- Modify: `service/app/static/dashboard.html`

**Interfaces:**
- Consumes: Task 4's `/ui/state.rules` + `POST /ui/rules` + trade agree fields; Task 5's M15 overlay (via dynamic tabs); Task 8's `setTab/zoomToTrade`.
- Produces: nav markup pattern reused by Tasks 10/11: a `<nav class="nav-links">` inside `.header` with links `/ui`, `/ui/backtest`, `/ui/onboarding`, the current page's link carrying class `active`.

- [ ] **Step 1: Nav bar**

CSS (append to the style block):

```css
.nav-links { display: flex; gap: 6px; margin-left: 28px; }
.nav-links a {
  color: var(--text-muted); text-decoration: none; font-size: 13px;
  font-weight: 600; padding: 6px 14px; border-radius: 8px;
  border: 1px solid transparent; transition: all 0.2s ease;
}
.nav-links a:hover { color: var(--text-main); background: rgba(255,255,255,0.05); }
.nav-links a.active {
  color: var(--gold-primary); background: rgba(245,197,66,0.1);
  border-color: rgba(245,197,66,0.3);
}
```

Markup — inside `<header class="header">`, right after the `.brand` div:

```html
    <nav class="nav-links">
      <a href="/ui" class="active">Dashboard</a>
      <a href="/ui/backtest">Backtest</a>
      <a href="/ui/onboarding">Settings</a>
    </nav>
```

- [ ] **Step 2: Rule toggles in the control bar**

After the `closeAll` button in the controls flex row:

```html
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="stat-label" style="margin-right:4px;">Entry</span>
        <button id="rAdr" class="btn-sm" onclick="setRule('entry_mode','adr')">ADR</button>
        <button id="rFixed" class="btn-sm" onclick="setRule('entry_mode','fixed')">FIXED</button>
      </div>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="stat-label" style="margin-right:4px;">M15 gate</span>
        <select id="rHtf" onchange="setRule('htf_enforce',this.value)"
                style="background:rgba(9,13,20,0.7);color:var(--text-main);border:1px solid var(--border-card);border-radius:6px;padding:4px 8px;font-size:12px;">
          <option>off</option><option>M15</option><option>M30</option><option>H1</option>
        </select>
      </div>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="stat-label" style="margin-right:4px;">EMA200 gate</span>
        <button id="rE200" class="btn-sm" onclick="toggleE200()">off</button>
      </div>
```

JS:

```js
let _rules=null;
async function setRule(key,value){
 const r=await fetch('/ui/rules',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value})});
 if(!r.ok) alert('Rule change failed ('+r.status+')');
 state();
}
function toggleE200(){
 setRule('ema200_enforce', _rules&&_rules.ema200_enforce==='on'?'off':'on');
}
```

In `state()`, after the mode buttons are painted (reuse its `onCss` const):

```js
 _rules=s.rules||null;
 if(_rules){
  $('rAdr').style.cssText=_rules.entry_mode==='adr'?onCss:'';
  $('rFixed').style.cssText=_rules.entry_mode==='fixed'?onCss:'';
  if(document.activeElement!==$('rHtf')) $('rHtf').value=_rules.htf_enforce;
  $('rE200').textContent=_rules.ema200_enforce==='on'?'ON':'off';
  $('rE200').style.cssText=_rules.ema200_enforce==='on'?onCss:'';
 }
```

- [ ] **Step 3: Dynamic strategy tabs**

Replace the hardcoded two buttons in `#stratTabs` with an empty container, and rebuild in `stats()`:

```js
 // tabs follow whatever strategies have actually signalled (per-tf keys
 // collapse to bare ids -- overlays are keyed by bare strategy_id)
 const ids=[...new Set(rows.map(([k])=>k.split(' @')[0]))]
   .filter(id=>id!=='pre-framework'&&id!=='stub');
 if(ids.length&&!ids.includes(_activeTab)) _activeTab=ids[0];
 $('stratTabs').innerHTML=ids.map(id=>
  `<button type="button" class="tab-btn${id===_activeTab?' active':''}" data-tab="${esc(id)}">${esc(id)}</button>`).join('');
```

Delegated listener (once, next to the `#cmp` one):

```js
$('stratTabs').addEventListener('click', e=>{
 const b=e.target.closest('[data-tab]');
 if(b) setTab(b.dataset.tab);
});
```

- [ ] **Step 4: Comparison table — Strategy and TF columns**

In `stats()`, change the header to `<th>strategy</th><th>tf</th><th>signals</th>...` and the row builder to:

```js
    const [sid, tf] = key.split(' @');
    return `<tr><td><b>${esc(sid)}</b></td><td>${esc(tf||'—')}</td><td>${v.signals}</td>...`
```

(keep the existing hit%/avg/switch-button cells; the switch button already uses `bareId` — now `sid`).

- [ ] **Step 5: Trade table — agree columns**

Helper + columns in `trades()`:

```js
const agree=v=>v===1?'<span class="badge badge-pos">✓</span>'
  :(v===0?'<span class="badge badge-neg">✗</span>':'—');
```

Header gains `<th>M15</th><th>E200</th>` (after `reason`); each row gains
`<td>${agree(t.htf_agree)}</td><td>${agree(t.ema200_agree)}</td>`.

- [ ] **Step 6: Signal log shadow filter**

In the Signal Log section header:

```html
      <select id="sigFilter" onchange="signals()"
              style="background:rgba(9,13,20,0.7);color:var(--text-main);border:1px solid var(--border-card);border-radius:6px;padding:4px 8px;font-size:12px;">
        <option value="all">all</option>
        <option value="active">active only</option>
        <option value="shadow">shadows only</option>
      </select>
```

In `signals()` before mapping:

```js
 const flt=$('sigFilter').value;
 const list=signals.filter(s=>flt==='all'||(flt==='active'?s.is_active:!s.is_active));
```

(map over `list` instead of `signals`).

- [ ] **Step 7: Verify + commit**

Run the same launch check as Task 8 Step 4 (grep for `nav-links`, `rHtf`, `sigFilter` in the served page). Full pytest still green: `cd service && .venv/bin/python -m pytest`.

```bash
git add service/app/static/dashboard.html
git commit -m "feat(dashboard): nav bar, rule toggles, dynamic strategy tabs, agree flags, TF column, shadow filter

izi.md: updated in the final task of this plan."
```

---

### Task 10: `backtest.html` page

**Files:**
- Create: `service/app/static/backtest.html`

**Interfaces:**
- Consumes: Task 7's endpoints; Task 9's nav pattern (Backtest link carries `active`).

- [ ] **Step 1: Create the page**

Reuse dashboard.html's `<head>` (fonts + the same `:root`/`body`/`.wrap`/`.header`/`.card-section`/table/button/`.nav-links` CSS — copy the style block; three static pages with duplicated CSS is the repo's accepted pattern, no build step). Body:

```html
<div class="wrap">
  <header class="header">
    <div class="brand">
      <div class="brand-logo">🥇</div>
      <div>
        <h1 class="brand-title">Backtest</h1>
        <div class="brand-subtitle">iziGold Replay Lab</div>
      </div>
    </div>
    <nav class="nav-links">
      <a href="/ui">Dashboard</a>
      <a href="/ui/backtest" class="active">Backtest</a>
      <a href="/ui/onboarding">Settings</a>
    </nav>
  </header>

  <div class="card-section">
    <div class="section-header"><h2>New run</h2>
      <span id="range" class="stat-label">loading data range…</span></div>
    <form id="f" novalidate>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;">
        <div><label class="stat-label">Strategy</label><select name="strategy" id="strat"></select></div>
        <div><label class="stat-label">From</label><input type="date" name="start" required></div>
        <div><label class="stat-label">To</label><input type="date" name="end" required></div>
        <div><label class="stat-label">Starting equity $</label><input type="number" name="balance" value="10000" min="1" step="100"></div>
        <div><label class="stat-label">Risk % / trade</label><input type="number" name="risk_pct" value="1.0" min="0.1" max="10" step="0.1"></div>
        <div><label class="stat-label">Entry mode</label>
          <select name="entry_mode"><option value="adr">adr (live)</option><option value="fixed">fixed lots</option></select></div>
        <div><label class="stat-label">Exit scheme</label>
          <select name="exit_scheme"><option>target-exit</option><option>floor-a</option><option>floor-b</option><option>floor-a-adds</option></select></div>
        <div><label class="stat-label">EMA-200 confirm</label>
          <select name="ema200_confirm"><option>off</option><option>on</option></select></div>
        <div><label class="stat-label">M15 bias (M5 lane)</label>
          <select name="m15_bias"><option>off</option><option>on</option></select></div>
      </div>
      <div style="margin-top:16px;display:flex;gap:12px;align-items:center;">
        <button type="submit" class="btn-sm" id="runBtn">▶ Run backtest</button>
        <span id="status" class="stat-label"></span>
      </div>
    </form>
  </div>

  <div class="card-section">
    <div class="section-header"><h2>Runs</h2></div>
    <div class="table-wrap"><table id="runs"></table></div>
  </div>
</div>
```

Inputs/selects reuse onboarding's `input, select` CSS rules — include those too.

Script:

```js
const $=id=>document.getElementById(id);
const fmt=(v,d=2)=>v==null?'—':Number(v).toFixed(d);
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function j(u){const r=await fetch(u);return r.json()}
const day=ts=>new Date(ts*1000).toISOString().slice(0,10);

let _watch=null;
async function loadRange(){
 const r=await j('/ui/backtest/range');
 $('strat').innerHTML=r.strategies.map(s=>
  `<option value="${esc(s.id)}" ${s.supported?'':'disabled'}>${esc(s.label)}${s.supported?'':' — not yet supported'}</option>`).join('');
 const f=document.getElementById('f');
 if(r.range){
  $('range').textContent=`data: ${day(r.range.start)} → ${day(r.range.end)} (${r.range.count.toLocaleString()} M5 bars)`;
  f.start.value=day(Math.max(r.range.start,r.range.end-30*86400));
  f.end.value=day(r.range.end);
  f.start.min=f.end.min=day(r.range.start);
  f.start.max=f.end.max=day(r.range.end);
 } else {
  $('range').textContent='no candles stored yet — run scripts/backfill_candles.py first';
  $('runBtn').disabled=true;
 }
}

document.getElementById('f').onsubmit=async e=>{
 e.preventDefault();
 const f=e.target, body={};
 for(const el of f.elements) if(el.name&&el.value!=='')
  body[el.name]=el.type==='number'?Number(el.value):el.value;
 $('status').textContent='starting…';
 const r=await fetch('/ui/backtest',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json().catch(()=>({}));
 if(!r.ok){$('status').textContent='✗ '+(d.detail||r.status);return;}
 watch(d.run_id, Date.now());
 runs();
};

function watch(rid,t0){
 clearInterval(_watch);
 _watch=setInterval(async()=>{
  const row=await j('/ui/backtest/'+rid);
  const secs=Math.round((Date.now()-t0)/1000);
  if(row.status==='running'){$('status').textContent=`⏳ running… ${secs}s`;return;}
  clearInterval(_watch);
  $('status').innerHTML=row.status==='done'
   ?`✅ done in ${secs}s — <a href="/ui/backtest/${rid}/report" target="_blank" style="color:var(--gold-primary)">open report</a>`
   :`✗ failed: ${esc(row.error||'unknown')}`;
  runs();
 },2000);
}

async function runs(){
 const {runs}=await j('/ui/backtest/runs');
 $('runs').innerHTML='<thead><tr><th>#</th><th>when</th><th>strategy</th><th>range</th>'+
  '<th>equity</th><th>net</th><th>trades</th><th>win %</th><th>max DD</th><th>status</th><th></th></tr></thead><tbody>'+
  runs.map(r=>{
   const p=r.params||{}, s=r.stats||{};
   const net=s.net==null?'—':`<span class="${s.net>=0?'pos':'neg'}">${s.net>=0?'+':''}${fmt(s.net)}</span>`;
   const badge=r.status==='done'?'badge-pos':(r.status==='failed'?'badge-neg':'badge-gold');
   const rep=r.status==='done'?`<a href="/ui/backtest/${r.id}/report" target="_blank" style="color:var(--gold-primary)">report</a>`:'';
   return `<tr><td>${r.id}</td><td>${new Date(r.created_ts*1000).toLocaleString()}</td>`+
    `<td><b>${esc(p.strategy||'—')}</b></td><td>${p.start_ts?day(p.start_ts)+' → '+day(p.end_ts):'—'}</td>`+
    `<td>$${fmt(p.balance,0)} @ ${fmt(p.risk_pct,1)}%</td><td>${net}</td>`+
    `<td>${s.trades==null?'—':s.trades}</td><td>${s.win_rate==null?'—':fmt(s.win_rate,1)+'%'}</td>`+
    `<td>${s.max_dd==null?'—':fmt(s.max_dd)}</td>`+
    `<td><span class="badge ${badge}">${esc(r.status)}${r.error?': '+esc(r.error.slice(0,60)):''}</span></td>`+
    `<td>${rep}</td></tr>`;
  }).join('')+'</tbody>';
}

loadRange();runs();setInterval(runs,15000);
```

- [ ] **Step 2: Verify by launch**

Run: start the service on a scratch port as in Task 8 Step 4, then
`curl -s http://127.0.0.1:9100/ui/backtest | grep -c "Replay Lab"` → `1`;
`curl -s http://127.0.0.1:9100/ui/backtest/range | python3 -m json.tool` → strategies listed.

End-to-end (only if `bars_max.json` or real candles are already loaded): submit a short range from the browser and confirm the run reaches `done` and the report opens — the report page itself is the frozen `backtest_report.html` product, already smoke-tested.

- [ ] **Step 3: Commit**

```bash
git add service/app/static/backtest.html
git commit -m "feat(ui): backtest page — filters, run lifecycle, runs table, report links

izi.md: updated in the final task of this plan."
```

---

### Task 11: onboarding → Settings (nav + restyle)

**Files:**
- Modify: `service/app/static/onboarding.html`

**Interfaces:** Consumes Task 9's nav pattern. No endpoint changes — `GET/POST /ui/profile` contract untouched.

- [ ] **Step 1: Add the nav + back path**

The page is a centered single card (`body{display:flex;align-items:center}`); keep that shell. Inside `.wrap`, ABOVE the existing `.header` div, add:

```html
  <nav class="nav-links" style="justify-content:center;margin-bottom:18px;">
    <a href="/ui">Dashboard</a>
    <a href="/ui/backtest">Backtest</a>
    <a href="/ui/onboarding" class="active">Settings</a>
  </nav>
```

and copy the `.nav-links` CSS rules from Task 9 into the style block.

Change the header copy to read as both first-run and settings: `<h1>iziGold setup</h1>` and the note to:

```html
    <p class="note">Everything below is optional and editable any time from
    the <strong>Settings</strong> menu. Save what you have, skip the rest —
    nothing here blocks or alters trading.</p>
```

- [ ] **Step 2: Verify + commit**

`curl -s http://127.0.0.1:9100/ui/onboarding | grep -c nav-links` → `1` (plus the class definition = grep may return 2; assert ≥1).

```bash
git add service/app/static/onboarding.html
git commit -m "feat(ui): onboarding doubles as Settings — nav bar and back path

izi.md: updated in the final task of this plan."
```

---

### Task 12: izi.md, setup.sh hint, spec status, final verification

**Files:**
- Modify: `.claude/agents/izi.md`, `scripts/setup.sh`, `docs/superpowers/specs/2026-08-24-ui-backtest-revamp-design.md`

- [ ] **Step 1: izi.md** — update the dashboard/endpoints/ops sections:
  - New endpoints table rows: `POST /ui/rules`, `GET/POST /ui/backtest`, `GET /ui/backtest/range|runs|{id}|{id}/report`, `/static` mount.
  - `candles` table (schema, fed by `/analyze`, seeds the chart on restart) + `backtest_runs` table.
  - Backfill runbook (the two-step Windows-pull → WSL-load from Task 3's docstring).
  - Dashboard: Lightweight Charts panel, trade markers/zoom, rule toggles mirroring `/agree` (last-writer-wins with Telegram), dynamic tabs incl. `halftrend_m15_v1`, shadow filter; onboarding reachable as Settings.
  - Backtest page: strategy↔flag mapping (M5=`--confirm 2`, M15=`--tf M15 --confirm 3`), one-run-at-a-time, artifacts under `service/data/backtests/{id}/`, and the engine caveats note (daily brake + news blackout not modeled — quote the engine header).
- [ ] **Step 2: setup.sh hint** — in the summary/verification phase, add a non-fatal notice:

```bash
if ! sqlite3 "$SERVICE_DIR/xau_assistant.db" \
     "SELECT COUNT(*) FROM candles" 2>/dev/null | grep -qv '^0$'; then
  echo "NOTE: candles table is empty — backtests need history."
  echo "      Backfill: python.exe scripts/dump_bars.py 75000 bars_max.json (Windows),"
  echo "      then: cd service && python3 ../scripts/backfill_candles.py ../bars_max.json"
fi
```

(match setup.sh's existing echo/log style; if `sqlite3` CLI is absent, guard with `command -v sqlite3 >/dev/null &&`).
- [ ] **Step 3: Spec status** — set the spec's `**Status:**` line to `Implemented (plan docs/superpowers/plans/2026-08-24-ui-backtest-revamp.md)` and record the seven deviations by reference to this plan.
- [ ] **Step 4: Full verification**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest
cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python -m pytest tests/test_backtest_golden.py -v
node tests/backtest_report_smoke.js   # from service/tests -- run as the suite already does
```

Expected: everything green; golden pins byte-identical.
- [ ] **Step 5: Commit**

```bash
git add .claude/agents/izi.md scripts/setup.sh docs/superpowers/specs/2026-08-24-ui-backtest-revamp-design.md
git commit -m "docs(izi): UI revamp + backtest page — endpoints, candles table, backfill runbook, ops notes"
```

---

## Post-plan checklist (for the human)

1. Restart the real service (launcher or the runbook restart pattern) — the dashboard chart now needs `/static` and the new endpoints.
2. Run the backfill once (Task 3 docstring) so the backtest page has 12 months of data.
3. Hard-refresh the browser (`Ctrl+Shift+R`) — the dashboard HTML changed shape.
4. No MetaEditor compile needed — zero MQL5 changes.
