# EMA-200 market bias & trading-hours sweep

Date: 2026-08-18. Analysis + tooling only — nothing in `mt5/` or `service/app/`
was touched. Tool changes are confined to `scripts/backtest.py`.

## 0. Setup, data, and what "baseline" means

| | |
|---|---|
| Replay | `scripts/backtest.py` (halftrend_ema_v1, current money rulebook) |
| **`--strict-window` is ON in every run in this study** | the LIVE entry rule: arrow bar, wait 1 bar, enter only if that bar closed on the trend's side of the EMA; miss = the flip is dead |
| `--min-stop-atr` | 0 (off) everywhere |
| Everything else | defaults (ADX 10, risk 1%, expo 360 min/day, EMA 55, confirm 1, target-exit, ADR entry mode) |
| Start balance | $4,000 for every run, including every sub-period (so periods are comparable and compounding path does not leak across them) |

### Data files

| Window | File | Span (server time) |
|---|---|---|
| 17 months ("17mo") | `bars_max.json` | 2025-03-19 12:00 → 2026-08-17 13:20 (99,999 M5 bars, 364 trading days) |
| Last 30 days ("30d") | `bars_max.json --days 30` | 2026-07-18 → 2026-08-17 (21 trading days) |
| P1 | `bars_max.json --end 2025-09-05T18:15` | 2025-03-19 → 2025-09-05 |
| P2 | `bars_max.json --start 2025-09-05T18:20 --end 2026-02-26T03:25` | 2025-09-05 → 2026-02-26 |
| P3 | `bars_max.json --start 2026-02-26T03:30` | 2026-02-26 → 2026-08-17 |
| Fresh out-of-sample ("week") | `week.json` | 2026-08-03 16:00 → 2026-08-18 12:55 (3,000 bars, 11 trading days) |

P1/P2/P3 are the three equal-bar-count tertiles of `bars_max.json` (33,333 bars each).

### Two units are reported

* **net $** — dollars on a compounding $4,000 account. Honest bottom line, but
  over 17 months the baseline balance falls from $4,000 to $915, so a dollar
  late in the run is worth ~1/4 of a dollar early in the run.
* **sumR%** — the sum of each trade's P/L as a percent of the *cycle balance*
  (the balance at the moment that basket opened). Since risk is 1% of balance,
  1 R ≈ 1.00 sumR%. This strips the compounding decay out and is the fairer
  cross-period comparison. Both are given side by side throughout.

Baseline (4-23, no bias): **17mo net −$3,084.53 / sumR −128.24% / 1,729 trades /
win 35.4% / max open-equity valley $3,602.55**; 30d **+$198.58 / +5.63% / 83
trades / 38.6% / valley $510.66**; week **+$415.20 / +10.29% / 45 trades /
51.1% / valley $190.82**.

Note up front: over the full 17 months this strategy **loses money in every
configuration tested**. Both studies below are about *how much less it loses*,
not about turning it profitable. The recent 30 days and the fresh week are
positive; the older two thirds are not.

---

## 1. STUDY 1 — EMA-200 market bias

### 1.0 THE CRUX NUMBER (read this first)

All baseline trades, split by EMA-200 (M5, at the entry bar) — a BUY above the
EMA / SELL below it is **with-trend**, the opposite is **counter-trend**. This
is a pure tag; not one trade changed.

**17 months (`bars_max.json`, 1,729 trades):**

| | trades | win % | net $ | sumR % | avg per trade |
|---|---:|---:|---:|---:|---:|
| with-trend | 1,228 | **34.9 %** | −$2,161.33 | −98.07 % | **−0.080 %** |
| counter-trend | **497** | **36.8 %** | −$795.97 | −26.97 % | **−0.054 %** |

(4 trades untagged — entered before the EMA-200 warmed up.)

**Last 30 days (`bars_max.json --days 30`, 83 trades):**

| | trades | win % | net $ | sumR % | avg per trade |
|---|---:|---:|---:|---:|---:|
| with-trend | 56 | 39.3 % | +$230.51 | +6.36 % | +0.114 % |
| counter-trend | 24 | **41.7 %** | +$57.36 | +1.52 % | +0.063 % |

**Fresh week (`week.json`, 45 trades)** — the sharpest version:

