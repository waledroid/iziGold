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
- no margin modelling (fine at these sizes), no slippage beyond spread;
- the daily loss brake (MaxDailyLossPct) is NOT modeled — no simulated day
  ever refuses entries after a losing run the way the live EA does;
- the news blackout (NewsGuard high-impact USD events) is NOT modeled — the
  replay takes entries the live EA would refuse near calendar events.


Usage: backtest.py [--balance 4000] [--source URL|file.json] [--verbose]
                   [--exit-scheme target-exit|floor-a|floor-b|floor-a-adds]
                   [--adx N] [--expo MIN] [--risk PCT] [--days N]
                   [--confirm N] [--stop-buffer ATR]
                   [--entry-mode adr|fixed] [--fixed-lots L]
                   [--regime-gate off|range|range-strict|highvol]
                   [--atr-spike-gate RATIO] [--ema-len N]
                   [--confirm-mode close|open] [--start T] [--end T]

--confirm overrides ConfirmCloses (consecutive closes beyond the EMA after a
HalfTrend flip before the entry fires — EA fake-out filter semantics: the
counter resets on every flip and whenever a close lands back on the wrong
side of the EMA; default 1 reproduces the historical latch exactly).
--stop-buffer overrides the ATR(14) multiple padding the wick-extreme stop.

--exit-scheme (profit-floor experiment, spec 2026-08-12-profit-floor-design):
default target-exit is the current EA behavior; floor-a converts the profit
target into a guaranteed floor stop at target-0.25*ATR-worth (adds frozen);
floor-b arms the full target as the floor once profit reaches
target+0.25*ATR-worth (adds frozen); floor-a-adds is floor-a with adds left
on, to quantify the erosion. Floor stop is a pure ratchet; the profit lock
and reversal exits are unchanged in every scheme.

--entry-mode (fixed-entry experiment, spec 2026-08-13-entry-mode-fixed):
default adr is the live behavior above; fixed replays a pure trend ride —
every entry is --fixed-lots lots (default 0.05, no 1%-risk sizing), no
pyramid adds, no profit target, no profit lock. Exits only on the confirmed
opposite signal (reversal), the shared wick-extreme stop, or the pre-break
flatten the replay already models.

--regime-gate (range-filter experiment): replays the SERVICE's live regime
classifier (app.regime.classify_regime — ADX(14) >= 25 => trend, ATR(14)
in the top 20% of its last 100 values => high_volatility, else range) on
the exact 300-closed-bar window the EA posts to /analyze (AiApi.mqh), and
refuses NEW entries when the bar's regime is "range" (range), "range" /
"high_volatility" (range-strict), or "high_volatility" only (highvol —
i.e. skip entries while ATR(14) ranks in the top 20% of its last 100
values). Adds, exits, stops are untouched. Every trade is tagged with its
entry regime and the summary breaks P/L down per regime, so an
--regime-gate off run shows what a gate WOULD have skipped.
Default off is byte-identical to the previous baseline.

--atr-spike-gate RATIO (volatility-spike experiment): parametric cousin of
the highvol gate using the replay's own Wilder ATR(14): refuse a NEW entry
when ATR(14) at the entry bar > RATIO x the median of ATR(14) over the last
ATR_SPIKE_N=100 closed bars (current bar included — the same 100-value
lookback regime.py uses for its percentile rank). Every trade is tagged with
its entry atr_ratio and the summary buckets P/L by ratio, so an off run
shows what each threshold WOULD have skipped. Default 0 = off (byte-
identical baseline). Combines freely with --regime-gate.

--ema-len N (EMA-length experiment): the trading EMA the HalfTrend flip must
be confirmed against (EA input EmaLength). Default 55 = byte-identical.

