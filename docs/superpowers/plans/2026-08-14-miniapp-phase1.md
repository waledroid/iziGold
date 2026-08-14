# Mini App Phase 1 — MT5 Bridge + Feed Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Windows-side MT5 feed bridge and a separate read-only mini-app FastAPI service (port 9001) with in-memory candle/tick/position state, a keyed push endpoint, a history API, and a WebSocket that pushes deltas — the data spine the Phase 2 chart page will sit on.

**Architecture:** `bridge/mt5_feed.py` (Windows Python + MetaTrader5 pkg) polls the running terminal and POSTs batches to `app/miniapp.py` (own FastAPI app) which keeps ring buffers and fans deltas out over `/ws`. Nothing here is publicly exposed yet (Phase 3 does tunnel+auth); a dev bypass guards the read endpoints meanwhile.

**Tech Stack:** Python/FastAPI/uvicorn (second process, port 9001), MetaTrader5 package on Windows Python, pytest with FastAPI TestClient WebSocket support.

**Spec:** `docs/superpowers/specs/2026-08-14-live-chart-miniapp-design.md` (Phase 1 scope)

## Global Constraints

- Port 9001, host 127.0.0.1 only. The main service (9000) is untouched except `scripts/setup.sh`.
- `POST /feed/push` requires header `X-Feed-Key` equal to `FEED_KEY` from settings → else 403. Read endpoints (`/api/history`, `/ws`) require auth: Phase 1 accepts `MINIAPP_DEV_BYPASS=true` (settings) and otherwise returns 403 — the Phase 3 initData validator will slot into the same dependency.
- Ring buffers: max 500 candles per TF; TF set exactly `["M1","M5","M15","M30","H1","H4","D1"]`; a pushed candle with `t` equal to the buffer tail REPLACES it (forming bar update), newer `t` appends, older is ignored.
- Bridge is read-only by construction: only `initialize/symbol_info_tick/copy_rates_from_pos/positions_get/shutdown` from the MetaTrader5 package. Infinite retry with backoff; `--once` mode prints one snapshot and exits (live verification).
- Fail-open: bridge push failures never crash the loop; miniapp never raises into WS clients; stale feed is the frontend's concern (Phase 2).
- Branch `feat/miniapp-phase1` from `main`. izi.md updated in Task 3. Venv: `cd service && source .venv/bin/activate`. Known flake rule applies.

---

### Task 1: Mini-app service (`app/miniapp.py`) — state, push, history, WS

**Files:**
- Create: `service/app/miniapp.py`
- Modify: `service/app/config.py` (add `feed_key: str = ""`, `miniapp_dev_bypass: bool = False` to the settings model, matching its existing field style)
- Test: `service/tests/test_miniapp.py` (create)

**Interfaces:**
- Consumes: `app.config.settings` pattern.
- Produces (Phase 2/3 rely on): FastAPI app `app.miniapp:app`; `FeedState` with `.apply_push(batch: dict) -> list[dict]` returning delta messages; `GET /api/history?tf=` → `{"tf": str, "candles": [{t,o,h,l,c,v}...]}`; `WS /ws` protocol: on connect one `{"type":"snapshot","tick":...,"positions":[...],"tfs":[...]}`, then `{"type":"tick"|"candle"|"positions",...}` deltas; auth dependency `require_viewer()` (dev-bypass now, initData later).

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_miniapp.py`:

```python
"""Mini-app feed service: keyed push, ring buffers, history, WS deltas."""
import importlib

import pytest
from fastapi.testclient import TestClient

TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "true")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        yield c


def _candle(t, c=4000.0):
    return {"t": t, "o": c - 1, "h": c + 2, "l": c - 2, "c": c, "v": 10}


def _push(client, body, key="sekret"):
    return client.post("/feed/push", json=body, headers={"X-Feed-Key": key})


def test_push_requires_key(client):
    assert _push(client, {"tick": None}, key="wrong").status_code == 403
    assert _push(client, {"tick": None}).status_code == 200


