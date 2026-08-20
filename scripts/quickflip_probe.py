#!/usr/bin/env python3
"""QuickFlip evidence probe — the tool behind the numbers in
docs/superpowers/specs/2026-08-20-quickflip-ny-design.md.

Box the first M15 candle of a chosen half-hour, qualify it against daily
ATR(14), then trade the SWEEP-AND-REVERSE: price leaves the box in the
opening candle's direction, comes back inside, and we bet the far side of the
box gets hit before the sweep extreme does.

TIME CONVENTION -- read this before editing: candle `t` is SERVER wall-clock
already. Read it with dt.datetime.fromtimestamp(t, dt.UTC) and NO offset.
Adding one shifts every session by that many hours; a +3h shift is what made
an earlier version of this analysis report the wrong sessions entirely.
Sanity check: server hour 00 contains zero bars (the daily market break).

NOTE: `scripts/backtest.py`'s `qf_signals()` runs this same sweep-and-reverse
logic inside the full replay engine. This probe and that function are
deliberately TWINS, not shared code -- this file stands alone (imports nothing
from the engine) so it can be pointed at any bars JSON directly. If you change
the trade logic here, change it there too, and vice versa. Every default they
share is pinned equal by
`service/tests/test_quickflip_probe.py::test_probe_and_engine_pin_the_same_defaults`,
so drift fails a test instead of passing silently.

WHAT THIS PROBE LEAVES OUT -- read before quoting a number from it. It reports
only setups that RESOLVE inside the window: price reaches the stop or the
target before the 90 minutes are up. Setups that expire unresolved are
DROPPED here, and the engine TRADES them, closing at the bar price when the
window ends. So this probe is systematically kinder to the strategy than what
ships. Measured 2026-08-20 over 17 months at the shipped 5% ATR gate:
this probe +$0.458/oz on 165 rows, the engine +$0.246/oz on 177 trades -- the
12 expired trades net -$52.27 and the probe never sees them. That is a 1.9x
overstatement. THE ENGINE'S NUMBERS ARE THE ONES THAT SHIP; use
`scripts/backtest.py --strategy qf` for anything that decides money.

Usage:
    python3 scripts/quickflip_probe.py [--source bars_max.json]
                                       [--hour 13] [--minute 30]
                                       [--sweep]     # every half-hour
"""
import argparse
import datetime as dt
import json

SPREAD_USD = 0.20     # per oz, round trip -- same charge the replay uses
WINDOW_MIN = 90
ATR_DAYS = 14


def _server(t):
    return dt.datetime.fromtimestamp(int(t), dt.UTC)


def _day(t):
    return int(t) // 86400


def load(path):
    raw = json.loads(open(path).read())
    c = raw["candles"] if isinstance(raw, dict) and "candles" in raw else raw
    for x in c:
        x["t"] = int(x["t"])
    return c


def daily_atr(candles):
    """server-day index -> ATR(ATR_DAYS) computed from the PRIOR days only."""
    days = {}
    for x in candles:
        d = days.setdefault(_day(x["t"]), {"h": x["h"], "l": x["l"], "c": x["c"]})
        d["h"] = max(d["h"], x["h"])
        d["l"] = min(d["l"], x["l"])
        d["c"] = x["c"]
    keys = sorted(days)
    out = {}
    for i, k in enumerate(keys):
        # Eligibility starts one day past the raw ATR_DAYS warm-up so that
        # the inner loop's `j - 1` never hits index 0: at i == ATR_DAYS,
        # j == 0 would make keys[j - 1] wrap around to keys[-1] -- the LAST
        # day in the whole dataset -- leaking months of future close into
        # the first computed ATR. Requiring i > ATR_DAYS keeps j >= 1.
        if i <= ATR_DAYS:
            continue
        s = 0.0
        for j in range(i - ATR_DAYS, i):
            dj, pc = days[keys[j]], days[keys[j - 1]]["c"]
            s += max(dj["h"] - dj["l"], abs(dj["h"] - pc), abs(dj["l"] - pc))
        out[k] = s / ATR_DAYS
    return out


