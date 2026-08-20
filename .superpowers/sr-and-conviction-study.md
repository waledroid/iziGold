# S/R proximity & AI conviction — two studies

Date: 2026-08-18
Instrument/strategy: XAUUSD M5, `halftrend_ema_v1`, **strict-window entry rule
ON in every run** (ADX 10, confirm 1, EMA 55, risk 1%, expo 360, start
balance $4,000).
Data: `bars_max.json` (99,999 M5 bars, 2025-03-19 12:00 → 2026-08-17 13:20
server time = 17 months, 365 trading days); `service/xau_assistant.db`
(read-only, signals 2026-08-03 → 2026-08-18).
Code: `scripts/backtest.py` @ `a2d08fc` (new flags `--sr-lookback`,
`--sr-min-headroom`, `--sr-report`; default off, byte-identical — see §1.3).

**Scope note:** this work touched only `scripts/backtest.py` (an analysis
tool). Nothing under `mt5/` or `service/app/` was modified, so live system
behavior is unchanged and no `izi.md` update is required for this commit.

---

## STUDY A — support/resistance proximity

**Hypothesis.** An entry whose immediate path is blocked by a recent level
does worse, because the first thing price meets is a wall.

### A.1 How it was implemented

At the entry bar the replay builds the level set that was actually visible at
that moment:

* **Fractal swing highs/lows** over the last N bars: bar *j* is a swing high
  when its high is the max of the k=2 bars on each side (mirrored, with min,
  for lows). Only pivots already **confirmed** at the entry bar count
  (`j + k <= i`) — no lookahead.
* **The previous completed server day's high and low.**
* **The current session's high and low**, measured over the bars *strictly
  before* the entry bar.
* Levels are sorted and **de-duplicated within 0.25 × ATR(14)** — two levels a
  quarter-ATR apart are the same wall.

