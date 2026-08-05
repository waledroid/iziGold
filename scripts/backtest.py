#!/usr/bin/env python3
"""Replay halftrend_ema_v1 with the CURRENT money rulebook over accumulated
candles (service /ui/candles, or a saved JSON) and report P/L.

Faithful to the EA where it matters: HalfTrend port from app.indicators,
ConfirmCloses=1 entry latch, wick-extreme stop padded 0.75*ATR(14), pyramid
adds (1*ATR advance, in-profit, condition-still-true, max 2, sizes *0.7),
halfway->lagging-ladder shared stop, 2%-of-cycle profit target, 50%-of-peak
profit lock (armed at 1R), 04-23 trading window, 23:54 pre-break flatten,
stop-and-reverse on opposite confirmed signal, Wilder ADX(14) >= 25 gate.

Simplifications (documented, keep in mind when reading results):
- acts on bar CLOSES only (the EA also acts once per closed bar);
- fills at close price +/- half-spread; SPREAD_USD charged per oz round-trip;
- broker min lot 0.01 (=1 oz), integer-oz sizing;
- no margin modelling (fine at these sizes), no slippage beyond spread.

Usage: backtest.py [--balance 4000] [--source URL|file.json] [--verbose]
"""
import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.indicators import ema, halftrend  # noqa: E402

# --- current EA inputs ---
RISK_PCT = 0.5
STOP_BUFFER_ATR = 0.75
CONFIRM_CLOSES = 1
EMA_LEN = 55
AMPLITUDE = 4
ADD_TRIGGER_ATR = 1.0
MAX_POSITIONS = 3
ADD_SHRINK = 0.7
PROFIT_TARGET_PCT = 2.0
TRAIL_LOCK_PCT = 50.0
TRAIL_ACTIVATE_R = 1.0
WINDOW = (4, 23)          # server hours
FLATTEN_HM = (23, 50)     # last acted bar before the 23:59 break
ADX_MIN = 20.0  # matches EA AdxTrendThreshold; overridable via --adx
SPREAD_USD = 0.20         # per oz, per round trip (typical 18-25 points)
MIN_OZ = 1                # 0.01 lots


def wilder(vals, period):
    out, s = [None] * len(vals), None
    for i, v in enumerate(vals):
        if v is None:
            continue
        s = v if s is None else (s * (period - 1) + v) / period
        out[i] = s
    return out


def atr_adx(c, period=14):
    n = len(c)
    tr = [None] * n
    pdm = [0.0] * n
    ndm = [0.0] * n
    for i in range(1, n):
        hi, lo, pc = c[i]["h"], c[i]["l"], c[i - 1]["c"]
        tr[i] = max(hi - lo, abs(hi - pc), abs(lo - pc))
        up, dn = hi - c[i - 1]["h"], c[i - 1]["l"] - lo
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
    atr = wilder(tr, period)
    spdm, sndm = wilder(pdm, period), wilder(ndm, period)
    adx, dxs = [None] * n, [None] * n
    for i in range(n):
        if atr[i] and spdm[i] is not None and sndm[i] is not None and atr[i] > 0:
            pdi, ndi = 100 * spdm[i] / atr[i], 100 * sndm[i] / atr[i]
            if pdi + ndi > 0:
                dxs[i] = 100 * abs(pdi - ndi) / (pdi + ndi)
    adx = wilder(dxs, period)
    return atr, adx


def hhmm(t):
    d = dt.datetime.fromtimestamp(t, dt.UTC)   # candle t is server wall-clock
    return d, d.hour, d.minute


