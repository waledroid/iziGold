#!/usr/bin/env python3
"""Backfill `trades.ema200_agree` for rows written before the EA recorded it.

Unlike scripts/backfill_htf_agree.py (a HIGHER timeframe's EMA), the
EMA-200 confirmation added 2026-08-22 is a SAME-timeframe check: BUY agrees
when price is above the strategy's own EMA-200, SELL when below, read at
the trade's own entry bar -- no buffer, no chop exception. The trading
timeframe differs per strategy (M5 for halftrend_ema_v1, M15 for
halftrend_m15_v1), so this script aggregates the M5 candle history into
M15 for that lane the same way scripts/backtest.py's --tf M15 does.

Candle history comes from a dump of the live terminal (see
scripts/dump_bars.py), always M5. Rows the history cannot cover (before the
dataset starts, or EMA-200 not yet warmed up) are left as -1 (unknown)
rather than guessed.

    python3 scripts/backfill_ema200_agree.py [--db service/xau_assistant.db]
                                              [--bars bars_max.json] [--apply]
"""
import argparse
import bisect
import json
import sqlite3

M5 = 300
M15 = 900
EMA_LEN = 200

# strategy_id -> its own trading timeframe, in seconds. Unknown strategy
# ids fall back to M5 (today's only other registered strategy,
# boll_stochrsi, also trades the chart's TradeTimeframe = M5).
STRATEGY_TF_SEC = {
    "halftrend_ema_v1": M5,
    "halftrend_m15_v1": M15,
}


def load_candles(path):
    raw = json.load(open(path))
    c = raw["candles"] if isinstance(raw, dict) and "candles" in raw else raw
    return [{"t": int(x["t"]), "c": float(x["c"])} for x in c]


def resample(candles, tf_sec):
    """Aggregate M5 candles into tf_sec bars (close = the bucket's last
    close). Only close is needed here -- this is an estimate for a backfill,
    not the live EA's exact aggregation."""
    if tf_sec == M5:
        return candles
    out = []
    last_bucket = None
    for x in candles:
        b = x["t"] - (x["t"] % tf_sec)
        if b != last_bucket:
            out.append({"t": b, "c": x["c"]})
            last_bucket = b
        else:
            out[-1]["c"] = x["c"]
    return out


def ema_series(closes, period):
    k = 2.0 / (period + 1.0)
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    sma = sum(closes[:period]) / period
    out[period - 1] = sma
    e = sma
    for i in range(period, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="service/xau_assistant.db")
    ap.add_argument("--bars", default="bars_max.json")
    ap.add_argument("--apply", action="store_true",
                    help="write the results (default is a dry run)")
    args = ap.parse_args()

    m5 = load_candles(args.bars)
    tf_cache = {}

    def series_for(tf_sec):
        if tf_sec not in tf_cache:
            candles = resample(m5, tf_sec)
            times = [x["t"] for x in candles]
            closes = [x["c"] for x in candles]
            tf_cache[tf_sec] = (times, ema_series(closes, EMA_LEN))
        return tf_cache[tf_sec]

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT id, ts, strategy_id, direction, price FROM trades"
        " WHERE event='open' ORDER BY ts").fetchall()
    agree = disagree = unknown = 0
    updates = []
    for rid, ts, strategy_id, direction, price in rows:
        tf_sec = STRATEGY_TF_SEC.get(strategy_id, M5)
        times, ema200 = series_for(tf_sec)
        # the bar covering ts, or the nearest one at/just before it (gaps,
        # weekends, or ts landing between two source bars)
        j = bisect.bisect_right(times, ts) - 1
        if j < 0 or ema200[j] is None:
            unknown += 1
            continue
        ev = ema200[j]
        ok = (price > ev) if direction == "BUY" else (price < ev)
        updates.append((1 if ok else 0, rid))
        agree += ok
        disagree += (not ok)
    print(f"  {len(rows)} open events: {agree} agree, {disagree} disagree, "
          f"{unknown} not covered by the candle history")
    if args.apply:
        conn.executemany("UPDATE trades SET ema200_agree=? WHERE id=?", updates)
        conn.commit()
        print(f"  wrote {len(updates)} rows")
    else:
        print("  dry run -- pass --apply to write")


if __name__ == "__main__":
    main()