`headroom` = distance from the fill (the decision bar's close) to the
**nearest opposing level** — for a BUY the nearest level at or above, for a
SELL the nearest at or below — in ATR(14) units. If nothing lies ahead the
headroom is "clear" and the entry is never refused.

One implementation decision is load-bearing and worth stating: **the entry bar
itself is excluded from the session high/low.** That bar's own high is ≥ its
close by construction, so including it would plant a "level" a few cents
overhead on literally every BUY and drown the signal. As defined, a bar that
breaks the session high simply has no session-high level above it, which is
the correct reading.

Verified on synthetic data: a strictly-varying zig-zag yields exactly the true
peak and trough; a pivot at bar *i* is invisible until bar *i+2* (no
lookahead); levels 0.2 ATR apart collapse, 0.4 ATR apart do not.

### A.2 THE CRUX — diagnostic on all baseline trades

`--sr-report` tags every baseline trade without refusing anything, so this is
the untouched live baseline.

**17 months — 1,729 trades, 35.4% win, net −$3,084.53, −$1.784/trade**

N = 100:

| headroom | trades | win% | net $ | avg $ | winners $ | losers $ |
|---|---|---|---|---|---|---|
| <0.5 ATR | 1192 | 35.2 | −1929.71 | −1.62 | +13633.99 | −15563.70 |
| 0.5–1 ATR | 301 | 32.6 | −1366.40 | **−4.54** | +3105.64 | −4472.04 |
| 1–2 ATR | 131 | 38.2 | +297.58 | +2.27 | +1848.96 | −1551.38 |
| >2 ATR | 69 | 42.0 | −62.06 | −0.90 | +746.03 | −808.09 |
| clear | 36 | 41.7 | −23.94 | −0.66 | +424.12 | −448.06 |

N = 300:

| headroom | trades | win% | net $ | avg $ | winners $ | losers $ |
|---|---|---|---|---|---|---|
| <0.5 ATR | 1376 | 35.0 | −2526.76 | −1.84 | +15631.19 | −18157.95 |
| 0.5–1 ATR | 227 | 35.2 | −604.47 | −2.66 | +2481.26 | −3085.73 |
| 1–2 ATR | 72 | 41.7 | +126.16 | +1.75 | +1092.78 | −966.62 |
| >2 ATR | 18 | 33.3 | −55.53 | −3.08 | +129.39 | −184.92 |
| clear | 36 | 41.7 | −23.94 | −0.66 | +424.12 | −448.06 |

**Last 30 days — 83 trades, 38.6% win, net +$198.58, +$2.393/trade**

N = 100:

| headroom | trades | win% | net $ | avg $ |
|---|---|---|---|---|
| <0.5 ATR | 49 | 34.7 | +67.66 | +1.38 |
| 0.5–1 ATR | 18 | 33.3 | −77.71 | −4.32 |
| 1–2 ATR | 9 | 33.3 | +49.12 | +5.46 |
| >2 ATR | 5 | 80.0 | +122.89 | +24.58 |
| clear | 2 | 100.0 | +36.62 | +18.31 |

N = 300:

| headroom | trades | win% | net $ | avg $ |
|---|---|---|---|---|
| <0.5 ATR | 59 | 37.3 | **+209.26** | **+3.55** |
| 0.5–1 ATR | 14 | 35.7 | −82.38 | −5.88 |
| 1–2 ATR | 6 | 33.3 | +39.18 | +6.53 |
| >2 ATR | 2 | 50.0 | −4.10 | −2.05 |
| clear | 2 | 100.0 | +36.62 | +18.31 |

**Verdict on the crux: the win rate does NOT fall as headroom shrinks. The
idea is dead.**

Concretely:

1. **Not monotone in 9 of 10 period × lookback combinations.** The only "YES"
   is P2 at N=300 (one sub-period out of ten tests) — exactly what you expect
   from noise when you run ten tests.
2. **The tightest bucket is never the worst.** Over 17 months at N=100 the
   <0.5 ATR bucket loses −$1.62/trade while the 0.5–1 ATR bucket loses
   −$4.54/trade — and −$1.62 is *better* than the −$1.78 baseline average. If
   the hypothesis were true the ordering would be the reverse.
3. **The most recent 30 days point the other way.** At N=300 the <0.5 ATR
   bucket is the single most profitable bucket (+$209 of the +$199 total).
   Filtering it out would have cost money in the period the owner cares about
   most.
4. **"Blocked path" is the normal state, not an exception.** Sampling real
   bars: N=100 yields a median of 18 levels per entry and a median headroom of
   0.35 ATR; N=300 yields 38 levels and 0.25 ATR. 62% (N=100) / 76% (N=300) of
   all bars sit within 0.5 ATR of a level. With ATR ≈ $4.33 and levels roughly
   0.5–1 ATR apart, a random price is ~0.25 ATR from the nearest one *by
   geometry*. A 0.5 ATR floor removes two-thirds of entries essentially at
   random with respect to outcome.

### A.3 The filter runs vs baseline

| period | variant | trades | win% | net $ | avg/trade | maxDD | refused |
|---|---|---|---|---|---|---|---|
| **last 30 days** | baseline | 83 | 38.6 | +198.58 | +2.39 | 506.86 | 0 |
| | N100 X0.5 | 37 | 43.2 | +63.39 | +1.71 | 202.56 | 52 |
| | N100 X1 | 19 | 57.9 | +375.09 | +19.74 | 86.24 | 72 |
| | N100 X1.5 | 11 | 72.7 | +287.91 | +26.17 | 48.10 | 80 |
| | N300 X0.5 | 25 | 36.0 | −110.72 | −4.43 | 253.20 | 67 |
| | N300 X1 | 11 | 45.5 | +58.68 | +5.33 | 114.33 | 81 |
| | N300 X1.5 | 7 | 57.1 | +67.72 | +9.67 | 43.70 | 85 |
| **17 months** | baseline | 1729 | 35.4 | −3084.53 | −1.78 | 3556.73 | 0 |
| | N100 X0.5 | 565 | 32.9 | −1804.57 | −3.19 | 2083.95 | 1263 |
| | N100 X1 | 251 | 36.3 | −70.96 | −0.28 | 910.56 | 1597 |
| | N100 X1.5 | 142 | 39.4 | +175.49 | +1.24 | 654.80 | 1709 |
| | N300 X0.5 | 372 | 35.5 | −709.35 | −1.91 | 1235.56 | 1470 |
| | N300 X1 | 134 | 37.3 | −36.60 | −0.27 | 607.87 | 1719 |
| | N300 X1.5 | 74 | 37.8 | −49.40 | −0.67 | 463.21 | 1782 |
| **P1** 2025-03-19..2025-09-01 | baseline | 560 | 32.9 | −1915.29 | −3.42 | 2206.72 | 0 |
| | N100 X0.5 | 191 | 30.4 | −1256.15 | −6.58 | 1292.30 | 404 |
| | N100 X1 | 88 | 33.0 | −277.38 | −3.15 | 512.03 | 518 |
| | N100 X1.5 | 52 | 40.4 | −19.85 | −0.38 | 316.11 | 555 |
| | N300 X0.5 | 127 | 32.3 | −866.06 | −6.82 | 1004.94 | 477 |
| | N300 X1 | 53 | 32.1 | −256.03 | −4.83 | 607.87 | 554 |
| | N300 X1.5 | 32 | 40.6 | −9.44 | −0.29 | 307.32 | 576 |
| **P2** 2025-09-01..2026-02-15 | baseline | 567 | 30.7 | −1735.53 | −3.06 | 1848.19 | 0 |
| | N100 X0.5 | 170 | 31.8 | −881.35 | −5.18 | 1021.66 | 440 |
| | N100 X1 | 72 | 40.3 | +280.61 | +3.90 | 390.90 | 541 |
| | N100 X1.5 | 38 | 42.1 | +314.51 | +8.28 | 235.71 | 575 |
| | N300 X0.5 | 107 | 43.0 | +346.19 | +3.24 | 572.77 | 504 |
| | N300 X1 | 39 | 48.7 | +436.22 | +11.19 | 187.56 | 575 |
| | N300 X1.5 | 16 | 43.8 | +94.59 | +5.91 | 183.02 | 598 |
| **P3** 2026-02-15..2026-08-17 | baseline | 574 | 38.0 | +80.64 | +0.14 | 2188.03 | 0 |
| | N100 X0.5 | 204 | 39.2 | −67.61 | −0.33 | 1109.94 | 417 |
| | N100 X1 | 92 | 37.0 | −61.52 | −0.67 | 728.72 | 537 |
| | N100 X1.5 | 53 | 37.7 | +84.76 | +1.60 | 365.26 | 578 |
| | N300 X0.5 | 138 | 37.7 | −312.76 | −2.27 | 791.94 | 489 |
| | N300 X1 | 43 | 37.2 | −41.64 | −0.97 | 423.15 | 589 |
| | N300 X1.5 | 27 | 33.3 | −121.81 | −4.51 | 280.19 | 607 |

The X=0.5 filter — the only setting that still leaves a tradeable number of
entries — makes the **per-trade** result *worse* than baseline in 8 of 10
cases. X=1.5 shows attractive per-trade numbers but keeps only 4–11% of
entries; that is not a filter, it is an off switch, and its sign is unstable
(P3 N300 X1.5 = −$4.51/trade, the worst cell in the table).

### A.4 Opportunity cost, and the control that kills it

Dollars of winners skipped vs dollars of losers avoided, 17 months, N=100:

| floor | trades skipped | winners forgone | losers avoided | naive net effect |
|---|---|---|---|---|
| 0.5 ATR | 1192 (69%) | +$13,633.99 | −$15,563.70 | +$1,929.71 |
| 1.0 ATR | 1493 (86%) | +$16,739.63 | −$20,035.75 | +$3,296.12 |
| 1.5 ATR | 1593 (92%) | +$17,821.51 | −$21,284.75 | +$3,463.24 |

Those "net effect" numbers look like a win — and they are the trap that killed
the earlier studies. **The baseline loses money, so removing any large block
of trades improves the total.** The honest control is: *does the kept set beat
a random cull of the same size?*

17 months, N=100, keeping headroom ≥ X:

| floor | kept | kept net | random-cull expectation | percentile of 2000 random culls | t (kept avg vs baseline avg) |
|---|---|---|---|---|---|
| 0.5 ATR | 537 (31%) | −$1,154.82 | **−$958.01** | 38th | −0.25 |
| 1.0 ATR | 236 (14%) | +$211.59 | −$421.02 | 92nd | +1.21 |
| 1.5 ATR | 136 (8%) | +$378.71 | −$242.62 | 95th | +1.49 |

At X=0.5 the S/R filter is **worse than picking 537 trades at random**. At
X=1.0/1.5 it is inside the noise band, and the equivalent N=300 runs give
+0.69 / +1.06, while P3 gives −0.20 / −0.03 and P1 N300 X0.5 gives −0.75. No
t-statistic anywhere in the study reaches 2.0. Across all periods and both
lookbacks the range is **−0.75 to +1.83**, with the sign flipping between
sub-periods.

### A.5 Verdict — Study A

**Dead.** The premise fails at the diagnostic stage: proximity to a level does
not predict a worse outcome. What little apparent improvement the filter
produces is turnover reduction in a losing baseline, and at the one threshold
that leaves a usable number of trades it underperforms random selection. Do
not build this.

---

## STUDY B — AI conviction (Chronos-Bolt)

No new logging was required. `service/app/analysis.py` already computes

```
move  = q50[-1] - last_close
band  = (q90[-1] - q10[-1]) / 2
confidence = |move| / (|move| + band)          # signal over uncertainty
```

and stores it per signal in `signals.confidence`.

**Join method.** `signals.bar_time` is **server time (GMT+3)**;
`trades.ts` is **UTC**. Verified on the first executed basket: signal #17,
bar_time 08-03 21:40, price 4041.49 → trade #1, open 08-03 18:45 UTC, price
4041.65 — i.e. `bar_time − 3h + one M5 bar` (the EA acts at the close of the
signal bar). Across all matches the join lag is 5 min (median) and the
entry-vs-signal price gap is $0.08 (median), so the join is tight. A basket is
one `open` event plus every event up to the next `open` (the `final` flag is
not trustworthy — several partial closes carry `final=1`). 38 baskets, net
+$458.46; 34 matched to a preceding same-direction signal of the same
strategy, 4 unmatched.

### B.1 Executed baskets by confidence (real dollars)

| confidence | baskets | win% | net $ | avg $ |
|---|---|---|---|---|
| 0–0.1 | 10 | 50.0 | +186.12 | +18.61 |
| 0.1–0.25 | 20 | 50.0 | +272.27 | +13.61 |
| 0.25–0.5 | 4 | 50.0 | +48.48 | +12.12 |
| >0.5 | 0 | — | — | — |

Collapsed: `cf < 0.10` → 10 baskets, 50.0% win, +$186.12, **+$18.61/trade**;
`cf ≥ 0.10` → 24 baskets, 50.0% win, +$320.75, **+$13.36/trade**.

**In real money there is no effect — if anything it is mildly reversed.** Win
rate is exactly 50% in every bucket and every bucket is profitable. With 34
trades this is far too small to conclude anything; it is reported to be
honest, not because it settles the question.

### B.2 All resolved signals by confidence (the meaningful sample)

109 resolved BUY/SELL signals, forward horizon 16 bars (80 min). "win%" =
the 80-minute forward move went the way the **strategy's** signal pointed;
"AI dir ok" = `ai_correct`, the AI's own directional call.

**All strategies (109):**

| confidence | signals | win% | net $/oz | avg $/oz | AI dir ok |
|---|---|---|---|---|---|
| 0–0.1 | 36 | **33.3** | −117.42 | **−3.262** | 39.3% |
| 0.1–0.25 | 58 | 58.6 | +141.94 | +2.447 | 60.3% |
| 0.25–0.5 | 15 | 60.0 | +160.28 | +10.685 | 60.0% |
| >0.5 | 0 | — | — | — | — |

**`halftrend_ema_v1` only (78):**

| confidence | signals | win% | net $/oz | avg $/oz | AI dir ok |
|---|---|---|---|---|---|
| 0–0.1 | 28 | 35.7 | −131.69 | −4.703 | 38.1% |
| 0.1–0.25 | 39 | 64.1 | +143.70 | +3.685 | 59.0% |
| 0.25–0.5 | 11 | 54.5 | +134.43 | +12.221 | 63.6% |
| >0.5 | 0 | — | — | — | — |

This is a **clean, monotone relationship** and it is the opposite of Study A's
result: low conviction really does mark signals that go the wrong way.

### B.3 Threshold sweep

Resolved signals below vs above each confidence threshold:

| threshold | n below | win% below | avg below | t | n above | win% above | avg above |
|---|---|---|---|---|---|---|---|
| 0.05 | 21 | 57.1 | +2.404 | +0.23 | 88 | 48.9 | +1.526 |
| **0.10** | **36** | **33.3** | **−3.262** | **−2.00** | **73** | **58.9** | **+4.140** |
| 0.15 | 56 | 42.9 | −0.904 | −1.39 | 53 | 58.5 | +4.442 |
| 0.20 | 77 | 44.2 | −0.655 | −1.52 | 32 | 65.6 | +7.350 |
| 0.25 | 94 | 48.9 | +0.261 | −1.04 | 15 | 60.0 | +10.685 |
| 0.30 | 101 | 48.5 | +0.370 | −1.01 | 8 | 75.0 | +18.424 |

Overall resolved-signal mean forward move: +1.695 $/oz over 109 signals.
**0.10 is the only threshold with a real edge**, and the effect is sharp there
rather than gradual.

### B.4 Robustness

* **Time split** — holds in both halves, so it is not one bad week:
  08-03..08-07 → `cf<0.1` 21 signals, 38.1% win, −2.390 avg; `cf≥0.1` 33
  signals, 54.5% win, +3.995. 08-10..08-18 → `cf<0.1` 15 signals, 26.7% win,
  −4.483; `cf≥0.1` 40 signals, 62.5% win, +4.260.
* **Permutation test** — the `cf<0.1` group's mean (−3.262) vs 20,000 random
  36-signal draws from the same pool: **p = 0.0063** (one-sided).
* **Not a regime proxy** — mean confidence by regime is 0.167 (trend) / 0.148
  (range) / 0.119 (high_volatility), while mean forward move is −1.003 /
  +1.797 / +4.737. High-volatility bars have the *lowest* confidence and the
  *highest* forward move, so confidence is not smuggling in the regime
  classifier or the chop filter.
* **Not just "small moves"** — mean |forward move| by bucket is 10.474 /
  9.067 / 17.452 $/oz. The 0–0.1 bucket has near-average move *magnitude* but
  only 33% directional accuracy. It is a genuine direction-quality signal, not
  a volatility measure.
* **Agreement split** — where the AI's direction agrees with the signal:
  56 signals, 53.6% win, +4.447 avg. Where it disagrees: 45 signals, 44.4%
  win, −1.862 avg. Where neutral: 8 signals, 62.5% win, +2.445 avg. Note the
  8 `neutral`/`0.0` rows are *not* the losers — the damage sits in the
  low-confidence *directional* reads.

### B.5 Verdict — Study B

**There is a real candidate threshold: confidence < 0.10.** Below it, signals
went the wrong way 67% of the time and averaged −3.3 $/oz over the next 80
minutes; above it they went the right way 59% of the time and averaged
+4.1 $/oz. The pattern is monotone across buckets, survives a time split,
survives a permutation test (p = 0.006), and is not explained by regime or by
move size.

**But be blunt about the limits:**

1. **The dollar-level evidence is absent.** The 34 executed baskets show
   exactly 50% win in every bucket and the low-confidence ones actually earned
   *more* per trade (+$18.61 vs +$13.36). The effect exists only in the
   80-minute forward-move proxy, which is not the same thing as a trade
   outcome with stops, targets, pyramiding and variable holding time.
2. **The sample is 109 signals over 15 days** (78 for `halftrend_ema_v1`),
   and the 0.10 threshold was picked after seeing the buckets — one t = −2.00
   out of six thresholds tested is not a discovery, it is a lead.
3. **The AI has never produced a high-conviction call.** Confidence maxes out
   at **0.42**; the `>0.5` bucket is empty in every table and cannot be
   evaluated at all. Every verdict logged so far is `neutral`.
4. **This is grading-only evidence.** Project rule #1/#2: the strategy decides
   and the AI is never in the trade path. A positive result here justifies a
   **sizing** rule at most — e.g. half risk when confidence < 0.10 — and only
   after several months more data. It does **not** justify a veto, and a veto
   would also break the fail-open rule (#3): the AI being down would then have
   to mean "trade nothing".

---

## Recommendations

* **Study A: drop it.** The S/R filter is not worth building. The flags are
  committed so the result is reproducible and nobody re-runs this experiment.
* **Study B: keep collecting, do not wire anything yet.** The conviction signal
  is the first AI-derived measure in this project that has shown a real,
  robust-looking edge. The right next step is to *wait* — the sample needs to
  roughly triple before a sizing rule is defensible. Re-run
  `.superpowers/` conviction analysis at ~300 resolved signals and check
  whether the `cf < 0.10` bucket is still negative **in executed dollars**,
  not just in forward moves.
* If it survives that, the maximum defensible change is a **half-risk sizing
  rule** below confidence 0.10, service-side, still fail-open (AI unavailable
  → full size, as today).

## Reproduction

```bash
cd /mnt/c/Users/aatanda/Desktop/xau

# Study A diagnostic (the crux)
python3 scripts/backtest.py --source bars_max.json --strict-window \
  --adx 10 --confirm 1 --ema-len 55 --risk 1 --expo 360 \
  --sr-lookback 100 --sr-report

# Study A filter
python3 scripts/backtest.py --source bars_max.json --strict-window \
  --adx 10 --confirm 1 --ema-len 55 --risk 1 --expo 360 \
  --sr-lookback 100 --sr-min-headroom 1.0

# byte-identical default-off check (30 days)
git show aa85df9:scripts/backtest.py > /tmp/head.py
PYTHONPATH=service python3 /tmp/head.py --source bars_max.json --days 30 \
  --strict-window --adx 10 --confirm 1 --ema-len 55 --risk 1 --expo 360 > /tmp/a
python3 scripts/backtest.py --source bars_max.json --days 30 \
  --strict-window --adx 10 --confirm 1 --ema-len 55 --risk 1 --expo 360 > /tmp/b
cmp /tmp/a /tmp/b     # identical
```

Study B was run from a standalone read-only script against
`service/xau_assistant.db` (no schema or service change).
