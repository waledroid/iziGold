# High-volatility entry-gate backtest report (2026-08-16)

Question: keep ADX 10's ~5.9 trades/day but skip NEW ENTRIES ONLY when volatility is spiking
(ATR relative to its normal) — the previous run (regime-gate-report.md) showed the baseline's
`high_volatility`-tagged entries were the worst bucket over 17 months (575 trades, −$1,605).

## Method

- `scripts/backtest.py` gained two flags (commit on main, `feat(backtest):`):
  - `--regime-gate highvol` — refuse a new entry when the SERVICE's live classifier
    (`app.regime.classify_regime`, imported directly, run on the exact 300-closed-bar window
    the EA posts to `/analyze`) says `high_volatility`. Its definition, unchanged: **the current
    ATR(14) ranks in the top 20 % of its last 100 ATR values** (`vol_percentile=0.8`,
    percentile-rank, not a ratio; ATR is SMA-seeded Wilder over the 300-bar window).
  - `--atr-spike-gate RATIO` — parametric variant on the replay's own Wilder ATR(14): refuse a
    new entry when ATR(14) at the entry bar > RATIO × the **median** of ATR(14) over the last
    **N = 100** closed bars (current bar included — the same 100-value lookback regime.py uses).
    Swept RATIO ∈ {1.3, 1.5, 1.8, 2.2}; two extra points (1.15, 1.2) were added after the first
    pass because the highvol tag turned out to sit at ratio ≈ 1.15+ (see below).
- Both gates touch **new entries only**; adds, exits, stops, flatten are untouched. Every trade is
  tagged with its entry regime *and* its entry ATR ratio, so the `off` run reports what each
  threshold would have refused and what those trades made (first-order "skipped" analysis —
  the gated path then diverges, so refused counts in gated runs are a bit higher).
- Default off re-verified byte-identical: net **−3,100.21**, 2,161 trades, valley 4,717.23,
  identical trade list to the pre-change script.
- All runs: `--balance 4700 --expo 360 --risk 1 --confirm 1 --stop-buffer 0.75 --source bars_max.json`
  (99,999 M5 bars). Sub-periods = equal-thirds slices of the dump, each restarted at $4,700 with
  its own indicator warm-up. Trades/day = closed trades ÷ distinct server dates with candles.
- Simplifications inherited from the replay: no daily-loss brake, no news blackout, spread
  $0.20/oz, close-based fills, balance compounds inside a window.


## Full 17 months (2025-03-14 → 2026-08-12, 365 trading days) (fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline (ADX10) | 2161 | 5.92 | -3,100 | 36.2 | 4,717 | +41 | -26 | 0 |
| B ADX10 + highvol (regime.py top-20% ATR rank) | 1742 | 4.77 | -1,399 | 34.7 | 3,250 | +56 | -31 | 600 |
| C ADX10 + atr-spike 1.3 | 1883 | 5.16 | -2,623 | 34.7 | 3,559 | +42 | -25 | 443 |
| D ADX10 + atr-spike 1.5 | 2039 | 5.59 | -3,212 | 35.3 | 3,940 | +39 | -24 | 213 |
| E ADX10 + atr-spike 1.8 | 2123 | 5.82 | -3,025 | 35.7 | 4,393 | +41 | -25 | 62 |
| F ADX10 + atr-spike 2.2 | 2143 | 5.87 | -3,078 | 36.1 | 4,639 | +41 | -25 | 16 |
| G ADX15 + atr-spike 1.3 | 1537 | 4.21 | -2,444 | 34.5 | 3,624 | +45 | -26 | 425 |
| H ADX15 + atr-spike 1.5 | 1717 | 4.70 | -3,583 | 34.7 | 4,459 | +36 | -22 | 214 |
| I ADX15 + highvol | 1397 | 3.83 | -2,012 | 34.0 | 2,912 | +51 | -28 | 577 |
| X ADX10 + atr-spike 1.15 (extra) | 1626 | 4.45 | -2,768 | 34.7 | 3,565 | +41 | -24 | 755 |
| X ADX10 + atr-spike 1.2 (extra) | 1727 | 4.73 | -3,061 | 34.7 | 3,621 | +39 | -23 | 632 |