--confirm-mode close|open (confirmation-price experiment, 2026-08-17 whipsaw
autopsy): close (default, byte-identical) counts closed-bar CLOSES beyond the
EMA, exactly like the EA. open counts bar OPENS beyond the EMA: at the
EA's decision moment (bar i just closed = bar i+1 just opened) it tests
open[i+1] against EMA[i] instead of close[i] against EMA[i]. Because on M5
open[i+1] == close[i] except for the rare tick gap, and because the EA's
counter already starts on the flip bar itself (no extra bar of delay), this
mode fires on the SAME bar as the close rule and exists to demonstrate that
"enter when a candle opens beyond the EMA" is not an earlier signal for an
EA that acts on closed bars. Fill stays at close[i]; the run prints how many
decision bars actually differed. Adds' condition-still-true stays close-based
(EA ConditionStillTrue).
"""
import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.indicators import ema, halftrend  # noqa: E402
from app.regime import classify_regime  # noqa: E402

# --- current EA inputs ---
RISK_PCT = 1.0
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
EXPO_MIN = 360            # daily open-position minutes budget; 0 = unlimited
FLATTEN_HM = (23, 50)     # last acted bar before the 23:59 break
ADX_MIN = 10.0  # matches EA AdxTrendThreshold; overridable via --adx
SPREAD_USD = 0.20         # per oz, per round trip (typical 18-25 points)
MIN_OZ = 1                # 0.01 lots

# --- profit-floor experiment (docs/superpowers/specs/2026-08-12-profit-floor-design.md) ---
# target-exit  : baseline — close the basket at +PROFIT_TARGET_PCT (current EA)
# floor-a      : at target, shared stop -> price where basket P/L =
#                target - 0.25*ATR(14)-worth; adds frozen from that moment
# floor-b      : at target + 0.25*ATR-worth of profit, stop -> price where
#                basket P/L = full target; adds frozen
# floor-a-adds : floor-a but pyramid adds left ON (quantifies the erosion)
# The floor stop is a pure ratchet (only tightens, directionally); profit
# lock (50% of peak once >= 1R) and the reversal exit stay unchanged.
EXIT_SCHEMES = ("target-exit", "floor-a", "floor-b", "floor-a-adds")
EXIT_SCHEME = "target-exit"
FLOOR_ARM_ATR = 0.25      # the 0.25*ATR(14) margin in both variants

# --- fixed-entry experiment (spec 2026-08-13-entry-mode-fixed) ---
# adr   : live behavior (1%-risk sizing, adds, target, lock) — the default
# fixed : FIXED_LOTS lots per entry, no adds/target/lock; exits only via
#         confirmed reversal, the shared stop, or the pre-break flatten
ENTRY_MODE = "adr"
FIXED_LOTS = 0.05         # lots (1 lot = 100 oz) when ENTRY_MODE == "fixed"

# --- regime-gate experiment ---
# off          : baseline (entries tagged with their regime, nothing refused)
# range        : refuse new entries when the service classifier says "range"
# range-strict : also refuse "high_volatility" (only "trend" bars may enter)
# highvol      : refuse new entries when the classifier says "high_volatility"
REGIME_GATES = ("off", "range", "range-strict", "highvol")
REGIME_GATE = "off"
REGIME_WINDOW = 300       # closed bars the EA posts per /analyze (AiApi.mqh)

# --- ATR-spike gate experiment ---
# refuse new entries when ATR(14) > ATR_SPIKE_RATIO * median(ATR(14) over the
# last ATR_SPIKE_N bars, current included); 0 = off. Reported buckets are the
# thresholds the sweep looks at.
ATR_SPIKE_RATIO = 0.0
ATR_SPIKE_N = 100
ATR_SPIKE_BUCKETS = (1.3, 1.5, 1.8, 2.2)

# --- confirmation-price experiment ---
# close : EA behavior — closes beyond the EMA feed the ConfirmCloses counter
# open  : the NEXT bar's open (== this close bar the EA acts on, minus tick
#         gaps) feeds the counter; see module docstring
CONFIRM_MODES = ("close", "open")
CONFIRM_MODE = "close"


class _Bar:
    __slots__ = ("h", "l", "c")

    def __init__(self, x):
        self.h, self.l, self.c = x["h"], x["l"], x["c"]


def regime_at(candles, i):
    """The service's live verdict for closed bar i: classify_regime() over
    the same 300-bar window the EA would have posted after that bar."""
    win = candles[max(0, i - REGIME_WINDOW + 1):i + 1]
    return classify_regime([_Bar(x) for x in win])


def atr_ratio_at(atr, i, n=None):
    """ATR(14) at bar i relative to the median of its last n values (bar i
    included), mirroring regime.py's 100-value lookback. None if <14 values."""
    n = n or ATR_SPIKE_N
    recent = [v for v in atr[max(0, i - n + 1):i + 1] if v is not None]
    if len(recent) < 14 or atr[i] is None:
        return None
    srt = sorted(recent)
    k = len(srt)
    med = srt[k // 2] if k % 2 else (srt[k // 2 - 1] + srt[k // 2]) / 2
    return atr[i] / med if med > 0 else None


def floor_price(legs, s, amount):
    """Price P where the basket's P/L equals `amount`, using the same
    convention as basket_pl(): sum(oz_i*(P-e_i)*s) - SPREAD_USD*T = amount
    (s=+1 BUY, s=-1 SELL; contract = 1 oz units, round-trip spread charged),
    so a stop fill exactly at P realizes exactly `amount`."""
    tot = sum(l["oz"] for l in legs)
    wsum = sum(l["oz"] * l["px"] for l in legs)
    return (wsum + s * (amount + SPREAD_USD * tot)) / tot


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
    consec_above = consec_below = 0   # EA fake-out counters (ConfirmCloses)
    trades = []
    peak_bal, max_dd = bal, 0.0
    peak_eq, max_valley = bal, 0.0     # open-equity (close-based) valley
    expo = {}              # server-day -> minutes of open-position time
    skipped = []           # entries a gate refused: (when, dir, reason)
    open_diff_bars = []    # confirm-mode open: bars where open[i+1] vs EMA
                           # landed on a different side than close[i]

    def basket_pl(px):
        s = 1 if basket["dir"] == "BUY" else -1
        return sum((px - l["px"]) * s * l["oz"] for l in basket["legs"]) \
            - SPREAD_USD * sum(l["oz"] for l in basket["legs"])

    def close_basket(px, when, why):
        nonlocal bal, basket, peak_bal, max_dd
        pl = basket_pl(px)
        bal += pl
        trades.append({"dir": basket["dir"], "legs": list(basket["legs"]),
                       "exit": px, "when": when, "why": why, "pl": pl,
                       "opened": basket.get("opened"),
                       "regime": basket.get("regime"),
                       "atr_ratio": basket.get("atr_ratio"),
                       "floor": basket.get("floor"),
                       "cycle_bal": basket["cycle_bal"]})
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
            consec_above = consec_below = 0   # flip re-arms the filter
        else:
            extreme = min(extreme, x["l"]) if trend == 0 else max(extreme, x["h"])
        cpx = px                     # price tested against the EMA
        if CONFIRM_MODE == "open" and i + 1 < len(candles):
            cpx = candles[i + 1]["o"]   # first price of the bar now opening
            if (cpx > e) != (px > e) or (cpx < e) != (px < e):
                open_diff_bars.append(when)
        if cpx > e:
            consec_above, consec_below = consec_above + 1, 0
        elif cpx < e:
            consec_below, consec_above = consec_below + 1, 0

        day = when.date()
        if basket:
            expo[day] = expo.get(day, 0) + 5   # one M5 bar of held time

        # ---- flatten before the break
        if h == FLATTEN_HM[0] and m >= FLATTEN_HM[1]:
            if basket:
                close_basket(px, when, "flatten")
            continue

        signal = None
        if fired_flip != last_flip:
            if trend == 0 and cpx > e and consec_above >= CONFIRM_CLOSES:
                signal = "BUY"
            elif trend == 1 and cpx < e and consec_below >= CONFIRM_CLOSES:
                signal = "SELL"
            if signal:
                fired_flip = last_flip

        # ---- manage open basket
        if basket:
            s = 1 if basket["dir"] == "BUY" else -1
            # shared stop hit (intrabar) — checked BEFORE any close-based exit
            # (existing convention: stop beats target/lock/reversal in a bar)
            hit = x["l"] <= basket["stop"] if s == 1 else x["h"] >= basket["stop"]
            if hit:
                why = "stop"
                if basket.get("floor") is not None and \
                        basket["stop"] * s >= basket["floor_px"] * s - 1e-9:
                    why = "floor stop"
                close_basket(basket["stop"], when, why)
            else:
                pl = basket_pl(px)
                basket["peak"] = max(basket["peak"], pl)
                risk_budget = basket["cycle_bal"] * RISK_PCT / 100
                target = basket["cycle_bal"] * PROFIT_TARGET_PCT / 100
                closed = False
                if ENTRY_MODE == "fixed":
                    pass   # pure ride: no profit target / floor in fixed mode
                elif EXIT_SCHEME == "target-exit":
                    if pl >= target:
                        close_basket(px, when, "profit target")
                        closed = True
                elif basket.get("floor") is None:
                    # arm the floor instead of closing at target
                    tot_oz = sum(l["oz"] for l in basket["legs"])
                    arm = None
                    if EXIT_SCHEME in ("floor-a", "floor-a-adds") \
                            and pl >= target:
                        arm = target - FLOOR_ARM_ATR * a * tot_oz
                    elif EXIT_SCHEME == "floor-b" \
                            and pl >= target + FLOOR_ARM_ATR * a * tot_oz:
                        arm = target
                    if arm is not None:
                        fpx = floor_price(basket["legs"], s, arm)
                        basket["floor"], basket["floor_px"] = arm, fpx
                        # pure ratchet: the floor may only tighten the stop
                        if fpx * s > basket["stop"] * s:
                            basket["stop"] = fpx
                        if verbose:
                            print(f"  floor {when:%m-%d %H:%M} armed "
                                  f"${arm:+.2f} stop->{basket['stop']:.2f}")
                if closed:
                    pass
                elif (ENTRY_MODE != "fixed"
                      and basket["peak"] >= TRAIL_ACTIVATE_R * risk_budget
                      and pl <= basket["peak"] * TRAIL_LOCK_PCT / 100):
                    close_basket(px, when, "profit lock")
                elif signal and signal != basket["dir"]:
                    close_basket(px, when, "reversal")
                elif ENTRY_MODE != "fixed":  # no pyramid adds in fixed mode
                    # pyramid add (frozen once the floor is armed, except
                    # in the floor-a-adds erosion probe)
                    frozen = basket.get("floor") is not None \
                        and EXIT_SCHEME != "floor-a-adds"
                    cond = (basket["dir"] == "BUY" and trend == 0 and px > e) or \
                           (basket["dir"] == "SELL" and trend == 1 and px < e)
                    adv = (px - basket["legs"][-1]["px"]) * s
                    if (not frozen and cond and pl > 0
                            and len(basket["legs"]) < MAX_POSITIONS
                            and adv >= ADD_TRIGGER_ATR * a):
                        oz = max(MIN_OZ, int(basket["legs"][-1]["oz"] * ADD_SHRINK))
                        basket["legs"].append({"px": px, "oz": oz})
                        n_adds = len(basket["legs"]) - 1
                        e0 = basket["legs"][0]["px"]
                        if n_adds == 1:      # halfway current stop -> entry
                            ladder = (basket["stop"] + e0) / 2
                        else:                # lagging ladder: mid of two prior entries
                            ladder = (basket["legs"][-3]["px"]
                                      + basket["legs"][-2]["px"]) / 2
                        if basket.get("floor") is not None:
                            # armed: ladder may tighten the stop, never loosen
                            if ladder * s > basket["stop"] * s:
                                basket["stop"] = ladder
                        else:
                            basket["stop"] = ladder
                        if verbose:
                            print(f"  add   {when:%m-%d %H:%M} {oz}oz @ {px:.2f} "
                                  f"stop->{basket['stop']:.2f}")

        # ---- entries
        if basket is None and signal:
            in_window = WINDOW[0] <= h < WINDOW[1]
            trending = adx[i] is not None and adx[i] >= ADX_MIN
            expo_ok = EXPO_MIN <= 0 or expo.get(day, 0) < EXPO_MIN
            if in_window and trending and expo_ok:
                regime = regime_at(candles, i)
                aratio = atr_ratio_at(atr, i)
                blocked = None
                if (REGIME_GATE == "range" and regime == "range") or \
                        (REGIME_GATE == "range-strict" and regime != "trend") or \
                        (REGIME_GATE == "highvol" and regime == "high_volatility"):
                    blocked = regime
                elif ATR_SPIKE_RATIO > 0 and aratio is not None \
                        and aratio > ATR_SPIKE_RATIO:
                    blocked = f"atr_spike>{ATR_SPIKE_RATIO:g}"
                if blocked:
                    skipped.append((when, signal, blocked))
                    if verbose:
                        print(f"  skip  {when:%m-%d %H:%M} {signal} "
                              f"{blocked} (regime={regime}, "
                              f"atr_ratio={aratio if aratio is None else round(aratio, 2)})")
                    signal = None
            if in_window and trending and expo_ok and signal:
                pad = STOP_BUFFER_ATR * a
                stop = extreme - pad if signal == "BUY" else extreme + pad
                dist = abs(px - stop)
                if dist > 0:
                    if ENTRY_MODE == "fixed":
                        oz = max(MIN_OZ, int(round(FIXED_LOTS * 100)))
                    else:
                        risk = bal * RISK_PCT / 100
                        oz = max(MIN_OZ, int(risk / dist))
                    basket = {"dir": signal, "legs": [{"px": px, "oz": oz}],
                              "stop": stop, "peak": 0.0, "cycle_bal": bal,
                              "opened": when, "regime": regime,
                              "atr_ratio": aratio}
                    if verbose:
                        print(f"  open  {when:%m-%d %H:%M} {signal} {oz}oz "
                              f"@ {px:.2f} stop {stop:.2f} (dist {dist:.2f})")

        # open-equity valley (marked at bar close)
        eq = bal + (basket_pl(px) if basket else 0.0)
        peak_eq = max(peak_eq, eq)
        max_valley = max(max_valley, peak_eq - eq)

    if basket:
        close_basket(candles[-1]["c"], hhmm(candles[-1]["t"])[0], "eod-open")
    run.skipped = skipped
    run.open_diff_bars = open_diff_bars
    return trades, bal, max_dd, max_valley


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
    ap.add_argument("--expo", type=float, default=None,
                    help="override daily exposure minutes (0 = unlimited)")
    ap.add_argument("--risk", type=float, default=None,
                    help="override risk percent per trade")
    ap.add_argument("--confirm", type=int, default=None,
                    help="override ConfirmCloses (consecutive closes beyond "
                         "the EMA required after a flip; default 1)")
    ap.add_argument("--stop-buffer", type=float, default=None,
                    help="override the stop pad in ATR(14) multiples "
                         "(default 0.75)")
    ap.add_argument("--exit-scheme", choices=EXIT_SCHEMES, default="target-exit",
                    help="profit-floor experiment scheme (default: current "
                         "behavior, close at profit target)")
    ap.add_argument("--entry-mode", choices=["adr", "fixed"], default="adr",
                    help="adr = live behavior; fixed = fixed lots, no adds/"
                         "target/lock, exit on confirmed reversal or stop")
    ap.add_argument("--fixed-lots", type=float, default=0.05)
    ap.add_argument("--regime-gate", choices=REGIME_GATES, default="off",
                    help="refuse new entries by the service's live regime "
                         "classifier: range = skip 'range' bars, "
                         "range-strict = only enter on 'trend' bars, "
                         "highvol = skip 'high_volatility' bars")
    ap.add_argument("--atr-spike-gate", type=float, default=0.0,
                    help="refuse new entries when ATR(14) > RATIO x its "
                         "median over the last 100 bars (0 = off)")
    ap.add_argument("--start", default=None,
                    help="drop candles before this server time "
                         "(YYYY-MM-DD[THH:MM]); indicators warm up from here")
    ap.add_argument("--end", default=None,
                    help="drop candles after this server time (YYYY-MM-DD[THH:MM])")
    ap.add_argument("--ema-len", type=int, default=None,
                    help="override the trading EMA length (default 55)")
    ap.add_argument("--confirm-mode", choices=CONFIRM_MODES, default="close",
                    help="close = EA behavior (closes beyond the EMA); open = "
                         "next bar's open beyond the EMA (same decision bar)")
    args = ap.parse_args()
    global EXIT_SCHEME, ENTRY_MODE, FIXED_LOTS, REGIME_GATE, ATR_SPIKE_RATIO
    global CONFIRM_MODE
    CONFIRM_MODE = args.confirm_mode
    if args.ema_len is not None:
        global EMA_LEN
        EMA_LEN = args.ema_len
    EXIT_SCHEME = args.exit_scheme
    ENTRY_MODE = args.entry_mode
    FIXED_LOTS = args.fixed_lots
    REGIME_GATE = args.regime_gate
    ATR_SPIKE_RATIO = args.atr_spike_gate
    if args.adx is not None:
        global ADX_MIN
        ADX_MIN = args.adx
    if args.expo is not None:
        global EXPO_MIN
        EXPO_MIN = args.expo
    if args.risk is not None:
        global RISK_PCT
        RISK_PCT = args.risk
    if args.confirm is not None:
        global CONFIRM_CLOSES
        CONFIRM_CLOSES = args.confirm
    if args.stop_buffer is not None:
        global STOP_BUFFER_ATR
        STOP_BUFFER_ATR = args.stop_buffer

    if args.source.startswith("http"):
        data = json.load(urllib.request.urlopen(args.source))
    else:
        data = json.load(open(args.source))
    candles = data["candles"]
    if args.days:
        cutoff = candles[-1]["t"] - int(args.days * 86400)
        candles = [c for c in candles if c["t"] >= cutoff]
    if args.start:
        t = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.UTC).timestamp()
        candles = [c for c in candles if c["t"] >= t]
    if args.end:
        t = dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.UTC).timestamp()
        candles = [c for c in candles if c["t"] <= t]
    if len(candles) < 100:
        sys.exit(f"only {len(candles)} candles available - need at least 100")

    t0, t1 = hhmm(candles[0]["t"])[0], hhmm(candles[-1]["t"])[0]
    mode = (f" | entry mode fixed ({FIXED_LOTS:g} lots)"
            if ENTRY_MODE == "fixed" else "")
    print(f"backtest: {len(candles)} bars  {t0:%Y-%m-%d %H:%M} -> {t1:%m-%d %H:%M} "
          f"(server time) | start balance ${args.balance:,.0f} "
          f"| exit scheme {EXIT_SCHEME}{mode} | regime gate {REGIME_GATE}"
          f" | atr-spike gate {ATR_SPIKE_RATIO:g} | ema {EMA_LEN} "
          f"| confirm {CONFIRM_CLOSES} ({CONFIRM_MODE})\n")

    trades, bal, max_dd, max_valley = run(candles, args.balance, args.verbose)

    # floor guarantee check: once armed, a trade may never realize less than
    # its floor amount — the only allowed leaks are the close-based forced
    # exits (pre-break flatten / end-of-data), which fill at bar close and
    # may sit one bar's move below the floor price.
    floor_leaks = []
    for t in trades:
        if t.get("floor") is not None and t["pl"] < t["floor"] - 1e-6:
            if t["why"] in ("flatten", "eod-open"):
                floor_leaks.append(t)
            elif EXIT_SCHEME == "floor-a-adds":
                floor_leaks.append(t)   # erosion by post-arm adds: measured
            else:
                raise AssertionError(
                    f"floor violated: {t['when']:%m-%d %H:%M} {t['why']} "
                    f"pl {t['pl']:+.2f} < floor {t['floor']:+.2f}")

    wins = [t for t in trades if t["pl"] > 0]
    losses = [t for t in trades if t["pl"] <= 0]
    print(f"\ntrades: {len(trades)}  |  wins {len(wins)}  losses {len(losses)}")
    if trades:
        print(f"gross win  {sum(t['pl'] for t in wins):+10.2f}")
        print(f"gross loss {sum(t['pl'] for t in losses):+10.2f}")
        for t in trades:
            legs = "+".join(f"{l['oz']}oz@{l['px']:.2f}" for l in t["legs"])
            fl = f"  floor {t['floor']:+.2f}" if t.get("floor") is not None else ""
            print(f"  {t['when']:%m-%d %H:%M} {t['dir']:4} [{legs}] -> "
                  f"{t['exit']:.2f} {t['why']:>13} {t['pl']:+9.2f}{fl}")
    tdays = len({hhmm(x["t"])[0].date() for x in candles})
    print(f"trading days {tdays}  trades/day {len(trades) / max(1, tdays):.2f}  "
          f"win% {100 * len(wins) / max(1, len(trades)):.1f}  "
          f"avg winner {sum(t['pl'] for t in wins) / max(1, len(wins)):+.2f}  "
          f"avg loser {sum(t['pl'] for t in losses) / max(1, len(losses)):+.2f}")
    print("entry regime breakdown (service classifier on the 300-bar window):")
    for rg in ("trend", "range", "high_volatility"):
        sub = [t for t in trades if t.get("regime") == rg]
        w = [t for t in sub if t["pl"] > 0]
        print(f"  {rg:16} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
              f"  win% {100 * len(w) / max(1, len(sub)):5.1f}")
    print("entry ATR-spike breakdown (ATR14 / median of its last 100 values):")
    for r in ATR_SPIKE_BUCKETS:
        sub = [t for t in trades if (t.get("atr_ratio") or 0) > r]
        w = [t for t in sub if t["pl"] > 0]
        print(f"  ratio > {r:<4} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
              f"  win% {100 * len(w) / max(1, len(sub)):5.1f}")
    sk = getattr(run, "skipped", [])
    if REGIME_GATE != "off" or ATR_SPIKE_RATIO > 0:
        from collections import Counter
        cnt = Counter(r for _, _, r in sk)
        print(f"gate (regime {REGIME_GATE}, atr-spike {ATR_SPIKE_RATIO:g}): "
              f"refused {len(sk)} entries "
              f"({', '.join(f'{k} {v}' for k, v in cnt.items()) or 'none'})")
    if CONFIRM_MODE == "open":
        od = getattr(run, "open_diff_bars", [])
        print(f"confirm-mode open: {len(od)} decision bars where the next "
              f"open sat on a different side of the EMA than the close")
    print(f"\nnet P/L    {bal - args.balance:+10.2f}  "
          f"({100 * (bal / args.balance - 1):+.2f}%)")
    print(f"final bal  {bal:10.2f}   max drawdown {max_dd:.2f}   "
          f"max open-equity valley {max_valley:.2f}")
    armed = [t for t in trades if t.get("floor") is not None]
    if EXIT_SCHEME != "target-exit":
        print(f"floor armed on {len(armed)} trades; "
              f"{len(floor_leaks)} realized below their floor "
              f"({', '.join(t['why'] for t in floor_leaks) or 'none'})")
    if args.chart:
        plot(candles, trades, args.balance, args.chart)
        print(f"chart      {args.chart}")


if __name__ == "__main__":
    main()
