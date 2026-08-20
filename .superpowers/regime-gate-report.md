# Regime-gate backtest report (2026-08-16)

Question: keep ADX-10 trade frequency (~4-6/day) but sit out RANGES specifically, using the
service's live regime classifier (`service/app/regime.py`) as the entry gate.

## Method

- New flag `scripts/backtest.py --regime-gate {off,range,range-strict}` (commit on main, `feat(backtest):`).
- Classifier: `app.regime.classify_regime` is **imported and called directly** (no port) on the exact
  window the EA posts to `/analyze`: the last **300 closed bars** ending at the acted bar
  (`AiApi.mqh` → `CopyRates(..., 1, 300, ...)`). Its math, unchanged: ATR(14) (SMA-seeded Wilder);
  `high_volatility` if the current ATR ranks in the top 20 % of its last 100 values
  (`vol_percentile=0.8`); else `trend` if ADX(14) ≥ 25 (`adx_threshold=25`); else `range`.
  Note the service's ADX is Wilder-**sum** seeded on the 300-bar window, so it is *not* numerically
  identical to the backtester's own ADX(14) used for the `--adx` gate — that's intentional: the gate
  replays what the service actually says.
- `range` gate refuses **new entries** whose bar regime is `range`; `range-strict` also refuses
  `high_volatility` (only `trend` bars may enter). Adds / exits / stops / flatten untouched.
- Every trade is tagged with its entry regime; `--regime-gate off` prints the per-regime P/L
  breakdown, which is how "what did the gate skip" is measured (baseline trades whose entry bar was
  `range` = what the range gate would have refused, first-order — the gated path then diverges,
  because a freed basket exposes later candidate entries, so refused counts in gated runs are a
  bit higher than baseline range-tagged trades).
- Baseline verified byte-identical before sweeping: `--regime-gate off` → net **−3,100.21**,
  2,161 trades, valley 4,717.23 (matches param-sweep-report). ADX30 reference → +697.45 / 180 /
  864.10 (matches sweep row `30|1|0.75`).
- All runs: `--balance 4700 --expo 360 --risk 1 --confirm 1 --stop-buffer 0.75 --source bars_max.json`
  (sub-periods = equal-thirds slices of the dump, each restarted at $4,700 with its own indicator
  warm-up, same convention as the param sweep). Trades/day = closed trades ÷ distinct server dates
  with candles in the window.
- Simplifications inherited from the replay: no daily-loss brake, no news blackout, spread $0.20/oz,
  close-based fills, and balance compounds inside a window (the 17-month baseline shrinks to
  $1,600 so late trades are sized smaller).


## Full 17 months (2025-03-14 → 2026-08-12, 365 trading days, fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused by gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A baseline (ADX10, gate off) | 2161 | 5.92 | -3,100 | 36.2 | 4,717 | +41 | -26 | — |
| B ADX10 + gate range | 893 | 2.45 | -2,583 | 33.8 | 3,721 | +47 | -28 | 1506 (range 1506) |
| C ADX10 + gate range-strict | 279 | 0.76 | -808 | 32.6 | 1,723 | +61 | -34 | 2149 (range 1529, high_volatility 620) |
| D ADX30 (reference) | 180 | 0.49 | +697 | 40.0 | 864 | +65 | -37 | — |
| E ADX15 + gate range | 859 | 2.35 | -2,669 | 33.9 | 4,002 | +45 | -28 | 1126 (range 1126) |

Entry-regime breakdown of the **baseline (A)** trades in this window — i.e. what a gate skips and what those entries actually made:

| entry regime (service classifier) | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 253 | -697 | 35.2 |
| range | 1333 | -798 | 36.3 |
| high_volatility | 575 | -1,605 | 36.3 |

Entry-regime breakdown of the ADX30 reference (D):

| entry regime | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 98 | +946 | 45.9 |
| high_volatility | 82 | -249 | 32.9 |

## Last 30 days (23 trading days, fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused by gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A baseline (ADX10, gate off) | 116 | 5.04 | +1,132 | 45.7 | 468 | +76 | -46 | — |
| B ADX10 + gate range | 56 | 2.43 | +857 | 46.4 | 360 | +87 | -47 | 79 (range 79) |
| C ADX10 + gate range-strict | 19 | 0.83 | -50 | 36.8 | 260 | +63 | -41 | 117 (high_volatility 37, range 80) |
| D ADX30 (reference) | 15 | 0.65 | +587 | 66.7 | 155 | +81 | -44 | — |
| E ADX15 + gate range | 53 | 2.30 | +562 | 45.3 | 336 | +79 | -46 | 62 (range 62) |

Entry-regime breakdown of the **baseline (A)** trades in this window — i.e. what a gate skips and what those entries actually made:

| entry regime (service classifier) | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 17 | -122 | 35.3 |
| range | 65 | +595 | 44.6 |
| high_volatility | 34 | +659 | 52.9 |

Entry-regime breakdown of the ADX30 reference (D):

| entry regime | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 8 | +335 | 75.0 |
| high_volatility | 7 | +252 | 57.1 |

## P1 2025-03-14 → 2025-09-02 (122 trading days, fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused by gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A baseline (ADX10, gate off) | 737 | 6.04 | -2,189 | 34.9 | 3,367 | +69 | -42 | — |
| B ADX10 + gate range | 323 | 2.65 | -1,797 | 33.4 | 2,769 | +63 | -40 | 493 (range 493) |
| C ADX10 + gate range-strict | 84 | 0.69 | -793 | 31.0 | 1,129 | +55 | -38 | 737 (range 497, high_volatility 240) |
| D ADX30 (reference) | 56 | 0.46 | -276 | 39.3 | 595 | +46 | -38 | — |
| E ADX15 + gate range | 309 | 2.53 | -1,755 | 33.7 | 2,818 | +63 | -41 | 364 (range 364) |