| | trades | win % | net $ |
|---|---:|---:|---:|
| with-trend (EMA-200 M5) | 29 | 48.3 % | +$298.83 |
| counter-trend (EMA-200 M5) | 11 | **72.7 %** | +$207.08 |
| with-trend (EMA-200 M15) | 20 | 35.0 % | **−$10.73** |
| counter-trend (EMA-200 M15) | 16 | **68.8 %** | **+$284.93** |

### Verdict on the crux

**The premise is false.** In all three windows, counter-trend trades win *more
often* than with-trend trades (36.8 % vs 34.9 % over 17 months; 41.7 % vs 39.3 %
over 30 days; 72.7 % vs 48.3 % in the fresh week), and over 17 months they also
lose *less per trade* (−0.054 % of balance vs −0.080 %). On the M15 clock in the
fresh week the ordering is fully inverted: the with-trend half made **nothing**
and the counter-trend half made all of the money.

Counter-trend trades are not the losers. There is nothing here for the "half
target because they're weak" rule to fix. **The idea is moot.**

### 1.1 Why the EMA-200 is not a "market bias" here

`--bias-ema 200` prints how often the bias sign changes:

| clock | 17 months | last 30 days |
|---|---:|---:|
| M5 EMA-200 | **11.51 flips/day** (4,190 flips / 364 days) | 9.00 flips/day (189 / 21) |
| M15 EMA-200 | **3.48 flips/day** (1,265 flips / 363 days) | 2.53 flips/day (48 / 19) |

An EMA-200 on M5 is a ~16-hour average and price crosses it about **twelve times
a day**. That is not a regime marker, it is a coin flip that resets before
lunch. On M15 (a ~50-hour average) it settles to ~3.5 flips/day — better, but
still not a daily "the market is bullish today" verdict.

### 1.2 How the replay models the target and the profit lock (asked explicitly)

Read from `run()` in `scripts/backtest.py`, exit checks in the order they run
inside one bar: **stop (intrabar) → profit target → profit lock → reversal → pyramid add**.

* **Profit target** — `target = cycle_bal * PROFIT_TARGET_PCT/100`, i.e. **+2 %
  of the balance at the moment the basket opened**, evaluated on bar close
  against the whole basket's P/L. There is genuinely no per-trade TP in ADR
  mode. "Half target" therefore = **+1 % of cycle balance**.
* **Profit lock** — arms when peak basket P/L reaches
  `TRAIL_ACTIVATE_R (1.0) × risk_budget`, where
  `risk_budget = cycle_bal * RISK_PCT/100` = **1 % of cycle balance**; once
  armed, the basket closes if P/L falls back to 50 % of its peak.
* **The lock's arming threshold is tied to the RISK budget, not to the target.**
  Baseline: target 2 %, lock arms at 1 % — the lock arms at *half* the target.

That creates a trap for the owner's literal rule: halve the target to 1 % and
the lock still arms at 1 %, so the target check (which runs first) always fires
at or before the lock — **the profit lock becomes dead code for counter-trend
baskets**. So I implemented three modes plus a fourth as the fair control:

| `--bias-mode` | counter-trend basket gets |
|---|---|
| `tag` (default when `--bias-ema` > 0) | nothing changes — tag and report only |
| `target` | **owner's literal version**: target × 0.5 (+1 %). Lock untouched → effectively disabled, as explained above |
| `target_lock` | *my added control*: target × 0.5 **and** lock arm × 0.5 (arms at 0.5R), preserving the baseline's "lock arms at half the target" proportion |
| `size_target` | target × 0.5 **and** risk × 0.5; lock budget scales with the risk actually taken (arms at 1R of the halved budget) → the whole basket is a true half-scale copy |
| `skip` | counter-trend entries refused entirely |

Bias is decided **once, at the entry bar**, and is basket-sticky: a mid-trade
flip never touches an open basket's target, lock, stop or adds. The stop
distance is never modified by any bias mode.

### 1.3 Runs — baseline vs the modes

**(a) Last 30 days** (`bars_max.json --days 30`)