def test_history_appends_and_replaces_forming_bar(client):
    _push(client, {"candles": {"M5": [_candle(1000, 4000.0)]}})
    _push(client, {"candles": {"M5": [_candle(1000, 4001.5)]}})   # same t -> replace
    _push(client, {"candles": {"M5": [_candle(1300, 4002.0)]}})   # newer -> append
    _push(client, {"candles": {"M5": [_candle(700, 3999.0)]}})    # older -> ignored
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert [c["t"] for c in body["candles"]] == [1000, 1300]
    assert body["candles"][0]["c"] == 4001.5


def test_history_ring_buffer_caps_at_500(client):
    _push(client, {"candles": {"M1": [_candle(1000 + 60 * i) for i in range(600)]}})
    body = client.get("/api/history", params={"tf": "M1"}).json()
    assert len(body["candles"]) == 500
    assert body["candles"][0]["t"] == 1000 + 60 * 100   # oldest 100 evicted


def test_history_unknown_tf_400(client):
    assert client.get("/api/history", params={"tf": "M7"}).status_code == 400


def test_history_requires_viewer_auth(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "false")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        assert c.get("/api/history", params={"tf": "M5"}).status_code == 403


def test_ws_snapshot_then_deltas(client):
    _push(client, {"tick": {"bid": 4000.1, "ask": 4000.4, "time": 111},
                   "candles": {"M5": [_candle(1000)]},
                   "positions": [{"ticket": 1, "direction": "SELL",
                                  "lots": 0.05, "entry": 4005.0, "sl": 4015.0,
                                  "tp": 0.0, "profit": 24.5, "magic": 990001}]})
    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["tick"]["bid"] == 4000.1
        assert snap["positions"][0]["ticket"] == 1
        assert snap["tfs"] == TFS
        _push(client, {"tick": {"bid": 4000.2, "ask": 4000.5, "time": 112}})
        msg = ws.receive_json()
        assert msg["type"] == "tick" and msg["tick"]["bid"] == 4000.2
        _push(client, {"candles": {"M5": [_candle(1300)]}})
        msg = ws.receive_json()
        assert msg["type"] == "candle" and msg["tf"] == "M5" \
            and msg["candle"]["t"] == 1300
        _push(client, {"positions": []})
        msg = ws.receive_json()
        assert msg["type"] == "positions" and msg["positions"] == []


def test_ws_requires_viewer_auth(monkeypatch):
    monkeypatch.setenv("FEED_KEY", "sekret")
    monkeypatch.setenv("MINIAPP_DEV_BYPASS", "false")
    from app import config, miniapp
    importlib.reload(config)
    importlib.reload(miniapp)
    with TestClient(miniapp.app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws"):
                pass


def test_push_never_500s_on_garbage(client):
    assert _push(client, {"candles": {"M5": [{"bad": 1}]}}).status_code == 200
    assert _push(client, {"tick": "nonsense"}).status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd service && python -m pytest tests/test_miniapp.py -v`
Expected: FAIL (`ModuleNotFoundError: app.miniapp`)

- [ ] **Step 3: Implement `service/app/miniapp.py`**

```python
"""Read-only mini-app feed service (port 9001) — the ONLY thing the
Phase 3 tunnel will expose. The Windows bridge POSTs batches to
/feed/push; browsers get history over REST and live deltas over one
WebSocket. No trading controls exist here by construction.

Runs as its own process: uvicorn app.miniapp:app --host 127.0.0.1
--port 9001. State is in-memory only — a restart just refills from the
bridge's next backfill push (fail-open).
"""
import asyncio
import contextlib
from collections import deque

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)

from app.config import settings

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


app = FastAPI(title="xau-miniapp")


