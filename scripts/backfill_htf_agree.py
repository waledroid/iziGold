#!/usr/bin/env python3
"""Backfill `trades.htf_agree` for rows written before the EA recorded it.

For every OPEN event, reconstruct what the higher-timeframe filter would have
said at that moment: price on the signal's side of the EMA-55 of the last
CLOSED M15 bar, cleared by BUFFER_ATR x ATR(14) when the tape was choppy
(path efficiency over CHOP_BARS closed bars below CHOP_EFF_MAX).

The thresholds mirror the shipped EA inputs; pass --buffer/--chop-max to
answer "what would a different setting have done".

Candle history comes from a dump of the live terminal (see
scripts/dump_bars.py). Rows the history cannot cover are left as -1
(unknown) rather than guessed.

    python3 scripts/backfill_htf_agree.py [--db service/xau_assistant.db]
                                          [--bars bars_max.json] [--apply]
"""
import argparse
import bisect
import datetime as dt
import json
import sqlite3

EMA_LEN = 55
ATR_LEN = 14
M15 = 900


def load_candles(path):
    raw = json.load(open(path))
    c = raw["candles"] if isinstance(raw, dict) and "candles" in raw else raw
    return [{"t": int(x["t"]), "h": float(x["h"]), "l": float(x["l"]),
             "c": float(x["c"])} for x in c]


def m15_ema(candles):
    """(time, EMA-55) per CLOSED M15 bar -- the series the EA reads at shift 1."""
    closes = []
    for i, x in enumerate(candles):
        b = x["t"] // M15
        nxt = candles[i + 1]["t"] // M15 if i + 1 < len(candles) else None
        if nxt != b or (x["t"] % M15) == M15 - 300:
            closes.append((x["t"], x["c"]))
    k = 2.0 / (EMA_LEN + 1)
    ema, out = None, []
    for j, (t, c) in enumerate(closes):
        if j + 1 == EMA_LEN:
            ema = sum(v for _, v in closes[:EMA_LEN]) / EMA_LEN
        elif j + 1 > EMA_LEN:
            ema = c * k + ema * (1 - k)
        out.append((t, ema))
    return out


def atr_series(candles):
    tr = [0.0] + [max(candles[i]["h"] - candles[i]["l"],
                      abs(candles[i]["h"] - candles[i - 1]["c"]),
                      abs(candles[i]["l"] - candles[i - 1]["c"]))
                  for i in range(1, len(candles))]
    out = [None] * len(candles)
    if len(candles) <= ATR_LEN:
        return out
    a = sum(tr[1:ATR_LEN + 1]) / ATR_LEN
    out[ATR_LEN] = a
    for i in range(ATR_LEN + 1, len(candles)):
        a = (a * (ATR_LEN - 1) + tr[i]) / ATR_LEN
        out[i] = a
    return out


def efficiency(candles, i, bars):
    seg = candles[max(0, i - bars):i + 1]
    path = sum(abs(seg[q]["c"] - seg[q - 1]["c"]) for q in range(1, len(seg)))
    return (abs(seg[-1]["c"] - seg[0]["c"]) / path) if path else 1.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="service/xau_assistant.db")
    ap.add_argument("--bars", default="bars_max.json")
    ap.add_argument("--buffer", type=float, default=2.0)
    ap.add_argument("--chop-max", type=float, default=0.08)
    ap.add_argument("--chop-bars", type=int, default=48)
    ap.add_argument("--apply", action="store_true",
                    help="write the results (default is a dry run)")
    args = ap.parse_args()

    candles = load_candles(args.bars)
    ema = m15_ema(candles)
    etimes = [t for t, _ in ema]
    atr = atr_series(candles)
    idx = {x["t"]: i for i, x in enumerate(candles)}

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT id, ts, direction, price FROM trades WHERE event='open'"
        " ORDER BY ts").fetchall()
    agree = disagree = unknown = 0
    updates = []
    for rid, ts, direction, price in rows:
        i = idx.get(ts - (ts % 300))
        j = bisect.bisect_left(etimes, ts) - 1
        if i is None or j < 0 or ema[j][1] is None or atr[i] is None:
            unknown += 1
            continue
        ev, av = ema[j][1], atr[i]
        pad = args.buffer * av
        if args.chop_max > 0 and efficiency(candles, i, args.chop_bars) > args.chop_max:
            pad = 0.0          # trending: the side test alone, as the EA does
        ok = (price > ev + pad) if direction == "BUY" else (price < ev - pad)
        updates.append((1 if ok else 0, rid))
        agree += ok
        disagree += (not ok)
    print(f"  {len(rows)} open events: {agree} agree, {disagree} disagree, "
          f"{unknown} not covered by the candle history")
    if args.apply:
        conn.executemany("UPDATE trades SET htf_agree=? WHERE id=?", updates)
        conn.commit()
        print(f"  wrote {len(updates)} rows")
    else:
        print("  dry run -- pass --apply to write")


if __name__ == "__main__":
    main()
