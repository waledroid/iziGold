# Chop-filter study — flip-count box filter (H1 skip) and "chop mode" half-risk/no-adds (H2 soft)

Date: 2026-08-17. Analysis only; nothing in `mt5/` or `service/app/` changed.
Tool: `scripts/backtest.py --chop-flips F --chop-bars N --chop-box-atr X --chop-mode skip|soft|off`
(commit b4111f2; defaults byte-identical — 30-day and 17-month verbose runs diffed before/after,
identical). Harness imports the module and calls `run()` per window, same code path as the CLI.
Data: `bars_max.json`, 99 999 M5 bars, 2025-03-19 12:00 -> 2026-08-17 13:20 server (GMT+3).
Params: live defaults (ADX gate 10, ConfirmCloses 1, EMA 55, 1 % risk, expo 360, stop pad 0.75 ATR,
target-exit, ADR mode). Balances: morning + 30 d $4 500 (as in the confirm-variants report), 17 mo
and sub-periods $4 000 (as in the regime-gate study).
Prior reports read first: `.superpowers/confirm-variants-report.md`, `.superpowers/regime-gate-study.md`.

## Verdict (short)

**Neither. Do not implement H1 or H2.**

1. The rule as specified (F=2 flips in N=24 bars AND box < 2.0 ATR) **never fires** — zero entries in
   17 months. Two HalfTrend(amplitude 4) flips cannot happen inside a 2-ATR box: the tightest 2-flip
   box the strategy ever entered was 2.63 ATR — and that was the 08-17 06:55 BUY itself. The 08-17
   box was 11 $ on a 3.6–4.8 $ ATR(14) = 2.6–3.4 ATR, not "~2 ATR".
2. Widen the box to 3.0 ATR and the rule fires 1–2 times a month (6 entries in 17 months at N=24, 19 at
   N=18). It does erase one or two of the 08-17 whipsaws, but over 17 months those rare entries are
   net ≈ 0 (N=24: 2 W +94 / 4 L −83; N=18: 9 W +433 / 10 L −283 → skipping them costs $200). Coin-toss
   sample; every 30-day gain (+$56…+$109) is inside the sizing-path noise (the tool shows ±$180 swings
   from skipping two winners, see §5).
3. Drop the box (flip count alone, the half of the rule that actually triggers): entries after ≥ 2 flips
   in 24 bars win MORE often than single-flip entries — 38.5 % vs 34.4 % over 17 months (z = 2.0,
   p = 0.046), 41.9 % vs 34.9 % in the current era P3, 48 % vs 46 % last 30 days. The second flip in
   a box is, on average, the breakout, not the trap. Skipping them: 30 d +949 → +194; P3 +1 481 → −287.
   Winner-dollars skipped $1 710 vs loser-dollars saved $1 231 (30 d); $8 794 vs $7 180 (P3).
4. H2 soft (half risk, no adds) on the same tags is never better than baseline in the recent era except
   on the 1–2-entry rules: 30 d +949 → +560 (flip-only F2/N24), P3 +1 481 → −326. It halves the
   damage on chop losers ($−87 → $−36 on 08-17) but also halves the winners and removes the adds that
   the P3 profit is built on.

The 08-17 morning was the single tightest 2-flip box in 17 months. A filter tuned to it fires
essentially only on it.

## 1. Sanity checks

### 1a. Baseline reproduces the 08-17 morning (run `--balance 4500 --start 2026-08-14 --end 2026-08-17T12:00`)
Identical to the confirm-variants report §0: 05:50 SELL 4 oz → 06:55 reversal −27.72 | 06:55 BUY 7 oz →
07:25 stop −43.25 | 07:55 SELL 5 oz → 08:30 stop −44.36 | 08:45 BUY 5+3+2 oz → 10:50 profit lock
+33.54 | 11:40 SELL 5 oz open at the 12:00 cut (−0.90). Day closed P/L **−82.69**; session (Fri 08-14
warm-up + Mon) +65.09.

### 1b. Flip counter vs the known flip times
Flips the counter sees on 08-17 (closed-bar timestamps, server): 02:30 DOWN, 03:25 UP, **05:30 DOWN,
06:55 UP, 07:50 DOWN**, 08:45 UP, 11:30 DOWN. The three owner-reported flips are reproduced exactly.
Per entry bar (N = 18 / 24 / 36; box = max high − min low over the last N bars incl. the entry bar;
ATR(14) at the bar):

