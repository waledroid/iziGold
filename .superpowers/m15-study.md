# M15 vs M5 — testing the owner's claim, 2026-08-19

> Owner's claim (2026-08-19): *"this signal looks very good on the 15-minute
> chart — less noise, more profit if we set a higher TP."*

**Verdict in one line: the first half is right and it is not close — M15 beats
M5 in every window tested, including all three sub-periods and 13 of the 18
calendar months. The second half is wrong — a *higher* profit target makes
M15 worse, not better; what helps is turning the target OFF, and even that is
not fully robust.**

Everything below runs the **strict entry window** (`--strict-window`), i.e.
the live entry rule as of 2026-08-17, on `bars_max.json` (99,999 M5 bars,
2025-03-19 12:00 → 2026-08-17 13:20 server, 365 trading days, 16.95 months),
starting balance $4,000 per window.

---

## 0. Tooling and method

### The new flags (scripts/backtest.py)

* `--tf {M5,M15}` — default `M5`, **byte-identical** to every result recorded
  so far (verified: `--days 30 --strict-window`, `--days 30`, `--days 30
  --strict-window --hour-table --min-stop-atr 0.6`, `--days 30
  --strict-window --sr-lookback 120 --sr-report`, `--days 30 --bias-ema 200
  --bias-tf M15 --regime-gate range`, and the full 17-month
  `--strict-window`, all `diff`-clean against `HEAD:scripts/backtest.py`).
* `--profit-target PCT` — overrides `ProfitTargetPct`. Default 2.0 =
  byte-identical. `<= 0` turns the target off exactly as the EA input
  documents it (`ProfitTargetPct = 2.0; 0 = off`).

The EA has a `TradeTimeframe` input that pins **every** trading decision to
one timeframe (`XauAssistant.mq5:22`, passed to the strategy, the risk
manager, the AI client and `iATR`), so this is a real, supported switch, not
a chart cosmetic.

### Resampling M5 → M15, and how it was verified

Buckets are `t // 900`, which lands on server `:00/:15/:30/:45`;
`open` = first open, `high` = max, `low` = min, `close` = last close,
`volume` = sum. Only a **trailing** incomplete bucket is dropped (the bar
still forming at the end of the feed).

**`scripts/dump_bars.py` only dumps M5, so it could not be used directly.**
It was cloned into a throwaway script that calls
`mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 1, 4000)` through the
Windows `python.exe` against the running terminal (the temp script has been
deleted; nothing in the repo changed). Result:

> **3,915 real broker M15 bars overlapped the resampled series.
> OHLC matched to the last cent on all 3,915, and tick volume matched
> exactly on all 3,915. Zero mismatches.**

(The other 85 real M15 bars were newer than the M5 data, not disagreements.)
The resampling therefore carries **no** residual risk worth stating. One
cosmetic footnote: 13 mid-history buckets are short an M5 bar because the M5
dump itself has gaps; they are kept, since a missing M5 bar means no ticks in
those five minutes, not a missing M15 bar. 13 bars out of 33,325.

### Which parameters scale and which do not

This is the part that decides whether an M15 result is meaningful at all.

**Bar-based — scale automatically** (same *number* of bars, 3× the wall
clock, because the replay simply runs the same code on the new series):

| parameter | M5 meaning | M15 meaning |
|---|---|---|
| ATR(14) / ADX(14) | last 70 min | last 3 h 30 min |
| EMA-55 (`EmaLength`) | ~4.5 h | ~13.75 h |
| HalfTrend amplitude 4 | 20 min window | 60 min window |
| `ConfirmCloses` 1 = strict-window waiting bar | wait 5 min | wait 15 min |
| pyramid trigger 1 × ATR | ATR itself grows with the TF | — |
| chop lookback 24, S/R lookback N, ATR-spike lookback 100, the 300-bar regime window the service reads | bars | bars |
| **`CatchupMaxAgeBars` 12** (EA) | 60 min | 180 min — the input is documented as "trade-TF bars", so it scales itself |

**Time-based — NOT scaled, and must not be scaled silently** (identical
wall-clock meaning on both timeframes, exactly like the EA inputs they
mirror):