def run(candles, start_balance, verbose):
    closes = [x["c"] for x in candles]
    ema55 = ema(closes, EMA_LEN)
    ht = halftrend(
        [type("C", (), x)() for x in candles], amplitude=AMPLITUDE)
    atr, adx = atr_adx(candles)

    bal = start_balance
    basket = None          # dict: dir, legs[{px,oz}], stop, peak, cycle_bal
    fired_flip = None      # flip index already traded
    last_flip = None
    extreme = None
    trades = []
    peak_bal, max_dd = bal, 0.0

    def basket_pl(px):
        s = 1 if basket["dir"] == "BUY" else -1
        return sum((px - l["px"]) * s * l["oz"] for l in basket["legs"]) \
            - SPREAD_USD * sum(l["oz"] for l in basket["legs"])

    def close_basket(px, when, why):
        nonlocal bal, basket, peak_bal, max_dd
        pl = basket_pl(px)
        bal += pl
        trades.append({"dir": basket["dir"], "legs": list(basket["legs"]),
                       "exit": px, "when": when, "why": why, "pl": pl})
        peak_bal = max(peak_bal, bal)
        max_dd = max(max_dd, peak_bal - bal)
        if verbose:
            legs = "+".join(f"{l['oz']}oz@{l['px']:.2f}" for l in basket["legs"])
            print(f"  close {when:%m-%d %H:%M} {basket['dir']} [{legs}] "
                  f"@ {px:.2f} {why:>14}  P/L {pl:+8.2f}  bal {bal:9.2f}")
        basket = None

    for i in range(EMA_LEN + AMPLITUDE + 2, len(candles)):
        x = candles[i]
        when, h, m = hhmm(x["t"])
        px, e, a = x["c"], ema55[i], atr[i]
        if e is None or a is None or ht[i] is None:
            continue
        trend = ht[i][1]
        if last_flip is None or (ht[i - 1] and trend != ht[i - 1][1]):
            last_flip, extreme = i, (x["l"] if trend == 0 else x["h"])
        else:
            extreme = min(extreme, x["l"]) if trend == 0 else max(extreme, x["h"])

        # ---- flatten before the break
        if h == FLATTEN_HM[0] and m >= FLATTEN_HM[1]:
            if basket:
                close_basket(px, when, "flatten")
            continue

        signal = None
        if fired_flip != last_flip:
            if trend == 0 and px > e:
                signal = "BUY"
            elif trend == 1 and px < e:
                signal = "SELL"
            if signal:
                fired_flip = last_flip

        # ---- manage open basket
        if basket:
            s = 1 if basket["dir"] == "BUY" else -1
            # shared stop hit (intrabar)
            hit = x["l"] <= basket["stop"] if s == 1 else x["h"] >= basket["stop"]
            if hit:
                close_basket(basket["stop"], when, "stop")
            else:
                pl = basket_pl(px)
                basket["peak"] = max(basket["peak"], pl)
                risk_budget = basket["cycle_bal"] * RISK_PCT / 100
                if pl >= basket["cycle_bal"] * PROFIT_TARGET_PCT / 100:
                    close_basket(px, when, "profit target")
                elif (basket["peak"] >= TRAIL_ACTIVATE_R * risk_budget
                      and pl <= basket["peak"] * TRAIL_LOCK_PCT / 100):
                    close_basket(px, when, "profit lock")
                elif signal and signal != basket["dir"]:
                    close_basket(px, when, "reversal")
                else:
                    # pyramid add
                    cond = (basket["dir"] == "BUY" and trend == 0 and px > e) or \
                           (basket["dir"] == "SELL" and trend == 1 and px < e)
                    adv = (px - basket["legs"][-1]["px"]) * s
                    if (cond and pl > 0 and len(basket["legs"]) < MAX_POSITIONS
                            and adv >= ADD_TRIGGER_ATR * a):
                        oz = max(MIN_OZ, int(basket["legs"][-1]["oz"] * ADD_SHRINK))
                        basket["legs"].append({"px": px, "oz": oz})
                        n_adds = len(basket["legs"]) - 1
                        e0 = basket["legs"][0]["px"]
                        if n_adds == 1:      # halfway current stop -> entry
                            basket["stop"] = (basket["stop"] + e0) / 2
                        else:                # lagging ladder: mid of two prior entries
                            basket["stop"] = (basket["legs"][-3]["px"]
                                              + basket["legs"][-2]["px"]) / 2
                        if verbose:
                            print(f"  add   {when:%m-%d %H:%M} {oz}oz @ {px:.2f} "
                                  f"stop->{basket['stop']:.2f}")

        # ---- entries
        if basket is None and signal:
            in_window = WINDOW[0] <= h < WINDOW[1]
            trending = adx[i] is not None and adx[i] >= ADX_MIN
            if in_window and trending:
                pad = STOP_BUFFER_ATR * a
                stop = extreme - pad if signal == "BUY" else extreme + pad
                dist = abs(px - stop)
                if dist > 0:
                    risk = bal * RISK_PCT / 100
                    oz = max(MIN_OZ, int(risk / dist))
                    basket = {"dir": signal, "legs": [{"px": px, "oz": oz}],
                              "stop": stop, "peak": 0.0, "cycle_bal": bal}
                    if verbose:
                        print(f"  open  {when:%m-%d %H:%M} {signal} {oz}oz "
                              f"@ {px:.2f} stop {stop:.2f} (dist {dist:.2f})")

    if basket:
        close_basket(candles[-1]["c"], hhmm(candles[-1]["t"])[0], "eod-open")
    return trades, bal, max_dd


