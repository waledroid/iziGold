# Rulebook parameter sweep — 17-month robustness study

**Date:** 2026-08-13
**Data:** `bars_max.json` — 99,999 M5 bars, 2025-03-14 16:25 → 2026-08-12 17:45 (server time)
**Tool:** `scripts/backtest.py` (commit `d8e4a8c` added `--confirm` / `--stop-buffer`; defaults verified byte-identical to the pre-change baseline before sweeping)
**Fixed in all runs:** risk 1%, exposure 360 min/day, window 04–23, exit scheme `target-exit`, start balance $4,700 (the balance the known baseline was quoted at)

**Baseline reproduction (current live settings: ADX≥10, ConfirmCloses=1, StopBufferATR=0.75):**
full window net **−3,100.21**, 2,161 trades, valley 4,717 — exact match to the quoted −$3,100.
Last 30 days: **+1,132.33** — exact match. Settings are confirmed regime-tuned.

## 1. Primary 24-run grid (ADX {10,15,20,25} × ConfirmCloses {1,2} × StopBufferATR {0.5,0.75,1.0})

Sorted by full-window net. **Every cell is negative over 17 months.**

| ADX | CC | SB | full net $ | valley $ | trades | win% | 30d net $ | 30d valley $ |
|----:|---:|-----:|-------:|-------:|------:|-----:|------:|------:|
| 25 | 2 | 1.00 | −1,766 | 2,660 | 457 | 33 | +587 | 240 |
| 25 | 1 | 1.00 | −2,173 | 2,917 | 470 | 33 | +116 | 410 |
| 10 | 1 | 1.00 | −2,204 | 4,024 | 2,007 | 35 | +460 | 506 |
| 15 | 1 | 0.50 | −2,352 | 3,862 | 1,929 | 34 | +1,330 | 576 |
| 25 | 2 | 0.50 | −2,352 | 2,836 | 459 | 32 | −54 | 253 |
| 25 | 2 | 0.75 | −2,363 | 3,042 | 457 | 33 | +327 | 241 |
| 25 | 1 | 0.75 | −2,377 | 3,063 | 470 | 32 | +310 | 444 |
| 20 | 1 | 1.00 | −2,415 | 3,531 | 1,094 | 34 | +111 | 651 |
| 25 | 1 | 0.50 | −2,493 | 3,308 | 473 | 31 | +417 | 387 |
| 20 | 2 | 0.75 | −2,595 | 3,913 | 1,113 | 33 | +605 | 338 |
| 20 | 1 | 0.75 | −2,740 | 3,476 | 1,109 | 33 | +418 | 524 |
| 20 | 1 | 0.50 | −2,796 | 3,850 | 1,117 | 32 | +668 | 519 |
| 20 | 2 | 0.50 | −2,805 | 3,889 | 1,121 | 33 | +188 | 383 |
| 20 | 2 | 1.00 | −3,019 | 4,259 | 1,098 | 34 | +448 | 248 |
| 10 | 1 | 0.50 | −3,052 | 4,277 | 2,254 | 35 | +942 | 548 |
| **10** | **1** | **0.75** | **−3,100** | **4,717** | **2,161** | **36** | **+1,132** | **468** | ← current live |
| 15 | 1 | 1.00 | −3,160 | 4,802 | 1,794 | 35 | +458 | 655 |
| 15 | 1 | 0.75 | −3,207 | 4,592 | 1,868 | 35 | +779 | 579 |
| 15 | 2 | 1.00 | −3,478 | 4,493 | 1,734 | 36 | +466 | 448 |
| 15 | 2 | 0.50 | −3,517 | 4,664 | 1,845 | 34 | +253 | 695 |
| 15 | 2 | 0.75 | −3,540 | 4,627 | 1,788 | 35 | +679 | 467 |
| 10 | 2 | 0.50 | −3,785 | 4,522 | 2,121 | 35 | +242 | 676 |
| 10 | 2 | 0.75 | −3,843 | 4,572 | 2,026 | 36 | +522 | 475 |
| 10 | 2 | 1.00 | −3,932 | 4,636 | 1,961 | 37 | +377 | 448 |

Clear monotone signal in the grid: **higher ADX gate → smaller losses** (ADX 25 rows occupy the
top). The grid's best was at the edge, so the sweep was extended.

## 2. Edge extension (ADX 28–40)