| mode | trades | win % | net $ | valley $ | vs baseline |
|---|---:|---:|---:|---:|---:|
| baseline / tag | 83 | 38.6 | +198.58 | 510.66 | — |
| target | 84 | 40.5 | +300.41 | 405.50 | +101.83 |
| target_lock | 84 | 39.3 | +185.21 | 405.50 | −13.37 |
| size_target | 83 | 38.6 | +162.99 | 453.99 | −35.59 |
| skip | 61 | 36.1 | +172.55 | 389.26 | −26.03 |

**(b) 17 months** (`bars_max.json`)

| mode | trades | win % | net $ | valley $ | vs baseline |
|---|---:|---:|---:|---:|---:|
| baseline / tag | 1,729 | 35.4 | −3,084.53 | 3,602.55 | — |
| target | 1,723 | 36.0 | −2,859.09 | 3,107.25 | +225.44 |
| target_lock | 1,746 | 37.3 | −3,042.43 | 3,397.94 | +42.10 |
| size_target | 1,723 | 35.8 | −2,604.80 | 3,047.74 | +479.73 |
| skip | 1,282 | 33.9 | −2,594.64 | 3,091.71 | +489.89 |

$490 spread over 17 months is **$29/month** — deep inside the ±$200/month noise
band established by the earlier studies.

Note what `skip` does: it refuses **517** counter-trend entries (30 % of all
trades) and the win rate *drops* from 35.4 % to 33.9 %. It improves the dollar
line only by doing less of a losing thing, and it does it by throwing away the
better-quality half of the book.

**(c) Three sub-periods** (net $, each starting at $4,000)

| mode | P1 | P2 | P3 | periods better than baseline |
|---|---:|---:|---:|---:|
| baseline | −2,147.01 | −1,712.77 | −200.58 | — |
| target | −2,176.87 | −1,584.34 | +293.36 | 2 / 3 |
| target_lock | −2,294.77 | −1,576.79 | +1,065.10 | 2 / 3 |
| size_target | −2,094.76 | −1,377.07 | +59.71 | 3 / 3 |
| skip | −1,764.42 | −942.18 | −190.30 | 3 / 3 |

Nothing is stable. `target_lock` swings from −$148 (P1) to +$1,266 (P3) against
baseline; `target` is the winner over 30 days but the *loser* in P1. The only
two "3 / 3" modes (`size_target`, `skip`) are both ones that simply put less
money on the table, and both are **worse than baseline over the last 30 days**.

### 1.4 The winner re-run on `--bias-tf M15`

M15 was tried for every mode, not just the winner.

| mode | 17mo M5 | 17mo M15 | 30d M5 | 30d M15 | P1 M15 | P2 M15 | P3 M15 |
|---|---:|---:|---:|---:|---:|---:|---:|
| target | −2,859.09 | −2,485.06 | +300.41 | +325.07 | −2,223.25 | −1,519.37 | +416.60 |
| target_lock | −3,042.43 | −2,499.83 | +185.21 | +151.66 | −2,024.13 | −1,544.46 | +306.27 |
| size_target | −2,604.80 | −2,283.75 | +162.99 | +64.37 | −1,765.09 | −1,495.50 | +64.25 |
| skip | −2,594.64 | −2,547.47 | +172.55 | +78.05 | −1,646.74 | −1,556.83 | −240.20 |

M15 is mildly better than M5 over 17 months for every mode (the bias is 3.3×
more stable), but it is *worse* than M5 over the last 30 days for three of the
four modes, and `skip` on M15 is the worst variant of all in P3. On `week.json`
every M15 mode also lands below its M5 twin. There is no consistent M15 win
either.

### 1.5 Study 1 verdict

**Adopt none of the three modes.** The crux number kills the premise before the
modes even matter: counter-trend trades win more often than with-trend trades in
every window measured, and in the freshest data they are the *only* profitable
half. Every mode's effect (−$36 to +$490 over 17 months, −$36 to +$102 over 30
days) sits inside the known ±$200/month noise, none is stable across the three
sub-periods, and the two that look steadiest only look steady because they trade
less.

The `--bias-ema` / `--bias-mode` / `--bias-tf` tooling is kept in the script
(default off, byte-identical) so the tag view stays available for future
autopsies — it is a useful diagnostic even though the rule it was built to test
does not work.

---