What each gate would have skipped — baseline (A) trades whose entry bar met the gate condition, and what they actually made:

| gate condition at entry | trades skipped | their net P/L $ | win % | sum of winners $ | sum of losers $ |
|---|---:|---:|---:|---:|---:|
| regime = high_volatility (top-20 % ATR rank) | 575 | -1,605 | 36.3 | +8,506 | -10,111 |
| ATR ratio > 1.15 | 698 | -871 | 38.0 | +10,889 | -11,760 |
| ATR ratio > 1.2 | 588 | -139 | 39.3 | +9,486 | -9,625 |
| ATR ratio > 1.3 | 414 | -202 | 39.9 | +6,212 | -6,414 |
| ATR ratio > 1.5 | 205 | +326 | 41.5 | +3,133 | -2,807 |
| ATR ratio > 1.8 | 61 | -72 | 37.7 | +871 | -943 |
| ATR ratio > 2.2 | 15 | -133 | 26.7 | +182 | -315 |

Baseline (A) trades bucketed by entry ATR ratio (ATR14 ÷ median of last 100):

| ratio bucket | trades | net P/L $ | win % |
|---|---:|---:|---:|
| (0, 0.8] | 255 | -630 | 38.0 |
| (0.8, 0.9] | 312 | +465 | 38.8 |
| (0.9, 1.0] | 404 | -293 | 33.2 |
| (1.0, 1.1] | 350 | -642 | 34.9 |
| (1.1, 1.2] | 252 | -1,861 | 30.6 |
| (1.2, 1.3] | 174 | +64 | 37.9 |
| (1.3, 1.5] | 209 | -528 | 38.3 |
| (1.5, 1.8] | 144 | +397 | 43.1 |
| (1.8, 2.2] | 46 | +62 | 41.3 |
| (2.2, ∞] | 15 | -133 | 26.7 |

(high_volatility-tagged entries in this window: ATR-ratio p10 1.15, median 1.36, p90 1.79)

## Last 30 days (23 trading days) (fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline (ADX10) | 116 | 5.04 | +1,132 | 45.7 | 468 | +76 | -46 | 0 |
| B ADX10 + highvol (regime.py top-20% ATR rank) | 96 | 4.17 | +485 | 42.7 | 562 | +66 | -41 | 37 |
| C ADX10 + atr-spike 1.3 | 101 | 4.39 | +486 | 39.6 | 561 | +75 | -41 | 30 |
| D ADX10 + atr-spike 1.5 | 109 | 4.74 | +718 | 43.1 | 460 | +71 | -42 | 17 |
| E ADX10 + atr-spike 1.8 | 114 | 4.96 | +788 | 44.7 | 444 | +68 | -43 | 4 |
| F ADX10 + atr-spike 2.2 | 115 | 5.00 | +773 | 45.2 | 452 | +68 | -44 | 2 |
| G ADX15 + atr-spike 1.3 | 86 | 3.74 | +461 | 43.0 | 555 | +69 | -43 | 27 |
| H ADX15 + atr-spike 1.5 | 91 | 3.96 | +432 | 44.0 | 556 | +66 | -43 | 18 |
| I ADX15 + highvol | 80 | 3.48 | +225 | 45.0 | 640 | +57 | -42 | 34 |
| X ADX10 + atr-spike 1.15 (extra) | 89 | 3.87 | +60 | 38.2 | 705 | +66 | -40 | 43 |
| X ADX10 + atr-spike 1.2 (extra) | 93 | 4.04 | +60 | 37.6 | 717 | +67 | -39 | 39 |

What each gate would have skipped — baseline (A) trades whose entry bar met the gate condition, and what they actually made:

| gate condition at entry | trades skipped | their net P/L $ | win % | sum of winners $ | sum of losers $ |
|---|---:|---:|---:|---:|---:|
| regime = high_volatility (top-20 % ATR rank) | 34 | +659 | 52.9 | +1,358 | -698 |
| ATR ratio > 1.15 | 39 | +974 | 56.4 | +1,687 | -714 |
| ATR ratio > 1.2 | 35 | +988 | 60.0 | +1,584 | -596 |
| ATR ratio > 1.3 | 26 | +578 | 53.8 | +1,064 | -486 |
| ATR ratio > 1.5 | 14 | +351 | 50.0 | +624 | -273 |
| ATR ratio > 1.8 | 4 | +183 | 50.0 | +276 | -94 |
| ATR ratio > 2.2 | 2 | +132 | 50.0 | +173 | -41 |

Baseline (A) trades bucketed by entry ATR ratio (ATR14 ÷ median of last 100):

| ratio bucket | trades | net P/L $ | win % |
|---|---:|---:|---:|
| (0, 0.8] | 11 | -236 | 36.4 |
| (0.8, 0.9] | 13 | +455 | 61.5 |
| (0.9, 1.0] | 24 | -404 | 20.8 |
| (1.0, 1.1] | 21 | -27 | 42.9 |
| (1.1, 1.2] | 12 | +357 | 50.0 |
| (1.2, 1.3] | 9 | +410 | 77.8 |
| (1.3, 1.5] | 12 | +226 | 58.3 |
| (1.5, 1.8] | 10 | +169 | 50.0 |
| (1.8, 2.2] | 2 | +51 | 50.0 |
| (2.2, ∞] | 2 | +132 | 50.0 |

(high_volatility-tagged entries in this window: ATR-ratio p10 1.17, median 1.42, p90 1.65)

## P1 2025-03-14 → 2025-09-02 (fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline (ADX10) | 737 | 6.04 | -2,189 | 34.9 | 3,367 | +69 | -42 | 0 |
| B ADX10 + highvol (regime.py top-20% ATR rank) | 564 | 4.62 | -938 | 35.3 | 1,512 | +69 | -40 | 236 |
| C ADX10 + atr-spike 1.3 | 620 | 5.08 | -2,022 | 33.7 | 2,243 | +64 | -38 | 170 |
| D ADX10 + atr-spike 1.5 | 687 | 5.63 | -2,420 | 33.5 | 2,746 | +64 | -37 | 68 |
| E ADX10 + atr-spike 1.8 | 720 | 5.90 | -2,267 | 34.4 | 3,040 | +66 | -40 | 19 |
| F ADX10 + atr-spike 2.2 | 734 | 6.02 | -2,293 | 35.0 | 3,387 | +67 | -41 | 4 |
| G ADX15 + atr-spike 1.3 | 501 | 4.11 | -1,937 | 32.3 | 2,486 | +69 | -39 | 162 |
| H ADX15 + atr-spike 1.5 | 579 | 4.75 | -2,390 | 32.8 | 3,055 | +60 | -35 | 66 |
| I ADX15 + highvol | 448 | 3.67 | -1,321 | 33.7 | 1,680 | +67 | -38 | 225 |
| X ADX10 + atr-spike 1.15 (extra) | 514 | 4.21 | -1,592 | 34.8 | 1,784 | +60 | -37 | 299 |
| X ADX10 + atr-spike 1.2 (extra) | 560 | 4.59 | -2,043 | 33.6 | 2,226 | +63 | -37 | 246 |

What each gate would have skipped — baseline (A) trades whose entry bar met the gate condition, and what they actually made:

| gate condition at entry | trades skipped | their net P/L $ | win % | sum of winners $ | sum of losers $ |
|---|---:|---:|---:|---:|---:|
| regime = high_volatility (top-20 % ATR rank) | 224 | -1,428 | 34.4 | +4,794 | -6,222 |
| ATR ratio > 1.15 | 277 | -589 | 36.5 | +6,602 | -7,191 |
| ATR ratio > 1.2 | 227 | +79 | 39.2 | +5,802 | -5,723 |
| ATR ratio > 1.3 | 159 | -65 | 40.3 | +3,720 | -3,785 |
| ATR ratio > 1.5 | 67 | +467 | 46.3 | +1,824 | -1,357 |
| ATR ratio > 1.8 | 19 | +229 | 52.6 | +578 | -349 |
| ATR ratio > 2.2 | 4 | -11 | 25.0 | +109 | -120 |