def setups_at(candles, hour, minute, atr, window_min=WINDOW_MIN,
              spread=SPREAD_USD):
    """Every completed sweep-and-reverse trade at this half-hour."""
    by_day = {}
    for x in candles:
        by_day.setdefault(_day(x["t"]), []).append(x)
    out = []
    for k in sorted(by_day):
        if k not in atr:
            continue
        rows = by_day[k]
        if len(rows) < 100:          # half-day / holiday
            continue
        box = [x for x in rows if _server(x["t"]).hour == hour
               and minute <= _server(x["t"]).minute < minute + 15]
        if len(box) != 3:            # need the whole 15-minute candle
            continue
        hi = max(x["h"] for x in box)
        lo = min(x["l"] for x in box)
        green = box[-1]["c"] >= box[0]["o"]
        # ratio is informational only in this probe -- it is not filtered
        # on here. scripts/backtest.py's qf_signals() (this file's twin,
        # see module docstring) gates trade selection on it.
        ratio = (hi - lo) / atr[k]
        t_end = box[-1]["t"] + 300
        path = [x for x in rows if t_end <= x["t"] < t_end + window_min * 60]
        swept = False
        ext = entry = stop = tp = entry_t = pl = None
        for x in path:
            if not swept:
                if green and x["h"] > hi:
                    swept, ext = True, x["h"]
                elif not green and x["l"] < lo:
                    swept, ext = True, x["l"]
                continue
            if entry is None:
                ext = max(ext, x["h"]) if green else min(ext, x["l"])
                if green and x["c"] < hi:
                    entry, stop, tp, entry_t = x["c"], ext, lo, x["t"]
                elif not green and x["c"] > lo:
                    entry, stop, tp, entry_t = x["c"], ext, hi, x["t"]
                continue
            if green:                                    # short
                if x["h"] >= stop:
                    pl = -(stop - entry) - spread
                    break
                if x["l"] <= tp:
                    pl = (entry - tp) - spread
                    break
            else:                                        # long
                if x["l"] <= stop:
                    pl = -(entry - stop) - spread
                    break
                if x["h"] >= tp:
                    pl = (tp - entry) - spread
                    break
        if pl is not None:
            out.append({"ratio": ratio, "pl": pl, "green": green,
                        "entry_t": entry_t, "stop": stop, "tp": tp,
                        "box_hi": hi, "box_lo": lo})
    return out


EXCLUSION_NOTE = (
    "NOTE: rows above count only setups that RESOLVED inside the "
    f"{WINDOW_MIN}-minute window. Setups that expire unresolved are EXCLUDED "
    "here; scripts/backtest.py's qf_signals() lane TRADES them and books the "
    "P/L, so the engine's expectancy is LOWER. At the shipped 5% ATR gate "
    "over 17 months: this probe +$0.458/oz on 165 rows vs the engine "
    "+$0.246/oz on 177 trades (its 12 expired trades net -$52.27). The "
    "ENGINE's numbers are what ships -- run `scripts/backtest.py "
    "--strategy qf` for a decision.")


def report(rows, label):
    if not rows:
        print(f"{label:>7}  (no completed trades)")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["pl"] > 0)
    tot = sum(r["pl"] for r in rows)
    half = n // 2
    h1 = sum(r["pl"] for r in rows[:half]) / max(half, 1)
    h2 = sum(r["pl"] for r in rows[half:]) / max(n - half, 1)
    both = "  <== positive in BOTH halves" if h1 > 0 and h2 > 0 else ""
    print(f"{label:>7} {n:>5} {100*wins/n:>6.1f}% {tot/n:>8.2f} {tot:>9.2f} "
          f"{h1:>8.2f} {h2:>8.2f}{both}")


def build_parser():
    """Extracted so a test can read the shipped defaults without running the
    probe: --hour/--minute ARE the measured session, and a bare run is what
    every number in the spec came from."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="bars_max.json")
    ap.add_argument("--hour", type=int, default=13)
    ap.add_argument("--minute", type=int, default=30)
    ap.add_argument("--sweep", action="store_true",
                    help="scan every half-hour instead of one session")
    return ap


def main():
    args = build_parser().parse_args()
    candles = load(args.source)
    atr = daily_atr(candles)
    print(f"{'server':>7} {'n':>5} {'win%':>6} {'exp$/oz':>8} {'total':>9} "
          f"{'H1':>8} {'H2':>8}")
    if args.sweep:
        for h in range(1, 24):
            for m in (0, 30):
                rows = setups_at(candles, h, m, atr)
                if len(rows) >= 80:
                    report(rows, f"{h:02d}:{m:02d}")
    else:
        report(setups_at(candles, args.hour, args.minute, atr),
               f"{args.hour:02d}:{args.minute:02d}")
    print()
    print(EXCLUSION_NOTE)


if __name__ == "__main__":
    main()