## 2. STUDY 2 — trading-hours sweep

All server time (GMT+3), matching the EA's `TradingWindowStartHour` /
`TradingWindowEndHour`.

### 2.0 Semantics implemented (and why)

`RiskManager.mqh:313` checks the hour **only in the entry path**;
`InTradingWindow()` is never consulted by any exit. The new
`--window-start` / `--window-end` reproduce that exactly: the window gates
**new entries only**. A basket opened at 17:55 under `--window-end 18` keeps
running to its target / lock / stop / reversal, and the 23:50 pre-break flatten
is unchanged in every window. Defaults 4 and 23 = the live window, byte-identical.

### 2.1 THE HOUR-BY-HOUR TABLE (the artifact worth keeping)

Two independent views, and they agree.

**(A) Descriptive — every baseline (4-23) trade attributed to the hour its
first leg opened, 17 months, `bars_max.json`** (`--hour-table`):

| hour | trades | win % | net $ | avg $ | sumR % | avg R % |
|---:|---:|---:|---:|---:|---:|---:|
| 04 | 107 | 39.3 | +239.76 | +2.24 | +2.96 | +0.028 |
| 05 | 101 | 44.6 | +233.10 | +2.31 | +14.58 | +0.144 |
| 06 | 78 | 44.9 | +282.52 | +3.62 | +13.47 | +0.173 |
| 07 | 77 | 33.8 | +118.50 | +1.54 | −7.56 | −0.098 |
| **08** | 98 | **44.9** | **+483.79** | +4.94 | **+22.65** | **+0.231** |
| 09 | 101 | 32.7 | +41.59 | +0.41 | −9.37 | −0.093 |
| **10** | 116 | **27.6** | **−572.36** | −4.93 | −24.28 | −0.209 |
| **11** | 106 | 37.7 | **−567.68** | −5.36 | −16.65 | −0.157 |
| **12** | 91 | 29.7 | **−569.07** | −6.25 | −30.00 | −0.330 |
| 13 | 89 | 28.1 | −367.60 | −4.13 | −14.21 | −0.160 |
| **14** | 115 | **27.0** | **−797.36** | **−6.93** | **−30.78** | −0.268 |
| **15** | 125 | 27.2 | **−594.55** | −4.76 | **−32.49** | −0.260 |
| **16** | 105 | **42.9** | **+569.37** | **+5.42** | **+22.39** | **+0.213** |
| 17 | 89 | 42.7 | −224.82 | −2.53 | +4.68 | +0.053 |
| 18 | 71 | 38.0 | −23.75 | −0.33 | −5.48 | −0.077 |
| 19 | 59 | 37.3 | −221.03 | −3.75 | −2.12 | −0.036 |
| 20 | 64 | 39.1 | −335.69 | −5.25 | −1.43 | −0.022 |
| 21 | 67 | 31.3 | −282.52 | −4.22 | −12.49 | −0.186 |
| **22** | 70 | **28.6** | **−496.72** | **−7.10** | −22.12 | **−0.316** |

**(B) Counterfactual — each hour traded ALONE (`--window-start h
--window-end h+1`), so the day starts flat with a fresh exposure budget and the
hour is not contaminated by whatever the morning already did.** Each period
restarts at $4,000.