Baseline (A) trades bucketed by entry ATR ratio (ATR14 ÷ median of last 100):

| ratio bucket | trades | net P/L $ | win % |
|---|---:|---:|---:|
| (0, 0.8] | 84 | -947 | 28.6 |
| (0.8, 0.9] | 90 | +191 | 36.7 |
| (0.9, 1.0] | 119 | +532 | 37.8 |
| (1.0, 1.1] | 116 | -216 | 37.1 |
| (1.1, 1.2] | 101 | -1,828 | 22.8 |
| (1.2, 1.3] | 68 | +144 | 36.8 |
| (1.3, 1.5] | 92 | -532 | 35.9 |
| (1.5, 1.8] | 48 | +237 | 43.8 |
| (1.8, 2.2] | 15 | +241 | 60.0 |
| (2.2, ∞] | 4 | -11 | 25.0 |

(high_volatility-tagged entries in this window: ATR-ratio p10 1.16, median 1.35, p90 1.76)

## P2 2025-09-02 → 2026-02-23 (fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline (ADX10) | 722 | 5.87 | -1,785 | 32.5 | 3,335 | +75 | -40 | 0 |
| B ADX10 + highvol (regime.py top-20% ATR rank) | 614 | 4.99 | -1,910 | 31.6 | 3,172 | +72 | -38 | 184 |
| C ADX10 + atr-spike 1.3 | 650 | 5.28 | -2,096 | 32.6 | 3,228 | +68 | -38 | 128 |
| D ADX10 + atr-spike 1.5 | 679 | 5.52 | -1,891 | 31.8 | 3,498 | +78 | -40 | 79 |
| E ADX10 + atr-spike 1.8 | 708 | 5.76 | -1,786 | 31.9 | 3,691 | +81 | -42 | 31 |
| F ADX10 + atr-spike 2.2 | 715 | 5.81 | -1,709 | 32.2 | 3,177 | +76 | -39 | 9 |
| G ADX15 + atr-spike 1.3 | 533 | 4.33 | -1,703 | 32.5 | 2,795 | +67 | -37 | 120 |
| H ADX15 + atr-spike 1.5 | 565 | 4.59 | -1,026 | 33.1 | 2,587 | +77 | -41 | 79 |
| I ADX15 + highvol | 484 | 3.93 | -1,649 | 32.4 | 2,433 | +67 | -37 | 175 |
| X ADX10 + atr-spike 1.15 (extra) | 571 | 4.64 | -2,300 | 30.1 | 3,120 | +63 | -33 | 221 |
| X ADX10 + atr-spike 1.2 (extra) | 600 | 4.88 | -2,376 | 30.7 | 2,911 | +63 | -33 | 190 |

What each gate would have skipped — baseline (A) trades whose entry bar met the gate condition, and what they actually made:

| gate condition at entry | trades skipped | their net P/L $ | win % | sum of winners $ | sum of losers $ |
|---|---:|---:|---:|---:|---:|
| regime = high_volatility (top-20 % ATR rank) | 170 | +562 | 35.3 | +4,811 | -4,249 |
| ATR ratio > 1.15 | 204 | +890 | 36.8 | +5,645 | -4,755 |
| ATR ratio > 1.2 | 176 | +489 | 34.1 | +4,692 | -4,203 |
| ATR ratio > 1.3 | 116 | +544 | 33.6 | +3,135 | -2,591 |
| ATR ratio > 1.5 | 74 | +411 | 35.1 | +1,923 | -1,512 |
| ATR ratio > 1.8 | 30 | +191 | 40.0 | +750 | -558 |
| ATR ratio > 2.2 | 8 | -60 | 25.0 | +92 | -152 |

Baseline (A) trades bucketed by entry ATR ratio (ATR14 ÷ median of last 100):

