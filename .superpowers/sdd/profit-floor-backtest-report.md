# Profit-floor backtest report (Phase 1)

**Spec:** `docs/superpowers/specs/2026-08-12-profit-floor-design.md` ·
**Date run:** 2026-08-12 ·
**Tool:** `scripts/backtest.py --exit-scheme {target-exit|floor-a|floor-b|floor-a-adds}`

## Data window

Maximum available M5 history pulled fresh from the running MT5 terminal via
`scripts/dump_bars.py` (Windows python): **99,999 bars, 2025-03-14 16:25 →
2026-08-12 17:45 server time** (~17 months). The terminal refused counts
above 99,999 — this is the hard maximum it holds.

Parameters, same as live: risk 1%, ADX ≥ 10, exposure 360 min/day, window
04–23 server hours, start balance $4,700.

## Mechanics as implemented

- **Floor price solve** (matches the backtester's P/L convention exactly):
  the backtester's `basket_pl` is `Σ oz_i × (P − e_i) × s − SPREAD_USD × Σoz`
  (contract = 1 oz units, $0.20/oz round-trip spread charged). The floor
  price solves that full expression `= floor_amount`, so a stop fill exactly
  at the floor price realizes exactly the floor amount in booked dollars —
  the spec's raw `Σ lots_i × (entry_i − P) × contract = floor` plus the
  backtester's own spread charge. "0.25×ATR-worth" = 0.25 × ATR(14) at the
  arming bar × total basket oz.
- **floor-a**: first bar-close where basket P/L ≥ target (2% of cycle
  balance) arms floor = target − 0.25×ATR-worth; adds frozen.
- **floor-b**: first bar-close where P/L ≥ target + 0.25×ATR-worth arms
  floor = full target; adds frozen. Between target and arming the basket
  runs under unchanged rules (this is where variant B can fail to arm).
- **floor-a-adds**: floor-a with adds left on; the floor price is NOT
  re-solved after an add (that stale stop is precisely the erosion being
  measured); the ladder may tighten the armed stop but never loosen it.
- **Ratchet**: the floor only ever tightens the shared stop (directional
  max); it never loosens an already-tighter ladder stop.
- **Profit lock** (close when P/L ≤ 50% of peak once peak ≥ 1R) and the
  **reversal exit** are unchanged and evaluated in every scheme; whichever
  protection is tighter effectively governs.
- **Intra-bar ordering** (existing backtester convention, followed as-is):
  the shared stop — floor included — is checked FIRST against the bar's
  low/high, before the close-based exits (target/arming, profit lock,
  reversal) are evaluated at the bar close. Stop beats everything else
  within a bar. The one pre-existing exception: the 23:5x pre-break flatten
  runs before the stop check and fills at bar close.

## Results — full window (2025-03-14 → 2026-08-12, 99,999 bars)

| scheme | net P/L | trades | win % | max valley¹ | avg winner | max winner | armed |
|---|---:|---:|---:|---:|---:|---:|---:|
| target-exit (baseline) | **−3,100.21** | 2,161 | 36.2 | 4,717 | 41.12 | 237.75 | — |
| floor-a (owner lean) | −3,236.64 | 2,022 | 35.4 | 7,700 | 50.29 | 1,161.42 | 390 |
| floor-b | **−2,544.14** | 2,000 | 34.5 | 7,355 | 58.46 | 1,241.04 | 316 |
| floor-a-adds | −2,829.10 | 2,011 | 34.2 | 6,976 | 55.21 | 1,335.44 | 378 |

¹ max valley = deepest peak-to-trough of open equity marked at bar closes
(closed-balance max drawdown is 4,661 / 7,146 / 6,762 / 6,317 respectively).

Headline caveat: **every scheme loses money over the full 17 months.** The
live parameter set was calibrated on recent weeks; most of this window is a
regime it was never tuned for. The full window is still the fairest
floor-vs-target comparison because both arms suffer it equally.

### Recent-regime slices (same data, tail of the window)

| window | target-exit | floor-a | floor-b | floor-a-adds |
|---|---:|---:|---:|---:|
| last 90 days — net | +1,113.00 | +1,243.01 | +360.18 | +1,390.21 |
| last 90 days — valley | 801 | 1,199 | 1,005 | 1,359 |
| last 30 days — net | **+1,132.33** | +429.66 | +560.67 | +441.75 |
| last 30 days — valley | **468** | 689 | 692 | 738 |

## Sanity checks (all required, all run)

1. **Baseline reproduction — PASS.** The unmodified script (run and captured
   before any edit) vs the extended script with `--exit-scheme` omitted vs
   `--exit-scheme target-exit`: all three produce **byte-identical** trade
   lists and net P/L over the full window (diff on all 2,161 trade lines +
   summary: zero differences).
2. **Floor guarantee — PASS.** Across all 390 armed floor-a trades,
   min(realized P/L − floor amount) = **+0.00, zero violations** (same for
   floor-b's 316). An in-code assertion now enforces this on every floor-a/
   floor-b run (close-based forced exits — flatten/end-of-data — are the
   only tolerated leak paths; none occurred below floor in this run). In
   floor-a-adds the guarantee intentionally breaks: **135 of 378 armed
   trades realized below their floor, worst −39.00 under** — that is the
   owner-predicted add erosion, measured.
3. **Unarmed trades identical to baseline — PASS.** Up to the first baseline
   profit-target exit (2025-03-17 12:25) all schemes are byte-identical to
   baseline. After that point balances diverge, so later trades can differ
   in size/thresholds even when the floor logic never engaged; matching
   trades by entry bar: floor-a 1,632 unarmed trades, 1,630 matched, 1,540
   byte-identical paths, and **every one of the 90 divergent cases is
   explained by a different cycle balance or entry size** (checked
   programmatically: zero divergences with equal cycle_bal + equal sizing —
   i.e. zero attributable to exit logic). Same result for floor-b (218
   divergent, all explained) and floor-a-adds (158, all explained).

## Why the aggregate hides the per-trade story

Matching armed floor trades to the baseline trade with the same entry bar
where baseline banked the profit target:

| scheme | pairs | mean delta $ | better | worse | min | max |
|---|---:|---:|---:|---:|---:|---:|
| floor-a | 362 | +14.79 | 127 | 235 | −112.76 | +1,027.62 |
| floor-b | 293 | **+38.18** | **227** | 66 | −87.27 | +1,099.68 |
| floor-a-adds | 331 | +21.85 | 168 | 163 | −138.15 | +1,201.64 |

Per armed trade the floor mechanic is **positive in expectation** — a few
huge riders (max ≈ +$1,000–1,200 extra) outweigh the many small give-backs.
floor-a loses on ~65% of armed trades (retracement to floor costs the
0.25×ATR haircut vs banking target immediately); floor-b wins on ~77%
(once armed it can never do worse than full target, so give-backs cost $0
minus nothing — its losses vs baseline come only from baseline's overshoot
fills and sizing divergence).

The drag that flips floor-a's positive per-trade edge into a worse net:
longer holds burn the 360-min/day exposure budget (2,022 trades vs
baseline's 2,161 — ~139 entries never taken), and the balance-compounding
path diverges. Armed-trade exit reasons, floor-a: 304 floor stop, 52 profit
lock, 21 reversal, 13 flatten — i.e. ~78% of armed trades retrace to the
floor rather than riding on.

## Per-trade divergence detail

Full matched-pair tables (every armed trade vs its baseline profit-target
counterpart, with delta, floor amount, and exit reason) are appended below.

## Interpretation

No floor variant clears the spec's decision bar. Over the full 17 months
only floor-b beats target-exit on net (−2,544 vs −3,100, a +$556 improvement)
but it worsens the open-equity valley by 56% (7,355 vs 4,717) — a materially
worse valley, which the decision rule disallows; and in the recent 30-day
regime (the one the live parameters were actually tuned for) the baseline
beats all three floors outright on both net (+1,132 vs +430/+561/+442) and
valley. floor-a — the owner's lean — is the worst of the three on the full
window (worse net AND a 63% deeper valley than baseline) because ~65% of
armed trades retrace to the floor and pay the 0.25×ATR haircut, while the
longer holds crowd out ~139 later entries through the exposure budget. The
per-trade numbers do show the floor idea has a real positive expectation on
armed trades (especially variant B: +$38/trade, 77% win), so the concept
isn't dead — but as specified it trades a small, regime-fragile net gain for
a much deeper valley. Recommendation from the numbers: keep `target-exit`;
if the idea is revisited, variant B with an exposure-budget exemption for
armed baskets is the only configuration these numbers suggest is worth a
second test.

## Reproduction

```bash
# dump (Windows python, terminal running)
/mnt/c/Users/aatanda/AppData/Local/Programs/Python/Python312/python.exe \
    scripts/dump_bars.py 99999 bars_max.json
# each scheme
service/.venv/bin/python scripts/backtest.py --balance 4700 \
    --source bars_max.json --exit-scheme floor-a
```

Backtester caveats (unchanged, apply to every scheme equally): bar-close
granularity, stop fills exactly at stop price even across gaps, flat $0.20/oz
spread, no daily-loss brake, no news blackout, no kill switch modeled.

---

# Appendix — matched armed-trade pair tables

### floor-a — armed trades vs baseline profit-target exit (same entry bar, 362 pairs)

| entry (server) | dir | baseline P/L | floor-a P/L | delta $ | floor $ | exit reason |
|---|---|---:|---:|---:|---:|---|
| 2025-03-17T11:55 | BUY | +94.11 | +83.34 | -10.77 | +83.34 | floor stop |
| 2025-03-17T17:15 | BUY | +104.85 | +76.83 | -28.02 | +76.83 | floor stop |
| 2025-03-18T14:20 | BUY | +119.61 | +77.66 | -41.95 | +77.66 | floor stop |
| 2025-03-19T05:30 | BUY | +122.42 | +81.41 | -41.01 | +81.41 | floor stop |
| 2025-03-19T10:45 | SELL | +95.82 | +85.52 | -10.30 | +85.52 | floor stop |
| 2025-03-20T06:30 | BUY | +121.20 | +83.34 | -37.86 | +83.34 | floor stop |
| 2025-03-20T10:15 | SELL | +101.57 | +80.11 | -21.46 | +80.11 | floor stop |
| 2025-03-20T13:00 | SELL | +131.81 | +82.44 | -49.37 | +82.44 | floor stop |
| 2025-03-21T16:30 | SELL | +99.31 | +122.45 | +23.14 | +80.54 | profit lock |
| 2025-03-24T19:35 | SELL | +113.85 | +78.07 | -35.78 | +78.07 | floor stop |
| 2025-03-25T06:20 | BUY | +141.73 | +77.47 | -64.26 | +77.47 | floor stop |
| 2025-03-25T14:55 | SELL | +103.06 | +78.59 | -24.47 | +78.59 | floor stop |
| 2025-03-26T08:40 | BUY | +103.18 | +175.16 | +71.98 | +79.14 | profit lock |
| 2025-03-27T17:10 | BUY | +108.10 | +85.01 | -23.09 | +85.01 | floor stop |
| 2025-03-31T04:30 | BUY | +109.67 | +81.86 | -27.81 | +81.86 | floor stop |
| 2025-03-31T16:25 | SELL | +117.80 | +78.66 | -39.14 | +78.66 | floor stop |
| 2025-04-02T06:50 | SELL | +101.70 | +79.99 | -21.71 | +79.99 | floor stop |
| 2025-04-02T13:30 | SELL | +134.08 | +82.27 | -51.81 | +82.27 | floor stop |
| 2025-04-04T13:00 | BUY | +116.28 | +79.89 | -36.39 | +79.89 | floor stop |
| 2025-04-08T08:10 | BUY | +95.43 | +75.68 | -19.75 | +75.68 | floor stop |
| 2025-04-09T16:20 | BUY | +125.84 | +76.92 | -48.92 | +76.92 | floor stop |
| 2025-04-15T12:40 | SELL | +91.28 | +67.38 | -23.90 | +67.38 | floor stop |
| 2025-04-17T08:25 | SELL | +105.97 | +74.74 | -31.23 | +63.14 | profit lock |
| 2025-04-21T08:25 | BUY | +95.85 | +66.00 | -29.85 | +66.00 | floor stop |
| 2025-04-21T20:35 | BUY | +95.82 | +67.95 | -27.87 | +67.95 | floor stop |
| 2025-04-22T04:15 | BUY | +104.90 | +67.69 | -37.21 | +67.69 | floor stop |
| 2025-04-22T10:00 | SELL | +97.62 | +72.50 | -25.12 | +72.50 | floor stop |
| 2025-04-22T15:40 | SELL | +94.60 | +66.05 | -28.55 | +66.05 | floor stop |
| 2025-04-23T05:35 | SELL | +122.19 | +72.79 | -49.40 | +72.79 | floor stop |
| 2025-04-23T08:40 | SELL | +119.56 | +68.73 | -50.83 | +68.73 | floor stop |
| 2025-04-25T06:45 | SELL | +104.46 | +66.88 | -37.58 | +66.88 | floor stop |
| 2025-04-25T18:50 | BUY | +93.64 | +67.39 | -26.25 | +67.39 | floor stop |
| 2025-04-28T04:25 | SELL | +102.33 | +67.97 | -34.36 | +67.97 | floor stop |
| 2025-04-28T13:30 | BUY | +93.39 | +63.74 | -29.65 | +63.74 | floor stop |
| 2025-04-29T19:20 | BUY | +103.20 | +66.08 | -37.12 | +66.08 | floor stop |
| 2025-04-30T11:05 | SELL | +126.35 | +69.97 | -56.38 | +69.97 | floor stop |
| 2025-04-30T21:20 | SELL | +98.64 | +67.36 | -31.28 | +67.36 | floor stop |
| 2025-05-01T12:20 | SELL | +108.28 | +64.87 | -43.41 | +64.87 | floor stop |
| 2025-05-02T05:05 | BUY | +118.50 | +67.52 | -50.98 | +67.52 | floor stop |
| 2025-05-05T04:05 | BUY | +91.04 | +64.37 | -26.67 | +64.37 | floor stop |
| 2025-05-06T09:35 | BUY | +132.65 | +63.01 | -69.64 | +63.01 | floor stop |
| 2025-05-06T18:20 | BUY | +94.71 | +350.88 | +256.17 | +62.73 | flatten |
| 2025-05-08T07:10 | SELL | +119.34 | +71.86 | -47.48 | +71.86 | floor stop |
| 2025-05-08T18:20 | SELL | +115.88 | +272.70 | +156.82 | +67.47 | flatten |
| 2025-05-09T04:25 | SELL | +129.36 | +98.54 | -30.82 | +69.74 | profit lock |
| 2025-05-12T08:10 | SELL | +113.30 | +62.99 | -50.31 | +62.99 | floor stop |
| 2025-05-13T06:10 | BUY | +107.16 | +164.26 | +57.10 | +73.73 | reversal |
| 2025-05-15T05:20 | SELL | +96.08 | +77.95 | -18.13 | +77.95 | floor stop |
| 2025-05-15T11:20 | BUY | +104.13 | +71.71 | -32.42 | +71.71 | floor stop |
| 2025-05-15T21:30 | BUY | +104.35 | +72.20 | -32.15 | +72.20 | floor stop |
| 2025-05-16T13:35 | SELL | +169.07 | +287.85 | +118.78 | +69.45 | profit lock |
| 2025-05-20T10:25 | BUY | +108.66 | +70.39 | -38.27 | +70.39 | floor stop |
| 2025-05-21T14:30 | SELL | +116.94 | +72.25 | -44.69 | +72.25 | floor stop |
| 2025-05-23T06:40 | BUY | +177.58 | +748.32 | +570.74 | +67.80 | reversal |
| 2025-05-27T06:55 | SELL | +168.12 | +1159.04 | +990.92 | +81.20 | reversal |
| 2025-05-29T13:05 | BUY | +121.22 | +601.80 | +480.58 | +100.13 | reversal |
| 2025-06-03T16:40 | SELL | +205.98 | +93.22 | -112.76 | +93.22 | floor stop |
| 2025-06-04T06:30 | SELL | +112.95 | +110.57 | -2.38 | +110.57 | floor stop |
| 2025-06-04T13:25 | SELL | +188.60 | +94.31 | -94.29 | +94.31 | floor stop |
| 2025-06-05T10:05 | BUY | +127.48 | +271.26 | +143.78 | +102.43 | profit lock |
| 2025-06-06T08:40 | SELL | +103.26 | +105.18 | +1.92 | +105.18 | floor stop |
| 2025-06-10T08:05 | BUY | +104.76 | +104.48 | -0.28 | +104.48 | floor stop |
| 2025-06-11T12:20 | SELL | +98.11 | +98.02 | -0.09 | +98.02 | floor stop |
| 2025-06-12T10:25 | SELL | +113.70 | +242.64 | +128.94 | +98.77 | profit lock |
| 2025-06-12T12:45 | BUY | +112.47 | +109.74 | -2.73 | +109.74 | floor stop |
| 2025-06-13T14:30 | BUY | +102.87 | +101.09 | -1.78 | +101.09 | floor stop |
| 2025-06-16T06:15 | SELL | +103.05 | +98.80 | -4.25 | +98.80 | floor stop |
| 2025-06-16T15:20 | SELL | +237.75 | +168.54 | -69.21 | +96.20 | profit lock |
| 2025-06-17T05:50 | SELL | +127.17 | +107.69 | -19.48 | +107.69 | floor stop |
| 2025-06-17T13:00 | BUY | +103.34 | +105.16 | +1.82 | +105.16 | floor stop |
| 2025-06-17T17:25 | SELL | +115.61 | +97.67 | -17.94 | +97.67 | floor stop |
| 2025-06-18T06:05 | BUY | +117.64 | +110.71 | -6.93 | +110.71 | floor stop |
| 2025-06-20T04:10 | SELL | +106.89 | +106.92 | +0.03 | +106.92 | floor stop |
| 2025-06-20T08:15 | SELL | +140.76 | +100.39 | -40.37 | +100.39 | floor stop |
| 2025-06-20T14:15 | SELL | +118.63 | +99.19 | -19.44 | +99.19 | floor stop |
| 2025-06-20T16:20 | BUY | +130.55 | +144.35 | +13.80 | +109.58 | reversal |
| 2025-06-23T07:35 | SELL | +185.06 | +104.93 | -80.13 | +104.93 | floor stop |
| 2025-06-24T08:00 | SELL | +210.95 | +996.18 | +785.23 | +100.00 | reversal |
| 2025-06-26T09:50 | BUY | +108.18 | +119.86 | +11.68 | +119.86 | floor stop |
| 2025-06-26T19:10 | BUY | +127.18 | +123.37 | -3.81 | +123.37 | floor stop |
| 2025-06-27T05:35 | SELL | +133.80 | +1161.42 | +1027.62 | +117.09 | reversal |
| 2025-07-02T11:10 | BUY | +114.75 | +133.56 | +18.81 | +133.56 | floor stop |
| 2025-07-03T07:15 | BUY | +108.46 | +129.77 | +21.31 | +129.77 | floor stop |
| 2025-07-03T12:25 | SELL | +108.25 | +127.63 | +19.38 | +127.63 | floor stop |
| 2025-07-04T06:25 | BUY | +101.26 | +124.88 | +23.62 | +124.88 | floor stop |
| 2025-07-04T19:30 | BUY | +102.16 | +117.49 | +15.33 | +117.49 | floor stop |
| 2025-07-08T16:45 | SELL | +115.36 | +119.36 | +4.00 | +119.36 | floor stop |
| 2025-07-09T05:35 | SELL | +106.68 | +114.19 | +7.51 | +114.19 | floor stop |
| 2025-07-10T07:45 | BUY | +93.14 | +110.25 | +17.11 | +110.25 | floor stop |
| 2025-07-11T08:15 | BUY | +106.83 | +100.51 | -6.32 | +100.51 | floor stop |
| 2025-07-11T12:50 | BUY | +104.98 | +100.89 | -4.09 | +100.89 | floor stop |
| 2025-07-14T09:10 | BUY | +125.82 | +159.01 | +33.19 | +104.91 | profit lock |
| 2025-07-15T18:20 | SELL | +150.77 | +99.58 | -51.19 | +99.58 | floor stop |
| 2025-07-16T06:30 | BUY | +94.00 | +100.79 | +6.79 | +100.79 | floor stop |
| 2025-07-16T18:00 | BUY | +108.80 | +104.21 | -4.59 | +104.21 | floor stop |
| 2025-07-17T08:30 | SELL | +149.55 | +115.90 | -33.65 | +100.31 | profit lock |
| 2025-07-17T17:05 | BUY | +89.41 | +99.79 | +10.38 | +99.79 | floor stop |
| 2025-07-18T09:25 | BUY | +94.15 | +425.71 | +331.56 | +98.28 | reversal |
| 2025-07-21T07:30 | BUY | +215.86 | +349.70 | +133.84 | +99.01 | profit lock |
| 2025-07-23T11:40 | BUY | +99.20 | +99.24 | +0.04 | +99.24 | floor stop |
| 2025-07-25T20:20 | BUY | +101.00 | +104.81 | +3.81 | +104.81 | floor stop |
| 2025-07-31T16:45 | SELL | +96.12 | +87.31 | -8.81 | +87.31 | floor stop |
| 2025-08-01T09:30 | SELL | +93.35 | +82.28 | -11.07 | +82.28 | floor stop |
| 2025-08-01T11:50 | BUY | +77.61 | +89.57 | +11.96 | +89.57 | floor stop |
| 2025-08-01T20:20 | BUY | +90.35 | +236.17 | +145.82 | +93.78 | flatten |
| 2025-08-04T05:20 | BUY | +88.81 | +97.14 | +8.33 | +97.14 | floor stop |
| 2025-08-05T11:45 | SELL | +83.84 | +88.87 | +5.03 | +88.87 | floor stop |
| 2025-08-05T15:25 | BUY | +79.43 | +300.97 | +221.54 | +91.79 | reversal |
| 2025-08-07T09:10 | BUY | +133.12 | +90.51 | -42.61 | +90.51 | floor stop |
| 2025-08-11T09:20 | SELL | +85.66 | +88.46 | +2.80 | +88.46 | floor stop |
| 2025-08-13T09:15 | BUY | +85.76 | +78.19 | -7.57 | +78.19 | floor stop |
| 2025-08-19T14:55 | SELL | +59.21 | +70.68 | +11.47 | +70.68 | floor stop |
| 2025-08-19T17:40 | SELL | +59.75 | +72.21 | +12.46 | +72.21 | floor stop |
| 2025-08-20T06:35 | BUY | +85.59 | +71.38 | -14.21 | +71.38 | floor stop |
| 2025-08-22T16:30 | BUY | +155.58 | +587.82 | +432.24 | +62.87 | flatten |
| 2025-08-25T20:30 | SELL | +56.28 | +107.15 | +50.87 | +78.32 | flatten |
| 2025-08-26T17:45 | BUY | +59.24 | +68.44 | +9.20 | +68.44 | floor stop |
| 2025-08-27T10:50 | BUY | +54.40 | +67.37 | +12.97 | +67.37 | floor stop |
| 2025-08-27T12:45 | SELL | +64.12 | +67.11 | +2.99 | +67.11 | floor stop |
| 2025-08-27T17:25 | BUY | +54.48 | +67.08 | +12.60 | +67.08 | floor stop |
| 2025-08-28T14:00 | BUY | +94.04 | +65.90 | -28.14 | +65.90 | floor stop |
| 2025-09-01T04:45 | BUY | +51.66 | +66.97 | +15.31 | +66.97 | floor stop |
| 2025-09-02T14:50 | BUY | +48.64 | +59.63 | +10.99 | +59.63 | floor stop |
| 2025-09-02T17:00 | BUY | +71.66 | +234.48 | +162.82 | +62.25 | flatten |
| 2025-09-03T13:50 | BUY | +77.08 | +60.09 | -16.99 | +60.09 | floor stop |
| 2025-09-08T08:45 | BUY | +50.01 | +57.02 | +7.01 | +57.02 | floor stop |
| 2025-09-09T16:20 | BUY | +54.61 | +53.89 | -0.72 | +53.89 | floor stop |
| 2025-09-10T05:05 | BUY | +49.26 | +58.81 | +9.55 | +58.81 | floor stop |
| 2025-09-12T04:00 | BUY | +48.97 | +55.23 | +6.26 | +55.23 | floor stop |
| 2025-09-12T09:20 | SELL | +65.80 | +55.70 | -10.10 | +55.70 | floor stop |
| 2025-09-12T14:55 | SELL | +92.37 | +51.78 | -40.59 | +51.78 | floor stop |
| 2025-09-17T08:35 | SELL | +69.65 | +50.71 | -18.94 | +50.71 | floor stop |
| 2025-09-18T06:45 | SELL | +56.63 | +50.17 | -6.46 | +50.17 | floor stop |
| 2025-09-18T10:30 | BUY | +46.63 | +56.81 | +10.18 | +56.81 | floor stop |
| 2025-09-18T15:55 | SELL | +49.39 | +56.54 | +7.15 | +56.54 | floor stop |
| 2025-09-19T05:55 | BUY | +48.28 | +49.62 | +1.34 | +49.62 | floor stop |
| 2025-09-22T08:15 | BUY | +48.50 | +267.51 | +219.01 | +55.29 | reversal |
| 2025-09-23T06:45 | BUY | +50.45 | +59.70 | +9.25 | +59.70 | floor stop |
| 2025-09-23T17:05 | SELL | +56.92 | +53.19 | -3.73 | +53.19 | floor stop |
| 2025-09-23T21:10 | SELL | +104.58 | +83.52 | -21.06 | +58.17 | profit lock |
| 2025-09-24T17:05 | SELL | +54.41 | +63.33 | +8.92 | +63.33 | floor stop |
| 2025-09-29T13:25 | BUY | +52.50 | +53.21 | +0.71 | +53.21 | floor stop |
| 2025-09-30T09:55 | SELL | +46.82 | +56.69 | +9.87 | +56.69 | floor stop |
| 2025-09-30T15:45 | BUY | +48.27 | +60.74 | +12.47 | +60.74 | floor stop |
| 2025-10-01T04:40 | BUY | +48.13 | +55.80 | +7.67 | +55.80 | floor stop |
| 2025-10-01T10:15 | BUY | +53.84 | +55.63 | +1.79 | +55.63 | floor stop |
| 2025-10-02T08:15 | BUY | +54.00 | +55.37 | +1.37 | +55.37 | floor stop |
| 2025-10-02T16:50 | SELL | +51.86 | +254.50 | +202.64 | +53.64 | profit lock |
| 2025-10-03T15:40 | BUY | +63.03 | +59.96 | -3.07 | +59.96 | floor stop |
| 2025-10-06T04:45 | BUY | +63.25 | +58.11 | -5.14 | +58.11 | floor stop |
| 2025-10-06T10:30 | BUY | +74.70 | +57.48 | -17.22 | +57.48 | floor stop |
| 2025-10-06T17:10 | BUY | +54.25 | +54.62 | +0.37 | +54.62 | floor stop |
| 2025-10-07T08:20 | SELL | +67.95 | +59.72 | -8.23 | +59.72 | floor stop |
| 2025-10-08T04:45 | BUY | +53.83 | +59.48 | +5.65 | +59.48 | floor stop |
| 2025-10-09T06:05 | BUY | +59.70 | +62.48 | +2.78 | +62.48 | floor stop |
| 2025-10-10T06:10 | SELL | +67.10 | +61.15 | -5.95 | +61.15 | floor stop |
| 2025-10-10T11:00 | BUY | +54.48 | +64.04 | +9.56 | +64.04 | floor stop |
| 2025-10-16T08:10 | SELL | +49.77 | +56.54 | +6.77 | +56.54 | floor stop |
| 2025-10-17T04:15 | SELL | +77.64 | +57.15 | -20.49 | +57.15 | floor stop |
| 2025-10-17T05:30 | BUY | +67.93 | +59.22 | -8.71 | +59.22 | floor stop |
| 2025-10-21T04:20 | SELL | +60.44 | +61.25 | +0.81 | +61.25 | floor stop |
| 2025-10-22T10:55 | SELL | +88.70 | +60.59 | -28.11 | +60.59 | floor stop |
| 2025-10-22T19:30 | BUY | +61.40 | +104.27 | +42.87 | +61.47 | flatten |
| 2025-10-24T13:20 | SELL | +93.18 | +53.41 | -39.77 | +53.41 | floor stop |
| 2025-10-27T10:10 | SELL | +69.08 | +199.92 | +130.84 | +57.90 | profit lock |
| 2025-10-29T09:05 | BUY | +61.60 | +59.92 | -1.68 | +59.92 | floor stop |
| 2025-10-30T07:05 | BUY | +57.33 | +64.32 | +6.99 | +64.32 | floor stop |
| 2025-10-30T11:25 | BUY | +79.53 | +63.91 | -15.62 | +63.91 | floor stop |
| 2025-10-31T15:00 | BUY | +67.25 | +64.78 | -2.47 | +64.78 | floor stop |
| 2025-11-03T13:25 | BUY | +58.64 | +62.25 | +3.61 | +62.25 | floor stop |
| 2025-11-04T19:15 | SELL | +82.04 | +173.47 | +91.43 | +60.36 | flatten |
| 2025-11-07T18:10 | BUY | +99.96 | +119.40 | +19.44 | +54.84 | profit lock |
| 2025-11-11T16:15 | SELL | +60.84 | +59.65 | -1.19 | +59.65 | floor stop |
| 2025-11-12T16:50 | BUY | +60.55 | +227.76 | +167.21 | +61.46 | flatten |
| 2025-11-13T09:25 | BUY | +77.64 | +81.71 | +4.07 | +58.74 | profit lock |
| 2025-11-13T16:00 | SELL | +102.04 | +68.08 | -33.96 | +60.25 | profit lock |
| 2025-11-14T04:10 | BUY | +58.05 | +64.24 | +6.19 | +64.24 | floor stop |
| 2025-11-14T07:05 | SELL | +67.27 | +68.35 | +1.08 | +68.35 | floor stop |
| 2025-11-14T11:30 | SELL | +60.83 | +66.38 | +5.55 | +66.38 | floor stop |
| 2025-11-17T09:10 | BUY | +66.28 | +69.54 | +3.26 | +69.54 | floor stop |
| 2025-11-18T10:00 | BUY | +65.74 | +67.03 | +1.29 | +67.03 | floor stop |
| 2025-11-18T15:20 | BUY | +58.48 | +67.13 | +8.65 | +67.13 | floor stop |
| 2025-11-19T11:20 | BUY | +81.93 | +96.68 | +14.75 | +63.64 | profit lock |
| 2025-11-20T14:00 | BUY | +59.59 | +65.69 | +6.10 | +65.69 | floor stop |
| 2025-11-21T14:20 | BUY | +60.55 | +65.02 | +4.47 | +65.02 | floor stop |
| 2025-11-27T14:25 | SELL | +66.78 | +56.32 | -10.46 | +56.32 | floor stop |
| 2025-11-28T09:25 | SELL | +71.13 | +215.63 | +144.50 | +57.07 | profit lock |
| 2025-12-01T16:00 | SELL | +70.02 | +56.85 | -13.17 | +56.85 | floor stop |
| 2025-12-02T17:05 | SELL | +53.54 | +56.95 | +3.41 | +56.95 | floor stop |
| 2025-12-05T05:15 | BUY | +61.42 | +192.53 | +131.11 | +54.19 | reversal |
| 2025-12-09T06:40 | SELL | +59.91 | +53.49 | -6.42 | +53.49 | floor stop |
| 2025-12-09T10:00 | BUY | +50.39 | +58.47 | +8.08 | +58.47 | floor stop |
| 2025-12-10T08:30 | SELL | +47.95 | +56.89 | +8.94 | +56.89 | floor stop |
| 2025-12-11T04:25 | SELL | +46.97 | +56.56 | +9.59 | +56.56 | floor stop |
| 2025-12-11T16:05 | BUY | +59.52 | +52.73 | -6.79 | +52.73 | floor stop |
| 2025-12-12T07:25 | BUY | +54.18 | +54.00 | -0.18 | +54.00 | floor stop |
| 2025-12-16T04:35 | SELL | +83.52 | +122.88 | +39.36 | +51.32 | profit lock |
| 2025-12-17T05:55 | BUY | +53.90 | +50.07 | -3.83 | +50.07 | floor stop |
| 2025-12-17T08:50 | SELL | +69.39 | +54.38 | -15.01 | +54.38 | floor stop |
| 2025-12-22T14:55 | BUY | +59.85 | +46.27 | -13.58 | +46.27 | floor stop |
| 2025-12-23T13:15 | BUY | +75.84 | +43.07 | -32.77 | +43.07 | floor stop |
| 2025-12-23T16:00 | SELL | +48.64 | +46.92 | -1.72 | +46.92 | floor stop |
| 2025-12-29T08:20 | SELL | +72.98 | +54.14 | -18.84 | +43.30 | profit lock |
| 2025-12-30T21:15 | SELL | +62.36 | +59.20 | -3.16 | +44.83 | profit lock |
| 2025-12-31T10:35 | SELL | +58.69 | +43.61 | -15.08 | +43.61 | floor stop |
| 2026-01-07T06:15 | SELL | +63.29 | +43.50 | -19.79 | +43.50 | floor stop |
| 2026-01-08T13:20 | SELL | +48.99 | +41.48 | -7.51 | +41.48 | floor stop |
| 2026-01-08T21:55 | BUY | +63.64 | +182.47 | +118.83 | +40.99 | flatten |
| 2026-01-12T14:55 | BUY | +41.80 | +40.32 | -1.48 | +40.32 | floor stop |
| 2026-01-13T08:00 | SELL | +45.00 | +47.28 | +2.28 | +36.66 | profit lock |
| 2026-01-13T14:50 | BUY | +53.56 | +101.10 | +47.54 | +39.93 | profit lock |
| 2026-01-14T20:10 | BUY | +41.29 | +39.91 | -1.38 | +39.91 | floor stop |
| 2026-01-15T14:50 | SELL | +45.04 | +42.99 | -2.05 | +42.99 | floor stop |
| 2026-01-16T14:30 | SELL | +69.40 | +40.36 | -29.04 | +40.36 | floor stop |
| 2026-01-20T05:30 | BUY | +43.94 | +328.30 | +284.36 | +37.89 | reversal |
| 2026-01-21T16:45 | SELL | +49.71 | +42.12 | -7.59 | +42.12 | floor stop |
| 2026-01-22T05:55 | BUY | +48.47 | +79.80 | +31.33 | +43.04 | reversal |
| 2026-01-23T07:15 | BUY | +37.56 | +42.69 | +5.13 | +42.69 | floor stop |
| 2026-01-23T08:25 | SELL | +76.28 | +72.86 | -3.42 | +44.19 | profit lock |
| 2026-01-29T09:25 | SELL | +40.87 | +37.91 | -2.96 | +37.91 | floor stop |
| 2026-02-02T05:25 | SELL | +41.65 | +29.83 | -11.82 | +29.83 | floor stop |
| 2026-02-02T07:30 | SELL | +55.97 | +96.49 | +40.52 | +30.75 | profit lock |
| 2026-02-03T05:55 | BUY | +45.54 | +32.67 | -12.87 | +32.67 | floor stop |
| 2026-02-03T18:10 | BUY | +33.87 | +30.57 | -3.30 | +30.57 | floor stop |
| 2026-02-04T21:05 | BUY | +43.85 | +32.18 | -11.67 | +32.18 | floor stop |
| 2026-02-05T14:00 | SELL | +33.70 | +30.31 | -3.39 | +30.31 | floor stop |
| 2026-02-05T18:35 | BUY | +63.15 | +27.55 | -35.60 | +27.55 | floor stop |
| 2026-02-13T07:30 | SELL | +29.85 | +25.23 | -4.62 | +25.23 | floor stop |
| 2026-02-13T11:55 | SELL | +31.62 | +25.28 | -6.34 | +25.28 | floor stop |
| 2026-02-13T14:10 | BUY | +51.06 | +25.08 | -25.98 | +25.08 | floor stop |
| 2026-02-16T04:25 | SELL | +34.15 | +50.98 | +16.83 | +25.77 | profit lock |
| 2026-02-18T14:35 | BUY | +29.83 | +116.85 | +87.02 | +26.41 | reversal |
| 2026-02-19T11:50 | SELL | +26.81 | +25.90 | -0.91 | +25.90 | floor stop |
| 2026-02-19T22:00 | BUY | +36.44 | +27.62 | -8.82 | +27.62 | floor stop |
| 2026-02-20T06:40 | BUY | +28.16 | +32.17 | +4.01 | +27.96 | profit lock |
| 2026-02-23T16:20 | BUY | +31.98 | +28.05 | -3.93 | +28.05 | floor stop |
| 2026-02-25T12:15 | SELL | +33.52 | +26.71 | -6.81 | +26.71 | floor stop |
| 2026-02-26T12:30 | SELL | +34.05 | +26.53 | -7.52 | +26.53 | floor stop |
| 2026-03-02T08:05 | BUY | +40.48 | +26.49 | -13.99 | +26.49 | floor stop |
| 2026-03-03T07:15 | SELL | +30.52 | +26.21 | -4.31 | +26.21 | floor stop |
| 2026-03-03T20:25 | BUY | +28.01 | +25.03 | -2.98 | +25.03 | floor stop |
| 2026-03-05T06:40 | SELL | +26.38 | +40.12 | +13.74 | +25.46 | profit lock |
| 2026-03-09T05:10 | BUY | +40.25 | +34.81 | -5.44 | +23.97 | profit lock |
| 2026-03-10T04:20 | BUY | +38.26 | +24.26 | -14.00 | +24.26 | floor stop |
| 2026-03-10T15:40 | BUY | +47.37 | +25.85 | -21.52 | +25.85 | floor stop |
| 2026-03-13T16:55 | SELL | +25.69 | +67.76 | +42.07 | +21.05 | profit lock |
| 2026-03-13T21:10 | SELL | +26.34 | +28.24 | +1.90 | +23.01 | profit lock |
| 2026-03-16T10:15 | SELL | +28.08 | +23.97 | -4.11 | +23.97 | floor stop |
| 2026-03-16T13:45 | BUY | +28.36 | +24.55 | -3.81 | +24.55 | floor stop |
| 2026-03-17T08:50 | SELL | +30.15 | +25.71 | -4.44 | +25.71 | floor stop |
| 2026-03-17T18:20 | SELL | +37.59 | +25.12 | -12.47 | +25.12 | floor stop |
| 2026-03-18T08:35 | BUY | +30.28 | +26.13 | -4.15 | +26.13 | floor stop |
| 2026-03-19T06:55 | SELL | +38.54 | +26.14 | -12.40 | +26.14 | floor stop |
| 2026-03-20T10:35 | BUY | +32.46 | +26.17 | -6.29 | +26.17 | floor stop |
| 2026-03-20T12:15 | SELL | +34.81 | +25.63 | -9.18 | +25.63 | floor stop |
| 2026-03-20T21:20 | SELL | +50.41 | +44.01 | -6.40 | +23.37 | profit lock |
| 2026-03-23T05:10 | SELL | +48.56 | +20.00 | -28.56 | +20.00 | floor stop |
| 2026-03-23T11:30 | BUY | +55.31 | +19.25 | -36.06 | +19.25 | floor stop |
| 2026-03-23T18:35 | SELL | +88.69 | +43.95 | -44.74 | +19.64 | profit lock |
| 2026-03-24T09:00 | BUY | +39.33 | +24.75 | -14.58 | +24.75 | floor stop |
| 2026-03-25T08:20 | SELL | +35.53 | +28.37 | -7.16 | +28.37 | floor stop |
| 2026-03-26T08:20 | SELL | +32.16 | +77.30 | +45.14 | +27.22 | profit lock |
| 2026-03-27T04:25 | BUY | +37.04 | +27.82 | -9.22 | +27.82 | floor stop |
| 2026-03-27T10:50 | SELL | +32.14 | +27.25 | -4.89 | +27.25 | floor stop |
| 2026-03-30T08:05 | BUY | +41.58 | +26.37 | -15.21 | +26.37 | floor stop |
| 2026-04-01T13:40 | BUY | +34.70 | +26.93 | -7.77 | +26.93 | floor stop |
| 2026-04-02T04:00 | SELL | +42.48 | +75.18 | +32.70 | +22.74 | profit lock |
| 2026-04-02T08:40 | SELL | +41.93 | +27.80 | -14.13 | +27.80 | floor stop |
| 2026-04-06T07:00 | BUY | +44.93 | +26.36 | -18.57 | +26.36 | floor stop |
| 2026-04-06T10:45 | BUY | +34.03 | +27.17 | -6.86 | +27.17 | floor stop |
| 2026-04-06T14:40 | SELL | +34.89 | +28.36 | -6.53 | +28.36 | floor stop |
| 2026-04-08T20:35 | SELL | +30.51 | +25.11 | -5.40 | +25.11 | floor stop |
| 2026-04-09T10:55 | BUY | +27.90 | +25.04 | -2.86 | +25.04 | floor stop |
| 2026-04-15T14:20 | BUY | +28.41 | +21.05 | -7.36 | +21.05 | floor stop |
| 2026-04-17T04:55 | BUY | +23.68 | +21.28 | -2.40 | +21.28 | floor stop |
| 2026-04-17T14:40 | BUY | +33.62 | +145.91 | +112.29 | +20.02 | reversal |
| 2026-04-20T04:10 | BUY | +29.03 | +22.95 | -6.08 | +22.95 | floor stop |
| 2026-04-23T04:25 | SELL | +33.74 | +21.71 | -12.03 | +21.71 | floor stop |
| 2026-04-23T11:40 | SELL | +24.41 | +21.34 | -3.07 | +21.34 | floor stop |
| 2026-04-23T14:15 | BUY | +53.32 | +38.41 | -14.91 | +21.20 | profit lock |
| 2026-04-24T05:05 | SELL | +27.17 | +23.34 | -3.83 | +23.34 | floor stop |
| 2026-04-28T04:05 | SELL | +31.40 | +22.90 | -8.50 | +22.90 | floor stop |
| 2026-04-29T19:45 | SELL | +28.14 | +21.21 | -6.93 | +21.21 | floor stop |
| 2026-04-30T05:35 | SELL | +25.15 | +21.83 | -3.32 | +21.83 | floor stop |
| 2026-04-30T08:30 | BUY | +27.94 | +148.36 | +120.42 | +21.76 | reversal |
| 2026-05-01T14:50 | BUY | +31.69 | +25.91 | -5.78 | +25.91 | floor stop |
| 2026-05-04T08:30 | SELL | +34.34 | +24.86 | -9.48 | +24.86 | floor stop |
| 2026-05-04T13:00 | SELL | +30.78 | +25.95 | -4.83 | +25.95 | floor stop |
| 2026-05-06T14:55 | SELL | +29.22 | +24.72 | -4.50 | +24.72 | floor stop |
| 2026-05-07T09:00 | BUY | +65.37 | +45.57 | -19.80 | +24.72 | profit lock |
| 2026-05-11T11:50 | SELL | +32.12 | +23.34 | -8.78 | +23.34 | floor stop |
| 2026-05-11T14:00 | BUY | +38.20 | +24.64 | -13.56 | +24.64 | floor stop |
| 2026-05-12T04:00 | SELL | +29.29 | +23.80 | -5.49 | +23.80 | floor stop |
| 2026-05-12T20:05 | BUY | +33.84 | +24.37 | -9.47 | +24.37 | floor stop |
| 2026-05-13T04:00 | SELL | +45.07 | +24.53 | -20.54 | +24.53 | floor stop |
| 2026-05-13T15:15 | SELL | +30.59 | +23.93 | -6.66 | +23.93 | floor stop |
| 2026-05-15T22:10 | SELL | +38.33 | +22.08 | -16.25 | +22.08 | floor stop |
| 2026-05-19T20:50 | SELL | +32.46 | +44.64 | +12.18 | +22.36 | flatten |
| 2026-05-21T12:55 | SELL | +35.07 | +20.98 | -14.09 | +20.98 | floor stop |
| 2026-05-26T14:25 | SELL | +25.44 | +21.31 | -4.13 | +21.31 | floor stop |
| 2026-05-27T12:40 | SELL | +51.41 | +76.67 | +25.26 | +19.58 | profit lock |
| 2026-05-28T15:45 | BUY | +33.42 | +20.20 | -13.22 | +20.20 | floor stop |
| 2026-05-29T16:20 | BUY | +36.24 | +20.55 | -15.69 | +20.55 | floor stop |
| 2026-06-02T06:15 | BUY | +23.90 | +83.33 | +59.43 | +19.89 | reversal |
| 2026-06-03T07:30 | SELL | +24.05 | +20.18 | -3.87 | +20.18 | floor stop |
| 2026-06-04T12:45 | BUY | +30.45 | +20.73 | -9.72 | +20.73 | floor stop |
| 2026-06-05T15:45 | SELL | +23.08 | +19.43 | -3.65 | +19.43 | floor stop |
| 2026-06-08T08:05 | SELL | +40.68 | +19.90 | -20.78 | +19.90 | floor stop |
| 2026-06-08T10:55 | SELL | +22.19 | +18.81 | -3.38 | +18.81 | floor stop |
| 2026-06-08T13:30 | BUY | +31.37 | +19.78 | -11.59 | +19.78 | floor stop |
| 2026-06-10T08:30 | BUY | +38.29 | +18.46 | -19.83 | +18.46 | floor stop |
| 2026-06-10T16:35 | BUY | +25.69 | +17.30 | -8.39 | +17.30 | floor stop |
| 2026-06-10T17:40 | SELL | +37.92 | +18.11 | -19.81 | +18.11 | floor stop |
| 2026-06-15T10:20 | BUY | +62.09 | +32.69 | -29.40 | +19.44 | profit lock |
| 2026-06-15T19:15 | SELL | +26.46 | +20.63 | -5.83 | +20.63 | floor stop |
| 2026-06-16T06:25 | BUY | +25.33 | +21.38 | -3.95 | +21.38 | floor stop |
| 2026-06-16T10:25 | BUY | +26.77 | +21.56 | -5.21 | +21.56 | floor stop |
| 2026-06-18T17:25 | SELL | +25.08 | +18.02 | -7.06 | +18.02 | floor stop |
| 2026-06-18T21:35 | SELL | +29.42 | +19.03 | -10.39 | +19.03 | floor stop |
| 2026-06-19T10:00 | BUY | +34.90 | +18.57 | -16.33 | +18.57 | floor stop |
| 2026-06-23T20:25 | SELL | +28.52 | +18.81 | -9.71 | +18.81 | floor stop |
| 2026-06-24T08:25 | BUY | +25.27 | +18.79 | -6.48 | +18.79 | floor stop |
| 2026-06-24T20:25 | SELL | +24.81 | +17.49 | -7.32 | +17.49 | floor stop |
| 2026-06-26T07:10 | BUY | +26.53 | +57.46 | +30.93 | +17.72 | reversal |
| 2026-06-29T11:50 | SELL | +35.10 | +22.98 | -12.12 | +17.59 | profit lock |
| 2026-06-29T16:45 | SELL | +30.62 | +20.80 | -9.82 | +18.35 | profit lock |
| 2026-07-03T19:40 | BUY | +25.40 | +16.40 | -9.00 | +16.40 | floor stop |
| 2026-07-06T05:20 | SELL | +22.72 | +16.24 | -6.48 | +16.24 | floor stop |
| 2026-07-07T13:15 | BUY | +25.96 | +52.45 | +26.49 | +15.76 | profit lock |
| 2026-07-07T21:30 | SELL | +31.06 | +85.34 | +54.28 | +16.11 | flatten |
| 2026-07-08T17:10 | SELL | +27.57 | +16.37 | -11.20 | +16.37 | floor stop |
| 2026-07-08T19:40 | BUY | +25.43 | +18.02 | -7.41 | +18.02 | floor stop |
| 2026-07-09T05:50 | SELL | +28.32 | +17.40 | -10.92 | +17.40 | floor stop |
| 2026-07-09T08:10 | BUY | +32.32 | +48.38 | +16.06 | +18.50 | reversal |
| 2026-07-13T10:50 | BUY | +24.30 | +19.03 | -5.27 | +19.03 | floor stop |
| 2026-07-13T13:10 | SELL | +30.56 | +19.67 | -10.89 | +19.67 | floor stop |
| 2026-07-13T16:45 | SELL | +65.00 | +126.92 | +61.92 | +16.02 | flatten |
| 2026-07-15T18:10 | SELL | +26.68 | +20.53 | -6.15 | +20.53 | floor stop |
| 2026-07-15T20:30 | BUY | +26.64 | +21.05 | -5.59 | +21.05 | floor stop |
| 2026-07-16T15:35 | SELL | +25.41 | +27.05 | +1.64 | +20.33 | profit lock |
| 2026-07-17T06:05 | BUY | +26.03 | +20.24 | -5.79 | +20.24 | floor stop |
| 2026-07-17T08:50 | BUY | +26.48 | +20.97 | -5.51 | +20.97 | floor stop |
| 2026-07-17T15:20 | SELL | +30.90 | +21.07 | -9.83 | +21.07 | floor stop |
| 2026-07-21T05:55 | BUY | +41.45 | +54.14 | +12.69 | +22.44 | profit lock |
| 2026-07-22T16:20 | BUY | +33.18 | +21.91 | -11.27 | +21.91 | floor stop |
| 2026-07-24T09:55 | BUY | +29.46 | +50.49 | +21.03 | +22.04 | reversal |
| 2026-07-28T11:50 | SELL | +29.12 | +26.88 | -2.24 | +22.02 | profit lock |
| 2026-07-29T07:55 | BUY | +51.68 | +25.53 | -26.15 | +21.09 | profit lock |
| 2026-07-29T19:05 | BUY | +32.95 | +82.55 | +49.60 | +23.06 | profit lock |
| 2026-07-31T10:20 | SELL | +29.32 | +22.70 | -6.62 | +22.70 | floor stop |
| 2026-07-31T15:35 | SELL | +28.38 | +23.05 | -5.33 | +23.05 | floor stop |
| 2026-08-04T14:10 | BUY | +33.82 | +21.56 | -12.26 | +21.56 | floor stop |
| 2026-08-05T08:30 | BUY | +41.26 | +39.43 | -1.83 | +21.18 | profit lock |
| 2026-08-05T14:05 | BUY | +42.40 | +68.44 | +26.04 | +21.03 | profit lock |
| 2026-08-05T17:00 | BUY | +34.31 | +22.62 | -11.69 | +22.62 | floor stop |
| 2026-08-05T19:35 | BUY | +40.00 | +23.21 | -16.79 | +23.21 | floor stop |
| 2026-08-06T05:45 | SELL | +38.60 | +23.82 | -14.78 | +23.82 | floor stop |
| 2026-08-07T06:55 | BUY | +35.83 | +24.02 | -11.81 | +24.02 | floor stop |
| 2026-08-10T07:10 | BUY | +34.17 | +24.00 | -10.17 | +24.00 | floor stop |
| 2026-08-10T11:40 | SELL | +33.13 | +24.77 | -8.36 | +24.77 | floor stop |
| 2026-08-10T17:25 | BUY | +33.10 | +24.48 | -8.62 | +24.48 | floor stop |
| 2026-08-11T07:40 | SELL | +51.03 | +70.89 | +19.86 | +24.32 | reversal |

**aggregate**: mean delta +14.79, better 127, worse 235, min -112.76, max +1027.62


### floor-b — armed trades vs baseline profit-target exit (same entry bar, 293 pairs)

| entry (server) | dir | baseline P/L | floor-b P/L | delta $ | floor $ | exit reason |
|---|---|---:|---:|---:|---:|---|
| 2025-03-17T11:55 | BUY | +94.11 | +92.94 | -1.17 | +92.94 | floor stop |
| 2025-03-18T14:20 | BUY | +119.61 | +91.78 | -27.83 | +91.78 | floor stop |
| 2025-03-19T05:30 | BUY | +122.42 | +90.72 | -31.70 | +90.72 | floor stop |
| 2025-03-19T10:45 | SELL | +95.82 | +92.54 | -3.28 | +92.54 | floor stop |
| 2025-03-20T06:30 | BUY | +121.20 | +93.92 | -27.28 | +93.92 | floor stop |
| 2025-03-20T13:00 | SELL | +131.81 | +93.78 | -38.03 | +93.78 | floor stop |
| 2025-03-21T16:30 | SELL | +99.31 | +122.45 | +23.14 | +91.81 | profit lock |
| 2025-03-24T19:35 | SELL | +113.85 | +91.01 | -22.84 | +91.01 | floor stop |
| 2025-03-25T06:20 | BUY | +141.73 | +91.35 | -50.38 | +91.35 | floor stop |
| 2025-03-26T08:40 | BUY | +103.18 | +90.91 | -12.27 | +90.91 | floor stop |
| 2025-03-27T17:10 | BUY | +108.10 | +93.66 | -14.44 | +93.66 | floor stop |
| 2025-03-31T04:30 | BUY | +109.67 | +90.36 | -19.31 | +90.36 | floor stop |
| 2025-03-31T16:25 | SELL | +117.80 | +90.30 | -27.50 | +90.30 | floor stop |
| 2025-04-02T06:50 | SELL | +101.70 | +86.17 | -15.53 | +86.17 | floor stop |
| 2025-04-02T13:30 | SELL | +134.08 | +88.34 | -45.74 | +88.34 | floor stop |
| 2025-04-04T13:00 | BUY | +116.28 | +87.38 | -28.90 | +87.38 | floor stop |
| 2025-04-09T16:20 | BUY | +125.84 | +84.45 | -41.39 | +84.45 | floor stop |
| 2025-04-17T08:25 | SELL | +105.97 | +74.24 | -31.73 | +74.24 | floor stop |
| 2025-04-21T08:25 | BUY | +95.85 | +74.78 | -21.07 | +74.78 | floor stop |
| 2025-04-21T20:35 | BUY | +95.82 | +74.74 | -21.08 | +74.74 | floor stop |
| 2025-04-22T04:15 | BUY | +104.90 | +75.54 | -29.36 | +75.54 | floor stop |
| 2025-04-22T10:00 | SELL | +97.62 | +76.42 | -21.20 | +76.42 | floor stop |
| 2025-04-22T15:40 | SELL | +94.60 | +77.15 | -17.45 | +77.15 | floor stop |
| 2025-04-25T06:45 | SELL | +104.46 | +75.44 | -29.02 | +75.44 | floor stop |
| 2025-04-28T04:25 | SELL | +102.33 | +75.31 | -27.02 | +75.31 | floor stop |
| 2025-04-29T19:20 | BUY | +103.20 | +72.91 | -30.29 | +72.91 | floor stop |
| 2025-04-30T11:05 | SELL | +126.35 | +72.14 | -54.21 | +72.14 | floor stop |
| 2025-04-30T21:20 | SELL | +98.64 | +74.12 | -24.52 | +74.12 | floor stop |
| 2025-05-01T12:20 | SELL | +108.28 | +73.65 | -34.63 | +73.65 | floor stop |
| 2025-05-02T05:05 | BUY | +118.50 | +72.92 | -45.58 | +72.92 | floor stop |
| 2025-05-05T04:05 | BUY | +91.04 | +71.53 | -19.51 | +71.53 | floor stop |
| 2025-05-06T09:35 | BUY | +132.65 | +71.39 | -61.26 | +71.39 | floor stop |
| 2025-05-06T18:20 | BUY | +94.71 | +70.91 | -23.80 | +70.91 | floor stop |
| 2025-05-08T07:10 | SELL | +119.34 | +70.03 | -49.31 | +70.03 | floor stop |
| 2025-05-08T18:20 | SELL | +115.88 | +272.70 | +156.82 | +70.75 | flatten |
| 2025-05-09T04:25 | SELL | +129.36 | +98.54 | -30.82 | +76.20 | profit lock |
| 2025-05-13T06:10 | BUY | +107.16 | +118.24 | +11.08 | +73.51 | reversal |
| 2025-05-15T05:20 | SELL | +96.08 | +74.90 | -21.18 | +74.90 | floor stop |
| 2025-05-15T11:20 | BUY | +104.13 | +841.39 | +737.26 | +75.06 | flatten |
| 2025-05-16T13:35 | SELL | +169.07 | +325.46 | +156.39 | +91.33 | profit lock |
| 2025-05-20T10:25 | BUY | +108.66 | +92.44 | -16.22 | +92.44 | floor stop |
| 2025-05-21T14:30 | SELL | +116.94 | +88.61 | -28.33 | +88.61 | floor stop |
| 2025-05-23T06:40 | BUY | +177.58 | +822.58 | +645.00 | +88.07 | reversal |
| 2025-05-27T06:55 | SELL | +168.12 | +1241.04 | +1072.92 | +103.59 | reversal |
| 2025-05-29T13:05 | BUY | +121.22 | +124.88 | +3.66 | +124.88 | floor stop |
| 2025-06-03T16:40 | SELL | +205.98 | +118.71 | -87.27 | +118.71 | floor stop |
| 2025-06-04T06:30 | SELL | +112.95 | +120.36 | +7.41 | +120.36 | floor stop |
| 2025-06-04T13:25 | SELL | +188.60 | +123.36 | -65.24 | +123.36 | floor stop |
| 2025-06-05T10:05 | BUY | +127.48 | +271.26 | +143.78 | +119.60 | profit lock |
| 2025-06-10T08:05 | BUY | +104.76 | +121.20 | +16.44 | +121.20 | floor stop |
| 2025-06-11T12:20 | SELL | +98.11 | +116.01 | +17.90 | +116.01 | floor stop |
| 2025-06-12T10:25 | SELL | +113.70 | +256.12 | +142.42 | +115.21 | profit lock |
| 2025-06-12T12:45 | BUY | +112.47 | +120.33 | +7.86 | +120.33 | floor stop |
| 2025-06-13T14:30 | BUY | +102.87 | +120.83 | +17.96 | +120.83 | floor stop |
| 2025-06-16T06:15 | SELL | +103.05 | +120.83 | +17.78 | +120.83 | floor stop |
| 2025-06-16T15:20 | SELL | +237.75 | +168.54 | -69.21 | +120.80 | profit lock |
| 2025-06-17T05:50 | SELL | +127.17 | +124.17 | -3.00 | +124.17 | floor stop |
| 2025-06-17T13:00 | BUY | +103.34 | +124.14 | +20.80 | +124.14 | floor stop |
| 2025-06-17T17:25 | SELL | +115.61 | +123.87 | +8.26 | +123.87 | floor stop |
| 2025-06-18T06:05 | BUY | +117.64 | +127.05 | +9.41 | +127.05 | floor stop |
| 2025-06-20T04:10 | SELL | +106.89 | +126.94 | +20.05 | +126.94 | floor stop |
| 2025-06-20T08:15 | SELL | +140.76 | +129.48 | -11.28 | +129.48 | floor stop |
| 2025-06-20T14:15 | SELL | +118.63 | +130.70 | +12.07 | +130.70 | floor stop |
| 2025-06-20T16:20 | BUY | +130.55 | +133.31 | +2.76 | +133.31 | floor stop |
| 2025-06-23T07:35 | SELL | +185.06 | +133.38 | -51.68 | +133.38 | floor stop |
| 2025-06-24T08:00 | SELL | +210.95 | +1016.87 | +805.92 | +133.94 | reversal |
| 2025-06-26T09:50 | BUY | +108.18 | +144.05 | +35.87 | +144.05 | floor stop |
| 2025-06-26T19:10 | BUY | +127.18 | +145.38 | +18.20 | +145.38 | floor stop |
| 2025-06-27T05:35 | SELL | +133.80 | +1233.48 | +1099.68 | +145.24 | reversal |
| 2025-07-02T11:10 | BUY | +114.75 | +161.87 | +47.12 | +161.87 | floor stop |
| 2025-07-04T06:25 | BUY | +101.26 | +149.61 | +48.35 | +149.61 | floor stop |
| 2025-07-08T16:45 | SELL | +115.36 | +132.89 | +17.53 | +132.89 | floor stop |
| 2025-07-09T05:35 | SELL | +106.68 | +135.36 | +28.68 | +135.36 | floor stop |
| 2025-07-10T07:45 | BUY | +93.14 | +129.32 | +36.18 | +129.32 | floor stop |
| 2025-07-11T08:15 | BUY | +106.83 | +120.88 | +14.05 | +120.88 | floor stop |
| 2025-07-11T12:50 | BUY | +104.98 | +121.56 | +16.58 | +121.56 | floor stop |
| 2025-07-14T09:10 | BUY | +125.82 | +173.29 | +47.47 | +123.71 | profit lock |
| 2025-07-15T18:20 | SELL | +150.77 | +122.14 | -28.63 | +122.14 | floor stop |
| 2025-07-16T06:30 | BUY | +94.00 | +121.45 | +27.45 | +121.45 | floor stop |
| 2025-07-16T18:00 | BUY | +108.80 | +121.56 | +12.76 | +121.56 | floor stop |
| 2025-07-17T08:30 | SELL | +149.55 | +121.58 | -27.97 | +121.58 | floor stop |
| 2025-07-17T17:05 | BUY | +89.41 | +119.80 | +30.39 | +119.80 | floor stop |
| 2025-07-18T09:25 | BUY | +94.15 | +536.09 | +441.94 | +117.05 | reversal |
| 2025-07-21T07:30 | BUY | +215.86 | +374.80 | +158.94 | +126.47 | profit lock |
| 2025-07-23T11:40 | BUY | +99.20 | +121.73 | +22.53 | +121.73 | floor stop |
| 2025-07-25T20:20 | BUY | +101.00 | +119.63 | +18.63 | +119.63 | floor stop |
| 2025-07-31T16:45 | SELL | +96.12 | +105.00 | +8.88 | +105.00 | floor stop |
| 2025-08-01T09:30 | SELL | +93.35 | +103.68 | +10.33 | +103.68 | floor stop |
| 2025-08-01T11:50 | BUY | +77.61 | +105.76 | +28.15 | +105.76 | floor stop |
| 2025-08-01T20:20 | BUY | +90.35 | +236.17 | +145.82 | +107.87 | flatten |
| 2025-08-04T05:20 | BUY | +88.81 | +112.60 | +23.79 | +112.60 | floor stop |
| 2025-08-05T11:45 | SELL | +83.84 | +105.24 | +21.40 | +105.24 | floor stop |
| 2025-08-05T15:25 | BUY | +79.43 | +334.11 | +254.68 | +107.34 | reversal |
| 2025-08-07T09:10 | BUY | +133.12 | +112.14 | -20.98 | +112.14 | floor stop |
| 2025-08-13T09:15 | BUY | +85.76 | +95.88 | +10.12 | +95.88 | floor stop |
| 2025-08-19T14:55 | SELL | +59.21 | +83.17 | +23.96 | +83.17 | floor stop |
| 2025-08-19T17:40 | SELL | +59.75 | +84.02 | +24.27 | +84.02 | floor stop |
| 2025-08-20T06:35 | BUY | +85.59 | +85.70 | +0.11 | +85.70 | floor stop |
| 2025-08-22T16:30 | BUY | +155.58 | +587.82 | +432.24 | +80.77 | flatten |
| 2025-08-25T20:30 | SELL | +56.28 | +107.15 | +50.87 | +86.52 | flatten |
| 2025-08-26T17:45 | BUY | +59.24 | +81.68 | +22.44 | +81.68 | floor stop |
| 2025-08-27T12:45 | SELL | +64.12 | +81.84 | +17.72 | +81.84 | floor stop |
| 2025-08-27T17:25 | BUY | +54.48 | +81.43 | +26.95 | +81.43 | floor stop |
| 2025-08-28T14:00 | BUY | +94.04 | +80.15 | -13.89 | +80.15 | floor stop |
| 2025-09-01T04:45 | BUY | +51.66 | +96.69 | +45.03 | +76.16 | profit lock |
| 2025-09-02T17:00 | BUY | +71.66 | +234.48 | +162.82 | +72.31 | flatten |
| 2025-09-03T13:50 | BUY | +77.08 | +72.77 | -4.31 | +72.77 | floor stop |
| 2025-09-08T08:45 | BUY | +50.01 | +69.14 | +19.13 | +69.14 | floor stop |
| 2025-09-09T16:20 | BUY | +54.61 | +67.74 | +13.13 | +67.74 | floor stop |
| 2025-09-10T05:05 | BUY | +49.26 | +68.20 | +18.94 | +68.20 | floor stop |
| 2025-09-12T04:00 | BUY | +48.97 | +65.12 | +16.15 | +65.12 | floor stop |
| 2025-09-12T09:20 | SELL | +65.80 | +66.42 | +0.62 | +66.42 | floor stop |
| 2025-09-12T14:55 | SELL | +92.37 | +67.28 | -25.09 | +67.28 | floor stop |
| 2025-09-17T08:35 | SELL | +69.65 | +62.96 | -6.69 | +62.96 | floor stop |
| 2025-09-18T06:45 | SELL | +56.63 | +61.15 | +4.52 | +61.15 | floor stop |
| 2025-09-18T10:30 | BUY | +46.63 | +62.38 | +15.75 | +62.38 | floor stop |
| 2025-09-18T15:55 | SELL | +49.39 | +63.62 | +14.23 | +63.62 | floor stop |
| 2025-09-19T05:55 | BUY | +48.28 | +87.33 | +39.05 | +63.25 | profit lock |
| 2025-09-22T08:15 | BUY | +48.50 | +308.10 | +259.60 | +64.38 | reversal |
| 2025-09-23T06:45 | BUY | +50.45 | +70.01 | +19.56 | +70.01 | floor stop |
| 2025-09-23T17:05 | SELL | +56.92 | +71.41 | +14.49 | +71.41 | floor stop |
| 2025-09-23T21:10 | SELL | +104.58 | +95.70 | -8.88 | +72.14 | profit lock |
| 2025-09-24T17:05 | SELL | +54.41 | +73.79 | +19.38 | +73.79 | floor stop |
| 2025-09-29T13:25 | BUY | +52.50 | +65.79 | +13.29 | +65.79 | floor stop |
| 2025-09-30T09:55 | SELL | +46.82 | +239.00 | +192.18 | +66.17 | reversal |
| 2025-09-30T15:45 | BUY | +48.27 | +70.95 | +22.68 | +70.95 | floor stop |
| 2025-10-01T04:40 | BUY | +48.13 | +72.37 | +24.24 | +72.37 | floor stop |
| 2025-10-01T10:15 | BUY | +53.84 | +71.71 | +17.87 | +71.71 | floor stop |
| 2025-10-02T16:50 | SELL | +51.86 | +282.13 | +230.27 | +71.73 | profit lock |
| 2025-10-03T15:40 | BUY | +63.03 | +74.99 | +11.96 | +74.99 | floor stop |
| 2025-10-06T04:45 | BUY | +63.25 | +74.34 | +11.09 | +74.34 | floor stop |
| 2025-10-06T10:30 | BUY | +74.70 | +75.21 | +0.51 | +75.21 | floor stop |
| 2025-10-07T08:20 | SELL | +67.95 | +74.53 | +6.58 | +74.53 | floor stop |
| 2025-10-08T04:45 | BUY | +53.83 | +454.41 | +400.58 | +75.76 | reversal |
| 2025-10-09T06:05 | BUY | +59.70 | +83.54 | +23.84 | +83.54 | floor stop |
| 2025-10-10T06:10 | SELL | +67.10 | +84.57 | +17.47 | +84.57 | floor stop |
| 2025-10-10T11:00 | BUY | +54.48 | +84.63 | +30.15 | +84.63 | floor stop |
| 2025-10-21T04:20 | SELL | +60.44 | +75.98 | +15.54 | +75.98 | floor stop |
| 2025-10-22T10:55 | SELL | +88.70 | +77.11 | -11.59 | +77.11 | floor stop |
| 2025-10-22T19:30 | BUY | +61.40 | +77.30 | +15.90 | +77.30 | floor stop |
| 2025-10-24T13:20 | SELL | +93.18 | +74.76 | -18.42 | +74.76 | floor stop |
| 2025-10-27T10:10 | SELL | +69.08 | +77.39 | +8.31 | +77.39 | floor stop |
| 2025-10-27T16:40 | SELL | +52.67 | +78.33 | +25.66 | +78.33 | floor stop |
| 2025-10-29T09:05 | BUY | +61.60 | +78.34 | +16.74 | +78.34 | floor stop |
| 2025-10-30T07:05 | BUY | +57.33 | +80.68 | +23.35 | +80.68 | floor stop |
| 2025-10-30T11:25 | BUY | +79.53 | +81.48 | +1.95 | +81.48 | floor stop |
| 2025-10-31T15:00 | BUY | +67.25 | +81.96 | +14.71 | +81.96 | floor stop |
| 2025-11-03T13:25 | BUY | +58.64 | +79.12 | +20.48 | +79.12 | floor stop |
| 2025-11-04T19:15 | SELL | +82.04 | +239.71 | +157.67 | +77.92 | flatten |
| 2025-11-07T18:10 | BUY | +99.96 | +77.02 | -22.94 | +77.02 | floor stop |
| 2025-11-11T16:15 | SELL | +60.84 | +75.70 | +14.86 | +75.70 | floor stop |
| 2025-11-12T16:50 | BUY | +60.55 | +227.76 | +167.21 | +74.42 | flatten |
| 2025-11-13T09:25 | BUY | +77.64 | +81.71 | +4.07 | +77.07 | profit lock |
| 2025-11-13T16:00 | SELL | +102.04 | +77.43 | -24.61 | +77.43 | floor stop |
| 2025-11-14T04:10 | BUY | +58.05 | +78.98 | +20.93 | +78.98 | floor stop |
| 2025-11-14T07:05 | SELL | +67.27 | +80.56 | +13.29 | +80.56 | floor stop |
| 2025-11-14T11:30 | SELL | +60.83 | +81.61 | +20.78 | +81.61 | floor stop |
| 2025-11-17T09:10 | BUY | +66.28 | +83.05 | +16.77 | +83.05 | floor stop |
| 2025-11-18T10:00 | BUY | +65.74 | +81.30 | +15.56 | +81.30 | floor stop |
| 2025-11-18T15:20 | BUY | +58.48 | +82.18 | +23.70 | +82.18 | floor stop |
| 2025-11-19T11:20 | BUY | +81.93 | +109.76 | +27.83 | +82.22 | profit lock |
| 2025-11-20T14:00 | BUY | +59.59 | +82.04 | +22.45 | +82.04 | floor stop |
| 2025-11-21T14:20 | BUY | +60.55 | +82.53 | +21.98 | +82.53 | floor stop |
| 2025-11-27T14:25 | SELL | +66.78 | +73.38 | +6.60 | +73.38 | floor stop |
| 2025-11-28T09:25 | SELL | +71.13 | +229.64 | +158.51 | +72.62 | profit lock |
| 2025-12-01T08:40 | BUY | +54.38 | +74.34 | +19.96 | +74.34 | floor stop |
| 2025-12-01T16:00 | SELL | +70.02 | +73.64 | +3.62 | +73.64 | floor stop |
| 2025-12-02T17:05 | SELL | +53.54 | +70.46 | +16.92 | +70.46 | floor stop |
| 2025-12-05T05:15 | BUY | +61.42 | +220.98 | +159.56 | +71.08 | reversal |
| 2025-12-09T06:40 | SELL | +59.91 | +68.45 | +8.54 | +68.45 | floor stop |
| 2025-12-10T08:30 | SELL | +47.95 | +70.05 | +22.10 | +70.05 | floor stop |
| 2025-12-11T04:25 | SELL | +46.97 | +70.06 | +23.09 | +70.06 | floor stop |
| 2025-12-11T16:05 | BUY | +59.52 | +68.51 | +8.99 | +68.51 | floor stop |
| 2025-12-12T07:25 | BUY | +54.18 | +69.15 | +14.97 | +69.15 | floor stop |
| 2025-12-16T04:35 | SELL | +83.52 | +138.24 | +54.72 | +68.52 | profit lock |
| 2025-12-17T05:55 | BUY | +53.90 | +68.52 | +14.62 | +68.52 | floor stop |
| 2025-12-17T08:50 | SELL | +69.39 | +69.89 | +0.50 | +69.89 | floor stop |
| 2025-12-22T14:55 | BUY | +59.85 | +62.75 | +2.90 | +62.75 | floor stop |
| 2025-12-23T08:55 | BUY | +47.72 | +62.41 | +14.69 | +62.41 | floor stop |
| 2025-12-23T13:15 | BUY | +75.84 | +63.04 | -12.80 | +63.04 | floor stop |
| 2025-12-23T16:00 | SELL | +48.64 | +64.30 | +15.66 | +64.30 | floor stop |
| 2025-12-29T08:20 | SELL | +72.98 | +84.46 | +11.48 | +58.65 | profit lock |
| 2025-12-30T11:05 | BUY | +49.56 | +59.88 | +10.32 | +59.88 | floor stop |
| 2025-12-30T21:15 | SELL | +62.36 | +71.04 | +8.68 | +61.02 | profit lock |
| 2025-12-31T10:35 | SELL | +58.69 | +62.76 | +4.07 | +62.76 | floor stop |
| 2026-01-07T06:15 | SELL | +63.29 | +58.86 | -4.43 | +58.86 | floor stop |
| 2026-01-07T18:05 | BUY | +48.66 | +58.88 | +10.22 | +58.88 | floor stop |
| 2026-01-08T13:20 | SELL | +48.99 | +57.88 | +8.89 | +57.88 | floor stop |
| 2026-01-08T21:55 | BUY | +63.64 | +226.60 | +162.96 | +58.19 | flatten |
| 2026-01-12T14:55 | BUY | +41.80 | +56.50 | +14.70 | +56.50 | floor stop |
| 2026-01-13T08:00 | SELL | +45.00 | +57.23 | +12.23 | +57.23 | floor stop |
| 2026-01-13T14:50 | BUY | +53.56 | +139.94 | +86.38 | +58.17 | profit lock |
| 2026-01-14T20:10 | BUY | +41.29 | +58.29 | +17.00 | +58.29 | floor stop |
| 2026-01-15T14:50 | SELL | +45.04 | +58.64 | +13.60 | +58.64 | floor stop |
| 2026-01-16T14:30 | SELL | +69.40 | +58.63 | -10.77 | +58.63 | floor stop |
| 2026-01-20T05:30 | BUY | +43.94 | +463.55 | +419.61 | +52.76 | reversal |
| 2026-01-22T05:55 | BUY | +48.47 | +60.00 | +11.53 | +60.00 | floor stop |
| 2026-01-23T07:15 | BUY | +37.56 | +61.13 | +23.57 | +61.13 | floor stop |
| 2026-01-23T08:25 | SELL | +76.28 | +112.87 | +36.59 | +62.36 | profit lock |
| 2026-01-29T09:25 | SELL | +40.87 | +56.85 | +15.98 | +56.85 | floor stop |
| 2026-02-02T07:30 | SELL | +55.97 | +136.81 | +80.84 | +50.68 | profit lock |
| 2026-02-03T18:10 | BUY | +33.87 | +49.35 | +15.48 | +49.35 | floor stop |
| 2026-02-05T14:00 | SELL | +33.70 | +48.36 | +14.66 | +48.36 | floor stop |
| 2026-02-05T18:35 | BUY | +63.15 | +49.32 | -13.83 | +49.32 | floor stop |
| 2026-02-13T11:55 | SELL | +31.62 | +40.88 | +9.26 | +40.88 | floor stop |
| 2026-02-13T14:10 | BUY | +51.06 | +41.70 | -9.36 | +41.70 | floor stop |
| 2026-02-16T04:25 | SELL | +34.15 | +60.25 | +26.10 | +42.53 | profit lock |
| 2026-02-18T14:35 | BUY | +29.83 | +178.29 | +148.46 | +42.33 | reversal |
| 2026-02-19T11:50 | SELL | +26.81 | +45.54 | +18.73 | +45.54 | floor stop |
| 2026-02-19T22:00 | BUY | +36.44 | +45.45 | +9.01 | +45.45 | floor stop |
| 2026-02-20T06:40 | BUY | +28.16 | +51.43 | +23.27 | +46.35 | profit lock |
| 2026-02-23T16:20 | BUY | +31.98 | +45.21 | +13.23 | +45.21 | floor stop |
| 2026-02-25T12:15 | SELL | +33.52 | +44.28 | +10.76 | +44.28 | floor stop |
| 2026-02-26T12:30 | SELL | +34.05 | +44.34 | +10.29 | +44.34 | floor stop |
| 2026-03-02T08:05 | BUY | +40.48 | +43.36 | +2.88 | +43.36 | floor stop |
| 2026-03-03T07:15 | SELL | +30.52 | +43.62 | +13.10 | +43.62 | floor stop |
| 2026-03-05T06:40 | SELL | +26.38 | +64.88 | +38.50 | +42.99 | profit lock |
| 2026-03-09T05:10 | BUY | +40.25 | +41.95 | +1.70 | +41.95 | floor stop |
| 2026-03-10T15:40 | BUY | +47.37 | +42.83 | -4.54 | +42.83 | floor stop |
| 2026-03-12T05:40 | SELL | +24.29 | +40.29 | +16.00 | +40.29 | floor stop |
| 2026-03-13T16:55 | SELL | +25.69 | +67.76 | +42.07 | +40.45 | profit lock |
| 2026-03-13T21:10 | SELL | +26.34 | +41.81 | +15.47 | +41.81 | floor stop |
| 2026-03-16T13:45 | BUY | +28.36 | +41.10 | +12.74 | +41.10 | floor stop |
| 2026-03-17T08:50 | SELL | +30.15 | +42.34 | +12.19 | +42.34 | floor stop |
| 2026-03-19T06:55 | SELL | +38.54 | +43.06 | +4.52 | +43.06 | floor stop |
| 2026-03-20T10:35 | BUY | +32.46 | +44.57 | +12.11 | +44.57 | floor stop |
| 2026-03-20T12:15 | SELL | +34.81 | +45.46 | +10.65 | +45.46 | floor stop |
| 2026-03-23T05:10 | SELL | +48.56 | +51.83 | +3.27 | +51.83 | floor stop |
| 2026-03-23T18:35 | SELL | +88.69 | +54.15 | -34.54 | +54.15 | floor stop |
| 2026-03-26T08:20 | SELL | +32.16 | +144.13 | +111.97 | +52.08 | profit lock |
| 2026-04-01T13:40 | BUY | +34.70 | +51.06 | +16.36 | +51.06 | floor stop |
| 2026-04-02T04:00 | SELL | +42.48 | +50.70 | +8.22 | +50.70 | floor stop |
| 2026-04-06T07:00 | BUY | +44.93 | +51.07 | +6.14 | +51.07 | floor stop |
| 2026-04-08T20:35 | SELL | +30.51 | +49.02 | +18.51 | +49.02 | floor stop |
| 2026-04-09T10:55 | BUY | +27.90 | +48.03 | +20.13 | +48.03 | floor stop |
| 2026-04-23T11:40 | SELL | +24.41 | +38.75 | +14.34 | +38.75 | floor stop |
| 2026-04-23T14:15 | BUY | +53.32 | +58.97 | +5.65 | +39.53 | profit lock |
| 2026-04-28T04:05 | SELL | +31.40 | +38.63 | +7.23 | +38.63 | floor stop |
| 2026-04-30T05:35 | SELL | +25.15 | +37.78 | +12.63 | +37.78 | floor stop |
| 2026-04-30T08:30 | BUY | +27.94 | +148.36 | +120.42 | +38.53 | reversal |
| 2026-05-07T09:00 | BUY | +65.37 | +65.90 | +0.53 | +41.50 | profit lock |
| 2026-05-11T11:50 | SELL | +32.12 | +39.33 | +7.21 | +39.33 | floor stop |
| 2026-05-11T14:00 | BUY | +38.20 | +40.12 | +1.92 | +40.12 | floor stop |
| 2026-05-12T20:05 | BUY | +33.84 | +89.40 | +55.56 | +40.16 | flatten |
| 2026-05-13T04:00 | SELL | +45.07 | +41.95 | -3.12 | +41.95 | floor stop |
| 2026-05-13T15:15 | SELL | +30.59 | +41.35 | +10.76 | +41.35 | floor stop |
| 2026-05-15T22:10 | SELL | +38.33 | +40.11 | +1.78 | +40.11 | floor stop |
| 2026-05-19T20:50 | SELL | +32.46 | +39.83 | +7.37 | +39.83 | floor stop |
| 2026-05-21T12:55 | SELL | +35.07 | +39.29 | +4.22 | +39.29 | floor stop |
| 2026-05-26T14:25 | SELL | +25.44 | +36.26 | +10.82 | +36.26 | floor stop |
| 2026-05-27T12:40 | SELL | +51.41 | +153.03 | +101.62 | +35.38 | profit lock |
| 2026-05-29T16:20 | BUY | +36.24 | +37.11 | +0.87 | +37.11 | floor stop |
| 2026-06-02T06:15 | BUY | +23.90 | +156.69 | +132.79 | +36.94 | reversal |
| 2026-06-03T07:30 | SELL | +24.05 | +39.29 | +15.24 | +39.29 | floor stop |
| 2026-06-04T12:45 | BUY | +30.45 | +38.81 | +8.36 | +38.81 | floor stop |
| 2026-06-05T15:45 | SELL | +23.08 | +38.96 | +15.88 | +38.96 | floor stop |
| 2026-06-08T08:05 | SELL | +40.68 | +39.25 | -1.43 | +39.25 | floor stop |
| 2026-06-10T08:30 | BUY | +38.29 | +38.48 | +0.19 | +38.48 | floor stop |
| 2026-06-10T17:40 | SELL | +37.92 | +39.99 | +2.07 | +39.99 | floor stop |
| 2026-06-15T10:20 | BUY | +62.09 | +49.79 | -12.30 | +39.28 | profit lock |
| 2026-06-15T19:15 | SELL | +26.46 | +40.28 | +13.82 | +40.28 | floor stop |
| 2026-06-16T06:25 | BUY | +25.33 | +40.79 | +15.46 | +40.79 | floor stop |
| 2026-06-16T10:25 | BUY | +26.77 | +40.87 | +14.10 | +40.87 | floor stop |
| 2026-06-16T16:20 | SELL | +38.06 | +40.95 | +2.89 | +40.95 | floor stop |
| 2026-06-18T21:35 | SELL | +29.42 | +39.22 | +9.80 | +39.22 | floor stop |
| 2026-06-24T20:25 | SELL | +24.81 | +36.72 | +11.91 | +36.72 | floor stop |
| 2026-06-26T07:10 | BUY | +26.53 | +36.07 | +9.54 | +36.07 | floor stop |
| 2026-06-29T11:50 | SELL | +35.10 | +36.14 | +1.04 | +36.14 | floor stop |
| 2026-06-29T16:45 | SELL | +30.62 | +36.55 | +5.93 | +36.55 | floor stop |
| 2026-07-03T19:40 | BUY | +25.40 | +35.56 | +10.16 | +35.56 | floor stop |
| 2026-07-06T05:20 | SELL | +22.72 | +35.95 | +13.23 | +35.95 | floor stop |
| 2026-07-06T11:30 | SELL | +26.02 | +36.48 | +10.46 | +36.48 | floor stop |
| 2026-07-07T13:15 | BUY | +25.96 | +74.48 | +48.52 | +36.53 | profit lock |
| 2026-07-07T21:30 | SELL | +31.06 | +37.72 | +6.66 | +37.72 | floor stop |
| 2026-07-09T05:50 | SELL | +28.32 | +36.63 | +8.31 | +36.63 | floor stop |
| 2026-07-09T08:10 | BUY | +32.32 | +75.56 | +43.24 | +37.36 | reversal |
| 2026-07-13T13:10 | SELL | +30.56 | +37.39 | +6.83 | +37.39 | floor stop |
| 2026-07-13T16:45 | SELL | +65.00 | +126.92 | +61.92 | +37.85 | flatten |
| 2026-07-16T15:35 | SELL | +25.41 | +39.95 | +14.54 | +39.95 | floor stop |
| 2026-07-17T08:50 | BUY | +26.48 | +40.47 | +13.99 | +40.47 | floor stop |
| 2026-07-17T15:20 | SELL | +30.90 | +40.65 | +9.75 | +40.65 | floor stop |
| 2026-07-22T16:20 | BUY | +33.18 | +39.33 | +6.15 | +39.33 | floor stop |
| 2026-07-24T09:55 | BUY | +29.46 | +84.15 | +54.69 | +39.69 | reversal |
| 2026-07-28T11:50 | SELL | +29.12 | +39.75 | +10.63 | +39.75 | floor stop |
| 2026-07-29T07:55 | BUY | +51.68 | +41.48 | -10.20 | +40.12 | profit lock |
| 2026-07-29T19:05 | BUY | +32.95 | +40.49 | +7.54 | +40.49 | floor stop |
| 2026-07-31T10:20 | SELL | +29.32 | +39.85 | +10.53 | +39.85 | floor stop |
| 2026-08-04T14:10 | BUY | +33.82 | +38.48 | +4.66 | +38.48 | floor stop |
| 2026-08-05T08:30 | BUY | +41.26 | +53.65 | +12.39 | +39.41 | profit lock |
| 2026-08-05T14:05 | BUY | +42.40 | +105.25 | +62.85 | +40.00 | profit lock |
| 2026-08-07T06:55 | BUY | +35.83 | +42.05 | +6.22 | +42.05 | floor stop |
| 2026-08-10T11:40 | SELL | +33.13 | +42.03 | +8.90 | +42.03 | floor stop |
| 2026-08-11T07:40 | SELL | +51.03 | +70.89 | +19.86 | +43.11 | reversal |

**aggregate**: mean delta +38.18, better 227, worse 66, min -87.27, max +1099.68


### floor-a-adds — armed trades vs baseline profit-target exit (same entry bar, 331 pairs)

| entry (server) | dir | baseline P/L | floor-a-adds P/L | delta $ | floor $ | exit reason |
|---|---|---:|---:|---:|---:|---|
| 2025-03-17T11:55 | BUY | +94.11 | +79.25 | -14.86 | +83.34 | floor stop |
| 2025-03-17T17:15 | BUY | +104.85 | +76.75 | -28.10 | +76.75 | floor stop |
| 2025-03-18T14:20 | BUY | +119.61 | +65.67 | -53.94 | +77.58 | floor stop |
| 2025-03-19T05:30 | BUY | +122.42 | +81.21 | -41.21 | +81.21 | floor stop |
| 2025-03-19T10:45 | SELL | +95.82 | +85.32 | -10.50 | +85.32 | floor stop |
| 2025-03-20T06:30 | BUY | +121.20 | +83.14 | -38.06 | +83.14 | floor stop |
| 2025-03-20T10:15 | SELL | +101.57 | +73.08 | -28.49 | +79.90 | floor stop |
| 2025-03-20T13:00 | SELL | +131.81 | +71.53 | -60.28 | +82.09 | floor stop |
| 2025-03-21T16:30 | SELL | +99.31 | +101.01 | +1.70 | +81.24 | profit lock |
| 2025-03-24T19:35 | SELL | +113.85 | +65.38 | -48.47 | +77.07 | floor stop |
| 2025-03-25T06:20 | BUY | +141.73 | +76.22 | -65.51 | +76.22 | floor stop |
| 2025-03-25T14:55 | SELL | +103.06 | +70.45 | -32.61 | +77.31 | floor stop |
| 2025-03-26T08:40 | BUY | +103.18 | +183.52 | +80.34 | +78.45 | profit lock |
| 2025-03-27T17:10 | BUY | +108.10 | +83.65 | -24.45 | +83.65 | floor stop |
| 2025-03-31T04:30 | BUY | +109.67 | +77.73 | -31.94 | +81.47 | floor stop |
| 2025-03-31T16:25 | SELL | +117.80 | +68.99 | -48.81 | +77.19 | floor stop |
| 2025-04-02T06:50 | SELL | +101.70 | +79.91 | -21.79 | +79.91 | floor stop |
| 2025-04-02T13:30 | SELL | +134.08 | +74.19 | -59.89 | +80.86 | floor stop |
| 2025-04-04T13:00 | BUY | +116.28 | +78.48 | -37.80 | +78.48 | floor stop |
| 2025-04-08T08:10 | BUY | +95.43 | +74.26 | -21.17 | +74.26 | floor stop |
| 2025-04-09T16:20 | BUY | +125.84 | +70.85 | -54.99 | +74.80 | floor stop |
| 2025-04-15T12:40 | SELL | +91.28 | +60.58 | -30.70 | +65.29 | floor stop |
| 2025-04-17T08:25 | SELL | +105.97 | +60.13 | -45.84 | +62.62 | profit lock |
| 2025-04-21T08:25 | BUY | +95.85 | +64.09 | -31.76 | +64.09 | floor stop |
| 2025-04-21T20:35 | BUY | +95.82 | +66.06 | -29.76 | +66.06 | floor stop |
| 2025-04-22T04:15 | BUY | +104.90 | +65.76 | -39.14 | +65.76 | floor stop |
| 2025-04-22T10:00 | SELL | +97.62 | +70.58 | -27.04 | +70.58 | floor stop |
| 2025-04-22T15:40 | SELL | +94.60 | +60.70 | -33.90 | +66.64 | floor stop |
| 2025-04-22T21:20 | SELL | +103.10 | +67.20 | -35.90 | +69.09 | floor stop |
| 2025-04-23T05:35 | SELL | +122.19 | +71.61 | -50.58 | +71.61 | floor stop |
| 2025-04-23T08:40 | SELL | +119.56 | +59.71 | -59.85 | +67.53 | floor stop |
| 2025-04-25T06:45 | SELL | +104.46 | +65.49 | -38.97 | +65.49 | floor stop |
| 2025-04-28T04:25 | SELL | +102.33 | +63.41 | -38.92 | +65.72 | floor stop |
| 2025-04-29T19:20 | BUY | +103.20 | +59.27 | -43.93 | +62.80 | floor stop |
| 2025-04-30T11:05 | SELL | +126.35 | +51.91 | -74.44 | +66.66 | floor stop |
| 2025-04-30T21:20 | SELL | +98.64 | +63.03 | -35.61 | +65.04 | floor stop |
| 2025-05-01T12:20 | SELL | +108.28 | +88.61 | -19.67 | +61.37 | profit lock |
| 2025-05-02T05:05 | BUY | +118.50 | +64.50 | -54.00 | +64.50 | floor stop |
| 2025-05-05T04:05 | BUY | +91.04 | +58.17 | -32.87 | +61.53 | floor stop |
| 2025-05-06T09:35 | BUY | +132.65 | +62.04 | -70.61 | +60.05 | profit lock |
| 2025-05-06T18:20 | BUY | +94.71 | +60.23 | -34.48 | +60.23 | floor stop |
| 2025-05-07T10:45 | SELL | +91.52 | +57.31 | -34.21 | +61.00 | floor stop |
| 2025-05-08T07:10 | SELL | +119.34 | +133.98 | +14.64 | +64.57 | reversal |
| 2025-05-09T04:25 | SELL | +129.36 | +76.69 | -52.67 | +61.08 | profit lock |
| 2025-05-12T08:10 | SELL | +113.30 | +55.03 | -58.27 | +55.03 | floor stop |
| 2025-05-12T10:05 | SELL | +106.54 | +62.02 | -44.52 | +62.02 | floor stop |
| 2025-05-13T06:10 | BUY | +107.16 | +118.24 | +11.08 | +65.49 | reversal |
| 2025-05-15T05:20 | SELL | +96.08 | +66.69 | -29.39 | +66.69 | floor stop |
| 2025-05-15T11:20 | BUY | +104.13 | +59.21 | -44.92 | +62.75 | floor stop |
| 2025-05-15T21:30 | BUY | +104.35 | +62.69 | -41.66 | +62.69 | floor stop |
| 2025-05-16T13:35 | SELL | +169.07 | +279.16 | +110.09 | +59.94 | profit lock |
| 2025-05-20T10:25 | BUY | +108.66 | +62.17 | -46.49 | +62.17 | floor stop |
| 2025-05-21T14:30 | SELL | +116.94 | +58.94 | -58.00 | +64.36 | floor stop |
| 2025-05-23T06:40 | BUY | +177.58 | +753.58 | +576.00 | +60.32 | reversal |
| 2025-05-27T06:55 | SELL | +168.12 | +1280.53 | +1112.41 | +73.42 | reversal |
| 2025-05-29T13:05 | BUY | +121.22 | +644.94 | +523.72 | +94.91 | reversal |
| 2025-06-03T16:40 | SELL | +205.98 | +90.18 | -115.80 | +90.18 | floor stop |
| 2025-06-04T06:30 | SELL | +112.95 | +105.43 | -7.52 | +105.43 | floor stop |
| 2025-06-04T13:25 | SELL | +188.60 | +50.45 | -138.15 | +89.45 | floor stop |
| 2025-06-05T10:05 | BUY | +127.48 | +244.03 | +116.55 | +98.12 | profit lock |
| 2025-06-06T08:40 | SELL | +103.26 | +100.84 | -2.42 | +100.84 | floor stop |
| 2025-06-10T08:05 | BUY | +104.76 | +80.24 | -24.52 | +98.71 | floor stop |
| 2025-06-11T12:20 | SELL | +98.11 | +84.33 | -13.78 | +92.55 | floor stop |
| 2025-06-12T10:25 | SELL | +113.70 | +268.62 | +154.92 | +93.36 | profit lock |
| 2025-06-12T12:45 | BUY | +112.47 | +104.13 | -8.34 | +104.13 | floor stop |
| 2025-06-13T14:30 | BUY | +102.87 | +91.08 | -11.79 | +96.47 | floor stop |
| 2025-06-16T06:15 | SELL | +103.05 | +93.31 | -9.74 | +93.31 | floor stop |
| 2025-06-16T15:20 | SELL | +237.75 | +115.76 | -121.99 | +93.40 | profit lock |
| 2025-06-17T05:50 | SELL | +127.17 | +101.21 | -25.96 | +101.21 | floor stop |
| 2025-06-17T13:00 | BUY | +103.34 | +98.22 | -5.12 | +98.22 | floor stop |
| 2025-06-17T17:25 | SELL | +115.61 | +93.82 | -21.79 | +93.82 | floor stop |
| 2025-06-18T06:05 | BUY | +117.64 | +105.97 | -11.67 | +105.97 | floor stop |
| 2025-06-20T04:10 | SELL | +106.89 | +99.15 | -7.74 | +99.15 | floor stop |
| 2025-06-20T08:15 | SELL | +140.76 | +71.43 | -69.33 | +91.05 | floor stop |
| 2025-06-20T14:15 | SELL | +118.63 | +91.44 | -27.19 | +91.44 | floor stop |
| 2025-06-20T16:20 | BUY | +130.55 | +125.11 | -5.44 | +101.43 | reversal |
| 2025-06-23T07:35 | SELL | +185.06 | +136.96 | -48.10 | +95.68 | profit lock |
| 2025-06-24T08:00 | SELL | +210.95 | +1052.43 | +841.48 | +93.09 | reversal |
| 2025-06-26T09:50 | BUY | +108.18 | +111.86 | +3.68 | +111.86 | floor stop |
| 2025-06-26T19:10 | BUY | +127.18 | +116.48 | -10.70 | +116.48 | floor stop |
| 2025-06-27T05:35 | SELL | +133.80 | +1335.44 | +1201.64 | +109.82 | reversal |
| 2025-07-02T09:00 | SELL | +103.37 | +126.80 | +23.43 | +126.80 | floor stop |
| 2025-07-02T11:10 | BUY | +114.75 | +117.87 | +3.12 | +130.07 | floor stop |
| 2025-07-03T07:15 | BUY | +108.46 | +114.87 | +6.41 | +126.43 | floor stop |
| 2025-07-03T12:25 | SELL | +108.25 | +116.38 | +8.13 | +124.81 | floor stop |
| 2025-07-04T06:25 | BUY | +101.26 | +121.04 | +19.78 | +121.04 | floor stop |
| 2025-07-04T19:30 | BUY | +102.16 | +114.56 | +12.40 | +114.56 | floor stop |
| 2025-07-08T16:45 | SELL | +115.36 | +115.75 | +0.39 | +115.75 | floor stop |
| 2025-07-09T05:35 | SELL | +106.68 | +110.51 | +3.83 | +110.51 | floor stop |
| 2025-07-10T07:45 | BUY | +93.14 | +98.83 | +5.69 | +106.92 | floor stop |
| 2025-07-11T08:15 | BUY | +106.83 | +97.78 | -9.05 | +97.78 | floor stop |
| 2025-07-11T12:50 | BUY | +104.98 | +179.08 | +74.10 | +97.36 | profit lock |
| 2025-07-14T09:10 | BUY | +125.82 | +159.01 | +33.19 | +102.95 | profit lock |
| 2025-07-15T18:20 | SELL | +150.77 | +63.70 | -87.07 | +97.78 | floor stop |
| 2025-07-16T06:30 | BUY | +94.00 | +95.42 | +1.42 | +95.42 | floor stop |
| 2025-07-16T18:00 | BUY | +108.80 | +100.25 | -8.55 | +105.11 | floor stop |
| 2025-07-17T08:30 | SELL | +149.55 | +107.89 | -41.66 | +98.83 | profit lock |
| 2025-07-17T17:05 | BUY | +89.41 | +97.33 | +7.92 | +97.33 | floor stop |
| 2025-07-18T09:25 | BUY | +94.15 | +509.86 | +415.71 | +95.90 | reversal |
| 2025-07-21T07:30 | BUY | +215.86 | +349.70 | +133.84 | +98.31 | profit lock |
| 2025-07-23T11:40 | BUY | +99.20 | +86.06 | -13.14 | +98.55 | floor stop |
| 2025-07-25T20:20 | BUY | +101.00 | +103.72 | +2.72 | +103.72 | floor stop |
| 2025-07-31T16:45 | SELL | +96.12 | +77.57 | -18.55 | +87.10 | floor stop |
| 2025-08-01T09:30 | SELL | +93.35 | +81.19 | -12.16 | +81.19 | floor stop |
| 2025-08-01T11:50 | BUY | +77.61 | +88.46 | +10.85 | +88.46 | floor stop |
| 2025-08-01T20:20 | BUY | +90.35 | +260.09 | +169.74 | +92.64 | flatten |
| 2025-08-04T05:20 | BUY | +88.81 | +96.48 | +7.67 | +96.48 | floor stop |
| 2025-08-05T11:45 | SELL | +83.84 | +81.15 | -2.69 | +88.24 | floor stop |
| 2025-08-05T15:25 | BUY | +79.43 | +300.97 | +221.54 | +91.00 | reversal |
| 2025-08-07T09:10 | BUY | +133.12 | +60.00 | -73.12 | +89.72 | floor stop |
| 2025-08-13T09:15 | BUY | +85.76 | +64.45 | -21.31 | +76.04 | floor stop |
| 2025-08-19T14:55 | SELL | +59.21 | +68.24 | +9.03 | +68.24 | floor stop |
| 2025-08-19T17:40 | SELL | +59.75 | +71.38 | +11.63 | +71.38 | floor stop |
| 2025-08-20T06:35 | BUY | +85.59 | +69.91 | -15.68 | +69.91 | floor stop |
| 2025-08-22T16:30 | BUY | +155.58 | +681.54 | +525.96 | +60.36 | flatten |
| 2025-08-25T20:30 | SELL | +56.28 | +107.15 | +50.87 | +77.68 | flatten |
| 2025-08-26T17:45 | BUY | +59.24 | +69.66 | +10.42 | +69.66 | floor stop |
| 2025-08-27T10:50 | BUY | +54.40 | +61.68 | +7.28 | +66.92 | floor stop |
| 2025-08-27T12:45 | SELL | +64.12 | +66.55 | +2.43 | +66.55 | floor stop |
| 2025-08-27T17:25 | BUY | +54.48 | +60.94 | +6.46 | +66.67 | floor stop |
| 2025-08-28T14:00 | BUY | +94.04 | +47.27 | -46.77 | +65.41 | floor stop |
| 2025-09-01T04:45 | BUY | +51.66 | +57.46 | +5.80 | +66.19 | floor stop |
| 2025-09-02T14:50 | BUY | +48.64 | +54.15 | +5.51 | +58.74 | floor stop |
| 2025-09-02T17:00 | BUY | +71.66 | +258.59 | +186.93 | +61.25 | flatten |
| 2025-09-03T13:50 | BUY | +77.08 | +54.62 | -22.46 | +59.71 | floor stop |
| 2025-09-08T08:45 | BUY | +50.01 | +52.03 | +2.02 | +57.09 | floor stop |
| 2025-09-09T16:20 | BUY | +54.61 | +53.31 | -1.30 | +53.31 | floor stop |
| 2025-09-10T05:05 | BUY | +49.26 | +58.21 | +8.95 | +58.21 | floor stop |
| 2025-09-11T04:55 | SELL | +47.46 | +55.65 | +8.19 | +55.65 | floor stop |
| 2025-09-12T04:00 | BUY | +48.97 | +55.32 | +6.35 | +55.32 | floor stop |
| 2025-09-12T09:20 | SELL | +65.80 | +46.27 | -19.53 | +55.79 | floor stop |
| 2025-09-12T14:55 | SELL | +92.37 | +27.84 | -64.53 | +51.68 | floor stop |
| 2025-09-17T08:35 | SELL | +69.65 | +40.35 | -29.30 | +51.17 | floor stop |
| 2025-09-18T06:45 | SELL | +56.63 | +50.42 | -6.21 | +50.42 | floor stop |
| 2025-09-18T10:30 | BUY | +46.63 | +57.06 | +10.43 | +57.06 | floor stop |
| 2025-09-18T15:55 | SELL | +49.39 | +56.80 | +7.41 | +56.80 | floor stop |
| 2025-09-19T05:55 | BUY | +48.28 | +45.23 | -3.05 | +49.89 | floor stop |
| 2025-09-22T08:15 | BUY | +48.50 | +313.98 | +265.48 | +55.56 | reversal |
| 2025-09-23T06:45 | BUY | +50.45 | +60.90 | +10.45 | +60.90 | floor stop |
| 2025-09-23T17:05 | SELL | +56.92 | +45.15 | -11.77 | +54.41 | floor stop |
| 2025-09-23T21:10 | SELL | +104.58 | +78.42 | -26.16 | +58.22 | profit lock |
| 2025-09-24T17:05 | SELL | +54.41 | +64.29 | +9.88 | +64.29 | floor stop |
| 2025-09-29T13:25 | BUY | +52.50 | +46.71 | -5.79 | +54.09 | floor stop |
| 2025-09-30T09:55 | SELL | +46.82 | +57.30 | +10.48 | +57.30 | floor stop |
| 2025-09-30T15:45 | BUY | +48.27 | +61.37 | +13.10 | +61.37 | floor stop |
| 2025-10-01T04:40 | BUY | +48.13 | +49.86 | +1.73 | +55.36 | floor stop |
| 2025-10-01T10:15 | BUY | +53.84 | +52.16 | -1.68 | +56.06 | floor stop |
| 2025-10-02T08:15 | BUY | +54.00 | +50.66 | -3.34 | +55.73 | floor stop |
| 2025-10-02T16:50 | SELL | +51.86 | +298.88 | +247.02 | +53.91 | profit lock |
| 2025-10-03T15:40 | BUY | +63.03 | +61.12 | -1.91 | +61.12 | floor stop |
| 2025-10-06T04:45 | BUY | +63.25 | +59.28 | -3.97 | +59.28 | floor stop |
| 2025-10-06T10:30 | BUY | +74.70 | +50.12 | -24.58 | +58.68 | floor stop |
| 2025-10-06T17:10 | BUY | +54.25 | +55.67 | +1.42 | +55.67 | floor stop |
| 2025-10-07T08:20 | SELL | +67.95 | +60.80 | -7.15 | +60.80 | floor stop |
| 2025-10-08T04:45 | BUY | +53.83 | +60.57 | +6.74 | +60.57 | floor stop |
| 2025-10-09T06:05 | BUY | +59.70 | +63.60 | +3.90 | +63.60 | floor stop |
| 2025-10-10T06:10 | SELL | +67.10 | +58.63 | -8.47 | +62.31 | floor stop |
| 2025-10-10T11:00 | BUY | +54.48 | +64.79 | +10.31 | +64.79 | floor stop |
| 2025-10-16T08:10 | SELL | +49.77 | +57.30 | +7.53 | +57.30 | floor stop |
| 2025-10-17T04:15 | SELL | +77.64 | +47.88 | -29.76 | +57.93 | floor stop |
| 2025-10-17T05:30 | BUY | +67.93 | +59.82 | -8.11 | +59.82 | floor stop |
| 2025-10-21T04:20 | SELL | +60.44 | +61.83 | +1.39 | +61.83 | floor stop |
| 2025-10-22T10:55 | SELL | +88.70 | +56.13 | -32.57 | +61.18 | floor stop |
| 2025-10-22T19:30 | BUY | +61.40 | +104.27 | +42.87 | +61.97 | flatten |
| 2025-10-24T13:20 | SELL | +93.18 | +36.47 | -56.71 | +53.84 | floor stop |
| 2025-10-27T10:10 | SELL | +69.08 | +226.84 | +157.76 | +57.99 | profit lock |
| 2025-10-29T09:05 | BUY | +61.60 | +60.41 | -1.19 | +60.41 | floor stop |
| 2025-10-30T07:05 | BUY | +57.33 | +64.82 | +7.49 | +64.82 | floor stop |
| 2025-10-30T11:25 | BUY | +79.53 | +58.45 | -21.08 | +64.42 | profit lock |
| 2025-10-31T15:00 | BUY | +67.25 | +47.83 | -19.42 | +65.18 | floor stop |
| 2025-11-03T13:25 | BUY | +58.64 | +58.40 | -0.24 | +62.31 | floor stop |
| 2025-11-04T19:15 | SELL | +82.04 | +173.47 | +91.43 | +60.34 | flatten |
| 2025-11-07T18:10 | BUY | +99.96 | +110.34 | +10.38 | +54.78 | profit lock |
| 2025-11-11T16:15 | SELL | +60.84 | +53.93 | -6.91 | +59.41 | floor stop |
| 2025-11-12T16:50 | BUY | +60.55 | +314.01 | +253.46 | +61.10 | flatten |
| 2025-11-13T09:25 | BUY | +77.64 | +76.57 | -1.07 | +60.10 | profit lock |
| 2025-11-13T16:00 | SELL | +102.04 | +77.18 | -24.86 | +61.50 | profit lock |
| 2025-11-14T04:10 | BUY | +58.05 | +61.38 | +3.33 | +61.38 | floor stop |
| 2025-11-14T07:05 | SELL | +67.27 | +69.80 | +2.53 | +69.80 | floor stop |
| 2025-11-14T11:30 | SELL | +60.83 | +65.70 | +4.87 | +65.70 | floor stop |
| 2025-11-17T09:10 | BUY | +66.28 | +70.40 | +4.12 | +70.40 | floor stop |
| 2025-11-18T10:00 | BUY | +65.74 | +66.30 | +0.56 | +66.30 | floor stop |
| 2025-11-18T15:20 | BUY | +58.48 | +63.03 | +4.55 | +68.00 | floor stop |
| 2025-11-19T11:20 | BUY | +81.93 | +108.64 | +26.71 | +63.33 | profit lock |
| 2025-11-20T14:00 | BUY | +59.59 | +64.56 | +4.97 | +64.56 | floor stop |
| 2025-11-21T14:20 | BUY | +60.55 | +64.13 | +3.58 | +66.02 | floor stop |
| 2025-11-27T14:25 | SELL | +66.78 | +49.63 | -17.15 | +57.21 | floor stop |
| 2025-11-28T09:25 | SELL | +71.13 | +244.03 | +172.90 | +57.85 | profit lock |
| 2025-12-01T16:00 | SELL | +70.02 | +57.95 | -12.07 | +57.95 | floor stop |
| 2025-12-02T17:05 | SELL | +53.54 | +58.07 | +4.53 | +58.07 | floor stop |
| 2025-12-05T05:15 | BUY | +61.42 | +254.54 | +193.12 | +53.95 | reversal |
| 2025-12-09T06:40 | SELL | +59.91 | +54.51 | -5.40 | +56.26 | floor stop |
| 2025-12-09T10:00 | BUY | +50.39 | +60.54 | +10.15 | +60.54 | floor stop |
| 2025-12-10T08:30 | SELL | +47.95 | +56.57 | +8.62 | +56.57 | floor stop |
| 2025-12-11T04:25 | SELL | +46.97 | +54.38 | +7.41 | +56.73 | floor stop |
| 2025-12-11T16:05 | BUY | +59.52 | +52.85 | -6.67 | +52.85 | floor stop |
| 2025-12-12T07:25 | BUY | +54.18 | +426.19 | +372.01 | +52.72 | profit lock |
| 2025-12-16T04:35 | SELL | +83.52 | +140.11 | +56.59 | +57.89 | profit lock |
| 2025-12-17T05:55 | BUY | +53.90 | +54.08 | +0.18 | +54.08 | floor stop |
| 2025-12-17T08:50 | SELL | +69.39 | +59.06 | -10.33 | +59.06 | floor stop |
| 2025-12-22T14:55 | BUY | +59.85 | +39.98 | -19.87 | +51.07 | floor stop |
| 2025-12-23T08:55 | BUY | +47.72 | +53.02 | +5.30 | +53.02 | floor stop |
| 2025-12-23T13:15 | BUY | +75.84 | +61.35 | -14.49 | +48.61 | profit lock |
| 2025-12-23T16:00 | SELL | +48.64 | +54.05 | +5.41 | +54.05 | floor stop |
| 2025-12-29T08:20 | SELL | +72.98 | +84.46 | +11.48 | +49.84 | profit lock |
| 2025-12-30T11:05 | BUY | +49.56 | +51.49 | +1.93 | +51.49 | floor stop |
| 2025-12-30T21:15 | SELL | +62.36 | +67.54 | +5.18 | +52.96 | profit lock |
| 2025-12-31T10:35 | SELL | +58.69 | +49.51 | -9.18 | +53.10 | floor stop |
| 2026-01-07T06:15 | SELL | +63.29 | +57.02 | -6.27 | +51.15 | profit lock |
| 2026-01-07T18:05 | BUY | +48.66 | +49.17 | +0.51 | +49.17 | floor stop |
| 2026-01-08T13:20 | SELL | +48.99 | +48.34 | -0.65 | +50.52 | floor stop |
| 2026-01-08T21:55 | BUY | +63.64 | +251.10 | +187.46 | +48.31 | flatten |
| 2026-01-12T14:55 | BUY | +41.80 | +42.12 | +0.32 | +47.62 | floor stop |
| 2026-01-13T08:00 | SELL | +45.00 | +40.04 | -4.96 | +45.03 | profit lock |
| 2026-01-13T14:50 | BUY | +53.56 | +132.16 | +78.60 | +47.23 | profit lock |
| 2026-01-14T20:10 | BUY | +41.29 | +49.33 | +8.04 | +49.33 | floor stop |
| 2026-01-15T14:50 | SELL | +45.04 | +47.29 | +2.25 | +51.67 | floor stop |
| 2026-01-16T14:30 | SELL | +69.40 | +43.85 | -25.55 | +49.94 | floor stop |
| 2026-01-20T05:30 | BUY | +43.94 | +463.55 | +419.61 | +44.02 | reversal |
| 2026-01-22T05:55 | BUY | +48.47 | +79.80 | +31.33 | +53.37 | reversal |
| 2026-01-23T07:15 | BUY | +37.56 | +46.18 | +8.62 | +51.34 | floor stop |
| 2026-01-23T08:25 | SELL | +76.28 | +112.87 | +36.59 | +51.57 | profit lock |
| 2026-01-29T09:25 | SELL | +40.87 | +25.87 | -15.00 | +47.20 | profit lock |
| 2026-02-02T07:30 | SELL | +55.97 | +237.29 | +181.32 | +39.77 | profit lock |
| 2026-02-03T18:10 | BUY | +33.87 | +37.32 | +3.45 | +40.87 | profit lock |
| 2026-02-05T14:00 | SELL | +33.70 | +23.59 | -10.11 | +40.26 | floor stop |
| 2026-02-05T18:35 | BUY | +63.15 | +37.37 | -25.78 | +37.37 | floor stop |
| 2026-02-13T11:55 | SELL | +31.62 | +33.17 | +1.55 | +33.17 | floor stop |
| 2026-02-13T14:10 | BUY | +51.06 | +26.98 | -24.08 | +35.14 | floor stop |
| 2026-02-16T04:25 | SELL | +34.15 | +60.25 | +26.10 | +34.36 | profit lock |
| 2026-02-18T14:35 | BUY | +29.83 | +221.60 | +191.77 | +36.74 | reversal |
| 2026-02-19T11:50 | SELL | +26.81 | +36.91 | +10.10 | +36.91 | floor stop |
| 2026-02-19T22:00 | BUY | +36.44 | +38.56 | +2.12 | +38.56 | floor stop |
| 2026-02-20T06:40 | BUY | +28.16 | +51.43 | +23.27 | +40.22 | profit lock |
| 2026-02-23T16:20 | BUY | +31.98 | +35.73 | +3.75 | +39.97 | floor stop |
| 2026-02-25T07:15 | BUY | +27.76 | +37.73 | +9.97 | +39.51 | floor stop |
| 2026-02-25T12:15 | SELL | +33.52 | +38.48 | +4.96 | +38.48 | floor stop |
| 2026-02-26T12:30 | SELL | +34.05 | +34.15 | +0.10 | +37.80 | floor stop |
| 2026-03-02T08:05 | BUY | +40.48 | +37.26 | -3.22 | +37.26 | floor stop |
| 2026-03-03T07:15 | SELL | +30.52 | +32.13 | +1.61 | +37.00 | floor stop |
| 2026-03-05T06:40 | SELL | +26.38 | +109.47 | +83.09 | +34.16 | profit lock |
| 2026-03-09T05:10 | BUY | +40.25 | +33.68 | -6.57 | +33.68 | floor stop |
| 2026-03-10T15:40 | BUY | +47.37 | +23.45 | -23.92 | +36.78 | floor stop |
| 2026-03-12T05:40 | SELL | +24.29 | +35.37 | +11.08 | +35.37 | floor stop |
| 2026-03-13T16:55 | SELL | +25.69 | +67.76 | +42.07 | +33.57 | profit lock |
| 2026-03-13T21:10 | SELL | +26.34 | +35.49 | +9.15 | +35.49 | floor stop |
| 2026-03-16T10:15 | SELL | +28.08 | +30.89 | +2.81 | +34.92 | floor stop |
| 2026-03-16T13:45 | BUY | +28.36 | +35.58 | +7.22 | +35.58 | floor stop |
| 2026-03-17T08:50 | SELL | +30.15 | +33.79 | +3.64 | +37.52 | floor stop |
| 2026-03-18T08:35 | BUY | +30.28 | +37.63 | +7.35 | +37.63 | floor stop |
| 2026-03-19T06:55 | SELL | +38.54 | +37.45 | -1.09 | +37.45 | floor stop |
| 2026-03-20T10:35 | BUY | +32.46 | +36.87 | +4.41 | +36.87 | floor stop |
| 2026-03-20T12:15 | SELL | +34.81 | +35.67 | +0.86 | +35.67 | floor stop |
| 2026-03-23T05:10 | SELL | +48.56 | +34.79 | -13.77 | +34.79 | floor stop |
| 2026-03-23T11:30 | BUY | +55.31 | +34.89 | -20.42 | +34.89 | floor stop |
| 2026-03-23T18:35 | SELL | +88.69 | +27.53 | -61.16 | +35.59 | profit lock |
| 2026-03-24T09:00 | BUY | +39.33 | +34.56 | -4.77 | +40.57 | floor stop |
| 2026-03-25T08:20 | SELL | +35.53 | +42.37 | +6.84 | +42.37 | floor stop |
| 2026-03-26T08:20 | SELL | +32.16 | +144.13 | +111.97 | +41.52 | profit lock |
| 2026-03-27T04:25 | BUY | +37.04 | +42.97 | +5.93 | +42.97 | floor stop |
| 2026-04-01T13:40 | BUY | +34.70 | +36.18 | +1.48 | +41.40 | floor stop |
| 2026-04-02T04:00 | SELL | +42.48 | +91.33 | +48.85 | +34.97 | profit lock |
| 2026-04-06T07:00 | BUY | +44.93 | +42.84 | -2.09 | +42.84 | floor stop |
| 2026-04-08T20:35 | SELL | +30.51 | +39.45 | +8.94 | +39.45 | floor stop |
| 2026-04-09T10:55 | BUY | +27.90 | +41.18 | +13.28 | +41.18 | floor stop |
| 2026-04-17T04:55 | BUY | +23.68 | +35.35 | +11.67 | +35.35 | floor stop |
| 2026-04-23T14:15 | BUY | +53.32 | +38.41 | -14.91 | +32.07 | profit lock |
| 2026-04-28T04:05 | SELL | +31.40 | +25.56 | -5.84 | +31.68 | floor stop |
| 2026-04-30T05:35 | SELL | +25.15 | +30.63 | +5.48 | +30.63 | floor stop |
| 2026-04-30T08:30 | BUY | +27.94 | +148.36 | +120.42 | +30.72 | reversal |
| 2026-05-07T09:00 | BUY | +65.37 | +65.90 | +0.53 | +32.72 | profit lock |
| 2026-05-11T11:50 | SELL | +32.12 | +30.16 | -1.96 | +30.16 | floor stop |
| 2026-05-11T14:00 | BUY | +38.20 | +31.11 | -7.09 | +33.61 | floor stop |
| 2026-05-12T20:05 | BUY | +33.84 | +89.40 | +55.56 | +32.91 | flatten |
| 2026-05-13T15:15 | SELL | +30.59 | +26.43 | -4.16 | +30.30 | floor stop |
| 2026-05-15T22:10 | SELL | +38.33 | +27.56 | -10.77 | +30.12 | floor stop |
| 2026-05-19T20:50 | SELL | +32.46 | +76.34 | +43.88 | +30.70 | flatten |
| 2026-05-21T12:55 | SELL | +35.07 | +33.74 | -1.33 | +31.73 | profit lock |
| 2026-05-26T14:25 | SELL | +25.44 | +24.63 | -0.81 | +30.28 | floor stop |
| 2026-05-27T12:40 | SELL | +51.41 | +28.34 | -23.07 | +30.10 | floor stop |
| 2026-05-28T15:45 | BUY | +33.42 | +25.76 | -7.66 | +28.44 | floor stop |
| 2026-06-02T06:15 | BUY | +23.90 | +117.54 | +93.64 | +26.75 | reversal |
| 2026-06-04T12:45 | BUY | +30.45 | +20.80 | -9.65 | +28.02 | floor stop |
| 2026-06-05T15:45 | SELL | +23.08 | +19.87 | -3.21 | +28.00 | floor stop |
| 2026-06-08T08:05 | SELL | +40.68 | +22.39 | -18.29 | +28.62 | floor stop |
| 2026-06-08T10:55 | SELL | +22.19 | +27.58 | +5.39 | +27.58 | floor stop |
| 2026-06-08T13:30 | BUY | +31.37 | +26.78 | -4.59 | +26.78 | floor stop |
| 2026-06-08T21:45 | SELL | +23.62 | +26.89 | +3.27 | +28.15 | floor stop |
| 2026-06-10T08:30 | BUY | +38.29 | +27.86 | -10.43 | +27.86 | floor stop |
| 2026-06-10T17:40 | SELL | +37.92 | +22.25 | -15.67 | +27.61 | floor stop |
| 2026-06-15T10:20 | BUY | +62.09 | +27.73 | -34.36 | +27.73 | floor stop |
| 2026-06-15T19:15 | SELL | +26.46 | +96.18 | +69.72 | +29.66 | flatten |
| 2026-06-16T06:25 | BUY | +25.33 | +27.20 | +1.87 | +29.54 | floor stop |
| 2026-06-16T10:25 | BUY | +26.77 | +30.64 | +3.87 | +30.64 | floor stop |
| 2026-06-16T16:20 | SELL | +38.06 | +27.74 | -10.32 | +30.47 | floor stop |
| 2026-06-18T21:35 | SELL | +29.42 | +27.99 | -1.43 | +27.99 | floor stop |
| 2026-06-19T10:00 | BUY | +34.90 | +29.05 | -5.85 | +29.05 | floor stop |
| 2026-06-23T20:25 | SELL | +28.52 | +29.25 | +0.73 | +29.25 | floor stop |
| 2026-06-24T08:25 | BUY | +25.27 | +27.79 | +2.52 | +27.79 | floor stop |
| 2026-06-24T20:25 | SELL | +24.81 | +26.10 | +1.29 | +26.10 | floor stop |
| 2026-06-26T07:10 | BUY | +26.53 | +33.40 | +6.87 | +27.19 | profit lock |
| 2026-06-29T11:50 | SELL | +35.10 | +26.34 | -8.76 | +26.34 | floor stop |
| 2026-06-29T16:45 | SELL | +30.62 | +26.43 | -4.19 | +26.43 | floor stop |
| 2026-07-03T19:40 | BUY | +25.40 | +22.56 | -2.84 | +25.74 | floor stop |
| 2026-07-06T05:20 | SELL | +22.72 | +27.30 | +4.58 | +27.30 | floor stop |
| 2026-07-07T13:15 | BUY | +25.96 | +74.48 | +48.52 | +26.90 | profit lock |
| 2026-07-07T21:30 | SELL | +31.06 | +182.64 | +151.58 | +26.78 | flatten |
| 2026-07-09T05:50 | SELL | +28.32 | +24.20 | -4.12 | +27.82 | floor stop |
| 2026-07-09T08:10 | BUY | +32.32 | +56.21 | +23.89 | +30.19 | reversal |
| 2026-07-13T13:10 | SELL | +30.56 | +24.90 | -5.66 | +30.29 | profit lock |
| 2026-07-13T16:45 | SELL | +65.00 | +126.92 | +61.92 | +28.79 | flatten |
| 2026-07-16T15:35 | SELL | +25.41 | +30.42 | +5.01 | +30.42 | floor stop |
| 2026-07-17T06:05 | BUY | +26.03 | +30.32 | +4.29 | +30.32 | floor stop |
| 2026-07-17T08:50 | BUY | +26.48 | +26.77 | +0.29 | +31.47 | floor stop |
| 2026-07-17T15:20 | SELL | +30.90 | +32.22 | +1.32 | +34.28 | floor stop |
| 2026-07-22T16:20 | BUY | +33.18 | +23.70 | -9.48 | +31.44 | floor stop |
| 2026-07-24T09:55 | BUY | +29.46 | +44.44 | +14.98 | +30.46 | profit lock |
| 2026-07-24T19:45 | SELL | +27.76 | +31.15 | +3.39 | +31.15 | floor stop |
| 2026-07-27T09:25 | BUY | +30.78 | +31.24 | +0.46 | +31.24 | floor stop |
| 2026-07-28T11:50 | SELL | +29.12 | +40.68 | +11.56 | +31.24 | profit lock |
| 2026-07-29T07:55 | BUY | +51.68 | +30.62 | -21.06 | +29.00 | profit lock |
| 2026-07-29T19:05 | BUY | +32.95 | +32.14 | -0.81 | +32.14 | floor stop |
| 2026-07-31T10:20 | SELL | +29.32 | +30.75 | +1.43 | +30.75 | floor stop |
| 2026-07-31T15:35 | SELL | +28.38 | +31.03 | +2.65 | +31.03 | floor stop |
| 2026-08-04T14:10 | BUY | +33.82 | +47.27 | +13.45 | +28.56 | profit lock |
| 2026-08-05T08:30 | BUY | +41.26 | +53.65 | +12.39 | +29.37 | profit lock |
| 2026-08-05T14:05 | BUY | +42.40 | +68.44 | +26.04 | +30.83 | profit lock |
| 2026-08-06T05:45 | SELL | +38.60 | +32.70 | -5.90 | +32.70 | floor stop |
| 2026-08-07T06:55 | BUY | +35.83 | +31.04 | -4.79 | +33.37 | floor stop |
| 2026-08-10T11:40 | SELL | +33.13 | +33.55 | +0.42 | +33.55 | floor stop |
| 2026-08-11T07:40 | SELL | +51.03 | +70.89 | +19.86 | +33.08 | reversal |

**aggregate**: mean delta +21.85, better 168, worse 163, min -138.15, max +1201.64