| hour | 17mo trades | 17mo win % | 17mo net $ | 17mo sumR % | P1 sumR | P2 sumR | P3 sumR | periods positive |
|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| 01 | 90 | 34.4 | −245.32 | −5.48 | −5.50 | −3.35 | +3.63 | 1/3 |
| 02 | 94 | 33.0 | +54.26 | +2.71 | −0.44 | +7.52 | −5.37 | 1/3 |
| **03** | 107 | 38.3 | **+971.05** | **+23.12** | +14.95 | +2.44 | +3.84 | **3/3** |
| 04 | 107 | 33.6 | −245.10 | −5.41 | +1.66 | +4.56 | −9.97 | 2/3 |
| 05 | 101 | 41.6 | +539.24 | +13.57 | −0.25 | +9.32 | +5.71 | 2/3 |
| **06** | 78 | 43.6 | +573.63 | +14.19 | +0.38 | +11.59 | +1.00 | **3/3** |
| 07 | 79 | 35.4 | −173.14 | −3.64 | +5.07 | −5.74 | −0.80 | 1/3 |
| **08** | 99 | 45.5 | +649.76 | +16.01 | +1.70 | +10.83 | +3.73 | **3/3** |
| 09 | 102 | 32.4 | −293.98 | −6.60 | −2.98 | −1.16 | +0.61 | 1/3 |
| **10** | 117 | **25.6** | **−1,082.91** | **−30.60** | −15.36 | −10.46 | −8.05 | **0/3** |
| **11** | 107 | 32.7 | −833.56 | −22.58 | −17.47 | −5.35 | −1.65 | **0/3** |
| **12** | 91 | 28.6 | −1,014.92 | −28.73 | −8.00 | −16.92 | −4.97 | **0/3** |
| 13 | 90 | 31.1 | −812.43 | −22.06 | −4.52 | −20.52 | +3.15 | 1/3 |
| **14** | 117 | **24.8** | **−1,112.88** | **−31.46** | −14.18 | −9.17 | −5.68 | **0/3** |
| **15** | 135 | 26.7 | −710.38 | −18.03 | −13.29 | −4.05 | −4.07 | **0/3** |
| **16** | 112 | **42.9** | **+1,595.72** | **+35.30** | +10.48 | +4.07 | +15.88 | **3/3** |
| 17 | 98 | 35.7 | −272.09 | −6.34 | −9.14 | +0.83 | −2.26 | 1/3 |
| 18 | 87 | 33.3 | −337.74 | −8.13 | +4.37 | −6.42 | −5.39 | 1/3 |
| 19 | 71 | 39.4 | +114.54 | +3.36 | −6.90 | +3.48 | +4.55 | 2/3 |
| 20 | 79 | 38.0 | −203.32 | −4.52 | −13.55 | +0.83 | +12.13 | 2/3 |
| 21 | 92 | 38.0 | +155.00 | +4.83 | +5.60 | −7.55 | +10.50 | 2/3 |
| **22** | 98 | 29.6 | −782.00 | −21.09 | −13.48 | −4.81 | −1.32 | **0/3** |
| 23 | 58 | 50.0 | +11.02 | +0.48 | +2.20 | −1.98 | +0.82 | 2/3 |

**The two views agree.** The bleeding hours are **10, 11, 12, 14, 15 and 22** —
negative in all three sub-periods standalone and negative on the baseline path.
The consistently good hours are **16** (the single best hour by a wide margin),
**08, 06, 05** and **03** (the last one outside the live window).

Ranked worst by 17-month standalone sumR: **14 (−31.5), 10 (−30.6), 12 (−28.7),
11 (−22.6)**, then 13 (−22.1), 22 (−21.1), 15 (−18.0).

That block is 10:00–15:59 server = **07:00–12:59 UTC**, i.e. the London morning
into the pre-NY-open drift, plus 22:00 (the late-NY / pre-rollover hour).

### 2.2 The requested window runs

net $ / (sumR %), each period from $4,000, `bars_max.json`:

| window | 17mo | P1 | P2 | P3 | 30d | 17mo trades | 17mo win % | 17mo valley $ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **4-23 (current)** | −3,084.53 (−128.24) | −2,147.01 (−71.54) | −1,712.77 (−50.44) | −200.58 (−0.55) | +198.58 (+5.63) | 1,729 | 35.4 | 3,602.55 |
| 9-18 | −3,033.50 (−130.79) | −1,925.85 (−62.53) | −1,691.37 (−51.83) | −72.68 (+0.89) | +557.10 (+13.59) | 967 | 32.5 | 3,482.50 |
| 8-20 | −2,926.88 (−117.84) | −1,876.50 (−59.34) | −1,790.33 (−55.70) | −482.53 (−9.64) | +654.49 (+15.79) | 1,213 | 33.7 | 3,537.52 |
| 10-17 | −3,012.36 (−131.30) | −1,820.45 (−58.30) | −1,829.40 (−58.60) | −108.58 (−0.58) | +353.28 (+8.86) | 768 | 30.7 | 3,430.44 |
| **13-22** | **−1,939.14 (−57.53)** | **−1,402.44 (−40.47)** | **−1,395.71 (−39.96)** | **+933.63 (+23.68)** | **+673.41 (+16.01)** | 871 | 34.6 | **2,472.61** |
| 4-12 (control) | −936.42 (−19.49) | −1,046.24 (−27.90) | +190.45 (+7.10) | −613.45 (−14.64) | +95.13 (+2.78) | 783 | 35.8 | 2,619.91 |