class _Hub:
    """WS clients + broadcast. Slow/dead clients are dropped, never
    awaited to death (send failures disconnect that client only)."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


state = FeedState()
hub = _Hub()


def require_viewer(request: Request = None):
    """Phase 1: dev bypass only. Phase 3 replaces the body with Telegram
    initData validation + owner/channel-membership authorization — same
    dependency, same call sites."""
    if settings.miniapp_dev_bypass:
        return True
    raise HTTPException(status_code=403, detail="viewer auth required")


@app.post("/feed/push")
async def feed_push(request: Request):
    if request.headers.get("X-Feed-Key", "") != settings.feed_key \
            or not settings.feed_key:
        raise HTTPException(status_code=403, detail="bad feed key")
    try:
        batch = await request.json()
    except Exception:
        return {"ok": False}
    deltas = state.apply_push(batch)
    for d in deltas:
        await hub.broadcast(d)
    return {"ok": True, "deltas": len(deltas)}


@app.get("/api/history")
def history(tf: str, _=Depends(require_viewer)):
    if tf not in TFS:
        raise HTTPException(status_code=400, detail=f"unknown tf {tf}")
    return {"tf": tf, "candles": list(state.candles[tf])}


@app.websocket("/ws")
async def ws_feed(ws: WebSocket):
    if not settings.miniapp_dev_bypass:
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
```

`service/app/config.py`: add `feed_key: str = ""` and
`miniapp_dev_bypass: bool = False` fields following the file's existing
pydantic-settings style (env names FEED_KEY / MINIAPP_DEV_BYPASS come for
free).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && python -m pytest tests/test_miniapp.py -v`
Expected: all PASS. Then the full suite: `python -m pytest` — green (the
new module must not disturb the main app).

- [ ] **Step 5: Commit**

```bash
git add service/app/miniapp.py service/app/config.py service/tests/test_miniapp.py
git commit -m "feat(miniapp): feed service — keyed push, ring buffers, history API, WS deltas"
```

---

### Task 2: Windows bridge (`bridge/mt5_feed.py`)

**Files:**
- Create: `bridge/mt5_feed.py`
- Create: `bridge/README.md` (3 lines: what it is, how it's started, --once)

**Interfaces:**
- Consumes: the running MT5 terminal via the `MetaTrader5` package (Windows Python — same environment `scripts/dump_bars.py` uses; read izi.md's backtest runbook for the exact python.exe invocation), `FEED_KEY` read from `service/.env`.
- Produces: batches POSTed to `http://127.0.0.1:9001/feed/push` in exactly the shape Task 1's `apply_push` consumes.

- [ ] **Step 1: Implement**

```python
"""MT5 -> mini-app feed bridge. Runs on WINDOWS Python next to the
terminal (the MetaTrader5 package only works there). Read-only by
construction: the only MT5 calls in this file are initialize,
symbol_info_tick, copy_rates_from_pos, positions_get, shutdown.

Usage:
  python bridge/mt5_feed.py            # run forever (launcher does this)
  python bridge/mt5_feed.py --once     # one snapshot printed + pushed, exit 0/1

Fail-open: any MT5/HTTP error backs off and retries; the trading system
never depends on this process.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

import MetaTrader5 as mt5

SYMBOL = "XAUUSD"
PUSH_URL = "http://127.0.0.1:9001/feed/push"
TFS = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
       "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
       "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
       "D1": mt5.TIMEFRAME_D1}
BACKFILL = 500
TICK_EVERY = 0.5
BARS_EVERY = 2.0