Entry-regime breakdown of the **baseline (A)** trades in this window — i.e. what a gate skips and what those entries actually made:

| entry regime (service classifier) | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 76 | -483 | 31.6 |
| range | 437 | -278 | 35.7 |
| high_volatility | 224 | -1,428 | 34.4 |

Entry-regime breakdown of the ADX30 reference (D):

| entry regime | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 26 | -102 | 42.3 |
| high_volatility | 30 | -174 | 36.7 |

## P2 2025-09-02 → 2026-02-23 (123 trading days, fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused by gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A baseline (ADX10, gate off) | 722 | 5.87 | -1,785 | 32.5 | 3,335 | +75 | -40 | — |
| B ADX10 + gate range | 277 | 2.25 | -173 | 33.2 | 973 | +78 | -40 | 534 (range 534) |
| C ADX10 + gate range-strict | 91 | 0.74 | -301 | 30.8 | 650 | +75 | -38 | 725 (high_volatility 186, range 539) |
| D ADX30 (reference) | 59 | 0.48 | +59 | 37.3 | 262 | +67 | -38 | — |
| E ADX15 + gate range | 267 | 2.17 | -549 | 32.2 | 1,061 | +75 | -38 | 394 (range 394) |

Entry-regime breakdown of the **baseline (A)** trades in this window — i.e. what a gate skips and what those entries actually made:

| entry regime (service classifier) | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 79 | -649 | 26.6 |
| range | 473 | -1,698 | 32.6 |
| high_volatility | 170 | +562 | 35.3 |

Entry-regime breakdown of the ADX30 reference (D):

| entry regime | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 32 | +327 | 43.8 |
| high_volatility | 27 | -268 | 29.6 |

## P3 2026-02-23 → 2026-08-12 (122 trading days, fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused by gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A baseline (ADX10, gate off) | 677 | 5.55 | +2,299 | 38.7 | 1,616 | +87 | -49 | — |
| B ADX10 + gate range | 291 | 2.39 | +208 | 36.4 | 1,010 | +66 | -37 | 477 (range 477) |
| C ADX10 + gate range-strict | 103 | 0.84 | +520 | 37.9 | 500 | +84 | -43 | 680 (range 487, high_volatility 193) |
| D ADX30 (reference) | 65 | 0.53 | +1,085 | 43.1 | 238 | +92 | -40 | — |
| E ADX15 + gate range | 280 | 2.30 | -355 | 36.8 | 1,246 | +58 | -36 | 366 (range 366) |

Entry-regime breakdown of the **baseline (A)** trades in this window — i.e. what a gate skips and what those entries actually made:

| entry regime (service classifier) | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 92 | +391 | 37.0 |
| range | 409 | +2,430 | 40.3 |
| high_volatility | 176 | -522 | 35.8 |

Entry-regime breakdown of the ADX30 reference (D):

| entry regime | trades | net P/L $ | win % |
|---|---:|---:|---:|
| trend | 39 | +1,015 | 48.7 |
| high_volatility | 26 | +71 | 34.6 |

## Reading

1. **The range gate does not preserve ADX-10 frequency.** It removes ~62 % of the baseline
   entries (1,333 of 2,161 baseline trades were tagged `range`; the gated run refused 1,506
   candidates) → 2.45 trades/day vs 5.92. It is not "ADX-10 minus the ranges", it is
   effectively an ADX-25 gate that additionally lets high-volatility bars through — because
   `regime.py`'s "range" IS "ADX(14) < 25 and not top-20 % ATR". There is no independent
   range detector in the live service to exploit.
2. **What it skipped was not where the money was lost.** Over 17 months the baseline's `range`
   entries lost −798 on 1,333 trades (−$0.60/trade, 36 % win — noise). The `high_volatility`
   entries lost −1,605 on 575 trades (−$2.79/trade) — the worst bucket by far, and the range gate
   keeps them. `trend` entries (ADX ≥ 25 by the service math) also lost −697 on 253.
3. **The skip is regime-dependent, not robust.** Baseline `range` trades: P1 −278, P2 −1,698,
   P3 **+2,430**, last-30d **+595**. In the most recent 5.7 months and the last 30 days the range
   gate skipped the *winners* (P3 net falls from +2,299 to +208; 30d from +1,132 to +857).
   The gate only "worked" in P2, where range entries bled −1,698.
4. **range-strict** (trend-only, 0.76/day, −808 over 17 mo, −50 last 30 d) is strictly worse than
   the owner's ADX30 safe harbor (0.49/day, +697, +587) — the service's ADX 25 threshold is below
   the ADX 30 sweet spot found in the sweep, and the extra trades in 25–30 lose.
5. **E (ADX15 + range gate)** ≈ B with slightly fewer trades and slightly worse P/L (−2,669,
   2.35/day, negative in every sub-period). Not a middle ground worth anything.
6. Nothing at ≥ 2 trades/day is profitable over 17 months in this replay. Every variant that
   keeps ADX-10-like frequency stays deep negative; only ADX 30 (0.5/day) is positive on both
   horizons, exactly as the param sweep found.

## Bottom line

Using the live regime classifier as an entry filter buys ~$500 of the 17-month loss back
(−3,100 → −2,583), cuts the equity valley from 4,717 to 3,721, but throws away 60 % of the trades
the owner wants to keep, and in the recent period/last 30 days it removed net winners. It does not
solve "sit out ranges, keep frequency": with this strategy the whipsaw losses are spread across all
three regimes (and are worst in high-volatility bars), so a regime filter cannot separate them.
If a filter is wanted, ADX 30 remains the only evidence-backed one, at the frequency the owner rejects.