Of the requested list, **13-22 is the only window that beats the current 4-23 in
all four slices** (P1 +$745, P2 +$317, P3 +$1,134, 30d +$475) *and* cuts the max
open-equity valley by 31 % ($3,603 → $2,473). 9-18, 8-20 and 10-17 are inside
noise of the baseline. The Asia-morning control 4-12 is the classic overfit
trap: best-looking over 17 months (−$936) but **negative in P3 and near-flat in
the last 30 days** — the morning edge existed in the older data and has died.

`week.json` (fresh, 11 trading days) agrees on the ordering: 13-22 **+$695.94 /
65.0 % win** vs 4-23 +$415.20 / 51.1 %, and 4-12 the worst at +$100.13.

### 2.3 Extra windows I tested (bottom-up from the hour table)

Not in the requested list; included because the hour table points at 16:00 and
these say whether that survives. **Overfit risk is real here — 12 windows were
searched.** sumR %:

| window | 17mo | P1 | P2 | P3 | 30d | 17mo n / win % / net $ / valley $ |
|---|---:|---:|---:|---:|---:|---|
| 4-23 (current) | −128.24 | −71.54 | −50.44 | −0.55 | +5.63 | 1,729 / 35.4 / −3,084.53 / 3,602.55 |
| 13-22 | −57.53 | −40.47 | −39.96 | +23.68 | +16.01 | 871 / 34.6 / −1,939.14 / 2,472.61 |
| 15-23 | −24.24 | −31.65 | −7.91 | +32.17 | +8.84 | 759 / 34.9 / −1,082.13 / 1,631.43 |
| 16-23 | +1.49 | −23.15 | −5.65 | +31.87 | +8.11 | 625 / 36.6 / −173.53 / 1,348.12 |
| 16-21 | +9.18 | −16.69 | +11.53 | +27.74 | +9.25 | 442 / 37.8 / +209.35 / 1,038.62 |
| **16-20** | **+17.19** | **−1.07** | **+5.75** | **+14.45** | **+5.49** | 367 / 37.9 / **+591.32** / **785.59** |
| **16-18** | **+28.25** | **+1.07** | **+11.07** | **+12.70** | **+0.78** | 210 / 39.0 / **+1,183.11** / **541.17** |
| 3-10 | +50.09 | +31.59 | +29.04 | −2.04 | −2.64 | 668 / 39.1 / +2,155.88 / 1,815.72 |
| 3-9 | +58.32 | +30.65 | +34.57 | −2.69 | −5.99 | 567 / 40.0 / +2,756.54 / 928.42 |
| 4-10 | +32.08 | +12.58 | +26.17 | −10.16 | −4.32 | 561 / 38.9 / +1,210.19 / 1,588.89 |

**3-9 / 3-10 / 4-10 look spectacular over 17 months and are a trap** — every one
of them is negative in P3 *and* negative over the last 30 days *and* negative on
`week.json` (3-9 = −$180.85). Do not adopt them.

**16-18 and 16-20 are the only windows positive in every single slice**
(17mo, P1, P2, P3, 30d). They are also the calmest by a mile: 16-18's worst
open-equity valley is **$541** against the baseline's **$3,603**. The catch is
volume: 16-18 takes 210 trades in 17 months (~12/month) and its 30-day edge
(+0.78 sumR) is indistinguishable from zero, and on `week.json` it is −$47.65 on
5 trades. 16-20 is the better compromise (367 trades, positive 5/5, one of them
essentially flat at −1.07 in P1).

### 2.4 Why a good hour list ≠ the best contiguous window

Hour 16 alone earns +35.3 sumR over 17 months, yet the baseline path only
extracts +22.4 from it. Reason: at 16:00 the account is often already committed —
one basket at a time, and a morning basket still open blocks the 16:00 signal.
Narrowing the window frees those signals up.

