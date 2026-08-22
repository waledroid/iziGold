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
                   [--chop-flips F] [--chop-bars N] [--chop-box-atr X]
                   [--chop-mode skip|soft|off] [--loose-window]
                   [--min-stop-atr K] [--bias-ema N]
                   [--sr-lookback N] [--sr-min-headroom X] [--sr-report]
                   [--bias-mode tag|target|target_lock|size_target|skip]
                   [--bias-tf M5|M15]
                   [--window-start H] [--window-end H] [--hour-table]
                   [--tf M5|M15] [--profit-target PCT]

Replays halftrend_ema_v1, the only registered lane (see the LANES/Lane
plug-in contract below -- a second lane, if one is ever added again, plugs
back in there without CLI surgery; QuickFlip, the second lane this replay
carried 2026-08-20 through 2026-08-22, was dropped as a paid experiment --
see docs/superpowers/specs/2026-08-20-quickflip-ny-design.md).

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

--chop-flips F / --chop-bars N / --chop-box-atr X / --chop-mode M (chop-filter
experiment, 2026-08-17 whipsaw autopsy #3): a bar is "chop" when HalfTrend has
flipped >= F times within the last N closed bars (bar i included; a flip is
counted on the bar whose trend differs from the previous bar's) AND the price
box over those N bars (max high - min low) is < X * ATR(14) at bar i. X = 0
drops the box condition (flip count alone). F = 0 (default) = off, byte-
identical baseline. Every entry is tagged chop/not-chop with its flip count and
box/ATR ratio, and the summary breaks P/L down by tag so a --chop-mode off run
shows exactly what the rule WOULD have skipped or shrunk (opportunity cost).
--chop-mode: skip (default, H1) refuses NEW entries on chop bars; soft (H2)
still enters but sizes at HALF the risk percent (--risk / 2) and takes NO
pyramid adds for that basket — target, lock, stop, reversal unchanged (the
lock still arms at TRAIL_ACTIVATE_R x the FULL risk budget, as the EA's
TradeManager reads m_risk.RiskPct(), so a half-size basket needs twice the
move to arm it); off = tag and report only, nothing refused or resized.
Adds/exits are otherwise untouched; the rule lives only in the entry block.

--loose-window (entry-window correctness fix, 2026-08-17 owner's rule; strict
is now the DEFAULT as of 2026-08-20 -- this flag restores the old replay):
the TRUE halftrend_ema_v1 entry is "arrow on bar 1; wait bar 2; ENTER at
bar 3's OPEN if bar 3 opens on the trend's side of the EMA (= bar 2 CLOSED
there); otherwise the signal is DEAD until the next HalfTrend flip". Default
(strict): exactly one decision per flip, at the close of the bar
CONFIRM_CLOSES bars after the arrow bar (default 2 = bar 3): pass -> signal
on that closed bar (fill at its close, which IS bar 3's open barring the
tick gap — the replay's usual entry-at-close convention, unchanged); fail ->
no entry for that flip, ever. Same for the reversal exit, since a reversal
is the opposite direction's entry. --loose-window reproduces what the EA
did before 2026-08-16: fire on the FIRST close beyond the EMA after a flip,
whenever that came (the arrow bar itself, bar 2, or a late drift 20 bars
on). Every trade is tagged with its entry offset in bars after the arrow
(0 = arrow bar, 1 = strict bar, >=2 = late drift) so a loose run can be
diffed against a strict run per flip. --strict-window still parses (no-op;
strict is already the default) so older scripted runs keep working.

--min-stop-atr K (minimum-stop-distance floor, 2026-08-18 noise-stop autopsy:
a $2.99 stop on a BUY at 4399.06 with ATR ~ $4-5 put 0.15 lots on and
ordinary noise took it out in 13 minutes): after the strategy stop is
computed as usual (HalfTrend wick extreme +/- STOP_BUFFER_ATR x ATR(14) —
that logic is untouched, this is a FLOOR only), if |entry - stop| <
K x ATR(14) the ENTRY stop is pushed out to exactly K x ATR(14) from the
fill, directionally; lots are then sized from the widened distance exactly as
before (1% risk over the ACTUAL distance -> fewer lots). Pyramid adds are
untouched: their ladder stop is derived from the current shared stop / entry
prices, so the first add's "halfway stop -> entry" ladder inherits the
floored stop implicitly, nothing else changes. Every trade is tagged with
its raw stop distance in ATRs, whether it was floored, the original stop,
and the first bar (1 = the bar after entry) whose intrabar extreme would
have hit the ORIGINAL stop while the basket was still open (None = never
while open), so the summary can count the "noise stops saved" — floored
entries the old stop would have killed within the first 3 bars — and the
"should have died" entries the floor let bleed further. K = 0 (default) =
off, byte-identical baseline.

--bias-ema N / --bias-mode M / --bias-tf TF (EMA-200 market-bias experiment,
owner's idea 2026-08-18: "price above EMA-200 = bullish, below = bearish;
trades WITH the bias keep the profit target, trades AGAINST it get HALF the
target — counter-trend bounces are short"). N = 0 (default) = off, byte-
identical baseline. N > 0: at the ENTRY bar the bias is close[i] vs EMA-N;
a BUY above / SELL below the EMA is "with", the opposite is "counter" (close
exactly on the EMA = with, never happens in practice). The bias is decided
once at entry and is basket-sticky: a mid-trade flip does not touch an open
basket's target/lock. Every trade is tagged with/counter and the summary
splits count / win% / net / max loser by tag, so `--bias-mode tag` on the
baseline shows whether counter-trend trades are actually the losers.
--bias-mode:
  tag         : tag and report only, nothing changes (default when N > 0);
  target      : owner's literal version — counter-trend basket profit target
                x BIAS_TARGET_MULT (0.5: +1% of cycle balance instead of +2%).
                The profit lock is NOT touched, because in the EA and in this
                replay the lock's arming threshold is tied to the RISK budget
                (TRAIL_ACTIVATE_R x RiskPct of cycle balance), not to the
                target — so with a 1% target and a 1R = 1% arm the target
                check (which runs first) always wins and the lock is
                effectively dead for counter-trend baskets;
  target_lock : as target, plus the lock's arming threshold x the same 0.5
                (arms at 0.5R), keeping the baseline's lock-at-half-target
                proportion — the fair comparison for the target idea;
  size_target : counter-trend target x 0.5 AND risk x BIAS_RISK_MULT (0.5:
                0.5% instead of 1% — same reward:risk as with-trend, smaller
                bet against the tide); the lock's risk budget scales with the
                actual risk taken (arms at 1R of the HALVED budget), i.e. the
                whole counter-trend basket is a half-scale copy;
  skip        : counter-trend entries refused entirely (tagged in skipped).
--bias-tf: M5 (default) = EMA-N on the trading bars; M15 = EMA-N on the M5
bars resampled to M15 (bucket = t // 900 s; a bucket closes on its last M5
bar), read as the LAST COMPLETED M15 bar's EMA (iMA(M15, shift 1) at the M5
close moment) vs the M5 close — entries, exits, everything else stay M5. The
summary always prints how often the bias flipped per day on both M5 and M15
so the two clocks can be compared.

--window-start H / --window-end H / --hour-table (trading-hours experiment,
2026-08-18): the EA's TradingWindowStartHour / TradingWindowEndHour (server
time, GMT+3). Defaults 4 and 23 reproduce the live window byte-identically.
The window gates NEW ENTRIES only — exactly like the EA, whose RiskManager
checks the hour in the entry path and never in the exit path — so a narrower
window does NOT force an early flatten: a basket opened at 17:55 under
--window-end 18 keeps running until its target / lock / stop / reversal, and
the 23:50 pre-break flatten is unchanged in every window. --hour-table (off
by default, so the summary stays byte-identical) prints an entry-hour
breakdown: for each server hour 0-23, trades / win% / net / avg P/L, each
trade attributed to the hour its FIRST leg opened.

--sr-lookback N / --sr-min-headroom X / --sr-report (support/resistance
proximity experiment, 2026-08-18): the hypothesis is that an entry whose
immediate path is blocked by a recent level does worse, because the first
thing price meets is a wall. N = 0 (default) = off, byte-identical baseline.

Level set visible at the close of entry bar i (N = --sr-lookback):
  * fractal swing highs/lows over the last N bars: bar j is a swing high when
    its high is the max of the SR_PIVOT_K (=2) bars each side (mirrored, with
    min, for lows). Only pivots already CONFIRMED at bar i count, i.e.
    j + K <= i — no lookahead;
  * the previous COMPLETED server day's high and low;
  * the current session's high and low, measured over the bars STRICTLY
    BEFORE the entry bar. Excluding the entry bar itself matters: the entry
    bar's own high is >= its close by construction, so including it would put
    a "level" a few cents overhead on literally every BUY and drown the
    signal. As defined, a bar that breaks the session high simply has no
    session-high level above it — which is the correct reading.
Levels are sorted and de-duplicated within SR_DEDUP_ATR (=0.25) x ATR(14):
two levels a quarter-ATR apart are the same wall.

headroom := distance from the entry price (the decision bar's close, which is
what this replay fills at) to the NEAREST OPPOSING level in the trade's
direction — for a BUY the nearest level at or above the fill, for a SELL the
nearest at or below — expressed in ATR(14) units. If no level lies ahead at
all the headroom is None ("clear"), and such an entry is never refused.

--sr-min-headroom X refuses entries whose headroom is below X ATR (tagged
"headroom<X" in the skipped list). --sr-report tags every trade with its
headroom and prints the bucket table but NEVER refuses anything, so the
diagnostic can be read off an otherwise untouched baseline; --sr-report
therefore overrides --sr-min-headroom. Any N > 0 prints the bucket table
(<0.5 / 0.5-1 / 1-2 / >2 ATR, plus "clear"): trades, win%, net, avg, and the
winners'/losers' dollars per bucket — the last two are the opportunity-cost
columns, the dollars a threshold would skip vs the dollars it would avoid.

--tf M5|M15 (trading-timeframe study, owner's claim 2026-08-19: "this signal
looks better on the 15-minute chart"). The EA has a TradeTimeframe input that
pins EVERY trading decision to one timeframe, so this is a real, supported
switch, and the replay mirrors it: M5 (default) is byte-identical to every
result recorded so far; M15 aggregates the M5 source into 15-minute bars
(bucket = t // 900, i.e. server :00/:15/:30/:45; open = first open, high =
max, low = min, close = last close, volume = sum) and then runs the SAME
rulebook on those bars. Verified against 3,915 real broker M15 bars pulled
with MetaTrader5.copy_rates_from_pos(TIMEFRAME_M15): OHLC and volume matched
exactly on every one. Only a TRAILING incomplete bucket is dropped (the M15
bar still forming at the end of the data); the handful of mid-history buckets
that are short an M5 bar are kept, because a missing M5 bar means no ticks,
not a missing M15 bar.

What scales and what does not, when --tf M15 is on:
  * BAR-BASED, scales automatically (same NUMBER of bars, 3x the wall clock):
    ATR(14)/ADX(14), the trading EMA (--ema-len, default 55), the bias EMA
    (--bias-ema), HalfTrend amplitude 4, --confirm / the strict-window
    waiting bars, the pyramid trigger (1 x ATR — the ATR itself grows),
    --chop-bars, --sr-lookback, the ATR-spike lookback (100 bars), the
    300-bar regime window the service classifier reads, and the EA's
    CatchupMaxAgeBars (documented as "trade-TF bars").
  * TIME-BASED, NOT scaled (identical wall-clock meaning on both timeframes,
    exactly like the EA inputs they mirror): --expo / MaxDailyExposureMin
    (minutes of open-position time per server day — the replay charges
    BAR_MIN minutes per held bar, 5 on M5 and 15 on M15, so the budget buys
    a third as many bars), the --window-start/--window-end trading hours,
    and the pre-break flatten.
The flatten bar is the last bar of the server day on each timeframe: 23:50
(and 23:55) on M5, 23:45 on M15 — the M5 rule "hour 23, minute >= 50" would
never match an M15 bar, which stamps :00/:15/:30/:45.

--profit-target PCT overrides ProfitTargetPct (the basket's bank-at +PCT% of
cycle balance). Default 2.0 = byte-identical. PCT <= 0 turns the target OFF
exactly like the EA input documents it ("0 = off"): the basket then rides to
the profit lock (50% of peak once peak >= 1R), the shared stop, the confirmed
reversal or the pre-break flatten — sizing, adds and lock are untouched. This
is NOT the same as --entry-mode fixed, which additionally drops the risk
sizing, the adds and the lock.
"""

# Stated in --help, in the --json artifact and on the report page. A model's
# limits must travel with its output.
CAVEATS = [
    "daily-loss brake NOT modelled -- a real losing day would have been cut short",
    "kill switch NOT modelled -- a real 10% drawdown would have stopped trading",
    "news blackout NOT modelled -- no offline calendar of high-impact USD events",
    "acts on bar CLOSES only; fills at close +/- half-spread, $0.20/oz round trip",
    "no margin modelling and no stop-out: a small account can go negative here",
]

import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))
from app.indicators import ema, halftrend  # noqa: E402
from app.regime import classify_regime  # noqa: E402

# --- shared strategy config (single source of truth) ---
# The 16 parameters below (plus the trading window) used to be declared
# TWICE -- once as an MQL5 `input` default in mt5/Experts/XauAssistant.mq5,
# once as a module constant here -- and only agreed because a human kept
# both edits in sync by hand. config/strategy.json is now the one place
# these values live; the EA inputs remain the LIVE authority and this file
# must match them (enforced by
# service/tests/test_strategy_config.py::test_strategy_config_matches_the_ea).
# A missing/unreadable config file fails LOUDLY here rather than silently
# falling back to hardcoded defaults, which would recreate the exact drift
# this file exists to prevent.
STRATEGY_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "strategy.json"


def _load_strategy_config(path=STRATEGY_CONFIG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise SystemExit(
            f"backtest.py: cannot read the strategy config at {path}: {exc}. "
            "This file is the single source of truth for the parameters "
            "mirrored from the live EA's `input` defaults -- without it the "
            "backtest cannot promise it still matches the EA. Restore "
            "config/strategy.json or fix its permissions.") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"backtest.py: {path} is not valid JSON: {exc}. Fix the file -- "
            "it is the single source of truth for the parameters mirrored "
            "from the live EA's `input` defaults.") from exc


_CFG_RAW = _load_strategy_config()
# config/strategy.json now groups parameters into `shared` (TradeManager/
# RiskManager inputs that apply no matter which registered strategy is
# ActiveStrategy -- there is only one set of these EA inputs) and
# `strategies` (per-instance HalfTrend inputs that DO differ between the M5
# lane and the M15 lane added 2026-08-22). This replay is the M5 (`ht`) lane
# only -- scripts/backtest.py does not gain an M15 lane -- so flatten the
# shared block with just the halftrend_ema_v1 block, exactly the values that
# were flat top-level keys before the restructure.
_CFG = {**_CFG_RAW["shared"], **_CFG_RAW["strategies"]["halftrend_ema_v1"]}

# --- current EA inputs (values loaded from config/strategy.json) ---
RISK_PCT = _CFG["risk_per_trade_pct"]
STOP_BUFFER_ATR = _CFG["stop_buffer_atr"]
CONFIRM_CLOSES = _CFG["confirm_closes"]
                        # waiting bars after the HalfTrend arrow; the entry
                        # bar is the NEXT one. Owner decision 2026-08-20 on
                        # measured evidence: 1 was the worst of {loose,1,2,3}
                        # in EVERY window tested; 2 was the best M5 variant
                        # (+$779 over 365d at $10k). Caveat recorded with it:
                        # 2's profit is one regime (+$4,155 newer half,
                        # -$4,517 older half), not a demonstrated edge.
EMA_LEN = _CFG["ema_length"]
AMPLITUDE = _CFG["ht_amplitude"]
ADD_TRIGGER_ATR = _CFG["add_trigger_atr"]
MAX_POSITIONS = _CFG["max_positions"]
ADD_SHRINK = 0.7
PROFIT_TARGET_PCT = _CFG["profit_target_pct"]
TRAIL_LOCK_PCT = _CFG["trail_lock_pct"]
TRAIL_ACTIVATE_R = _CFG["trail_activate_r"]
WINDOW = (_CFG["trading_window_start_hour"], _CFG["trading_window_end_hour"])
                          # server hours (EA TradingWindowStart/EndHour);
                          # gates NEW ENTRIES only, never exits
HOUR_TABLE = False        # --hour-table: print the entry-hour breakdown
EXPO_MIN = _CFG["max_daily_exposure_min"]  # daily open-position minutes budget; 0 = unlimited
FLATTEN_HM = (23, 50)     # last acted bar before the 23:59 break

# --- trading timeframe (EA input TradeTimeframe; M15 study 2026-08-19) ---
# The source JSON is always M5; --tf M15 aggregates it before anything else
# runs, so every BAR-based parameter above is read in M15 bars while every
# TIME-based one (EXPO_MIN, WINDOW, the flatten) keeps its wall-clock meaning.
TFS = ("M5", "M15")
TF = "M5"
TF_SEC = {"M5": 300, "M15": 900}
SRC_SEC = 300             # the source feed's bar length
BAR_MIN = 5               # minutes of open-position time charged per held bar
FLATTEN_BY_TF = {"M5": (23, 50), "M15": (23, 45)}   # last bar of the server day

ADX_MIN = _CFG["adx_trend_threshold"]  # matches EA AdxTrendThreshold; overridable via --adx
SPREAD_USD = 0.20         # per oz, per round trip (typical 18-25 points)
MIN_OZ = 1                # 0.01 lots

# Starting-balance floors. The binding constraint is the 0.01 minimum lot,
# not spread: when 1% of balance cannot cover one ounce at the stop distance,
# sizing clamps to the minimum and OVER-risks instead of skipping the trade.
#
# Measured 2026-08-20 on the SHIPPED DEFAULT (strict entry window),
# `--source bars_max.json --days 365` -- 1,196-1,302 trades depending on
# balance. Earlier copies of this table were measured under the LOOSE window
# before strict became the default and read materially lower:
#     balance   entries clamped   risk actually taken (median / p90)
#     $500          94.7%            1.72% / 34.71%
#     $800          68.5%            1.43% /  3.89%
#     $1,200        47.0%            0.98% /  2.28%
#     $2,000        32.3%            0.90% /  1.77%
#     $4,000        16.7%            0.89% /  1.27%
#     $10,000        1.3%            0.93% /  0.99%
#     $25,000        0.0%            0.97% /  0.99%
# Over the full source (516 days) the same run clamps 20.5% at $4,000 and
# 3.7% at $10,000 -- the rate depends on how wide the stops were in the
# window tested, which is why every run measures and prints its own.
# $4,000 clamps roughly one entry in six and therefore trips this tool's own
# ">10% => results distorted" flag; $10,000+ is the honest floor for a clean
# test of the risk rules.
MIN_BALANCE = 500.0     # below this the result is fiction (no margin stop-out
                        # is modelled either -- a $300 account goes negative)
WARN_BALANCE = 2000.0   # below this, warn loudly and name the clamp rate


def validate_balance(value):
    """None = fine, str = warn and run, SystemExit = refuse."""
    if value < MIN_BALANCE:
        raise SystemExit(
            f"--balance {value:.0f} is below the ${MIN_BALANCE:.0f} floor.\n"
            "At that size nearly every entry clamps to the 0.01 minimum lot "
            "(94.7% at $500, measured over the last 365 days), so the replay "
            "measures minimum-lot behaviour, not the rulebook -- and margin "
            "stop-out is not modelled, so the account can go negative. "
            "$10,000+ is the floor for a clean test of the risk rules; "
            "$4,000 still clamps roughly one entry in six.")
    if value < WARN_BALANCE:
        return (f"WARNING: at ${value:.0f}, 1% risk often cannot cover one "
                f"ounce at the stop distance, so sizing falls back to the "
                f"0.01 minimum lot and takes MORE than 1% risk (measured over "
                f"the last 365 days: 47% of entries clamp at $1,200, 32% at "
                f"$2,000). The clamp rate for this run is reported below -- "
                f"read it before trusting the P/L.")
    return None


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

# --- chop-filter experiment ---
# chop bar := HalfTrend flips in the last CHOP_BARS bars >= CHOP_FLIPS and
# (CHOP_BOX_ATR == 0 or box(CHOP_BARS) < CHOP_BOX_ATR * ATR14). CHOP_FLIPS 0 = off.
CHOP_FLIPS = 0
CHOP_BARS = 24
CHOP_BOX_ATR = 2.0
CHOP_MODES = ("skip", "soft", "off")
CHOP_MODE = "skip"
CHOP_SOFT_RISK_DIV = 2.0   # soft mode: risk percent divided by this

# --- strict entry window (owner's rule 2026-08-17) ---
# False: legacy latch (first close beyond the EMA after the flip, any bar).
# True : one-shot decision at bar (flip + CONFIRM_CLOSES) close; miss = dead.
STRICT_WINDOW = True      # EA law since 2026-08-16: flip -> wait one closed
                          # bar -> enter only if the next bar OPENS beyond the
                          # EMA, else the signal is dead until the next flip.
                          # --loose-window restores the pre-2026-08-16 replay.

# --- minimum stop distance floor (2026-08-18 noise-stop autopsy) ---
# 0 = off. K > 0: entry stop may not sit closer than K x ATR(14) from the
# fill; sizing rescales over the widened distance. Entry stop only.
MIN_STOP_ATR = 0.0
NOISE_BARS = 3            # "stopped in the first N bars" = noise stop

# --- support/resistance proximity experiment (2026-08-18) ---
# SR_LOOKBACK 0 = off. N > 0: build the level set (fractal pivots over the
# last N bars + previous day's H/L + session H/L before the entry bar), then
# tag each entry with its headroom = distance to the nearest opposing level
# in ATR(14) units. SR_MIN_HEADROOM > 0 refuses entries below that headroom;
# SR_REPORT tags without ever refusing (diagnostic, overrides the filter).
SR_LOOKBACK = 0
SR_MIN_HEADROOM = 0.0
SR_REPORT = False
SR_PIVOT_K = 2            # fractal half-width (bars each side of the pivot)
SR_DEDUP_ATR = 0.25       # levels within this x ATR(14) collapse into one
SR_BUCKETS = (0.5, 1.0, 2.0)   # headroom bucket edges, in ATR

# --- EMA-200 market-bias experiment (owner's idea 2026-08-18) ---
# BIAS_EMA 0 = off. bias := close vs EMA-N at the entry bar; with/counter
# tags every trade; BIAS_MODE decides what a counter-trend entry gets.
# Higher-timeframe agreement, LIVE DEFAULT since 2026-08-20 (EA inputs
# HtfConfirm/HtfConfirmTf/HtfConfirmEma). An M5 entry is refused unless price
# sits on the signal's side of the EMA-55 of the last CLOSED M15 bar. Owner
# asked for it to survive zigzag markets; measured over 516 days it cut the
# worst chop quarter from -4,255.64 to -1,723.72 (-59%), was near-neutral in
# trending quarters, and turned the full period from -2,234.95 to +1,679.70.
# --bias-ema 0 turns it off (the pre-2026-08-20 replay).
# Price must CLEAR the bias EMA by this multiple of ATR(14), not merely sit on
# the correct side of it. Owner autopsy 2026-08-20: two losing sells that day
# passed the side-only gate by $0.46 and $1.85 -- in chop the M15 EMA sits
# exactly where price is, so "which side?" answers yes on a coin flip.
# Measured over 516 days at $10k (M5, c=2, M15 EMA-55 skip): every buffer from
# 1.0 to 5.0 x ATR is positive in BOTH halves. 2.0 is the middle of that
# plateau, chosen over the 3.0 peak (+$14,168) precisely because a lone spike
# ~3x its neighbours is what overfitting looks like.
#   buffer 0.0: H1 -1861.15  H2 +4456.35  full +1679.70
#   buffer 2.0: H1 +1324.40  H2 +3427.33  full +4860.44   <- default
#   buffer 3.0: H1 +5984.05  H2 +4725.19  full +14168.17  <- peak, not trusted
# The buffer applies ONLY in chop (owner, 2026-08-20: "the filter is only
# supposed to work in that zigzaggy market times"). efficiency = |net move| /
# total path over CHOP_EFF_BARS closed bars: 1.0 = a straight line, under ~0.10
# is textbook chop. Above the threshold the M15 test degrades to side-only.
# Measured 17 months, M5, $10k, ht lane -- a genuine plateau, not a spike:
#   never     H1 -1861.15  H2 +4675.39  full  +1498.72
#   always    H1 +1324.40  H2 +3524.56  full  +4781.54
#   eff<0.06  H1  +527.29  H2 +9543.06  full +11184.42
#   eff<0.08  H1  +401.27  H2 +9416.09  full +11349.93   <- default, mid-plateau
#   eff<0.10  H1  +368.09  H2 +9937.24  full +11453.73
#   eff<0.15  H1 +1686.53  H2 +5015.65  full  +7125.89
# Higher thresholds trade total return for a stronger older half; 0.06-0.10 sit
# within 2.5% of each other, so 0.08 is chosen as the middle of the flat region.
CHOP_EFF_BARS = _CFG["htf_chop_bars"]        # 4 hours of M5
# Above this efficiency the tape is TRENDING and the higher-timeframe check is
# skipped entirely -- not merely relaxed to a side test. Owner's design, stated
# repeatedly: M15 confirmation is a chop tool and must not gate a trend.
# Measured 17 months, M5, $10k, ht lane, M15 check OFF in trends:
#   eff<0.08  H1 -1371.15  H2 +9693.90  full +7380.53   <- default
#   eff<0.12  H1  +654.62  H2 +3916.71  full +4046.18
#   eff<0.16  H1  -360.70  H2 +1882.24  full +1508.53
#   eff<0.25  H1  +230.84  H2  -111.88  full  +911.01
# Widening the chop definition filters MORE and earns LESS, which is the same
# finding from the other direction: the check only pays inside real chop.
# For comparison, running its side test all day scores full +11349.93 with a
# weaker recent half (+8837.28) -- kept available via --chop-eff-max 0.
CHOP_EFF_MAX = _CFG["htf_chop_eff_max"]       # 0 = run the check all day (pre-2026-08-21)
BIAS_BUFFER_ATR = _CFG["htf_confirm_buffer_atr"]
BIAS_EMA = _CFG["htf_confirm_ema"]
BIAS_MODES = ("tag", "target", "target_lock", "size_target", "skip")
BIAS_MODE = "skip"
BIAS_TFS = ("M5", "M15")
BIAS_TF = "M15"
BIAS_TARGET_MULT = 0.5    # counter-trend profit target multiplier
BIAS_RISK_MULT = 0.5      # counter-trend risk multiplier (size_target only)
M15_SEC = 900


class _Bar:
    __slots__ = ("h", "l", "c")

    def __init__(self, x):
        self.h, self.l, self.c = x["h"], x["l"], x["c"]


def resample(candles, sec):
    """Aggregate the M5 source feed into `sec`-second bars (sec = SRC_SEC is a
    no-op returning the input unchanged). Buckets are t // sec, so on M15 they
    land on server :00/:15/:30/:45; open = the bucket's first open, high/low =
    the extremes, close = the last close, volume = the sum. Only a TRAILING
    incomplete bucket is dropped (its last source bar does not sit in the
    bucket's final SRC_SEC slot, i.e. the bar is still forming); mid-history
    buckets that are short a source bar are kept, because a missing M5 bar in
    the feed means no ticks in those five minutes, not a missing M15 bar."""
    if sec == SRC_SEC:
        return candles
    out, cur_b, off = [], None, None
    for x in candles:
        b = x["t"] // sec
        off = x["t"] % sec
        if b != cur_b:
            cur_b = b
            out.append({"t": b * sec, "o": x["o"], "h": x["h"], "l": x["l"],
                        "c": x["c"], "v": x["v"]})
        else:
            cur = out[-1]
            cur["h"] = max(cur["h"], x["h"])
            cur["l"] = min(cur["l"], x["l"])
            cur["c"] = x["c"]
            cur["v"] += x["v"]
    if out and off != sec - SRC_SEC:
        out.pop()
    return out


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


def chop_at(candles, ht, atr, i, flips=None, bars=None, box_atr=None):
    """Chop diagnostics for closed bar i: (is_chop, n_flips, box_ratio,
    flip_times). Flips counted on bars j in (i-bars, i] where ht[j].trend !=
    ht[j-1].trend; box = max high - min low over the same bars; box_ratio =
    box / ATR14[i] (None if ATR missing)."""
    flips = CHOP_FLIPS if flips is None else flips
    bars = CHOP_BARS if bars is None else bars
    box_atr = CHOP_BOX_ATR if box_atr is None else box_atr
    lo = max(1, i - bars + 1)
    n, ft = 0, []
    for j in range(lo, i + 1):
        if ht[j] and ht[j - 1] and ht[j][1] != ht[j - 1][1]:
            n += 1
            ft.append(hhmm(candles[j]["t"])[0])
    seg = candles[lo:i + 1]
    box = max(x["h"] for x in seg) - min(x["l"] for x in seg)
    ratio = (box / atr[i]) if atr[i] else None
    is_chop = flips > 0 and n >= flips and \
        (box_atr <= 0 or (ratio is not None and ratio < box_atr))
    return is_chop, n, ratio, ft


def bias_ema_series(candles, n, tf):
    """Per-M5-bar bias EMA value (None while warming up). tf M5: ema(closes,
    n). tf M15: resample to M15 buckets (t // 900); a bucket closes on its
    last M5 bar (the next bar belongs to a later bucket, or the bar sits at
    the bucket's last 5-minute slot); each M5 bar reads the EMA of the last
    COMPLETED M15 bar (iMA(M15, shift 1) at the M5 close moment)."""
    if tf == "M5":
        return ema([x["c"] for x in candles], n)
    m15_closes, out, last = [], [None] * len(candles), None
    k = 2.0 / (n + 1.0)
    for i, x in enumerate(candles):
        b = x["t"] // M15_SEC
        nxt = candles[i + 1]["t"] // M15_SEC if i + 1 < len(candles) else None
        closes_now = nxt != b or (x["t"] % M15_SEC) == M15_SEC - 300
        if closes_now:
            m15_closes.append(x["c"])
            if len(m15_closes) == n:
                last = sum(m15_closes) / n
            elif len(m15_closes) > n:
                last = x["c"] * k + last * (1.0 - k)
        out[i] = last
    return out


def bias_flips_per_day(candles, series, m15_only=False):
    """Mean number of bias sign changes per server day. m15_only counts only
    at bars that close an M15 bucket (the M15 clock); otherwise every M5 bar."""
    days, flips, prev = set(), 0, None
    for i, x in enumerate(candles):
        e = series[i]
        if e is None:
            continue
        if m15_only:
            b = x["t"] // M15_SEC
            nxt = candles[i + 1]["t"] // M15_SEC if i + 1 < len(candles) else None
            if not (nxt != b or (x["t"] % M15_SEC) == M15_SEC - 300):
                continue
        side = 1 if x["c"] > e else (-1 if x["c"] < e else prev)
        days.add(hhmm(x["t"])[0].date())
        if prev is not None and side is not None and side != prev:
            flips += 1
        prev = side
    return flips / max(1, len(days)), flips, len(days)


def sr_context(candles):
    """Per-bar (prev_day_high, prev_day_low, session_high, session_low) in
    server time. The session values cover the bars STRICTLY BEFORE bar i (so
    the entry bar's own high can never masquerade as overhead resistance —
    it is >= the close by construction); they are None on a day's first bar.
    The prev-day values come from the last COMPLETED server day."""
    out = [None] * len(candles)
    cur_day, prev_hl, shi, slo = None, (None, None), None, None
    for i, x in enumerate(candles):
        d = hhmm(x["t"])[0].date()
        if d != cur_day:
            if cur_day is not None:
                prev_hl = (shi, slo)
            out[i] = (prev_hl[0], prev_hl[1], None, None)
            cur_day, shi, slo = d, x["h"], x["l"]
        else:
            out[i] = (prev_hl[0], prev_hl[1], shi, slo)
            shi, slo = max(shi, x["h"]), min(slo, x["l"])
    return out


def sr_levels_at(candles, i, ctx, n, a, k=None, dedup_atr=None):
    """Sorted, de-duplicated level list visible at the close of bar i: fractal
    swing highs/lows over the last n bars (confirmed only, j + k <= i) plus
    the previous day's and current session's high/low from ctx. Levels closer
    than dedup_atr x ATR(14) to the previous kept level are dropped."""
    k = SR_PIVOT_K if k is None else k
    lv = []
    for j in range(max(k, i - n + 1), i - k + 1):
        hj, lj = candles[j]["h"], candles[j]["l"]
        if all(hj >= candles[j + d]["h"] for d in range(-k, k + 1) if d):
            lv.append(hj)
        if all(lj <= candles[j + d]["l"] for d in range(-k, k + 1) if d):
            lv.append(lj)
    lv.extend(v for v in ctx[i] if v is not None)
    if not lv:
        return []
    lv.sort()
    tol = (SR_DEDUP_ATR if dedup_atr is None else dedup_atr) * (a or 0.0)
    out = [lv[0]]
    for v in lv[1:]:
        if v - out[-1] > tol:
            out.append(v)
    return out


def headroom_atr(levels, px, direction, a):
    """Distance from px to the nearest opposing level, in ATR(14) units.
    BUY: nearest level at or above px; SELL: nearest at or below. None when
    nothing lies ahead (a clear path) or ATR is unavailable."""
    if not a:
        return None
    ahead = [v for v in levels if v >= px] if direction == "BUY" \
        else [v for v in levels if v <= px]
    if not ahead:
        return None
    d = (min(ahead) - px) if direction == "BUY" else (px - max(ahead))
    return d / a


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


# --- lane plug-in contract --------------------------------------------------
# A lane is a self-contained trading unit that runs on the shared account
# without reaching into another lane's state. QuickFlip was the reference
# implementation (spec docs/superpowers/specs/2026-08-20-quickflip-ny-design.md);
# dropped 2026-08-22 as a paid experiment (+$118 marginal over 17 months,
# ~$7/month -- see the STATUS note at the top of that spec). HalfTrend stays
# inline in run() (tangled with basket/pyramiding/SR/bias state -- see the
# note above the main loop), so this contract is deliberately the floor a
# lane needs to clear, not everything run() happens to offer HalfTrend --
# kept live (LANES has just the one entry below) so a future second lane
# plugs back in here without CLI/report surgery.
class Account:
    """The shared account surface a plug-in lane may touch.

    `bal` is a read-only view of the CURRENT shared balance (both lanes'
    realized P/L folded in, in whichever order their trades actually
    closed). `realize(trade)` is the only way a lane may spend money: it
    appends `trade` to the shared trade list and folds `trade["pl"]` into
    bal/peak_bal/max_dd -- the same three numbers close_basket() updates
    for the (inline) HalfTrend lane, so both paths keep one ledger. A lane
    must never touch bal/trades/peak_bal/max_dd directly.
    """
    __slots__ = ("_get_bal", "_realize")

    def __init__(self, get_bal, realize):
        self._get_bal = get_bal
        self._realize = realize

    @property
    def bal(self):
        return self._get_bal()

    def realize(self, trade):
        self._realize(trade)


class Lane:
    """Minimal per-bar contract a plug-in lane must satisfy to run inside
    run()'s loop alongside (inline) HalfTrend, and to be folded into the
    open-equity valley every bar without run() needing to know it exists.

    step(i, candles, account) is called once per closed bar, for every
    ACTIVE lane, before the HalfTrend block runs. It may open/manage/close
    the lane's own position(s), calling account.realize() for each close.

    floating_pl(px) returns the lane's CURRENT unrealized P/L at price px
    (0.0 if flat). mark_equity() -- the one place the open-equity valley is
    computed, for every active lane, every bar -- sums this over `lanes`
    unconditionally. THAT is what keeps the mark-every-bar invariant from
    being an accident of code order: see mark_equity()'s docstring for the
    bug that shipped when the marker instead lived inside the HalfTrend
    code path (a plug-in-lane-only run reported a fabricated valley of
    0.00 -- caught with QuickFlip, the plug-in lane this contract was
    built for; see the removal note above the class).
    """
    id = None

    def step(self, i, candles, account):
        raise NotImplementedError

    def floating_pl(self, px):
        raise NotImplementedError


# ht has no factory: it is not a plug-in, it is orchestrated inline in
# run() (tangled with basket/pyramiding/SR/bias state -- see the note above
# the main loop). Its entry keeps LANES the one place that lists every lane
# id backtest.py knows about, including the one that is not pluggable yet --
# so a second (plug-in) lane is an entry here, not an edit to a conditional.
LANES = {
    "ht": None,
}


def lanes_for(strategy=None):
    """The set of active lane ids. `strategy` is accepted (and ignored) for
    backward compatibility with call sites written when --strategy selected
    among multiple lanes (ht/qf/both); with only `ht` registered in LANES
    there is nothing left to select, so this always returns every lane.
    Kept as a function -- not inlined as `set(LANES)` at each call site --
    so a second lane's caller-side code needs no change, only a LANES entry."""
    return set(LANES)


def run(candles, start_balance, verbose, active_lanes=None):
    closes = [x["c"] for x in candles]
    ema55 = ema(closes, EMA_LEN)
    ht = halftrend(
        [type("C", (), x)() for x in candles], amplitude=AMPLITUDE)
    atr, adx = atr_adx(candles)
    sr_ctx = sr_context(candles) if SR_LOOKBACK > 0 else None
    bias_ema = bias_ema_series(candles, BIAS_EMA, BIAS_TF) if BIAS_EMA > 0 \
        else None

    active_lanes = set(LANES) if active_lanes is None else active_lanes

    bal = start_balance
    basket = None          # dict: dir, legs[{px,oz}], stop, peak, cycle_bal
    fired_flip = None      # flip index already traded
    last_flip = None
    extreme = None
    consec_above = consec_below = 0   # EA fake-out counters (ConfirmCloses)
    trades = []

    # --- plug-in lanes: which are active this run, instantiated once -------
    # LANES lists every lane id backtest.py knows (ht's factory is None: it
    # is not a plug-in, see the note by LANES); today that leaves this empty
    # ({ht} has no factory), but the loop stays so a second plug-in lane
    # needs no change here, only an entry in LANES.
    lanes = [factory(candles, verbose) for lane_id, factory in LANES.items()
             if factory is not None and lane_id in active_lanes]

    sizing = {"entries": 0, "clamped": 0, "risk_pct": []}
    peak_bal, max_dd = bal, 0.0
    peak_eq, max_valley = bal, 0.0     # open-equity (close-based) valley
    expo = {}              # server-day -> minutes of open-position time
    skipped = []           # entries a gate refused: (when, dir, reason)
    open_diff_bars = []    # confirm-mode open: bars where open[i+1] vs EMA
                           # landed on a different side than close[i]
    dead_signals = []      # strict window: (when, dir) flips whose decision
                           # bar closed on the wrong side -> no entry ever

    def basket_pl(px):
        s = 1 if basket["dir"] == "BUY" else -1
        return sum((px - l["px"]) * s * l["oz"] for l in basket["legs"]) \
            - SPREAD_USD * sum(l["oz"] for l in basket["legs"])

    def close_basket(px, when, why):
        nonlocal bal, basket, peak_bal, max_dd
        pl = basket_pl(px)
        bal += pl
        trades.append({"lane": "ht",
                       "dir": basket["dir"], "legs": list(basket["legs"]),
                       "exit": px, "when": when, "why": why, "pl": pl,
                       "opened_t": basket.get("opened_t"),
                       "exit_t": int(when.timestamp()),
                       "stop_history": list(basket.get("stop_history", [])),
                       "tp": basket.get("tp"),
                       "bal_after": bal,
                       "opened": basket.get("opened"),
                       "regime": basket.get("regime"),
                       "atr_ratio": basket.get("atr_ratio"),
                       "floor": basket.get("floor"),
                       "chop": basket.get("chop"),
                       "chop_flips": basket.get("chop_flips"),
                       "chop_box": basket.get("chop_box"),
                       "soft": basket.get("soft", False),
                       "flip_t": basket.get("flip_t"),
                       "entry_offset": basket.get("entry_offset"),
                       "dist_atr": basket.get("dist_atr"),
                       "floored": basket.get("floored", False),
                       "orig_stop": basket.get("orig_stop"),
                       "orig_dist": basket.get("orig_dist"),
                       "orig_oz": basket.get("orig_oz"),
                       "entry_stop": basket.get("entry_stop"),
                       "orig_hit_bar": basket.get("orig_hit_bar"),
                       "bars_open": basket.get("bars_open", 0),
                       "bias": basket.get("bias"),
                       "bias_ema": basket.get("bias_ema"),
                       "headroom": basket.get("headroom"),
                       "cycle_bal": basket["cycle_bal"]})
        peak_bal = max(peak_bal, bal)
        max_dd = max(max_dd, peak_bal - bal)
        if verbose:
            legs = "+".join(f"{l['oz']}oz@{l['px']:.2f}" for l in basket["legs"])
            print(f"  close {when:%m-%d %H:%M} {basket['dir']} [{legs}] "
                  f"@ {px:.2f} {why:>14}  P/L {pl:+8.2f}  bal {bal:9.2f}")
        basket = None

    def _realize(trade):
        """Account.realize(): fold a plug-in lane's closed trade into the
        shared bal/trades/peak_bal/max_dd -- the same three numbers
        close_basket() above updates for the (inline) HalfTrend lane, so
        both paths keep one ledger."""
        nonlocal bal, peak_bal, max_dd
        bal += trade["pl"]
        trade["bal_after"] = bal
        trades.append(trade)
        peak_bal = max(peak_bal, bal)
        max_dd = max(max_dd, peak_bal - bal)

    account = Account(get_bal=lambda: bal, realize=_realize)

    def mark_equity(px):
        """Mark the open-equity valley at this bar's close, in EVERY mode.

        EVERY active lane's floating P/L counts, because the valley
        describes the ACCOUNT: an account $300 under water on a plug-in
        lane's position is $300 under water whether or not HalfTrend also
        holds a basket. This used to live inside the HalfTrend section,
        BELOW a lane-selection short-circuit, so a plug-in-lane-only run
        reported a fabricated valley of 0.00 (caught with QuickFlip, the
        plug-in lane this invariant was built for and later dropped --
        see the LANES note above). Summing over `lanes` (rather than
        naming a lane) is what keeps a future second lane covered by this
        invariant automatically, with no edit here.
        """
        nonlocal peak_eq, max_valley
        eq = bal + (basket_pl(px) if basket else 0.0) \
            + sum(ln.floating_pl(px) for ln in lanes)
        peak_eq = max(peak_eq, eq)
        max_valley = max(max_valley, peak_eq - eq)

    def note_stop(bk, t, stop):
        """Append to the basket's stop history when the stop actually moved."""
        hist = bk["stop_history"]
        if not hist or hist[-1]["stop"] != stop:
            hist.append({"t": int(t), "stop": stop})

    for i in range(EMA_LEN + AMPLITUDE + 2, len(candles)):
        # ---- plug-in lanes (independent of HalfTrend below) ----------------
        for ln in lanes:
            ln.step(i, candles, account)

        if "ht" not in active_lanes:
            # Same place in the bar as the HalfTrend path's mark below: after
            # this bar has been fully processed. Nothing under this point
            # runs when HalfTrend isn't active -- but every ACTIVE lane's
            # floating P/L still gets marked (see mark_equity()'s docstring):
            # this is not conditioned on which lanes those are.
            mark_equity(candles[i]["c"])
            continue

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
            expo[day] = expo.get(day, 0) + BAR_MIN   # one bar of held time

        # ---- flatten before the break
        if h == FLATTEN_HM[0] and m >= FLATTEN_HM[1]:
            if basket:
                close_basket(px, when, "flatten")
            continue

        signal = None
        if fired_flip != last_flip:
            if STRICT_WINDOW:
                # one-shot decision on the bar CONFIRM_CLOSES bars after the
                # arrow bar (i - last_flip == CONFIRM_CLOSES); this bar's
                # close is the entry bar's open. Pass -> signal now; fail ->
                # dead until the next flip (fired_flip latched either way).
                if i - last_flip == CONFIRM_CLOSES:
                    if trend == 0 and cpx > e:
                        signal = "BUY"
                    elif trend == 1 and cpx < e:
                        signal = "SELL"
                    else:
                        dead_signals.append((when, "BUY" if trend == 0 else "SELL"))
                    fired_flip = last_flip
            else:
                if trend == 0 and cpx > e and consec_above >= CONFIRM_CLOSES:
                    signal = "BUY"
                elif trend == 1 and cpx < e and consec_below >= CONFIRM_CLOSES:
                    signal = "SELL"
                if signal:
                    fired_flip = last_flip

        # ---- manage open basket
        if basket:
            s = 1 if basket["dir"] == "BUY" else -1
            # bar count since entry + would the ORIGINAL (un-floored) stop
            # have been touched intrabar on this bar? (diagnostic only)
            basket["bars_open"] = basket.get("bars_open", 0) + 1
            if basket.get("orig_hit_bar") is None and \
                    basket.get("orig_stop") is not None:
                ohit = x["l"] <= basket["orig_stop"] if s == 1 \
                    else x["h"] >= basket["orig_stop"]
                if ohit:
                    basket["orig_hit_bar"] = basket["bars_open"]
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
                risk_budget = basket["cycle_bal"] * RISK_PCT / 100 \
                    * basket.get("lock_mult", 1.0)
                target = basket["cycle_bal"] * PROFIT_TARGET_PCT / 100 \
                    * basket.get("target_mult", 1.0)
                # Recomputed EVERY bar, not frozen at the first leg: each
                # pyramid add moves the price at which the basket reaches
                # `target`, so a one-shot tp drew a green zone further out
                # than the level actually banked (median $2.89, max $34.69
                # off on multi-leg target exits over 12 months). `tp` feeds
                # the artifact and the page only -- nothing decides on it.
                if ENTRY_MODE != "fixed" and PROFIT_TARGET_PCT > 0:
                    basket["tp"] = floor_price(basket["legs"], s, target)
                closed = False
                if ENTRY_MODE == "fixed" or PROFIT_TARGET_PCT <= 0:
                    pass   # pure ride: no profit target / floor (EA: 0 = off)
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
                            note_stop(basket, x["t"], fpx)
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
                    frozen = (basket.get("floor") is not None
                              and EXIT_SCHEME != "floor-a-adds") \
                        or basket.get("soft", False)
                    cond = (basket["dir"] == "BUY" and trend == 0 and px > e) or \
                           (basket["dir"] == "SELL" and trend == 1 and px < e)
                    adv = (px - basket["legs"][-1]["px"]) * s
                    if (not frozen and cond and pl > 0
                            and len(basket["legs"]) < MAX_POSITIONS
                            and adv >= ADD_TRIGGER_ATR * a):
                        oz = max(MIN_OZ, int(basket["legs"][-1]["oz"] * ADD_SHRINK))
                        basket["legs"].append({"px": px, "oz": oz, "t": int(x["t"])})
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
                                note_stop(basket, x["t"], ladder)
                        else:
                            basket["stop"] = ladder
                            note_stop(basket, x["t"], ladder)
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
                chop, nfl, boxr, ftimes = (False, 0, None, [])
                soft = False
                if CHOP_FLIPS > 0:
                    chop, nfl, boxr, ftimes = chop_at(candles, ht, atr, i)
                    if chop and verbose:
                        print(f"  chop  {when:%m-%d %H:%M} {signal} flips={nfl} "
                              f"box/atr={boxr if boxr is None else round(boxr, 2)} "
                              f"flip times "
                              f"{', '.join(f'{f:%m-%d %H:%M}' for f in ftimes)}")
                    if chop and not blocked and CHOP_MODE == "skip":
                        blocked = "chop"
                    elif chop and CHOP_MODE == "soft":
                        soft = True
                bias, bval = None, None
                if bias_ema is not None and bias_ema[i] is not None:
                    bval = bias_ema[i]
                    if px == bval:
                        bias = "with"
                    else:
                        buf = BIAS_BUFFER_ATR * (a or 0.0)
                        # NAME THIS SEPARATELY. It was called `trending`, which
                        # SHADOWED the ADX entry gate set ~30 lines above and
                        # still read by the final `if in_window and trending
                        # and expo_ok and signal:`. Effect: in choppy tape this
                        # went False and blocked the entry OUTRIGHT, whatever
                        # M15 said -- so the replay was really doing "never
                        # trade in chop", not "require M15 clearance in chop".
                        # Found 2026-08-22 by the characterization suite built
                        # before the HalfTrend lane extraction. The live EA
                        # never had this: HalfTrendEma.mqh keeps the verdict
                        # (HtfAgrees) and the enforcement (HtfEnforced)
                        # separate, so the replay was UNDERSTATING the EA.
                        tape_trending = False
                        if CHOP_EFF_MAX > 0:
                            seg = candles[max(0, i - CHOP_EFF_BARS):i + 1]
                            path = sum(abs(seg[q]["c"] - seg[q - 1]["c"])
                                       for q in range(1, len(seg)))
                            eff = (abs(seg[-1]["c"] - seg[0]["c"]) / path) if path else 1.0
                            tape_trending = eff > CHOP_EFF_MAX
                        if tape_trending:
                            # Trending tape: the higher-timeframe check does
                            # NOT run at all. It is a chop tool; letting even
                            # its side test gate a trend costs entries the
                            # trend would have paid for.
                            bias = "with"
                        elif signal == "BUY":
                            bias = "with" if px > bval + buf else "counter"
                        else:
                            bias = "with" if px < bval - buf else "counter"
                    if bias == "counter" and BIAS_MODE == "skip" and not blocked:
                        blocked = "counter-trend"
                hr = None
                if sr_ctx is not None:
                    hr = headroom_atr(
                        sr_levels_at(candles, i, sr_ctx, SR_LOOKBACK, a),
                        px, signal, a)
                    if (SR_MIN_HEADROOM > 0 and not SR_REPORT and not blocked
                            and hr is not None and hr < SR_MIN_HEADROOM):
                        blocked = f"headroom<{SR_MIN_HEADROOM:g}"
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
                orig_stop, orig_dist, floored = stop, dist, False
                if MIN_STOP_ATR > 0 and dist < MIN_STOP_ATR * a:
                    # floor: never closer than K x ATR(14) from the fill
                    dist = MIN_STOP_ATR * a
                    stop = px - dist if signal == "BUY" else px + dist
                    floored = True
                if dist > 0:
                    if ENTRY_MODE == "fixed":
                        oz = max(MIN_OZ, int(round(FIXED_LOTS * 100)))
                        orig_oz = oz
                    else:
                        rp = RISK_PCT / (CHOP_SOFT_RISK_DIV if soft else 1.0)
                        if bias == "counter" and BIAS_MODE == "size_target":
                            rp *= BIAS_RISK_MULT
                        risk = bal * rp / 100
                        want = int(risk / dist)
                        oz = max(MIN_OZ, want)
                        sizing["entries"] += 1
                        if want < MIN_OZ:
                            sizing["clamped"] += 1
                        sizing["risk_pct"].append(100.0 * oz * dist / bal)
                        orig_oz = max(MIN_OZ, int(risk / orig_dist)) \
                            if orig_dist > 0 else oz
                    basket = {"dir": signal,
                              "legs": [{"px": px, "oz": oz, "t": int(x["t"])}],
                              "stop": stop, "peak": 0.0, "cycle_bal": bal,
                              "stop_history": [{"t": int(x["t"]), "stop": stop}],
                              "opened_t": int(x["t"]),
                              "dist_atr": (orig_dist / a) if a else None,
                              "floored": floored, "orig_stop": orig_stop,
                              "orig_dist": orig_dist, "orig_oz": orig_oz,
                              "entry_stop": stop, "bars_open": 0,
                              "orig_hit_bar": None,
                              "opened": when, "regime": regime,
                              "atr_ratio": aratio, "chop": chop,
                              "chop_flips": nfl, "chop_box": boxr,
                              "soft": soft,
                              "headroom": hr,
                              "flip_t": candles[last_flip]["t"],
                              "entry_offset": i - last_flip,
                              "bias": bias, "bias_ema": bval}
                    if bias == "counter" and BIAS_MODE in (
                            "target", "target_lock", "size_target"):
                        # basket-sticky: decided once, here, at entry
                        basket["target_mult"] = BIAS_TARGET_MULT
                        if BIAS_MODE == "target_lock":
                            basket["lock_mult"] = BIAS_TARGET_MULT
                        elif BIAS_MODE == "size_target":
                            basket["lock_mult"] = BIAS_RISK_MULT
                    if verbose:
                        print(f"  open  {when:%m-%d %H:%M} {signal} {oz}oz "
                              f"@ {px:.2f} stop {stop:.2f} (dist {dist:.2f})"
                              f"{' SOFT (half risk, no adds)' if soft else ''}"
                              + (f" bias {bias} (ema{BIAS_EMA} {bval:.2f}"
                                 f"{', target x' + format(BIAS_TARGET_MULT, 'g') if basket.get('target_mult') else ''}"
                                 f"{', lock x' + format(basket['lock_mult'], 'g') if basket.get('lock_mult') else ''})"
                                 if bias else "")
                              + (f" FLOORED from {orig_stop:.2f} "
                                 f"(dist {orig_dist:.2f}, {orig_oz}oz)"
                                 if floored else ""))

        mark_equity(px)          # open-equity valley, every active lane included

    if basket:
        close_basket(candles[-1]["c"], hhmm(candles[-1]["t"])[0], "eod-open")
    run.skipped = skipped
    run.expo = expo          # server-day -> minutes of open-position time
    run.open_diff_bars = open_diff_bars
    run.dead_signals = dead_signals
    run.bias_flips = None
    if BIAS_EMA > 0:
        run.bias_flips = {
            "M5": bias_flips_per_day(candles, bias_ema_series(candles, BIAS_EMA, "M5")),
            "M15": bias_flips_per_day(candles, bias_ema_series(candles, BIAS_EMA, "M15"), True)}
    r = sorted(sizing["risk_pct"])
    n = sizing["entries"]
    # None, not 0.0, when nothing was risk-sized (--entry-mode fixed sizes
    # every entry at --fixed-lots): "0.00% risk taken" reads as "we risked
    # nothing", when the truth is that risk sizing never ran.
    run.sizing = {
        "entries": n,
        "clamped": sizing["clamped"],
        "clamp_pct": round(100.0 * sizing["clamped"] / n, 1) if n else None,
        "risk_median": round(r[len(r) // 2], 2) if r else None,
        "risk_p90": round(r[int(0.9 * len(r))], 2) if r else None,
    }
    return trades, bal, max_dd, max_valley


def _lane_drawdown(rows):
    """Max peak-to-trough of ONE lane's own realized P/L curve, in dollars.

    Deliberately the lane's own curve, not the account's: the account
    drawdown is a joint number and already reported as `max drawdown`. This
    answers "how deep did THIS strategy dig on its own?" -- distinct from
    the account's number, and not meant to add up to it (it did not, back
    when a second lane shared the balance -- see LANES).
    """
    peak = run_sum = 0.0
    dd = 0.0
    for t in sorted(rows, key=lambda t: t["exit_t"]):
        run_sum += t["pl"]
        peak = max(peak, run_sum)
        dd = max(dd, peak - run_sum)
    return round(dd, 2)


def _lane_stats(trades):
    """Per-lane breakdown, keyed by lane id (not flattened) so a future
    second lane -- and any per-lane comparison, like the QuickFlip-vs-
    HalfTrend overlap count this used to report -- drops back into
    `stats.lanes` / the HTML report's per-lane block without reshaping
    either consumer. Only `ht` is registered today (see LANES)."""
    out = {}
    for lane in LANES:
        rows = [t for t in trades if t.get("lane", "ht") == lane]
        wins = sum(1 for t in rows if t["pl"] > 0)
        out[lane] = {
            "trades": len(rows),
            "wins": wins,
            "losses": sum(1 for t in rows if t["pl"] < 0),
            "win_rate": round(100.0 * wins / len(rows), 1) if rows else 0.0,
            "net": round(sum(t["pl"] for t in rows), 2),
            "max_dd": _lane_drawdown(rows),
            "best": round(max((t["pl"] for t in rows), default=0.0), 2),
            "worst": round(min((t["pl"] for t in rows), default=0.0), 2),
        }
    return out


def _run_fingerprint(candles) -> str:
    """Short hash of the exact bars a run measured. The header and the --json
    artifact both use THIS function so they can never disagree."""
    return hashlib.sha256(
        json.dumps([[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in candles],
                   separators=(",", ":")).encode()).hexdigest()[:12]


def build_run_json(candles, trades, args, res):
    """The run artifact (spec 2026-08-20 section 2). Parallel arrays, not
    per-bar objects: 12 months of M5 is ~74k bars, and the array form roughly
    halves the payload with no loss of detail."""
    closes = [x["c"] for x in candles]
    ht = halftrend([type("C", (), x)() for x in candles], amplitude=AMPLITUDE)
    r2 = lambda v: None if v is None else round(v, 2)   # noqa: E731
    sizing = getattr(run, "sizing", {}) or {}
    n = len(trades)
    wins = sum(1 for t in trades if t["pl"] > 0)
    losses = sum(1 for t in trades if t["pl"] < 0)   # pl == 0 is flat
    net = res["bal"] - args.balance
    return {
        "meta": {
            "generated_at": int(dt.datetime.now(dt.UTC).timestamp()),
            "source": args.source, "tf": TF, "bars": len(candles),
            # see the DATASET FINGERPRINT note in main(): the source file is
            # mutable, so a result is only reproducible against the dataset
            # that produced it
            "dataset": _run_fingerprint(candles),
            "start": int(candles[0]["t"]), "end": int(candles[-1]["t"]),
            "strict_window": STRICT_WINDOW,
            "entry_mode": ENTRY_MODE,
            "args": {k: v for k, v in vars(args).items() if v is not None},
            "caveats": CAVEATS,
        },
        "stats": {
            "trades": n, "wins": wins, "losses": losses,
            "flat": n - wins - losses,
            "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
            "net": round(net, 2),
            "start_balance": round(args.balance, 2),
            "end_balance": round(res["bal"], 2),
            "max_dd": round(res["max_dd"], 2),
            "max_valley": round(res["valley"], 2),
            "best": round(max((t["pl"] for t in trades), default=0.0), 2),
            "worst": round(min((t["pl"] for t in trades), default=0.0), 2),
            # null when no entry was risk-sized -- the page renders "n/a"
            "clamp_pct": sizing.get("clamp_pct"),
            "risk_median": sizing.get("risk_median"),
            "risk_p90": sizing.get("risk_p90"),
            "lanes": _lane_stats(trades),
        },
        "candles": {
            "t": [int(x["t"]) for x in candles],
            "o": [r2(x["o"]) for x in candles],
            "h": [r2(x["h"]) for x in candles],
            "l": [r2(x["l"]) for x in candles],
            "c": [r2(x["c"]) for x in candles],
        },
        "ind": {
            "ema9": [r2(v) for v in ema(closes, 9)],
            "ema21": [r2(v) for v in ema(closes, 21)],
            "ema55": [r2(v) for v in ema(closes, 55)],
            "ema200": [r2(v) for v in ema(closes, 200)],
            "ht": {"v": [r2(p[0]) if p else None for p in ht],
                   "trend": [p[1] if p else None for p in ht]},
        },
        "trades": [{
            "lane": t.get("lane", "ht"),
            "dir": t["dir"],
            "legs": [{"t": leg["t"], "px": r2(leg["px"]), "oz": leg["oz"]}
                     for leg in t["legs"]],
            "tp": r2(t.get("tp")),
            "stop_history": [{"t": h["t"], "stop": r2(h["stop"])}
                             for h in t["stop_history"]],
            "exit": r2(t["exit"]), "exit_t": t["exit_t"], "why": t["why"],
            "pl": round(t["pl"], 2), "bal_after": round(t["bal_after"], 2),
            "regime": t.get("regime"),
        } for t in trades],
    }


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
        # the entry bar is RECORDED (legs[0]["t"]), so the span is the trade's
        # real span -- it used to be drawn as a fixed 12 bars back from the
        # exit, which invented a duration for every trade on the chart
        entry_i = idx.get(int(t["legs"][0]["t"]), exit_i)
        color = "#2ecc71" if t["pl"] > 0 else "#e74c3c"
        ax.axvspan(entry_i, exit_i, color=color, alpha=0.10)
        m = "^" if t["dir"] == "BUY" else "v"
        ax.scatter([exit_i], [t["exit"]], marker="x", color=color, s=22, zorder=5)
        ax.scatter([entry_i], [t["legs"][0]["px"]], marker=m,
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


def build_parser():
    ap = argparse.ArgumentParser(
        description="Replay halftrend_ema_v1 with the current money rulebook "
                    "over historical candles and report P/L.",
        epilog="NOT MODELLED:\n  " + "\n  ".join(CAVEATS) +
               "\n\nSTARTING BALANCE: $10,000+ for a clean test of the risk "
               "rules. $4,000 still clamps\nroughly one entry in six to the "
               "0.01 minimum lot -- enough to trip this tool's\nown \"results "
               "distorted\" flag -- and below $2,000 clamping dominates the "
               "result\nentirely. Below $500 the run is refused. Measured "
               "2026-08-20 on the default\n(strict) window over the last 365 "
               "days: entries clamped 94.7% at $500, 47.0%\nat $1,200, 32.3% "
               "at $2,000, 16.7% at $4,000, 1.3% at $10,000, 0.0% at "
               "$25,000.\nEvery run prints its OWN clamp rate: that is the "
               "number to read, since it depends\non how wide the stops were "
               "in the window tested.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    data = ap.add_argument_group("Data")
    rules = ap.add_argument_group(
        "Rules", "the EA's real knobs -- defaults are what the live EA does")
    exp = ap.add_argument_group(
        "Experiments",
        "study knobs. All default to the live EA's behaviour: the filters "
        "(regime/atr-spike/chop/bias/sr/min-stop) default to OFF because the "
        "live EA has none, but --window-start/--window-end are the EA's REAL "
        "trading window and default to its live 4-23")
    out = ap.add_argument_group("Output")

    data.add_argument("--balance", type=float, default=4000,
                    help="starting account balance in USD (default 4000). "
                         "Drives 1%%-risk sizing and the 2%%-of-cycle profit "
                         "target, so it changes the RESULT, not just the "
                         "scale: below $10,000 some entries clamp to the 0.01 "
                         "minimum lot and over-risk (see STARTING BALANCE "
                         "below). Refused below $500")
    data.add_argument("--source", default="http://127.0.0.1:9000/ui/candles",
                    help="candle source: a URL serving the service's "
                         "/ui/candles JSON (default, capped at 2000 bars) or "
                         "the path to a saved JSON dump of the same shape "
                         "(e.g. bars_max.json, ~12 months of M5)")
    out.add_argument("--verbose", action="store_true",
                    help="print per-bar decision detail (entries, adds, stop "
                         "moves, floor arming) as the replay runs")
    rules.add_argument("--adx", type=float, default=None, help="override ADX gate")
    out.add_argument("--chart", default=None, metavar="PATH",
                    help="legacy PNG chart (price bars + equity curve, needs "
                         "matplotlib). --web is the maintained report: an "
                         "interactive, self-contained HTML page with the same "
                         "trades drawn against EMAs, HalfTrend and their "
                         "actual stop paths")
    out.add_argument("--json", default=None, metavar="PATH",
                     help="write the full run (candles, indicators, trades, "
                          "stats) to this JSON file")
    data.add_argument("--days", type=float, default=None,
                    help="backtest only the last N days of the source data")
    rules.add_argument("--expo", type=float, default=None,
                    help="override daily exposure minutes (0 = unlimited)")
    rules.add_argument("--risk", type=float, default=None,
                    help="override risk percent per trade")
    rules.add_argument("--confirm", type=int, default=None,
                    help="override ConfirmCloses (consecutive closes beyond "
                         "the EMA required after a flip; default 1)")
    rules.add_argument("--stop-buffer", type=float, default=None,
                    help="override the stop pad in ATR(14) multiples "
                         "(default 0.75)")
    rules.add_argument("--exit-scheme", choices=EXIT_SCHEMES, default="target-exit",
                    help="profit-floor experiment scheme (default: current "
                         "behavior, close at profit target)")
    rules.add_argument("--entry-mode", choices=["adr", "fixed"], default="adr",
                    help="adr = live behavior; fixed = fixed lots, no adds/"
                         "target/lock, exit on confirmed reversal or stop")
    rules.add_argument("--fixed-lots", type=float, default=0.05,
                    help="lots per entry in --entry-mode fixed (default 0.05); "
                         "ignored in the default adr mode, which risk-sizes")
    exp.add_argument("--regime-gate", choices=REGIME_GATES, default="off",
                    help="refuse new entries by the service's live regime "
                         "classifier: range = skip 'range' bars, "
                         "range-strict = only enter on 'trend' bars, "
                         "highvol = skip 'high_volatility' bars")
    exp.add_argument("--atr-spike-gate", type=float, default=0.0,
                    help="refuse new entries when ATR(14) > RATIO x its "
                         "median over the last 100 bars (0 = off)")
    data.add_argument("--start", default=None,
                    help="drop candles before this server time "
                         "(YYYY-MM-DD[THH:MM]); indicators warm up from here")
    data.add_argument("--end", default=None,
                    help="drop candles after this server time (YYYY-MM-DD[THH:MM])")
    rules.add_argument("--ema-len", type=int, default=None,
                    help="override the trading EMA length (default 55)")
    exp.add_argument("--confirm-mode", choices=CONFIRM_MODES, default="close",
                    help="close = EA behavior (closes beyond the EMA); open = "
                         "next bar's open beyond the EMA (same decision bar)")
    exp.add_argument("--chop-flips", type=int, default=0,
                    help="chop filter: HalfTrend flips within --chop-bars "
                         "that mark a bar as chop (0 = off)")
    exp.add_argument("--chop-bars", type=int, default=24,
                    help="chop filter lookback in closed bars (default 24)")
    exp.add_argument("--chop-box-atr", type=float, default=2.0,
                    help="chop filter: box over the lookback must be < X x "
                         "ATR(14) (default 2.0; 0 = flip count alone)")
    exp.add_argument("--chop-mode", choices=CHOP_MODES, default="skip",
                    help="skip = refuse chop entries (H1); soft = enter at "
                         "half risk with no adds (H2); off = tag/report only")
    rules.add_argument("--loose-window", action="store_true",
                    help="disable the EA's strict 3-bar entry window (flip -> "
                         "one waiting bar -> entry only if that bar opens "
                         "beyond the EMA). Use to reproduce studies run before "
                         "2026-08-20, when loose was the default.")
    rules.add_argument("--strict-window", action="store_true",
                    help="accepted and now a NO-OP: the strict entry window "
                         "became the default on 2026-08-20, so passing this "
                         "changes nothing. Kept so scripted runs written "
                         "before the flip still parse; --loose-window is what "
                         "restores the old replay")
    exp.add_argument("--min-stop-atr", type=float, default=0.0,
                    help="minimum stop distance floor in ATR(14) multiples: "
                         "an ENTRY stop closer than K x ATR is pushed out to "
                         "exactly K x ATR and lots are sized over the wider "
                         "distance (0 = off, byte-identical)")
    exp.add_argument("--chop-eff-max", type=float, default=0.08,
                     help="apply the M15 buffer ONLY when path efficiency over "
                          "--chop-eff-bars is below this (0 = always apply)")
    exp.add_argument("--chop-eff-bars", type=int, default=48,
                     help="bars in the path-efficiency window (48 = 4h of M5)")
    exp.add_argument("--bias-buffer-atr", type=float, default=2.0,
                     help="price must clear the bias EMA by this multiple of "
                          "ATR(14) before the trade counts as with-bias; 0 = "
                          "the side-only test used before 2026-08-20")
    exp.add_argument("--bias-ema", type=int, default=55,
                    help="EMA-N market bias at the entry bar (close vs EMA-N; "
                         "0 = off, byte-identical). Tags every trade "
                         "with/counter and prints the split")
    exp.add_argument("--bias-mode", choices=BIAS_MODES, default="skip",
                    help="tag = report only; target = counter-trend target "
                         "x0.5 (lock untouched, EA-literal); target_lock = "
                         "target x0.5 and lock arm x0.5; size_target = target "
                         "x0.5 and risk x0.5; skip = refuse counter-trend")
    exp.add_argument("--bias-tf", choices=BIAS_TFS, default="M15",
                    help="timeframe of the bias EMA: M5 (default) or M15 "
                         "(resampled, last completed M15 bar)")
    exp.add_argument("--window-start", type=int, default=None,
                    help="first server hour that may OPEN a trade "
                         "(EA TradingWindowStartHour, default 4)")
    exp.add_argument("--window-end", type=int, default=None,
                    help="first server hour that may NOT open a trade "
                         "(EA TradingWindowEndHour, default 23); exits and "
                         "the 23:50 flatten are never gated by the window")
    out.add_argument("--hour-table", action="store_true",
                    help="print the entry-hour breakdown (trades / win%% / "
                         "net / avg P/L per server hour 0-23)")
    exp.add_argument("--sr-lookback", type=int, default=0,
                    help="support/resistance: swing-pivot lookback in bars "
                         "(0 = off). Tags every entry with its headroom.")
    exp.add_argument("--sr-min-headroom", type=float, default=0.0,
                    help="skip entries whose headroom to the nearest opposing "
                         "level is below this many ATR(14) (0 = tag only)")
    out.add_argument("--sr-report", action="store_true",
                    help="S/R diagnostic: tag and report headroom but never "
                         "refuse an entry (overrides --sr-min-headroom)")
    data.add_argument("--tf", choices=TFS, default="M5",
                    help="trading timeframe (EA TradeTimeframe): M5 (default, "
                         "byte-identical) or M15 (the M5 source aggregated to "
                         "15-minute bars before anything else runs). Bar-based "
                         "parameters are then read in M15 bars; the exposure "
                         "budget and the trading-window hours keep their "
                         "wall-clock meaning")
    rules.add_argument("--profit-target", type=float, default=None,
                    help="override ProfitTargetPct, the basket's bank-at "
                         "percent of cycle balance (default 2.0; <= 0 turns "
                         "the target off exactly like the EA input, leaving "
                         "the lock / stop / reversal to close the basket)")
    out.add_argument("--web", default=None, metavar="PATH",
                     help="write a self-contained HTML report (chart with "
                          "HalfTrend/EMA overlays and every trade drawn with "
                          "its SL/TP and stop path) to this file")
    return ap


def apply_window_args(args):
    """Wire --loose-window (and the suppressed --strict-window no-op) into
    the STRICT_WINDOW runtime flag. Extracted out of main() so a test can
    drive the real CLI-to-global path without running a whole backtest."""
    global STRICT_WINDOW
    STRICT_WINDOW = not args.loose_window


def main():
    args = build_parser().parse_args()
    warning = validate_balance(args.balance)
    if warning:
        print(warning)
    global TF, BAR_MIN, FLATTEN_HM
    TF = args.tf
    BAR_MIN = TF_SEC[TF] // 60
    FLATTEN_HM = FLATTEN_BY_TF[TF]
    if args.profit_target is not None:
        global PROFIT_TARGET_PCT
        PROFIT_TARGET_PCT = args.profit_target
    global WINDOW, HOUR_TABLE
    WINDOW = (WINDOW[0] if args.window_start is None else args.window_start,
              WINDOW[1] if args.window_end is None else args.window_end)
    HOUR_TABLE = args.hour_table
    global BIAS_EMA, BIAS_MODE, BIAS_TF
    global BIAS_BUFFER_ATR
    BIAS_EMA, BIAS_MODE, BIAS_TF = args.bias_ema, args.bias_mode, args.bias_tf
    BIAS_BUFFER_ATR = args.bias_buffer_atr
    global CHOP_EFF_MAX, CHOP_EFF_BARS
    CHOP_EFF_MAX, CHOP_EFF_BARS = args.chop_eff_max, args.chop_eff_bars
    # Lane selection is NOT a mutated global (that was the smell): it is an
    # explicit set of lane ids passed straight into run() below. Only `ht`
    # is registered in LANES today; lanes_for() stays a function (not
    # inlined) so a future second lane's CLI wiring, if any, has somewhere
    # to plug in without touching run()'s call sites.
    active_lanes = lanes_for()
    global EXIT_SCHEME, ENTRY_MODE, FIXED_LOTS, REGIME_GATE, ATR_SPIKE_RATIO
    global CONFIRM_MODE, CHOP_FLIPS, CHOP_BARS, CHOP_BOX_ATR, CHOP_MODE
    global MIN_STOP_ATR
    apply_window_args(args)
    MIN_STOP_ATR = args.min_stop_atr
    global SR_LOOKBACK, SR_MIN_HEADROOM, SR_REPORT
    SR_LOOKBACK = args.sr_lookback
    SR_MIN_HEADROOM = args.sr_min_headroom
    SR_REPORT = args.sr_report
    CHOP_FLIPS, CHOP_BARS = args.chop_flips, args.chop_bars
    CHOP_BOX_ATR, CHOP_MODE = args.chop_box_atr, args.chop_mode
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
    candles = resample(data["candles"], TF_SEC[TF])
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
    # DATASET FINGERPRINT. bars_max.json is untracked and MUTABLE -- a refresh
    # from the terminal overwrites months of history with the broker's current
    # version. On 2026-08-21 that moved a published figure from +7380.53 to
    # +7625.63 with the code byte-identical (the frozen-fixture golden pins
    # proved it). Without a fingerprint nobody can tell "the code changed" from
    # "the data changed", so every quoted number must name the dataset it came
    # from. Cheap: hashing 100k bars costs well under a second.
    _fp = _run_fingerprint(candles)
    # The header is the only place a reader learns what the run measured.
    head = [f"backtest: {len(candles)} bars  {t0:%Y-%m-%d %H:%M} -> "
            f"{t1:%m-%d %H:%M} (server time)  [dataset {_fp}]",
            f"start balance ${args.balance:,.0f}",
            "strategy halftrend_ema_v1",
            f"exit scheme {EXIT_SCHEME}"]
    if ENTRY_MODE == "fixed":
        head.append(f"entry mode fixed ({FIXED_LOTS:g} lots)")
    head += [f"regime gate {REGIME_GATE}",
             f"atr-spike gate {ATR_SPIKE_RATIO:g}",
             f"ema {EMA_LEN}",
             f"confirm {CONFIRM_CLOSES} ({CONFIRM_MODE})"]
    if CHOP_FLIPS > 0:
        head.append(f"chop {CHOP_MODE} F{CHOP_FLIPS}/N{CHOP_BARS}"
                    f"/X{CHOP_BOX_ATR:g}")
    if STRICT_WINDOW:
        head.append("STRICT WINDOW")
    if MIN_STOP_ATR > 0:
        head.append(f"min stop {MIN_STOP_ATR:g} ATR")
    if SR_LOOKBACK > 0:
        head.append(f"sr lookback {SR_LOOKBACK}"
                    + (" report-only" if SR_REPORT else
                       (f" min-headroom {SR_MIN_HEADROOM:g} ATR"
                        if SR_MIN_HEADROOM > 0 else " tag-only")))
    if BIAS_EMA > 0:
        head.append(f"bias ema{BIAS_EMA} {BIAS_TF} mode {BIAS_MODE}")
    if WINDOW != (4, 23):
        head.append(f"window {WINDOW[0]}-{WINDOW[1]}")
    if PROFIT_TARGET_PCT != 2.0:
        head.append(f"profit target {PROFIT_TARGET_PCT:g}%")
    if TF != "M5":
        head.append(f"TF {TF}")
    print(" | ".join(head))
    print()

    trades, bal, max_dd, max_valley = run(
        candles, args.balance, args.verbose, active_lanes)
    # Several report blocks below read keys only HalfTrend's baskets carry
    # (orig_dist/orig_oz, regime, chop, bias, floor). A future plug-in lane's
    # trade reaching them would raise KeyError or silently pollute a
    # per-regime table, so those blocks read ht_trades instead of the full
    # list -- this is currently every trade (only `ht` is registered, see
    # LANES), but keeping the filter costs nothing and stays correct if a
    # second lane ever plugs back in. The overall net/balance/drawdown lines
    # below still read `trades` -- those describe the ACCOUNT, which every
    # active lane shares.
    ht_trades = [t for t in trades if t.get("lane", "ht") == "ht"]

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
    losses = [t for t in trades if t["pl"] < 0]   # pl == 0 is flat, not a loss
    flat = len(trades) - len(wins) - len(losses)
    print(f"\ntrades: {len(trades)}  |  wins {len(wins)}  losses {len(losses)}"
          + (f"  flat {flat}" if flat else ""))
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
    # Every block from here to the lane summary reads HalfTrend-only fields.
    # With no HalfTrend trades they printed rows of zeros -- "trend trades 0
    # net +0.00" on a run with no ht_trades is not a fact about anything
    # (a real case before 2026-08-22: a plug-in-only run took zero HalfTrend
    # trades and every regime/spike bucket printed a fabricated zero). Say
    # nothing instead.
    if ht_trades:
        print("entry regime breakdown (service classifier on the 300-bar window):")
        for rg in ("trend", "range", "high_volatility"):
            sub = [t for t in ht_trades if t.get("regime") == rg]
            w = [t for t in sub if t["pl"] > 0]
            print(f"  {rg:16} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
                  f"  win% {100 * len(w) / max(1, len(sub)):5.1f}")
        print("entry ATR-spike breakdown (ATR14 / median of its last 100 values):")
        for r in ATR_SPIKE_BUCKETS:
            sub = [t for t in ht_trades if (t.get("atr_ratio") or 0) > r]
            w = [t for t in sub if t["pl"] > 0]
            print(f"  ratio > {r:<4} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
                  f"  win% {100 * len(w) / max(1, len(sub)):5.1f}")
    sk = getattr(run, "skipped", [])
    if ht_trades and (REGIME_GATE != "off" or ATR_SPIKE_RATIO > 0):
        from collections import Counter
        cnt = Counter(r for _, _, r in sk)
        print(f"gate (regime {REGIME_GATE}, atr-spike {ATR_SPIKE_RATIO:g}): "
              f"refused {len(sk)} entries "
              f"({', '.join(f'{k} {v}' for k, v in cnt.items()) or 'none'})")
    if ht_trades and CHOP_FLIPS > 0:
        ch = [t for t in ht_trades if t.get("chop")]
        nc = [t for t in ht_trades if not t.get("chop")]
        for lab, sub in (("chop-tagged", ch), ("not chop", nc)):
            w = [t for t in sub if t["pl"] > 0]
            l = [t for t in sub if t["pl"] <= 0]
            print(f"chop {lab:12} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
                  f"  win% {100 * len(w) / max(1, len(sub)):5.1f}"
                  f"  winners {len(w)} {sum(t['pl'] for t in w):+.2f}"
                  f"  losers {len(l)} {sum(t['pl'] for t in l):+.2f}")
        nchop = sum(1 for _, _, r in sk if r == "chop")
        print(f"chop mode {CHOP_MODE}: refused {nchop} entries, "
              f"soft-sized {sum(1 for t in ht_trades if t.get('soft'))} baskets")
    if ht_trades and STRICT_WINDOW:
        dd = getattr(run, "dead_signals", [])
        print(f"strict window: {len(dd)} flips died at the decision bar "
              f"(entry bar would open on the wrong side of the EMA); "
              f"every entry sits {CONFIRM_CLOSES} bar(s) after its arrow")
    if ht_trades and MIN_STOP_ATR > 0:
        fl = [t for t in ht_trades if t.get("floored")]
        saved = [t for t in fl if t.get("orig_hit_bar") is not None
                 and t["orig_hit_bar"] <= NOISE_BARS]
        surv = [t for t in saved if not (t["why"] == "stop"
                                         and t["bars_open"] <= NOISE_BARS)]
        later = [t for t in fl if t.get("orig_hit_bar") is not None
                 and t["orig_hit_bar"] > NOISE_BARS]
        never = [t for t in fl if t.get("orig_hit_bar") is None]
        w = [t for t in fl if t["pl"] > 0]
        print(f"min-stop floor {MIN_STOP_ATR:g} ATR: floored {len(fl)} of "
              f"{len(ht_trades)} entries; floored net {sum(t['pl'] for t in fl):+.2f}"
              f"  win% {100 * len(w) / max(1, len(fl)):.1f}")
        print(f"  original stop would have hit within {NOISE_BARS} bars: "
              f"{len(saved)} (of which {len(surv)} survived past bar "
              f"{NOISE_BARS} under the floor; their eventual net "
              f"{sum(t['pl'] for t in saved):+.2f}; the old stop would have "
              f"realized {sum(-(t['orig_dist'] + SPREAD_USD) * t['orig_oz'] for t in saved):+.2f})")
        print(f"  original stop would have hit later (bar > {NOISE_BARS}): "
              f"{len(later)}  net {sum(t['pl'] for t in later):+.2f}"
              f"  |  never touched while open: {len(never)}  "
              f"net {sum(t['pl'] for t in never):+.2f}")
        for t in fl:
            print(f"    {t['opened']:%m-%d %H:%M} {t['dir']:4} "
                  f"{t['legs'][0]['oz']}oz (was {t['orig_oz']}oz) "
                  f"stop {t['entry_stop']:.2f} (was {t['orig_stop']:.2f}, "
                  f"{t['dist_atr']:.2f} ATR) old-stop hit bar "
                  f"{t['orig_hit_bar']} -> {t['why']} {t['pl']:+.2f}")
    if ht_trades and BIAS_EMA > 0:
        print(f"bias ema{BIAS_EMA} {BIAS_TF} mode {BIAS_MODE} "
              f"(with = BUY above / SELL below the EMA at entry):")
        for lab in ("with", "counter"):
            sub = [t for t in ht_trades if t.get("bias") == lab]
            w = [t for t in sub if t["pl"] > 0]
            l = [t for t in sub if t["pl"] <= 0]
            worst = min((t["pl"] for t in sub), default=0.0)
            print(f"  {lab:8} trades {len(sub):5}  net {sum(t['pl'] for t in sub):+10.2f}"
                  f"  win% {100 * len(w) / max(1, len(sub)):5.1f}"
                  f"  winners {len(w)} {sum(t['pl'] for t in w):+.2f}"
                  f"  losers {len(l)} {sum(t['pl'] for t in l):+.2f}"
                  f"  worst {worst:+.2f}")
        untag = [t for t in ht_trades if t.get("bias") is None]
        if untag:
            print(f"  (untagged {len(untag)}: entered before the bias EMA warmed up)")
        nb = sum(1 for _, _, r in sk if r == "counter-trend")
        if BIAS_MODE == "skip":
            print(f"  skip: refused {nb} counter-trend entries")
        bf = getattr(run, "bias_flips", None) or {}
        for tf in ("M5", "M15"):
            if tf in bf:
                per, tot, nd = bf[tf]
                print(f"  bias flips/day on {tf}: {per:.2f} ({tot} flips over {nd} days)")
    if ht_trades and HOUR_TABLE:
        print("entry-hour breakdown (server time; trade attributed to the "
              "hour its first leg opened):")
        print("  hour  trades   win%        net        avg      worst")
        for hr in range(24):
            sub = [t for t in ht_trades
                   if t.get("opened") is not None and t["opened"].hour == hr]
            if not sub:
                continue
            w = [t for t in sub if t["pl"] > 0]
            net = sum(t["pl"] for t in sub)
            print(f"  {hr:02d}    {len(sub):6}  {100 * len(w) / len(sub):5.1f}"
                  f"  {net:+10.2f} {net / len(sub):+10.2f} "
                  f"{min(t['pl'] for t in sub):+10.2f}")
    if ht_trades and SR_LOOKBACK > 0:
        b1, b2, b3 = SR_BUCKETS
        print(f"headroom to the nearest opposing level (pivots k={SR_PIVOT_K} "
              f"over {SR_LOOKBACK} bars + prev-day H/L + session H/L, "
              f"deduped {SR_DEDUP_ATR:g} ATR):")
        print("  bucket        trades   win%        net        avg"
              "   winners $   losers $")
        rows = [(f"<{b1:g} ATR", lambda v: v is not None and v < b1),
                (f"{b1:g}-{b2:g} ATR", lambda v: v is not None and b1 <= v < b2),
                (f"{b2:g}-{b3:g} ATR", lambda v: v is not None and b2 <= v < b3),
                (f">{b3:g} ATR", lambda v: v is not None and v >= b3),
                ("clear", lambda v: v is None)]
        for lab, pred in rows:
            sub = [t for t in ht_trades if pred(t.get("headroom"))]
            if not sub:
                continue
            w = [t for t in sub if t["pl"] > 0]
            l = [t for t in sub if t["pl"] <= 0]
            net = sum(t["pl"] for t in sub)
            print(f"  {lab:12} {len(sub):7}  {100 * len(w) / len(sub):5.1f}"
                  f"  {net:+10.2f} {net / len(sub):+10.2f}"
                  f"  {sum(t['pl'] for t in w):+10.2f}"
                  f" {sum(t['pl'] for t in l):+10.2f}")
        print("  opportunity cost of a headroom floor (from THESE trades; "
              "path effects not modelled):")
        for x in (b1, b2, 1.5):
            sub = [t for t in ht_trades
                   if t.get("headroom") is not None and t["headroom"] < x]
            w = sum(t["pl"] for t in sub if t["pl"] > 0)
            l = sum(t["pl"] for t in sub if t["pl"] <= 0)
            print(f"    floor {x:g} ATR would skip {len(sub):4} trades: "
                  f"winners {w:+10.2f} forgone, losers {l:+10.2f} avoided, "
                  f"net effect {-(w + l):+10.2f}")
        nsr = sum(1 for _, _, r in sk if str(r).startswith("headroom<"))
        if SR_MIN_HEADROOM > 0 and not SR_REPORT:
            print(f"  filter: refused {nsr} entries below "
                  f"{SR_MIN_HEADROOM:g} ATR of headroom")
        untag = [t for t in ht_trades if t.get("headroom") is None]
        if untag:
            print(f"  ('clear' = no level ahead of the fill: {len(untag)} "
                  f"trades; these are never refused)")
    if ht_trades and CONFIRM_MODE == "open":
        od = getattr(run, "open_diff_bars", [])
        print(f"confirm-mode open: {len(od)} decision bars where the next "
              f"open sat on a different side of the EMA than the close")
    ls = _lane_stats(trades)
    if ls["ht"]["trades"]:
        d = ls["ht"]
        print(f"lane {'halftrend':<10} trades {d['trades']:>5}  win% "
              f"{d['win_rate']:>5.1f}  net {d['net']:>10.2f}  "
              f"max dd {d['max_dd']:>9.2f}")
        print("           lane max dd walks that lane's OWN realized curve; "
              "the account's joint drawdown is on the line below.")
    print(f"\nnet P/L    {bal - args.balance:+10.2f}  "
          f"({100 * (bal / args.balance - 1):+.2f}%)")
    print(f"final bal  {bal:10.2f}   max drawdown {max_dd:.2f}   "
          f"max open-equity valley {max_valley:.2f}")
    s = getattr(run, "sizing", None)
    if s and s["entries"]:
        flag = "  <-- results distorted" if s["clamp_pct"] > 10 else ""
        print(f"sizing     {s['clamp_pct']:.1f}% of {s['entries']} entries "
              f"clamped to the 0.01 minimum lot{flag}")
        print(f"           risk actually taken: median {s['risk_median']:.2f}% "
              f"p90 {s['risk_p90']:.2f}%  (target {RISK_PCT:.2f}%)")
    armed = [t for t in ht_trades if t.get("floor") is not None]
    if EXIT_SCHEME != "target-exit":
        print(f"floor armed on {len(armed)} trades; "
              f"{len(floor_leaks)} realized below their floor "
              f"({', '.join(t['why'] for t in floor_leaks) or 'none'})")
    if args.chart:
        plot(candles, trades, args.balance, args.chart)
        print(f"chart      {args.chart}")
    art = None
    if args.json:
        art = build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        Path(args.json).write_text(json.dumps(art, separators=(",", ":")))
        print(f"json       {args.json} "
              f"({Path(args.json).stat().st_size / 1e6:.1f} MB)")
    if args.web:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from backtest_report import write_report
        if art is None:
            art = build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        write_report(art, args.web)
        print(f"report     {args.web} "
              f"({Path(args.web).stat().st_size / 1e6:.1f} MB)")
    # The caveats reached --help, --json and the --web page but never stdout,
    # where most runs are actually read. One line, built from the same list.
    print("\nNOT MODELLED: " + " | ".join(c.split(" -- ")[0] for c in CAVEATS) +
          "  (full text: --help, --json meta.caveats, the --web report)")


if __name__ == "__main__":
    main()