| entry bar | ATR14 | N=18 flips, box/ATR | N=24 flips, box/ATR | N=36 flips, box/ATR |
|---|---|---|---|---|
| 05:50 SELL | 4.80 | 1 (05:30), 5.23 | 1, 5.55 | 2 (03:25, 05:30), 10.08 |
| 06:55 BUY  | 3.84 | 2 (05:30, 06:55), **2.62** | 2, **2.63** | 2, 7.38 |
| 07:55 SELL | 3.58 | 2 (06:55, 07:50), **2.99** | 2, 3.22 | 3 (05:30, 06:55, 07:50), 3.22 |
| 08:45 BUY (winner) | 4.18 | 2 (07:50, 08:45), 3.43 | 3, 3.43 | 3, 3.43 |
| 11:40 SELL | 3.71 | 1 (11:30), 4.18 | 1, 4.18 | 2 (08:45, 11:30), 5.15 |

So: the first whipsaw (05:50) is a first flip — no flip-count rule can see it. X ≤ 2.0 tags nothing.
X = 3.0 tags 06:55 (both N=18/24) and 07:55 (N=18 only). X ≥ 3.5 also tags the 08:45 winner.

### 1c. Entries only
The rule lives in the entry block only (`backtest.py`, chop block just after the regime/ATR-spike gate;
soft mode sets `basket["soft"]`, which the add block treats as frozen and the sizing divides risk by 2).
Exits, stops, target, lock, reversal, flatten untouched.

## 2. Full grid, first-order opportunity cost (baseline run, `--chop-mode off`, entries tagged)

"chop entries" = baseline trades whose entry bar met the rule; their P/L is what H1 skips and H2 shrinks.
F ∈ {2,3}, N ∈ {18,24,36}, X ∈ {1.5, 2.0, 3.0, 3.5, 4.0, 0=box off}. **X = 1.5 and 2.0 tag 0 entries in
every window** and are omitted below.

### 08-17 morning session (10 baseline trades, +65.09)
| F/N/X | chop entries | W / L | skip net (trades, win%, valley) | soft net |
|---|---|---|---|---|
| 2/18/3 | 2 (−88) | 0 W / 2 L −88 | **+153** (8, 50 %, 111) | +116 |
| 2/18/3.5 | 3 (−54) | 1 W +34 / 2 L −88 | +119 | +81 |
| 2/18/0 | 5 (+146) | 3 W +234 / 2 L −88 | −74 | −57 |
| 2/24/3 | 1 (−43) | 0 / 1 L −43 | +108 | +90 |
| 2/24/3.5 | 4 (−101) | 1 W +34 / 3 L −134 | +166 | +104 |
| 2/24/0 | 6 (+100) | 3 W +234 / 3 L −134 | −28 | −34 |
| 2/36/3 | 0 | — | +65 | +65 |
| 2/36/0 | 8 (+71) | 3 W +234 / 5 L −163 | −6 | −20 |
| 3/24/0 | 1 (+34) | 1 W +34 | +32 | +29 |
| 3/36/0 | 3 (+91) | 2 W +135 / 1 L −44 | −19 | −82 |

08-17 under the "best" rule F2/N18/X3 skip: 05:50 SELL −27.72 (first flip, still taken) | 06:55 BUY
**refused** (flips 05:30, 06:55; box 2.62 ATR) | 07:55 SELL **refused** (flips 06:55, 07:50; box
2.99 ATR) | 08:45 BUY +33.54 taken | 11:40 SELL open. Day −82.69 → **+4.92**; the two stops (−87.61)
avoided. Under F2/N18/X3 soft: the two whipsaws are taken at 3 oz / 2 oz → −18.54 / −17.74 (−36.28
instead of −87.61), day −31.36. Under F2/N24/X3 skip only 06:55 is refused (day −39.44).