def plot(candles, trades, start_balance, out_path):
    """Two-panel PNG: price with trade spans/markers, and the equity curve."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    ts = [x["t"] for x in candles]
    idx = {t: i for i, t in enumerate(ts)}
    fig = Figure(figsize=(12, 7))
    ax, ax2 = fig.subplots(2, 1, height_ratios=[3, 1], sharex=True)
    for i, x in enumerate(candles):
        color = "#2ecc71" if x["c"] >= x["o"] else "#e74c3c"
        ax.vlines(i, x["l"], x["h"], color=color, linewidth=0.6)
    eq_x, eq_y = [0], [start_balance]
    for t in trades:
        exit_i = idx.get(int(t["when"].timestamp()), len(candles) - 1)
        first = t["legs"][0]["px"]
        # entry bar: search back for the first leg's price era (approx: span
        # from exit back by number of bars is unknown -- mark exit and legs)
        color = "#2ecc71" if t["pl"] > 0 else "#e74c3c"
        ax.axvspan(max(0, exit_i - 12), exit_i, color=color, alpha=0.10)
        m = "^" if t["dir"] == "BUY" else "v"
        ax.scatter([exit_i], [t["exit"]], marker="x", color=color, s=22, zorder=5)
        ax.scatter([max(0, exit_i - 12)], [t["legs"][0]["px"]], marker=m,
                   color=color, s=18, zorder=5, alpha=0.8)
        ax.annotate(f"{t['pl']:+.0f}", xy=(exit_i, t["exit"]),
                    xytext=(3, 6), textcoords="offset points",
                    color=color, fontsize=7)
        eq_x.append(exit_i)
        eq_y.append(eq_y[-1] + t["pl"])
    eq_x.append(len(candles) - 1)
    eq_y.append(eq_y[-1])
    ax2.step(eq_x, eq_y, where="post", color="dodgerblue", linewidth=1.5)
    ax2.axhline(start_balance, color="#888", linewidth=0.7, linestyle=":")
    ax2.set_ylabel("equity $")
    ax.set_title(f"halftrend backtest — {len(trades)} trades, "
                 f"net {eq_y[-1] - start_balance:+.2f} on {start_balance:.0f}")
    n = len(candles)
    ticks = list(range(0, n, max(1, n // 8)))
    import datetime as dt
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([dt.datetime.fromtimestamp(candles[i]["t"], dt.UTC)
                         .strftime("%d %H:%M") for i in ticks], fontsize=7)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--balance", type=float, default=4000)
    ap.add_argument("--source", default="http://127.0.0.1:9000/ui/candles")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--adx", type=float, default=None, help="override ADX gate")
    ap.add_argument("--chart", default=None, help="write a PNG chart to this path")
    ap.add_argument("--days", type=float, default=None,
                    help="backtest only the last N days of the source data")
    args = ap.parse_args()
    if args.adx is not None:
        global ADX_MIN
        ADX_MIN = args.adx

    if args.source.startswith("http"):
        data = json.load(urllib.request.urlopen(args.source))
    else:
        data = json.load(open(args.source))
    candles = data["candles"]
    if args.days:
        cutoff = candles[-1]["t"] - int(args.days * 86400)
        candles = [c for c in candles if c["t"] >= cutoff]
    if len(candles) < 100:
        sys.exit(f"only {len(candles)} candles available - need at least 100")

    t0, t1 = hhmm(candles[0]["t"])[0], hhmm(candles[-1]["t"])[0]
    print(f"backtest: {len(candles)} bars  {t0:%Y-%m-%d %H:%M} -> {t1:%m-%d %H:%M} "
          f"(server time) | start balance ${args.balance:,.0f}\n")

    trades, bal, max_dd = run(candles, args.balance, args.verbose)

    wins = [t for t in trades if t["pl"] > 0]
    losses = [t for t in trades if t["pl"] <= 0]
    print(f"\ntrades: {len(trades)}  |  wins {len(wins)}  losses {len(losses)}")
    if trades:
        print(f"gross win  {sum(t['pl'] for t in wins):+10.2f}")
        print(f"gross loss {sum(t['pl'] for t in losses):+10.2f}")
        for t in trades:
            legs = "+".join(f"{l['oz']}oz@{l['px']:.2f}" for l in t["legs"])
            print(f"  {t['when']:%m-%d %H:%M} {t['dir']:4} [{legs}] -> "
                  f"{t['exit']:.2f} {t['why']:>13} {t['pl']:+9.2f}")
    print(f"\nnet P/L    {bal - args.balance:+10.2f}  "
          f"({100 * (bal / args.balance - 1):+.2f}%)")
    print(f"final bal  {bal:10.2f}   max drawdown {max_dd:.2f}")
    if args.chart:
        plot(candles, trades, args.balance, args.chart)
        print(f"chart      {args.chart}")


if __name__ == "__main__":
    main()