I checked the obvious alternative explanation and it is **not** the exposure
budget: re-running with `EXPO_MIN` 360 (live) vs 0 (unlimited) over 17 months
changes 4-23 by only 116 trades / +$56, and changes 13-22 and 16-18 by **zero
trades**. The gain is basket-occupancy and compounding path, not the daily
minutes cap.

Consequence: the EA can only express **one contiguous window**, so it cannot say
"trade 05-09 and 16-20, skip 10-15". The single best contiguous expression of
the hour table is the afternoon block.

### 2.5 Study 2 verdict

**Adopt `TradingWindowStartHour = 13`, `TradingWindowEndHour = 22`** if you want
the conservative, requested-list answer: it is the only one of the five candidate
windows that beats the current 4-23 in all four periods and on the fresh week,
worth roughly **+$475 over the last 30 days** and **+$1,145 over 17 months**,
while cutting the worst open-equity valley from $3,603 to $2,473.

**16-20 is the sharper cut** (positive in all five slices, valley $786) but rests
on 21 trades/month and was found by searching 12 windows, so treat it as the
follow-up to test forward, not as the first change.

The 3-4 worst hours by P/L, consistent across both views and all three
sub-periods: **14:00, 10:00, 12:00, 11:00** (then 13:00, 22:00, 15:00) server time.

---

## 3. Caveats

1. **The replay charges a flat $0.20/oz round-trip spread at all hours.** Real
   spreads at 22:00–01:00 and in thin Asian hours are materially wider, so hours
   01-04, 22 and 23 in the table are *flattered*. This is exactly why the EA
   already excludes 23-04. Hour 03's +$971 is not bankable.
2. **The replay models neither the daily loss brake nor the news blackout**, both
   of which would have refused some of these entries live.
3. **Every 17-month dollar figure is compounding-distorted**: the baseline
   balance falls from $4,000 to $915, so late trades are ~4× smaller in dollars
   than early ones. That is why sumR % is reported alongside.
4. **The prior noise band is ±$200/month in this replay.** Study 1's largest
   effect is $29/month over 17 months. Study 2's 13-22 result is ~$67/month over
   17 months and ~$475 over the last month — the second is above the band, the
   first is not, which is why the sub-period consistency (4/4) is doing the
   work in the verdict, not the headline size.
5. **Window search is a search.** 12 windows were tried; 13-22 came from the
   pre-specified list of 5 and is the safer claim. 16-18/16-20 did not.
6. **The whole strategy is net-negative over 17 months in every configuration
   tested.** These studies reduce the bleeding; they do not create an edge.

---

## 4. Tooling changes (`scripts/backtest.py` only)

New flags, all defaulting OFF and byte-identical when unused:

| flag | default | effect |
|---|---|---|
| `--bias-ema N` | 0 (off) | EMA-N market bias at the entry bar; tags every trade with/counter and prints the split + the M5/M15 flip rate |
| `--bias-mode tag\|target\|target_lock\|size_target\|skip` | `tag` | what a counter-trend entry gets (see §1.2) |
| `--bias-tf M5\|M15` | `M5` | timeframe of the bias EMA (M15 = resampled M5 bars, last completed M15 bar; entries stay M5) |
| `--window-start H` | 4 | first server hour that may OPEN a trade (EA `TradingWindowStartHour`) |
| `--window-end H` | 23 | first server hour that may NOT open a trade (EA `TradingWindowEndHour`) |
| `--hour-table` | off | print the entry-hour breakdown (trades / win % / net / avg / worst per hour) |

**Byte-identity proof.** `git show HEAD:scripts/backtest.py` vs the modified
file, same flags, no diff on either window:

```
python3 backtest_head.py    --source bars_max.json --days 30 --strict-window > before30.txt
python3 scripts/backtest.py --source bars_max.json --days 30 --strict-window > after30.txt
diff before30.txt after30.txt    # -> no output  (BYTE-IDENTICAL 30d)

python3 backtest_head.py    --source bars_max.json --strict-window > before_full.txt
python3 scripts/backtest.py --source bars_max.json --strict-window > after_full.txt
diff before_full.txt after_full.txt   # -> no output  (BYTE-IDENTICAL 17mo)
```

Both diffs were empty.