| parameter | note |
|---|---|
| `MaxDailyExposureMin` 360 | minutes of open-position time per server day. The replay charges 5 min per held M5 bar and 15 min per held M15 bar, so the same budget buys **a third as many bars**. |
| `TradingWindowStartHour/EndHour` 4–23 | server hours |
| pre-break flatten | last bar of the server day: 23:50 (and 23:55) on M5, **23:45** on M15. The M5 rule "hour 23, minute ≥ 50" can never match an M15 bar, which stamps `:00/:15/:30/:45`, so the replay would otherwise never flatten on M15. Live, `FlattenBeforeBreak()` runs on `OnTimer` at 23:54 regardless of timeframe — see caveat 3. |

**Exposure usage, in bars and minutes** (17 months, defaults):

| | avg min/day | avg **bars**/day | max min in a day | days at/over the 360 cap |
|---|---|---|---|---|
| M5 | 268.5 | 53.7 | 665 | 109 of 365 (30%) |
| M15 | 248.4 | 16.6 | 1,020 | 114 of 365 (31%) |

**The 360-minute budget does not need changing.** Held wall-clock time per
day is nearly the same on both timeframes (248 vs 269 min), and the cap binds
on the same ~31% of days. It buys only 16.6 M15 bars instead of 53.7 M5 bars,
but since each M15 trade is held ~3× longer that is the same trading day.

---

## 1. HEAD TO HEAD at today's parameters

`--strict-window`, ADX 10, StopBufferATR 0.75, ProfitTargetPct 2.0, expo 360,
ConfirmCloses 1, TrailLockPct 50 / TrailActivateR 1.0, window 4–23.

| window | TF | trades | trades/mo | win % | net P/L | $/month | max open-equity valley | avg winner | avg loser | avg hold (min) | PF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **last 30 days** | M5 | 83 | 88.6 | 38.6 | **+198.58** | +212 | 510.66 | +61.97 | −34.99 | 68 | 1.11 |
| | M15 | 32 | 34.2 | 53.1 | **+727.82** | +777 | **107.69** | +66.99 | −27.40 | 182 | 2.77 |
| **full 17 months** | M5 | 1,729 | 102.0 | 35.4 | **−3,084.53** | −182 | 3,602.55 | +32.29 | −20.45 | 61 | 0.86 |
| | M15 | 539 | 31.8 | 38.2 | **+483.61** | +29 | **980.03** | +53.14 | −31.42 | 169 | 1.05 |
| **P1** 2025-03-19→09-05 (5.60 mo) | M5 | 590 | 105.3 | 32.5 | −2,147.01 | −383 | 2,495.13 | +54.82 | −31.84 | 59 | 0.83 |
| | M15 | 180 | 32.1 | 36.1 | −357.87 | −64 | 876.40 | +52.86 | −32.99 | 162 | 0.91 |
| **P2** 2025-09-08→2026-02-25 (5.62 mo) | M5 | 572 | 101.8 | 31.3 | −1,684.17 | −300 | 2,374.91 | +58.08 | −30.74 | 58 | 0.86 |
| | M15 | 175 | 31.2 | 37.1 | −39.74 | −7 | 732.33 | +54.91 | −32.81 | 176 | 0.99 |
| **P3** 2026-02-26→08-17 (5.67 mo) | M5 | 536 | 94.6 | 38.1 | −30.03 | −5 | 2,154.60 | +58.42 | −35.99 | 79 | 1.00 |
| | M15 | 184 | 32.5 | 41.3 | **+523.64** | +92 | 618.69 | +51.18 | −31.17 | 167 | 1.16 |

**M15 wins 5 of 5 windows**, including all three sub-periods:

| window | M15 − M5 | per month |
|---|---|---|
| last 30 days | **+529.24** | +565 |
| full 17 months | **+3,568.14** | +210 |
| P1 | +1,789.15 | +319 |
| P2 | +1,644.43 | +293 |
| P3 | +553.67 | +98 |

Month by month (17 months, entry month of the first leg):

* M15 out-earned M5 in **13 of 18** calendar months.
* M15 was **profitable in 12 of 18** months; M5 in **5 of 18**.
* Worst month: M5 2025-08 **−1,472**; M15 2026-01 **−505**.

Drawdown, which is where the difference is most brutal:

| | closed-balance max DD | % of running peak | open-equity valley |
|---|---|---|---|
| M5, 17 mo | $3,556.73 | **82.9 %** | $3,602.55 |
| M15, 17 mo | $954.88 | **21.8 %** | $980.03 |

**M15 is not "a bit better". On the full history M5 loses three quarters of
the account and M15 does not.**

Why, mechanically: the M5 loss is a death-by-a-thousand-cuts problem. 55.1 %
of M5 trades exit on the stop for −$21,059 gross; the strategy pays the
spread and the noise-stop 1,729 times in 17 months. On M15 the same rulebook
takes 539 trades, holds each ~2.8× longer, and the stop rate falls to 50.3 %
with a materially better win rate (38.2 vs 35.4) and a 65 % larger average
winner. Fewer, longer, cleaner — which is exactly what the owner said he saw
on the chart.

---

## 2. The "higher TP" idea, on M15 only

`--profit-target {2,3,4,6}` and target OFF. "Target OFF" is modelled as
`--profit-target 0` = **the EA's own `ProfitTargetPct = 0`**: sizing, pyramid
adds, the 50 %-of-peak profit lock, the shared stop, the reversal exit and the
pre-break flatten all stay exactly as they are; only the "bank at +2 % of
cycle balance" rule is removed. (`--entry-mode fixed` is shown for reference
but is a *different* experiment — it also throws away risk sizing, the adds
and the lock, and trades a flat 0.05 lots.)

Net P/L per window:

| target | 30 d | 17 mo | P1 | P2 | P3 | wins |
|---|---|---|---|---|---|---|
| **2 % (today)** | **+727.82** | **+483.61** | −357.87 | −39.74 | +523.64 | — |
| 3 % | +546.06 | +100.98 | −454.47 | −43.99 | +405.05 | 0/5 |
| 4 % | +554.83 | +198.57 | −370.10 | −121.17 | +269.57 | 0/5 |
| 6 % | +444.62 | +205.90 | −388.17 | −278.58 | +277.45 | 0/5 |
| **OFF** | **+1,087.67** | **+829.58** | −511.93 | +259.97 | +859.87 | **4/5** |
| *(ref)* entry-mode fixed | +870.25 | +9,355.01 | +1,127.60 | +3,576.73 | +4,546.87 | 5/5 |

**A higher number is strictly worse. 3 %, 4 % and 6 % lose to 2 % in every
single window.** The reason is visible in the exit mix (M15, 17 months):

| target | profit target | profit lock | stop | reversal | flatten |
|---|---|---|---|---|---|
| 2 % | 83 (15.4 %) **+8,473** | 93 (17.3 %) +1,530 | 271 (50.3 %) −9,512 | 8 −411 | 84 +404 |
| 3 % | 48 (9.0 %) +6,580 | 126 (23.6 %) +2,541 | 268 −9,112 | 8 −407 | 85 +499 |
| 4 % | 36 (6.8 %) +6,349 | 136 (25.6 %) +3,028 | 268 −9,273 | 8 −407 | 84 +501 |
| 6 % | 20 (3.8 %) +4,827 | 146 (27.5 %) +3,646 | 270 −9,283 | 8 −412 | 86 +1,428 |
| OFF | — | 154 (29.2 %) +4,697 | 269 −9,615 | 8 −407 | **97 +6,155** |

Raising the target does not let winners run — **it hands them to the profit
lock instead**, which arms at 1R and gives back 50 % of the peak. Only 20 of
530 baskets ever reach +6 %. The lock, not the target, is the binding
constraint.

