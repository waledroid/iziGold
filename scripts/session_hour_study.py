#!/usr/bin/env python3
"""Hour-of-day return study for the session_structure_v1 shadow strategy.

Reads a dump_bars.py JSON ({symbol, timeframe, candles:[{t,o,h,l,c,v}]},
timestamps in SERVER time) and prints per-server-hour return stats plus the
strategy's configured windows. This is the study that set the 2026-08-30
defaults (window 1 = 01-04); re-run it when the bull regime turns or a new
bars dump lands, before trusting those defaults further.

Usage: python3 scripts/session_hour_study.py [bars_max.json]
"""
import datetime
import json
import math
import sys


def study(path):
    with open(path) as f:
        d = json.load(f)
    cs = d["candles"]
    first = datetime.datetime.utcfromtimestamp(cs[0]["t"])
    last = datetime.datetime.utcfromtimestamp(cs[-1]["t"])
    print(f"{d['symbol']} {d['timeframe']}  {len(cs)} bars  {first} -> {last} (server time)")

    hours = {h: [] for h in range(24)}
    for prev, cur in zip(cs, cs[1:]):
        if cur["t"] - prev["t"] > 3600:  # weekend/session gap: skip the jump bar
            continue
        r = math.log(cur["c"] / prev["c"])
        hours[datetime.datetime.utcfromtimestamp(cur["t"]).hour].append(r)

    print(f"{'hour':>4} {'bars':>7} {'cum_ret_%':>10} {'mean_bp':>8} {'tstat':>7}")
    sums = {}
    for h in range(24):
        v = hours[h]
        if not v:
            print(f"{h:4d}  (no bars)")
            sums[h] = 0.0
            continue
        n = len(v)
        m = sum(v) / n
        sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5 if n > 1 else 0.0
        t = m / (sd / n ** 0.5) if sd else 0.0
        sums[h] = 100 * sum(v)
        print(f"{h:4d} {n:7d} {sums[h]:+10.2f} {10000 * m:+8.3f} {t:+7.2f}")

    def window(label, lo, hi):
        tot = sum(sums[h] for h in range(lo, hi))
        print(f"  {label:<28} hours {lo:02d}-{hi:02d}: {tot:+.2f}% cumulative")

    print("\nStrategy windows (server hours):")
    window("win1 Asia drift (default ON)", 1, 4)
    window("win2 candidate (default OFF)", 9, 10)
    window("short fix-fade (default OFF)", 16, 18)


if __name__ == "__main__":
    study(sys.argv[1] if len(sys.argv) > 1 else "bars_max.json")
