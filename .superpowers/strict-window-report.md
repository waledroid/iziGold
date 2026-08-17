# Strict entry window (`--strict-window`) — backtest report, 2026-08-17

Owner's TRUE `halftrend_ema_v1` entry rule: **HalfTrend arrow on bar 1; wait bar 2; ENTER at bar 3's
OPEN if bar 3 opens on the trend's side of EMA-55 (= bar 2 CLOSED there); otherwise the signal is DEAD
until the next HalfTrend flip.** Until today the EA (and the replay baseline) fired on the FIRST close
beyond the EMA after a flip, whenever that came — the arrow bar itself (bar 2's open), bar 2, or a
late drift 4 or 20 bars on. This is a CORRECTNESS fix the owner mandated; the numbers below inform,
they do not veto.

## Implementation (`scripts/backtest.py`, `--strict-window`, default OFF)

- OFF is byte-identical to the previous baseline (30-day run diffed against `git show HEAD:` copy:
  identical output).
- ON: exactly one decision per flip, at the close of the bar `CONFIRM_CLOSES` bars after the arrow bar
  (`i - last_flip == CONFIRM_CLOSES`, default 1 = bar 2). Pass (that close on the trend's side of the
  EMA) → signal on that closed bar, filled at its close — which IS bar 3's open barring the tick gap;
  the replay's existing entry-at-close-of-signal-bar convention is unchanged. Fail → `fired_flip`
  latched, no entry for that flip ever (dead until the next flip). Reversal exits use the same signal,
  so a dead opposite flip no longer closes the open basket (same as the EA now).
- Every trade is tagged `flip_t` (arrow bar time) and `entry_offset` (bars after the arrow: 0 = arrow
  bar, 1 = strict bar, >=2 = late drift) so baseline and strict can be matched per flip.
- Same-flip categories used below: **late drift** (baseline entered >=2 bars after the arrow → strict
  never enters); **arrow-bar dead** (baseline entered on the arrow bar's close, bar 2 then closed on
  the wrong side → strict never enters); **arrow-bar shifted** (baseline entered on the arrow bar,
  strict enters ONE bar later at bar 2's close — kept, but at a different price/size); **offset-1
  identical** (both enter on the same bar); **chain** (trades that exist in only one run because the
  basket/exposure/balance path diverged).

Data: `bars_max.json` (99,999 M5 bars, 2025-03-19 → 2026-08-17 13:20). Live defaults (ADX gate 10,
ConfirmCloses 1, EMA 55, 1 % risk, expo 360, stop pad 0.75 ATR, target-exit).

## Results

| run | baseline net | strict net | Δ | trades b→s | win % b→s | max valley b→s | dead flips |
|---|---|---|---|---|---|---|---|
| 08-17 morning session (`--balance 4500 --start 2026-08-14 --end 2026-08-17T12:00`) | +65.09 | +55.20 | **−9.89** | 10→7 | 40.0→42.9 | 180.91→135.00 | 5 |
| last 30 days (`--days 30`, $4000) | +780.08 | +198.58 | **−581.50** | 111→83 | 46.8→38.6 | 343.10→510.66 | 65 |
| full 17 months ($4000) | −2296.56 | −3084.53 | **−787.97** | 2157→1729 | 36.4→35.4 | 3827.11→3602.55 | 939 |
| sub-period 1/3 (03-19 → 09-05) | −2011.97 | −2147.01 | −135.04 | 732→590 | 34.6→32.5 | 2917.14→2495.13 | 314 |
| sub-period 2/3 (09-05 → 02-26) | −1198.56 | −1712.77 | −514.21 | 723→575 | 32.8→31.5 | 3329.37→2333.47 | 301 |
| sub-period 3/3 (02-26 → 08-17) | +1481.16 | −198.62 | **−1679.77** | 673→537 | 38.2→38.0 | 1506.31→2319.69 | 323 |

(Sub-periods are independent runs — indicators re-warm, balance resets to $4000 — so they do not sum
to the full run. Max drawdown on closed balance: 30d 289→507, 17mo 3788→3557.)

### What the strict rule REMOVES (baseline trades that cannot exist under strict) — the honest cost/benefit

| window | late-drift entries removed | arrow-bar entries killed by a failed bar 2 | arrow-bar entries kept but SHIFTED 1 bar later: baseline P/L → strict P/L of the same flips |
|---|---|---|---|
| 08-17 morning | 2 trades, **−28.62** (0 W / 2 L: 05:50 SELL −27.72, 11:40 SELL −0.90) | 1 trade, **+33.54** (the 08:45 BUY winner: 08:50 closed 4393.11 vs EMA 4393.33) | 5 trades +69.63 → strict +49.30 |
| last 30 days | 25 trades, **+177.75** (11 W +507.92 / 14 L −330.17) — net WINNERS | 11 trades, **−326.06** (2 W +59.85 / 9 L −385.91) | 70 trades +1011.13 → 67 strict trades +439.86 |
| full 17 months | 360 trades, **−1292.52** (116 W +3742.58 / 244 L −5035.10) — net LOSERS | 161 trades, **−1897.30** (30 W +1109.54 / 131 L −3006.84) | 1536 trades +1198.19 → 1493 strict trades −2509.67 |
| sub 1/3 | 119, −452.36 (36 W / 83 L) | 44, −1336.85 | 539 −0.99 → 515 −1974.85 |
| sub 2/3 | 122, −938.67 (31 W / 91 L) | 60, −914.38 | 505 +1228.07 → 493 −830.69 |
| sub 3/3 | 120, −266.46 (35 W / 85 L) | 58, −1066.15 | 461 +2677.69 → 449 +57.04 |

Chain effects (trades present in only one run because the paths diverged): 30d baseline-only 3 (+257.58),
strict-only 11 (−189.10); 17mo baseline-only 43 (−173.44), strict-only 136 (−493.51).

### Reading

1. **Removing late-drift entries is right AND pays over the long run**: 360 trades in 17 months, net
   −1292 (only 32 % winners). In the last 30 days they happened to be net winners (+178), so the
   short window shows a cost there — that is the noise, the 17-month sign is the signal.
2. **Killing arrow-bar entries whose bar 2 fails also pays**: −1897 removed over 17 months, −326 in the
   last 30 days. Every sub-period agrees. This is the "fake-out" the rule is designed to filter.
3. **The whole cost comes from entering ONE BAR LATER on the flips that survive**: the same 1,536 flips
   made +1198 when entered at the arrow bar's close, but −2510 when entered at bar 2's close (bar 3's
   open). One M5 bar of delay on a HalfTrend arrow (which by construction closes hard in the new
   direction, and bar 2 usually follows through) means a worse price, a wider stop (bar 2's wick is
   inside the extreme), smaller size and less room to the target/lock. This effect (~−3.7k over 17
   months, ~−570 in the last 30 days) outweighs the ~+3.2k saved by removing bad entries.
4. **08-17 morning**: the strict rule does NOT rescue the day. It removes the 05:50 late-drift loser
   (−27.72, the bug the owner reported) and the 11:40 late SELL, but the two whipsaw stops remain
   (06:55 arrow → 07:00 BUY −47.57 stop; 07:50 arrow → 07:55 SELL −44.36 stop) and the day's only
   winner (08:45 BUY +33.54) dies because bar 2 (08:50) closed 22 cents under the EMA. Day P/L
   −82.69 → −91.93.
5. **Sub-period robustness**: strict is worse in all three thirds and in the 30-day window; the max
   open-equity valley improves in two of three thirds and over 17 months (fewer, cleaner entries) but
   worsens in the recent third and the last 30 days.

### Caveat

The replay fills every entry at the decision bar's close and models neither the daily loss brake nor
the news blackout; the "shifted" comparison also carries balance-path/sizing differences (a strict
trade sizes off a different running balance). Direction of every effect is consistent across windows;
exact dollars are indicative. The rule is a mandated correctness fix — these numbers say what it costs
(mainly the one-bar delay), not whether to do it.

## Commands

```
python3 scripts/backtest.py --source bars_max.json --balance 4500 --start 2026-08-14 --end 2026-08-17T12:00 [--strict-window]
python3 scripts/backtest.py --source bars_max.json --days 30 [--strict-window]
python3 scripts/backtest.py --source bars_max.json [--strict-window]
```
Per-flip matching was done by a scratch driver importing `backtest.run` and pairing trades on `flip_t`.
