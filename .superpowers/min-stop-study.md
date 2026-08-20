# Minimum stop-distance floor study (`--min-stop-atr K`) — 2026-08-18

## Verdict (plain language)

**Do not adopt a floor to fix today's trade; if a floor is wanted at all, K=1.5 is the only
candidate, and it is not a clean win.** Today's 13:50 BUY dies under every K (the market ran
$6-7 = 2.4 ATR against it in 15 minutes; the stop was already ~1 ATR wide, not $2.99 vs a $4-5
ATR — the replay's Wilder ATR(14) at 13:45 was **$2.91**). Over the last 30 days K=1.5 adds
+$187 (+$366 vs +$179), over 17 months +$395 (−$2,672 vs −$3,067) but it is worse in one of three
sub-periods (−$48) and it converts noise stops into slower, larger bleeds almost 1-for-1
(17 months: 72 entries saved from a ≤3-bar stop, 153 entries that lost MORE than the old stop
would have cost). K=2 is better over 17 months (+$1,026) and in all three sub-periods, but
loses the last 30 days (−$54 vs +$179) and floors 58% of all entries — it is no longer "a floor",
it is a different stop rule.

## What was built

`scripts/backtest.py --min-stop-atr K` (default 0 = off). After the strategy stop is computed
exactly as before (HalfTrend wick extreme ± 0.75×ATR(14) — untouched), if |entry − stop| <
K×ATR(14) the ENTRY stop is pushed out to exactly K×ATR(14) from the fill, directionally; lots
are then sized from the widened distance by the existing `int(risk/dist)` (1% risk over the
actual, wider distance → fewer lots).

- **Entry stop only.** Pyramid adds are untouched: the ladder is computed from the current
  shared stop / entry prices (`(stop+e0)/2` for add 1, mid of the two prior entries after), so the
  first add's ladder inherits the floored stop implicitly. Nothing else changed.
- Every trade is tagged: raw stop distance in ATRs (`dist_atr`), `floored`, `orig_stop`,
  `orig_oz` (lots the old stop would have used), and `orig_hit_bar` — the first bar after entry
  whose intrabar extreme would have touched the ORIGINAL stop while the basket was open.
- Summary (only printed when K>0): floored count, "old stop would have hit within 3 bars"
  subset with eventual P/L vs what the old stop would have realized, later hits, never touched,
  and a per-trade list.
- **Byte-identical at K=0**: `git show HEAD:scripts/backtest.py` vs new, 30-day
  `--strict-window --verbose` on bars_max.json → `diff` empty.

## Method

Live rulebook everywhere: `--strict-window` ON (the rule that trades today), ADX 10, confirm 1,
EMA 55, 1% risk, expo 360, $4,000 start, target-exit. Sweep K ∈ {0, 1.0, 1.5, 2.0}.