**Removing the target entirely is the version of the owner's intuition that
works**: +$1,088 on 30 days (vs +$728), +$830 over 17 months (vs +$484), and
it wins in 4 of 5 windows. But read the last column before adopting it: with
the target off, **+$6,155 of the +$830 net comes from the 23:45 pre-break
flatten** — the profit is "hold the winner all day and get closed at the end
of the session", concentrated in 97 trades. Combined with `ADX 15 +
StopBufferATR 1.0` it reaches +$3,255 over 17 months (+$192/mo) and is
positive in all five windows, but P1 is only +$56, i.e. barely.

*(`--entry-mode fixed` is spectacular on paper — +$9,355 over 17 months,
positive in every sub-period — but it is a flat 0.05-lot bet with no
compounding and no risk sizing, and it has its own report
(`.superpowers/entry-mode-backtest-report.md`). It is not "a higher TP" and
is out of scope here.)*

---

## 3. Do the other M5-calibrated knobs transfer?

M15, 17 months, `--strict-window`. ADX ∈ {10, 15, 20, 25} × StopBufferATR ∈
{0.5, 0.75, 1.0} × exposure ∈ {360, 720, unlimited} — 36 combinations.

Headline numbers (net over 17 months, $/month in brackets):

| ADX \ stop buffer | 0.50 | 0.75 | 1.00 |
|---|---|---|---|
| **10** (today) | −941.78 (−56) | **+483.61 (+29)** | +1,018.00 (+60) |
| **15** | −177.44 (−10) | +605.35 (+36) | **+1,685.43 (+99)** ← best |
| **20** | −245.76 (−15) | +222.33 (+13) | +706.04 (+42) |
| **25** | −43.75 (−3) | +150.94 (+9) | +441.73 (+26) |

*(exposure 360 shown; 720 and unlimited move every cell by less than $100 over
17 months and never change the ordering — see below.)*

**Exposure: leave it at 360.** M15 17-month net is +483.61 @ 360, +576.96 @
720, +523.16 @ unlimited. A $93 spread over 17 months = $5/month. Irrelevant.

**Best combination: ADX 15, StopBufferATR 1.0, exposure 360.**

| window | M15 defaults (ADX 10 / SB 0.75) | best (ADX 15 / SB 1.0) | delta | delta $/mo |
|---|---|---|---|---|
| 30 d | +727.82 | +787.34 | +59.52 | +64 |
| 17 mo | +483.61 | +1,685.43 | +1,201.82 | **+71** |
| P1 | −357.87 | **+224.05** | +581.92 | +104 |
| P2 | −39.74 | **+655.47** | +695.21 | +124 |
| P3 | +523.64 | +847.89 | +324.25 | +57 |

**Is it beyond noise? No.** The prior noise band on this system is
**±$200/month**; the best combination beats M15-with-M5-defaults by
**+$71/month over 17 months**, and by +$57…+$124/month in the sub-periods.
Every single figure sits inside the noise band.

What *is* notable is the **direction**: the improvement is positive in 5 of 5
windows, and the single knob doing the work is the stop buffer — 1.0 ATR beats
0.75 beats 0.5 in **every one of the 12 ADX × exposure cells**. That
monotonicity across the whole grid is stronger evidence than any one cell's
dollar figure, and it points the same way as the 2026-08-18 noise-stop
autopsy: on M15 the wick-extreme stop is still too tight. Treat
`StopBufferATR 1.0` as a *plausible* follow-up to test, not as a proven win.

ADX itself barely matters between 10 and 15 (it mostly trades volume for
selectivity: 539 → 459 trades); 20 and 25 cut the sample to 297 and 149 trades
for no gain and would make calibration hopeless.

Two more knobs the brief flagged, checked on M15 (17 mo / 30 d, defaults
otherwise):

| knob | 17 mo net | 30 d net | keep? |
|---|---|---|---|
| **ConfirmCloses 1** (today) | **+483.61** | **+727.82** | **yes** |
| ConfirmCloses 2 | −101.16 | +289.20 | no |
| ConfirmCloses 3 | −59.94 | +346.96 | no |
| **TrailLockPct 50** (today) | **+483.61** | **+727.82** | **yes** |
| TrailLockPct 60 / 70 / off | +409.96 / +118.71 / +409.48 | +577.95 / +536.72 / +510.86 | no |
| **TrailActivateR 1.0** (today) | +483.61 | +727.82 | see note |
| TrailActivateR 1.5 | +1,001.79 (+59/mo) | +899.43 | +$30/mo — noise |
| TrailActivateR 2.0 | +750.19 | +798.55 | noise |
| TrailActivateR 0.5 | +946.21 | +565.44 | noise |

`ConfirmCloses 1` and `TrailLockPct 50` transfer cleanly and are still the
best values on M15 — **do not change them.** `TrailActivateR` shows a mild
preference for arming later (1.5R), consistent with the target findings, but
at +$30/month it is far inside the noise band.

---

## 4. The hours question, re-asked on M15

The 13:00–22:00 window won on M5 by **+$475 over 30 days** and **+$1,145 over
17 months** *as a delta against the 4–23 baseline* (reproduced exactly:
M5 30 d +673.41 vs +198.58 = +474.83; M5 17 mo −1,939.14 vs −3,084.53 =
+1,145.39).

**On M15 it does the opposite — 13:00–22:00 loses in every window.**

| window (server) | M15 30 d | M15 17 mo | M15 P1 | M15 P2 | M15 P3 |
|---|---|---|---|---|---|
| **4–23 (today)** | **+727.82** | **+483.61** | −357.87 | −39.74 | +523.64 |
| **13–22** | +172.23 | **−509.53** | −243.84 | −427.02 | −71.84 |
| 4–10 | +392.31 | **+1,677.31** | −325.93 | +898.49 | +927.44 |
| 4–17 | +609.27 | +1,046.77 | −266.85 | +507.43 | +766.04 |
| 4–13 | +547.92 | +937.03 | −296.52 | +358.79 | +864.87 |
| 5–17 | +491.55 | +415.95 | −56.31 | +303.57 | +426.97 |
| 8–17 | +453.96 | +359.14 | +167.61 | −225.41 | +392.63 |

13–22 costs **−$994 over 17 months** and **−$556 over 30 days** relative to
the current 4–23 window. **Do not carry the M5 hours finding across.**

If anything the M15 edge sits in the **morning**: 4–10 is the best 17-month
window (+$1,677, +$99/mo, delta +$1,194). But it is negative in P1
(−$326) and rests on 195 trades in 17 months (11/month), so it is an
overfitting candidate, not a recommendation. **Leave the window at 4–23.**

### Hour-by-hour, M15, 17 months, window 4–23

Trade attributed to the server hour its first leg opened.

| hour | trades | win % | net | avg | worst | avg hold (min) |
|---|---|---|---|---|---|---|
| 04 | 47 | 42.6 | **+536.84** | +11.42 | −58.85 | 196 |
| 05 | 30 | 50.0 | **+580.07** | +19.34 | −46.03 | 200 |
| 06 | 32 | 46.9 | +45.66 | +1.43 | −42.12 | 178 |
| 07 | 26 | 34.6 | −15.60 | −0.60 | −55.53 | 148 |
| 08 | 28 | 57.1 | **+759.48** | +27.12 | −49.70 | 131 |
| 09 | 32 | 40.6 | +55.55 | +1.74 | −51.54 | 225 |
| 10 | 35 | 28.6 | −463.08 | −13.23 | −72.42 | 209 |
| 11 | 25 | 20.0 | −145.21 | −5.81 | −58.33 | 196 |
| 12 | 27 | 33.3 | −52.28 | −1.94 | −50.43 | 158 |
| 13 | 19 | 10.5 | **−396.62** | −20.87 | −38.98 | 141 |
| 14 | 31 | 35.5 | −70.94 | −2.29 | −46.53 | 96 |
| 15 | 33 | 42.4 | +96.36 | +2.92 | −52.40 | 132 |
| 16 | 36 | 44.4 | +240.16 | +6.67 | −53.03 | 164 |
| 17 | 36 | 22.2 | **−610.98** | −16.97 | −272.98 | 225 |
| 18 | 22 | 45.5 | −12.44 | −0.57 | −55.92 | 222 |
| 19 | 18 | 33.3 | −66.92 | −3.72 | −42.18 | 164 |
| 20 | 21 | 38.1 | +35.87 | +1.71 | −39.20 | 161 |
| 21 | 15 | 46.7 | −17.74 | −1.18 | −45.89 | 108 |
| 22 | 26 | 46.2 | −14.58 | −0.56 | −37.54 | 73 |
| **total** | **539** | **38.2** | **+483.61** | | | **169** |

Reading it: M15's money is made **04:00–09:00** (+$1,962 across five hours)
and given back **10:00–14:00** and at **17:00** (−$1,689). The 13:00 and
17:00 hours are the two worst on the whole clock — precisely the hours the
M5 study's 13–22 window keeps. Note the sample sizes: 15–47 trades per hour
over 17 months. **No single hour here carries enough trades to act on.**

---

## 5. Crux numbers

* **Trades per month: M15 ≈ 32 (31.8 over 17 months), M5 ≈ 102.** M15 trades
  **3.2× less often**.
* **M15 beats M5 on the last 30 days (+$529) AND on the full 17 months
  (+$3,568), and in 3 of 3 sub-periods** (+$1,789 / +$1,644 / +$554), and in
  13 of 18 calendar months.
* **Calibration slows by the same 3.2×.** At 31.8 trades/month, **100 trades
  takes ~3.1 months** of live trading on M15, versus ~1.0 month on M5. The
  AI-accuracy log and every threshold sweep that depends on it get three
  times slower. That is the real price of the switch.

---

## 6. Caveats

1. **The 10 % kill switch is not modelled, and both timeframes trip it.**
   `MaxDrawdownPct = 10.0` halts trading at 10 % drawdown from peak equity.
   Over 17 months the replay shows 82.9 % peak-to-trough on M5 and 21.8 % on
   M15. Neither 17-month curve could actually have been traded to completion.
   The comparison stays apples-to-apples — the same omission on both sides,
   and it flatters M5 far more than M15 — but neither absolute P/L is
   "what the account would have made". Same for the daily-loss brake
   (`MaxDailyLossPct 3.0`) and the news blackout, both unmodelled.
2. **The 30-day window is three trades wide.** M15's +$728 over 30 days is
   62 % made of its top 3 winners (M5's +$199 is 192 % made of its top 3).
   The 17-month and sub-period numbers are the ones to trust.
3. **The M15 flatten sits at 23:45, not 23:54.** Live, `FlattenBeforeBreak()`
   runs on `OnTimer` and fires at 23:54 wall-clock on any timeframe; the
   replay can only act at a bar close, which on M15 is the bar stamped 23:45
   (closing at 00:00). That is a ~6-minute price approximation on ~84 trades
   at target 2 % — small there, but it matters a lot in the *target OFF*
   scenario, where 97 flatten exits carry +$6,155 of the profit.
4. The replay does not model slippage beyond a flat $0.20/oz round-trip
   spread, margin, or the M15 catch-up path after downtime.

## 7. If we adopt M15 — exact EA input changes

Minimum change (recommended):

```
TradeTimeframe = PERIOD_M15    // was PERIOD_M5
```

**That is the whole change.** Everything else stays: `AdxTrendThreshold 10`,
`StopBufferATR 0.75`, `ProfitTargetPct 2.0`, `MaxDailyExposureMin 360`,
`CatchupMaxAgeBars 12`, `ConfirmCloses 1`, `TrailLockPct 50`,
`TrailActivateR 1.0`, `TradingWindowStartHour 4`, `TradingWindowEndHour 23`.
Each was re-checked above and none of them has a change that clears the
±$200/month noise bar.

Operational notes for the switch:
* The MT5 chart timeframe is cosmetic — `XauAssistant.mq5:508` warns when
  `Period() != TradeTimeframe`, so set the chart to M15 too, for sanity.
* `CatchupMaxAgeBars 12` silently becomes a 3-hour catch-up window (12 M15
  bars) instead of 1 hour. If a 3-hour-old signal is not acceptable, drop it
  to **4** to preserve the 1-hour meaning. This is the one bar-based input
  whose *wall-clock* meaning may matter more than its bar meaning.
* Expect ~32 trades/month instead of ~100. Telegram will go quiet by
  comparison; that is the intended effect, not a fault.

Candidate follow-up, **not** recommended yet (all inside noise, needs its own
forward test): `StopBufferATR 1.0` (+$71/mo, and the only knob that improves
monotonically across all 12 grid cells) and `ProfitTargetPct 0` (+$192/mo
combined, but leaning on the end-of-day flatten).

---

## Reproducing

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
python3 scripts/backtest.py --source bars_max.json --strict-window                       # M5, 17 mo
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15              # M15, 17 mo
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15 --days 30
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15 --profit-target 0
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15 --adx 15 --stop-buffer 1.0
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15 --hour-table
python3 scripts/backtest.py --source bars_max.json --strict-window --tf M15 --window-start 13 --window-end 22
```

Sub-periods use `--start/--end` at `2025-09-07` and `2026-02-26`. Hold times,
per-month splits and exposure-in-bars came from a scratch driver importing
`backtest.run` directly (the CLI summary does not print them); every net /
trade-count / win% / valley figure above is reproducible from the commands
here.