def feed_key() -> str:
    env = Path(__file__).resolve().parent.parent / "service" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("FEED_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def push(key: str, batch: dict) -> bool:
    req = urllib.request.Request(
        PUSH_URL, data=json.dumps(batch).encode(),
        headers={"Content-Type": "application/json", "X-Feed-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def rates(tf_const, count) -> list:
    rows = mt5.copy_rates_from_pos(SYMBOL, tf_const, 0, count)
    if rows is None:
        return []
    return [{"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]), "c": float(r["close"]),
             "v": int(r["tick_volume"])} for r in rows]


def tick_batch() -> dict:
    t = mt5.symbol_info_tick(SYMBOL)
    if t is None:
        return {}
    return {"tick": {"bid": t.bid, "ask": t.ask,
                     "spread": round((t.ask - t.bid) * 100) / 100,
                     "time": int(t.time)}}


def positions_batch() -> dict:
    poss = mt5.positions_get(symbol=SYMBOL)
    out = []
    for p in (poss or []):
        out.append({"ticket": int(p.ticket),
                    "direction": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "lots": float(p.volume), "entry": float(p.price_open),
                    "sl": float(p.sl), "tp": float(p.tp),
                    "profit": float(p.profit), "magic": int(p.magic)})
    return {"positions": out}


def bars_batch(count: int) -> dict:
    return {"candles": {name: rates(const, count)
                        for name, const in TFS.items()}}


def main() -> int:
    once = "--once" in sys.argv
    key = feed_key()
    if not key:
        print("mt5_feed: FEED_KEY missing in service/.env"); return 1
    if not mt5.initialize():
        print("mt5_feed: MT5 initialize failed:", mt5.last_error()); return 1
    try:
        if once:
            batch = {**tick_batch(), **bars_batch(2), **positions_batch()}
            print(json.dumps({k: (v if k != "candles" else
                                  {tf: len(rows) for tf, rows in v.items()})
                              for k, v in batch.items()}, indent=2))
            ok = push(key, batch)
            print("push:", "ok" if ok else "FAILED")
            return 0 if ok else 1
        # run forever: full backfill on start + whenever pushes recover
        need_backfill = True
        last_bars = 0.0
        while True:
            if need_backfill:
                if push(key, bars_batch(BACKFILL)):
                    need_backfill = False
                else:
                    time.sleep(3)
                    continue
            batch = tick_batch()
            now = time.time()
            if now - last_bars >= BARS_EVERY:
                batch.update(bars_batch(2))
                batch.update(positions_batch())
                last_bars = now
            if batch and not push(key, batch):
                need_backfill = True     # service restarted -> refill buffers
                time.sleep(3)
            time.sleep(TICK_EVERY)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Live verification (`--once`)**

Start the miniapp locally (`cd service && MINIAPP_DEV_BYPASS=true FEED_KEY=<generated> .venv/bin/uvicorn app.miniapp:app --port 9001 &` — or export via .env per Task 3), ensure `FEED_KEY` exists in `service/.env`, then run the bridge once via Windows Python (izi runbook invocation). Expected: snapshot JSON printed, `push: ok`, and `curl "127.0.0.1:9001/api/history?tf=M5"` returns candles. Record the output in the report.

- [ ] **Step 3: Commit**

```bash
git add bridge/mt5_feed.py bridge/README.md
git commit -m "feat(bridge): MT5 feed bridge — ticks, multi-TF candles, positions"
```

---

### Task 3: Wiring — FEED_KEY generation, miniapp process in setup, izi

**Files:**
- Modify: `scripts/setup.sh` (new idempotent phase: generate `FEED_KEY` into `service/.env` if absent (`openssl rand -hex 24` or python secrets fallback); start `uvicorn app.miniapp:app --host 127.0.0.1 --port 9001` with nohup + pidfile pattern matching the existing service phase; SKIP cleanly when already running)
- Modify: `.claude/agents/izi.md`
- Modify: `service/.env.example` (document `FEED_KEY=`, `MINIAPP_DEV_BYPASS=false`)

- [ ] **Step 1: setup.sh phase** — follow the file's existing phase structure/output conventions exactly (read it first); the phase must be re-runnable (second run prints SKIP/OK, doesn't double-start).
- [ ] **Step 2: izi.md** — new §: mini-app feed service (port 9001, NEVER exposed until Phase 3's tunnel; endpoints; FEED_KEY; dev bypass), the bridge (Windows-only, read-only call set, `--once` self-test, started by setup/launcher — note launcher wiring for the bridge itself lands in Phase 3 with the tunnel), restart procedure (pkill pattern for `app.miniapp`), and the Phase 1/2/3 roadmap pointer to the spec.
- [ ] **Step 3: Gates** — full service suite green; `bash -n scripts/setup.sh` clean; run the setup phase once live (miniapp comes up on 9001, `curl 127.0.0.1:9001/api/history?tf=M5` behaves per auth setting).
- [ ] **Step 4: Commit** — `git add scripts/setup.sh service/.env.example .claude/agents/izi.md && git commit -m "feat(setup): mini-app service phase — FEED_KEY + port 9001 process"`

---

## Self-Review Notes (applied)

- Spec Phase-1 scope ↔ tasks: state/push/history/WS (T1), bridge with --once + read-only call set (T2), FEED_KEY + process management + izi (T3). Auth = dev bypass only, with the Phase 3 seam (`require_viewer`) named.
- WS auth in Phase 1 mirrors REST (bypass or closed) — closes the "WS forgot auth" hole early.
- Type consistency: batch shape {tick, candles{tf:[{t,o,h,l,c,v}]}, positions[]} identical in T1 tests, T1 code, T2 bridge.
- The bridge's backfill-on-push-failure covers miniapp restarts without any persistence.