Data: `week.json` in the repo root actually ends **2026-08-18 12:55 server** (not 15:55 — its
`t` values are server-clock, same base as bars_max.json: 2,729 identical bars overlap at offset
0, and no bar ever falls in hour 00 = the broker's daily break). It therefore does NOT contain
the 13:50 trade. I pulled `/ui/candles` from the running service (326 bars, 08-17 10:05 →
**08-18 14:10 server**) and merged all three into one series
(`bars_max.json` ∪ `week.json` ∪ live = 100,285 bars, 2025-03-19 12:00 → 2026-08-18 14:10).
Windows:

| window | source | notes |
|---|---|---|
| (a) today 08-18 04:00–16:00 | week.json + live (merged), warm-up from 08-17 01:00 | data ends 14:10; trades filtered by open time |
| (b) last 30 days | merged, `--days 30` = 07-19 14:10 → 08-18 14:10 | |
| (c) full 17 months | merged | balance compounds through the whole run |
| (d) sub-periods | merged, `--start/--end`, each restarts at $4,000; indicators warm up from the start | 03-19→09-05-2025, 09-06→02-25, 02-26→08-18 |

Column key: "old-stop hit ≤3 bars" = floored entries whose ORIGINAL stop would have been touched
within the first 3 bars (new P/L under the floor / P/L the old stop would have realized,
= −(orig_dist+spread)×orig_lots). "saved" = those that then survived past bar 3 under the floor.
"bled" = floored entries where the original stop was touched (any bar) AND the floored trade
lost MORE dollars than the old stop would have. "floored-set net" = the floored entries' P/L in
this run vs the SAME entries (matched by open time) in the K=0 run.

## Results

### (a) today 08-18 04:00–16:00 (3 trades in every run)
| K | net P/L | trades | win % | max valley | avg loss | floored | old-stop hit ≤3 (new/old) | saved | bled |
|---|---|---|---|---|---|---|---|---|---|
| 0 | −66.95 | 3 | 33.3 | 138.16 | −37.99 | 0 | – | – | – |
| 1.0 | −67.43 | 3 | 33.3 | 137.23 | −38.23 | 1 | 1 (−40.39 / −39.90) | 0 | 1 |
| 1.5 | −68.09 | 3 | 33.3 | 141.33 | −38.56 | 1 | 1 (−41.04 / −39.90) | 1 | 1 |
| 2.0 | −48.88 | 3 | 33.3 | 129.79 | −28.96 | 2 | 2 (−57.91 / −75.98) | 1 | 1 |

Today's trades (replay): 04:40 SELL → profit lock +9.03 (unchanged, stop 3.13 ATR); 11:25 SELL
6oz stop 4395.21 (1.64 ATR) → stopped −36.07 (K=2 floors it to 4396.50, still stopped −36.49);
13:50 BUY → see below.

### The 13:50 BUY
Replay version (HalfTrend flips one bar later than the live EA did — the replay's flip is the
13:45 bar, strict decision at 13:50 close, fill 4396.98; raw stop 4394.33 = 2.65 = **0.91 ATR**,
ATR(14) = 2.91):

| K | lots | stop | outcome |
|---|---|---|---|
| 0 | 0.14 | 4394.33 | stopped in bar 14:00, −39.90 |
| 1.0 | 0.13 | 4394.07 | stopped in bar 14:00, −40.39 |
| 1.5 | 0.09 | 4392.62 | stopped in bar 14:05 (low 4392.62 touches), −41.04 |
| 2.0 | 0.06 | 4391.17 | still open at end of data 14:10 (4393.61), −21.42 unrealized; 14:10 low 4392.02 is 0.85 above the stop |

Live version (fill 4399.06 at 13:50 open, stop 4396.07, dist 2.99, $45 risk, ATR at 13:45 = 2.91):

| K | dist | stop | lots | outcome |
|---|---|---|---|---|
| 0 / 1.0 | 2.99 (K=1 floor 2.91 < 2.99 → no change) | 4396.07 | 0.15 | stopped bar 14:00 (low 4393.64), −47.85 (live −45.90) |
| 1.5 | 4.37 | 4394.69 | 0.10 | stopped bar 14:00, −45.68 |
| 2.0 | 5.82 | 4393.24 | 0.07 | stopped bar 14:05 (low 4392.62), −42.17 |

So the floor does not rescue this trade at any K: the stop was already ≈1 ATR and price ran
$7 (2.4 ATR) against it in three bars. It only changes WHICH bar kills it and shaves $2-6.

### (b) last 30 days (07-19 → 08-18, 90 trades baseline)
| K | net P/L | trades | win % | max valley | avg loss | floored | old-stop hit ≤3 (new/old) | saved (new/old) | bled (new/old) | floored-set net (this run vs baseline) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | **+179.45** | 90 | 38.9 | 510.66 | −35.28 | 0 | – | – | – | – |
| 1.0 | +176.91 | 90 | 38.9 | 514.62 | −35.19 | 9 | 7 (−282.62 / −288.18) | 0 | 3 (−122.19 / −119.73) | −192.50 vs −189.96 |
| 1.5 | **+366.20** | 90 | 43.3 | 452.09 | −37.94 | 28 | 11 (−390.30 / −466.59) | 2 (−33.40 / −85.90) | 5 (−211.86 / −204.70) | +405.29 vs +59.39 |
| 2.0 | −53.66 | 89 | 40.4 | 490.71 | −33.18 | 54 | 15 (−499.46 / −578.14) | 6 (−176.68 / −244.25) | 7 (−256.71 / −233.76) | −267.13 vs −229.35 |

### (c) full 17 months (1,736 trades baseline)
| K | net P/L | trades | win % | max valley | avg loss | floored | old-stop hit ≤3 (new/old) | saved (new/old) | bled (new/old) | floored-set net (this run vs baseline) |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | −3066.86 | 1736 | 35.4 | 3602.55 | −20.40 | 0 | – | – | – | – |
| 1.0 | −3015.93 | 1730 | 35.1 | 3518.23 | −20.58 | 147 (8%) | 65 (−1069.84 / −1334.99) | 9 (−0.28 / −250.34) | 50 (−1030.43 / −911.89) | −340.51 vs −628.24 |
| 1.5 | −2672.06 | 1688 | 35.3 | 3317.01 | −22.80 | 565 (33%) | 151 (−2469.80 / −3701.46) | 72 (−677.25 / −1832.10) | 153 (−3517.09 / −3091.25) | +509.12 vs −437.40 |
| 2.0 | **−2040.89** | 1614 | 35.3 | 3416.71 | −25.38 | 1001 (62%) | 194 (−3245.20 / −5449.23) | 117 (−1313.51 / −3428.06) | 204 (−5708.54 / −5016.06) | +459.08 vs −588.38 |

(Avg loss is small here because the balance compounds down to ~$1,000-2,000 during the run.)

### (d) sub-periods (each from $4,000)
| period | K=0 | K=1.0 | K=1.5 | K=2.0 |
|---|---|---|---|---|
| 1: 2025-03-19 → 09-05 (590 tr) | −2147.01 (val 2495, win 32.5%) | −2139.61 | **−1844.12** (+303; val 2346, win 33.1%) | **−1469.95** (+677; val 2612, win 35.0%) |
| 2: 2025-09-06 → 2026-02-25 (572 tr) | −1684.17 (val 2375, win 31.3%) | −1684.05 | −1731.78 (**−48**; val 2710) | **−756.84** (+927; val 2431, win 33.0%) |
| 3: 2026-02-26 → 08-18 (543 tr) | −64.40 (val 2155, win 38.1%) | −60.18 | **+75.82** (+140; val 2080) | **+158.91** (+223; val 1856, win 38.5%) |
| saved / bled per period, K=1.5 | – | 5/14, 2/16, 2/14 | 30/38, 21/44, 21/44 | 42/55, 40/64, 35/49 |

Sub-period robustness: K=1.5 beats baseline in 2 of 3 (and the last 30 days); K=2 beats baseline
in 3 of 3 sub-periods but not in the last 30 days; K=1 is noise (±$10, it floors <10% of entries
because most raw stops already sit ≥1 ATR — the 0.75×ATR pad plus wick distance).

## Noise stops saved vs "should have died" trades that bled further

Across 17 months (K=1.5): 151 floored entries would have been stopped within 3 bars by the old
stop (old cost −$3,701 → −$2,470 under the floor, +$1,232), of which 72 actually survived past
bar 3 (their eventual net −$677 vs −$1,832 old cost = +$1,155 saved). Against that, 153 floored
entries lost MORE than the old stop would have (−$3,517 vs −$3,091 = −$426 extra bleed). Net of
the floored set: +$509 vs −$437 for the same entries in the baseline = ≈+$950 — but the total
run improves only +$395, because the wider stops keep baskets open longer and 48 later entries
never happen (1,688 vs 1,736 trades) plus small sizing/compounding effects. Roughly: for every
noise stop it saves it lets one "right the first time" stop bleed ~40% more.

## Caveats

1. **The stop today was not really "noise-level" by the replay's own ATR**: 2.99 vs ATR(14)
   2.91 (Wilder, M5). If the owner's "$4-5 ATR" comes from a different ATR (chart period,
   different smoothing, or the ATR *after* the 14:00 spike), the K numbers here don't map to that
   reading — K must be defined against the SAME ATR the EA uses for the 0.75 pad. Whatever ATR is
   used, price ran 2.4 ATR against the entry in 3 bars, and no floor ≤2 ATR survives that.
2. Replay ≠ EA on flip timing for today (replay flip 13:45 vs EA 13:40 — HalfTrend is
   path-dependent on history start); results are about the rule, not a tick-exact autopsy.
3. Standard replay simplifications: close-based actions, no daily-loss brake, no news guard, no
   slippage. Sub-periods restart at $4,000 and re-warm indicators from their start.
4. The floored-set "old-stop P/L" for the ≤3-bar subset is an estimate (old stop fill at
   orig_stop with orig lots); the "vs baseline" column is the actual matched K=0 trade.

## Reproduce
```
python3 scripts/backtest.py --source <merged.json> --days 30 --strict-window --min-stop-atr 1.5
python3 scripts/backtest.py --source bars_max.json --days 30 --strict-window   # byte-identical to HEAD
```
(merged.json = bars_max.json ∪ week.json ∪ /ui/candles snapshot 08-18 14:10; the sweep driver
lived in the session scratchpad and only calls `backtest.run()` with `MIN_STOP_ATR` set.)
