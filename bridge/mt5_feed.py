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
    # Keep the LAST matching line, not the first: this mirrors
    # pydantic-settings/dotenv "last value wins" semantics, so if .env
    # ever ends up with more than one FEED_KEY= line (e.g. a blank one
    # shipped by .env.example plus a real one appended later) the bridge
    # reads the same value the service does, rather than the stale blank.
    env = Path(__file__).resolve().parent.parent / "service" / ".env"
    val = ""
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("FEED_KEY="):
            v = line.split("=", 1)[1].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            val = v
    return val


_last_depth = None   # buffer depth the service reported on the last push


def push(key: str, batch: dict) -> bool:
    """POST one batch. Returns True on HTTP 200. Also records the service's
    reported ring-buffer depth (`depth` in the response) so the run loop can
    re-backfill a service that came back empty after a restart."""
    global _last_depth
    req = urllib.request.Request(
        PUSH_URL, data=json.dumps(batch).encode(),
        headers={"Content-Type": "application/json", "X-Feed-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status != 200:
                return False
            try:
                _last_depth = json.loads(r.read().decode() or "{}").get("depth")
            except Exception:
                _last_depth = None
            return True
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
    if poss is None:
        return {}   # fail-open: a failed read must never overwrite last known truth
    out = []
    for p in poss:
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
    if once:
        if not mt5.initialize():
            print("mt5_feed: MT5 initialize failed:", mt5.last_error()); return 1
    else:
        # unattended: the bridge may start before the terminal finishes
        # loading, or lose it mid-run — retry forever, don't exit.
        last_print = 0.0
        while not mt5.initialize():
            now = time.time()
            if now - last_print >= 60:
                print("mt5_feed: MT5 initialize failed, retrying:", mt5.last_error())
                last_print = now
            time.sleep(10)
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
                need_backfill = True     # service down -> refill when it's back
                time.sleep(3)
            elif _last_depth is not None and _last_depth < BACKFILL // 2:
                # Service is up but SHALLOW (it restarted between two of our
                # pushes and lost its buffers): re-send the full backfill now
                # instead of feeding it 2 bars per push forever.
                need_backfill = True
            time.sleep(TICK_EVERY)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