### Last 30 days (07-20 → 08-17, 111 baseline trades, +949.47, valley 387)
| F/N/X | chop entries | W $ / L $ | skip: net / trades / win% / valley | soft: net / trades / win% / valley |
|---|---|---|---|---|
| 2/18/3 | 2 (−109) | 0 / 2 L −109 | +1058 / 109 / 47.7 / 387 | +1007 / 111 / 46.8 / 387 |
| 2/18/3.5 | 5 (+158) | 3 W +266 / 2 L −109 | +689 / 107 / 45.8 / 449 | +643 |
| 2/18/4 | 14 (+354) | 8 W +657 / 6 L −302 | +326 | +644 |
| 2/18/0 | 38 (+580) | 19 W +1515 / 19 L −935 | +187 / 77 / 44.2 / 386 | +612 / 87 / 43.7 / 279 |
| 2/24/3 | 1 (−56) | 0 / 1 L −56 | +1005 / 110 / 47.3 / 387 | +980 |
| 2/24/3.5 | 4 (−121) | 1 W +42 / 3 L −163 | +1070 / 107 / 47.7 / 384 | +988 |
| 2/24/4 | 10 (−5) | 5 W +251 / 5 L −256 | +1055 / 103 / 46.6 / 384 | +762 |
| 2/24/0 | 50 (+479) | 24 W +1710 / 26 L −1231 | **+194** / 67 / 43.3 / 243 | +560 / 87 / 41.4 / 233 |
| 2/36/3.5 | 2 (−11) | 1 W +42 / 1 L −53 | +960 | +930 |
| 2/36/0 | 72 (+552) | 32 W +2293 / 40 L −1741 | +250 / 45 / 46.7 / 255 | +607 / 82 / 40.2 / 250 |
| 3/24/4 | 2 (+183) | 2 W +183 / 0 | +1136 (!) | +797 |
| 3/24/0 | 4 (+237) | 4 W +237 / 0 | +1029 | +823 |
| 3/36/0 | 23 (+524) | 13 W +1045 / 10 L −521 | +763 | +555 |

(!) 3/24/4 skips two winners and ends $187 ABOVE baseline — pure sizing-path effect (a freed slot /
different balance re-times later entries). That is the noise floor for one-month comparisons: any
"gain" of ≤ $200 from a 1–2-entry rule is not evidence.

### Full 17 months (2 157 baseline trades, −2 296.56, valley 3 827)
| F/N/X | chop entries | W $ / L $ | skip: net / trades / win% / valley | soft: net / trades / win% / valley |
|---|---|---|---|---|
| 2/18/3 | 19 (+151) | 9 W +433 / 10 L −283 | −2498 / 2144 / 36.2 / 3940 | −2340 |
| 2/18/3.5 | 125 (−9) | 52 W +1827 / 73 L −1836 | −2748 | −2757 |
| 2/18/0 | 756 (+236) | 296 W +11065 / 460 L −10830 | −2583 / 1515 / 34.2 / 2837 | −2516 / 1917 / 34.1 / 2971 |
| 2/24/3 | 6 (+11) | 2 W +94 / 4 L −83 | −2313 | −2305 |
| 2/24/3.5 | 83 (−134) | 28 W +1061 / 55 L −1195 | −2341 / 2100 / 36.0 / 3467 | −2334 |
| 2/24/4 | 234 (−244) | 93 W +3230 / 141 L −3474 | −2794 | −2296 |
| 2/24/0 | 1057 (−313) | 407 W +14692 / 650 L −15005 | −2681 / 1200 / 33.4 / 2907 | −2553 / 1826 / 32.9 / 3512 |
| 2/36/3 | 1 (−17) | 0 / 1 L −17 | −2335 | −2297 |
| 2/36/0 | 1471 (−732) | 547 W +20348 / 924 L −21081 | −1993 / 744 / 33.6 / 2299 | −1939 / 1695 / 31.6 / 3097 |
| 3/24/0 | 127 (+488) | 49 W +2157 / 78 L −1669 | −2394 | −2881 |
| 3/36/0 | 443 (−435) | 162 W +5976 / 281 L −6410 | −2565 / 1813 / 35.8 / 2938 | −2531 |

Only 2/36/0 (skip 68 % of all entries) improves the 17-month total (−2 297 → −1 993, valley 3 827 →
2 299) — by amputation, and it destroys the current era (P3 +1 481 → +390, 30 d → +250).

### Three sub-periods (33 333 bars each)
| period | base | 2/18/3 skip / soft | 2/24/3 skip / soft | 2/24/3.5 skip / soft | 2/24/0 skip / soft | 2/36/0 skip / soft |
|---|---|---|---|---|---|---|
| P1 03-19 → 09-05 (732 tr) | −2012 | −1946 / −2041 | −1950 / −2010 | −1930 / −2051 | −1644 / −1346 | −1176 / −1103 |
| P2 09-05 → 02-26 (723 tr) | −1199 | −1215 / −1202 | −1183 / −1207 | −1476 / −1056 | −1173 / −1581 | −1344 / −1570 |
| P3 02-26 → 08-17 (673 tr) | +1481 | +1564 / +1508 | +1540 / +1514 | +1603 / +1417 | **−287 / −326** | +390 / +312 |
| last 30 d | +949 | +1058 / +1007 | +1005 / +980 | +1070 / +988 | +194 / +560 | +250 / +607 |