| ratio bucket | trades | net P/L $ | win % |
|---|---:|---:|---:|
| (0, 0.8] | 91 | +915 | 40.7 |
| (0.8, 0.9] | 112 | -649 | 29.5 |
| (0.9, 1.0] | 151 | -1,508 | 29.1 |
| (1.0, 1.1] | 122 | -1,318 | 27.9 |
| (1.1, 1.2] | 70 | +287 | 38.6 |
| (1.2, 1.3] | 60 | -56 | 35.0 |
| (1.3, 1.5] | 42 | +133 | 31.0 |
| (1.5, 1.8] | 44 | +220 | 31.8 |
| (1.8, 2.2] | 22 | +252 | 45.5 |
| (2.2, ∞] | 8 | -60 | 25.0 |

(high_volatility-tagged entries in this window: ATR-ratio p10 1.16, median 1.33, p90 1.96)

## P3 2026-02-23 → 2026-08-12 (fresh $4,700)

| variant | trades | trades/day | net P/L $ | win % | max open-equity valley $ | avg winner $ | avg loser $ | entries refused |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A baseline (ADX10) | 677 | 5.55 | +2,299 | 38.7 | 1,616 | +87 | -49 | 0 |
| B ADX10 + highvol (regime.py top-20% ATR rank) | 564 | 4.62 | +3,500 | 40.1 | 1,212 | +101 | -57 | 189 |
| C ADX10 + atr-spike 1.3 | 609 | 4.99 | +2,340 | 38.1 | 1,447 | +87 | -47 | 147 |
| D ADX10 + atr-spike 1.5 | 661 | 5.42 | +2,591 | 39.0 | 1,423 | +86 | -49 | 69 |
| E ADX10 + atr-spike 1.8 | 678 | 5.56 | +2,925 | 38.9 | 1,692 | +90 | -50 | 15 |
| F ADX10 + atr-spike 2.2 | 675 | 5.53 | +2,650 | 38.8 | 1,616 | +88 | -50 | 3 |
| G ADX15 + atr-spike 1.3 | 508 | 4.16 | +499 | 37.6 | 2,175 | +74 | -43 | 143 |
| H ADX15 + atr-spike 1.5 | 571 | 4.68 | +9 | 37.3 | 2,660 | +69 | -41 | 71 |
| I ADX15 + highvol | 468 | 3.84 | +1,749 | 39.3 | 1,692 | +88 | -51 | 180 |
| X ADX10 + atr-spike 1.15 (extra) | 536 | 4.39 | +1,937 | 38.4 | 853 | +83 | -46 | 233 |
| X ADX10 + atr-spike 1.2 (extra) | 564 | 4.62 | +1,231 | 37.8 | 1,191 | +78 | -44 | 198 |

What each gate would have skipped — baseline (A) trades whose entry bar met the gate condition, and what they actually made:

| gate condition at entry | trades skipped | their net P/L $ | win % | sum of winners $ | sum of losers $ |
|---|---:|---:|---:|---:|---:|
| regime = high_volatility (top-20 % ATR rank) | 176 | -522 | 35.8 | +4,932 | -5,454 |
| ATR ratio > 1.15 | 211 | +271 | 37.4 | +6,470 | -6,199 |
| ATR ratio > 1.2 | 181 | +856 | 39.2 | +5,940 | -5,083 |
| ATR ratio > 1.3 | 135 | -253 | 37.0 | +3,505 | -3,758 |
| ATR ratio > 1.5 | 64 | -153 | 34.4 | +1,604 | -1,757 |
| ATR ratio > 1.8 | 15 | -258 | 20.0 | +272 | -530 |
| ATR ratio > 2.2 | 3 | +119 | 33.3 | +209 | -90 |

Baseline (A) trades bucketed by entry ATR ratio (ATR14 ÷ median of last 100):

| ratio bucket | trades | net P/L $ | win % |
|---|---:|---:|---:|
| (0, 0.8] | 75 | +1,060 | 46.7 |
| (0.8, 0.9] | 107 | +1,027 | 42.1 |
| (0.9, 1.0] | 125 | -935 | 31.2 |
| (1.0, 1.1] | 112 | +477 | 40.2 |
| (1.1, 1.2] | 77 | -187 | 35.1 |
| (1.2, 1.3] | 46 | +1,109 | 45.7 |
| (1.3, 1.5] | 71 | -99 | 39.4 |
| (1.5, 1.8] | 49 | +105 | 38.8 |
| (1.8, 2.2] | 12 | -377 | 16.7 |
| (2.2, ∞] | 3 | +119 | 33.3 |