| ADX | CC | SB | full net $ | valley $ | trades | win% | 30d net $ | 30d valley $ |
|----:|---:|-----:|-------:|-------:|------:|-----:|------:|------:|
| 28 | 1 | 0.75 | −1,400 | 2,081 | 272 | 35 | +474 | 252 |
| 28 | 1 | 1.00 | +27 | 1,385 | 272 | 36 | +349 | 218 |
| **30** | **1** | **0.50** | **+1,084** | **866** | **180** | **39** | **+861** | **107** |
| 30 | 1 | 0.75 | +697 | 864 | 180 | 40 | +587 | 155 |
| **30** | **1** | **1.00** | **+901** | **601** | **180** | **40** | **+533** | **132** | ← recommended |
| **30** | **1** | **1.25** | **+939** | **742** | **180** | **40** | **+603** | **134** |
| 30 | 2 | 1.00 | −232 | 1,122 | 160 | 38 | +746 | 101 |
| 30 | 2 | 1.25 | −12 | 1,090 | 160 | 36 | +518 | 97 |
| 32 | 1 | 1.00 | +279 | 825 | 113 | 38 | +393 | 111 |
| 32 | 1 | 1.25 | +462 | 687 | 113 | 39 | +506 | 93 |
| 35 | 1 | 1.00 | −198 | 737 | 52 | 29 | +257 | 67 |
| 35 | 2 | 1.00 | −485 | 560 | 46 | 28 | +31 | 61 |
| 40 | 1 | 1.00 | −208 | 368 | 16 | 19 | +104 | 1 |

Only **ADX 30 with ConfirmCloses 1** is positive on BOTH horizons, and it is positive across the
whole StopBuffer range 0.5–1.25 (entries are identical at fixed ADX/CC — 180 trades in every
SB row — so the SB robustness is about exits/sizing only). ConfirmCloses 2 flips the same ADX 30
cell negative. ADX 28 and 32 are much weaker, 35+ starves (52 → 16 trades).

## 3. Top 3 by full-window net — both-horizon comparison

| setting | full net $ | 30d net $ | valley $ | trades | win% |
|---|-------:|------:|-------:|------:|-----:|
| ADX30 / CC1 / SB0.50 | +1,084 | +861 | 866 | 180 | 39 |
| ADX30 / CC1 / SB1.25 | +939 | +603 | 742 | 180 | 40 |
| ADX30 / CC1 / SB1.00 | +901 | +533 | 601 | 180 | 40 |
| *current: ADX10 / CC1 / SB0.75* | *−3,100* | *+1,132* | *4,717* | *2,161* | *36* |

All three pass the requirement: positive on both horizons with a valley 5–8× shallower than
today's 4,717. **SB 1.00 is recommended over SB 0.50** despite the slightly lower net: lowest
valley of the three, and SB 0.50 is the loser leg in sub-period 1 (below) — its extra net comes
from one period only.

## 4. Robustness: three ~5.7-month sub-periods (fresh $4,700 each)

| period | dates | ADX30/CC1/SB1.0 | trades | valley | current (ADX10/CC1/SB0.75) | trades | valley |
|---|---|-------:|---:|---:|-------:|---:|---:|
| P1 | 2025-03-14 → 2025-09-02 | **+188** | 56 | 469 | −2,261 | 736 | 3,367 |
| P2 | 2025-09-02 → 2026-02-20 | **−86** | 59 | 368 | −1,786 | 718 | 3,656 |
| P3 | 2026-02-23 → 2026-08-12 | **+925** | 66 | 252 | +1,108 | 679 | 2,348 |

Neighbor SB values per period (same ADX30/CC1): SB0.50 → −261 / +227 / +1,243;
SB1.25 → +129 / −356 / +1,147.

Reading: the candidate is not a one-period wonder in the way the current settings are — current
settings lose ~$2,000 in each of the first two thirds and only win recently; the candidate never
loses more than $86 in any third. But it is also honest to say **most of its profit (+925 of
+901 total — sub-periods don't compound identically) comes from the last third**; P1/P2 are
roughly breakeven, not independently profitable.

## 5. Caveats

- **ADX 30 sits on a narrow peak along the ADX axis** (28 → +27, 32 → +279, 35 → −198). The
  SB-axis plateau and the never-deeply-negative sub-periods argue it is a real regime filter
  (only trade genuinely trending bars), not pure curve-fit — but the sharpness at exactly 30
  warrants humility. Treat it as "stops the bleeding and keeps the recent edge," not as a
  proven long-run money-maker.
- 180 trades / 17 months ≈ 10–11 per month — much less activity than today's ~127/month. The
  system will feel very quiet.
- Backtest simplifications apply as documented in `scripts/backtest.py` (no daily-loss brake,
  no news blackout, close-based fills, flat $0.20/oz spread) — they apply equally to every cell,
  so the *ranking* is trustworthy even where absolute dollars are optimistic.
- ConfirmCloses 2 is harmful everywhere tested; keep it at 1.

## 6. Recommendation

`AdxTrendThreshold = 30` (from 10), keep `ConfirmCloses = 1`, `StopBufferATR = 1.0`
(from 0.75), risk 1% / expo 360 / window 04–23 unchanged.
Over the full 17 months this turns −$3,100 into +$901 and cuts the worst equity valley from
$4,717 to $601, while still making +$533 over the last 30 days (vs +$1,132 for current — the
price of the robustness is roughly half the recent-month profit).

Raw run outputs: scratchpad `sweep/` directory (session ddeb1ce7); reproduce any cell with
`python3 scripts/backtest.py --source bars_max.json --balance 4700 --adx A --confirm C --stop-buffer S [--days 30]`.