Chop entries per period for the flip-only F2/N24 tag (the half of the rule that carries the weight):
P1 361 (−709: 131 W +7 547 / 230 L −8 256) · P2 364 (−227: 129 W +9 286 / 235 L −9 513) ·
P3 315 (**+1 614**: 132 W +8 794 / 183 L −7 180). In the only profitable era the flip-tagged entries
supplied more than the whole profit — exactly the pattern that killed the regime gate.
Robustness of the flip-only skip: helps P1 and (barely) P2, destroys P3 and the last 30 days = a
regime-tuned filter, not a structural edge. The X = 3.0 rules "help" 3 of 4 recent windows by
+$56…+$109 on 1–2 entries and lose $200 over 17 months — noise.

## 3. Is the label predictive at all? (baseline, F2/N24, tag = flips ≥ 2, box ratio recorded)

| window | tagged (≥2 flips) win % / n | untagged (1 flip) win % / n | z, p |
|---|---|---|---|
| 17 months | **38.5 %** / 1 057 | 34.4 % / 1 084 | 2.00, **0.046** |
| P3 | 41.9 % / 315 | 34.9 % / 350 | 1.86, 0.063 |
| last 30 d | 48.0 % / 50 | 45.9 % / 60 | 0.22, 0.83 |

By flip count in the last 24 bars (17 months): 1 flip 34.0 % (n 1 084, −2 034) · 2 flips 38.5 %
(n 930, −801) · 3 flips 38.1 % (n 126, +409). By box/ATR among ≥2-flip entries (17 months): min 2.63,
p10 3.56, median 4.62, p90 6.03; [0,3) n 6 33 % · [3,3.5) n 77 34 % · [3.5,4) n 151 43 % · [4,5)
n 451 41 % · [5,7) n 345 35 %. No monotone "tighter box = worse" relationship; the tight end is a
handful of trades.

Interpretation: after HalfTrend has already flipped once and come back, the NEXT confirmed flip is
slightly more often the real move. Refusing it is refusing the strategy's best-conditioned entries.

## 4. H2 soft specifics

Soft = same tag, entry sized at RISK_PCT/2 (0.5 %), no pyramid adds for that basket; profit target
(2 % of cycle balance), lock (armed at 1R of the FULL 1 % budget — TradeManager reads
`m_risk.RiskPct()`, so a half-size basket needs twice the move to arm), stop and reversal untouched.
Effect on the tagged trades themselves is what you'd expect (losers roughly halved, winners roughly
halved and un-pyramided): 08-17 06:55/07:55 −87.61 → −36.28. Portfolio effect: on the flip-only tags
soft beats skip (30 d +560 vs +194; P1 −1 346 vs −1 644) because it keeps half of the winners, but it
never beats baseline in P3 or the last 30 days (P3 −326, 30 d +560 vs +949). On the X = 3.0 rules
soft is a rounding error (1–2 baskets/month). "ADR sizing is where the damage compounds" is true for a
loser, but the same sizing and the adds are where the P3 profit came from — the tag can't tell them apart.

## 5. Caveats

- Replay simplifications as before (close fills ± spread, no daily-loss brake, no news blackout,
  integer oz, balance compounds inside a window).
- Sizing-path divergence: skipping or shrinking one basket changes the balance and re-times later
  entries; the "chop entries (baseline)" columns are the clean first-order measure, the skip/soft nets
  include second-order effects (see the 3/24/4 example: skipping two winners ended $187 better).
  Treat any 30-day difference below ~$200 as noise.
- The morning window uses Fri 08-14 as indicator warm-up (lots 4/7/5 vs live 4/8/5); the day's closed
  P/L is −82.69 in replay vs −81.79/−78 live depending on which trades are counted.
- Grid was extended beyond the requested X ∈ {1.5, 2, 3} to {3.5, 4, off} once X ≤ 2 proved to be a
  no-op; nothing in the extension changes the conclusion.

## 6. If it were implemented anyway (for the record, not recommended)

The only rule that (a) fires on 08-17, (b) does not lose money in the last 30 days and P3, and (c) does
not tag winners in P3 is F2/N24/X3.0 skip: 6 entries in 17 months, +$56 (30 d), +$59 (P3), −$16
(17 mo). It would be an EA-side `RiskManager.CanEnter` refusal ("chop: 2 flips/24 bars, box 2.6 ATR")
computed from the strategy's own HalfTrend flip history (fail-open: no history → allow), and it would
have refused exactly one trade in the last month. That is not worth a code path.