(high_volatility-tagged entries in this window: ATR-ratio p10 1.13, median 1.40, p90 1.73)


## Reading

1. **`--regime-gate highvol` is the only variant that moves the needle**, and it keeps most of the
   frequency: 4.77 trades/day (−19 %), 17-month net −1,399 vs −3,100 (+$1,700), open-equity valley
   3,250 vs 4,717. Sub-periods: P1 −938 vs −2,189 (better), P2 −1,910 vs −1,785 (slightly worse),
   P3 +3,500 vs +2,299 (better). Last 30 days: +485 vs +1,132 (**worse**, valley 562 vs 468).
2. **The parametric ATR-spike gate does NOT reproduce it at any ratio.** 1.3 → −2,623 (5.16/day),
   1.5 → −3,212, 1.8 → −3,025, 2.2 → −3,078; the extra 1.15/1.2 points (closest to the
   classifier's cut) → −2,768 / −3,061. Every ratio variant is worse than highvol despite skipping
   a similar or larger number of entries, and every one is worse than baseline in the last 30 days
   (+60 … +788 vs +1,132).
3. **Big spikes are not the losers.** Over 17 months the baseline's entries at ratio > 1.5 made
   **+326** (205 trades, 41.5 % win); > 1.8 −72; > 2.2 −133 on 15 trades. The owner's hypothesis
   ("volatility spiking = the losers") is not supported by the ratio data — the money is lost in the
   *mildly* elevated band (1.1, 1.2]: −1,861 on 252 trades (P1 alone −1,828, concentrated in
   Jun–Aug 2025: −585 / −699 / −504 by month), while the neighbouring (1.2, 1.3] band is +64 and
   (1.5, 1.8] is +397. That is a bucket-to-bucket sign flip, i.e. noise plus one bad summer, not a
   monotonic "more spike = more loss" relationship a ratio threshold could exploit.
4. **Why highvol "works" when the ratio doesn't:** the classifier's tag is a percentile *rank*
   (median ratio 1.36 but p10 1.15) so it fires on ~27 % of baseline entries and, by luck of the
   rank math, catches most of that Jun–Aug 2025 stretch (highvol P/L by month: Jul −778, Aug −598,
   May −391) — half of the highvol bucket's −1,605 total. The rest of the 17-month gain (+1,700 vs
   the −1,605 first-order skip) is path/compounding effect (a smaller early loss sizes later trades
   bigger; P3 gains +1,200 although its skipped trades were only −522). In the most recent 30 days
   the highvol entries were **winners** (34 trades, +659, 53 % win) and the gate skipped them.
5. **ADX 15 + highvol** (I) is worse than ADX 10 + highvol on every horizon (3.83/day, −2,012, 30 d
   +225): the ADX 15 cut removes winners, exactly as in the previous report. ADX 15 + atr-spike 1.3
   / 1.5 (G/H) likewise add nothing (−2,444 / −3,583).
6. Nothing at ~5 trades/day is profitable over 17 months in this replay; highvol takes the loss from
   −3,100 to −1,399, i.e. still −30 % of the account, and it did so by cutting the last-30-day gain
   in half.

## Bottom line

Skipping entries when the service says `high_volatility` (ADX 10 + `--regime-gate highvol`) is the
best-looking of the six variants — 4.8 trades/day, roughly halves the 17-month loss, cuts the worst
equity dip by ~$1,500 — but the effect is not what the hypothesis predicted: it is not "big ATR spikes
lose"; the parametric spike gate fails at every ratio, the classifier's gain is concentrated in one
2025 summer stretch plus compounding, and in the last 30 days and in P2 the gate skipped net winners.
Treat it as a candidate to *watch* (the service already logs the regime on every signal — the SQLite
hit-rate per regime will show whether high_volatility entries keep losing live), not as an
evidence-backed filter to enable now.
