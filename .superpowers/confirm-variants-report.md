# Confirmation-rule variants — 2026-08-17 morning whipsaw autopsy

Question: would `--confirm 2`, an "open beyond EMA" confirmation, or EMA 50
(instead of 55) have avoided the two Monday-morning whipsaw losses (SELL 05:55
→ 07:00 −$27.40, BUY 07:00 → 07:28 −$49.92, server GMT+3), and what do they
cost/earn over the last 30 days?

Data: `bars_max.json` re-dumped 2026-08-17 (99,999 M5 bars, 2025-03-19 →
2026-08-17 13:20 server). Tooling added to `scripts/backtest.py`:
`--ema-len N`, `--confirm-mode close|open`, `--start T`, `--end T`
(all defaults byte-identical to the previous baseline).

## 0. Baseline sanity — the replay reproduces the real morning

Run: `--balance 4500 --start 2026-08-14 --end 2026-08-17T12:00` (Friday 08-14
serves as indicator warm-up; Friday's replay P/L +$148 means Monday starts at
~$4,648 instead of the real ~$4,500, so lot sizes are within 1 oz of the live
ones). Replay bar times are the CLOSED bar's timestamp; the EA acts 5 min later.

| | real EA (DB `trades`) | replay 55/close/1 |
|---|---|---|
| SELL entry | 05:55 0.04 @ 4390.39 SL 4401.65 | 05:50-bar close 4 oz @ 4390.43 stop 4401.80 |
| SELL exit | 07:00 reversal @ 4397.40 → **−27.40** | 06:55-bar reversal @ 4397.16 → **−27.72** |
| BUY entry | 07:00 0.08 @ 4397.03 SL 4391.59 | 06:55-bar 7 oz @ 4397.16 stop 4391.18 |
| BUY exit | 07:28 stop @ 4390.97 → **−49.92** | 07:25-bar stop @ 4391.18 → **−43.25** |
| SELL #2 | 08:00 0.05 @ 4390.78, stop 08:29 @ 4398.92 → −41.60 | 07:55-bar 5 oz @ 4390.83, stop 08:30-bar @ 4399.50 → −44.36 |
| BUY #2 | 08:50 0.05 @ 4397.43 +adds, profit lock 10:55 → +35.79 | 08:45-bar 5 oz @ 4397.33 +3+2, profit lock 10:50 → +33.54 |
| SELL #3 | 11:45 0.05 @ 4396.29 (open) | 11:40-bar 5 oz @ 4396.34 (open at the 12:00 cut) |

Every live trade of the morning is reproduced within a few cents / one bar.
Note the morning actually had THREE losses (the 08:00 SELL is the third whipsaw
in the same 4388–4399 box), then a small winner.

## 1. Morning session 08-17 04:00–12:00 server, per variant

All rows: `--balance 4500 --start 2026-08-14 --end 2026-08-17T12:00 --verbose`.
Times = closed-bar timestamps (EA fill is at the next bar open, +5 min).

### 55 / close / 1 — baseline (current EA)
| entry | exit | why | P/L |
|---|---|---|---|
| 05:50 SELL 4 oz @ 4390.43 (stop 4401.80) | 06:55 @ 4397.16 | reversal | −27.72 |
| 06:55 BUY 7 oz @ 4397.16 (stop 4391.18) | 07:25 @ 4391.18 | stop | −43.25 |
| 07:55 SELL 5 oz @ 4390.83 (stop 4399.50) | 08:30 @ 4399.50 | stop | −44.36 |
| 08:45 BUY 5 oz @ 4397.33 +3 @ 4402.19 +2 @ 4407.22 | 10:50 @ 4404.32 | profit lock | +33.54 |
| 11:40 SELL 5 oz @ 4396.34 | (open at 12:00 cut) | — | (−0.90 floating) |
**Session closed P/L: −81.79** (3 losses, 1 win)

### 55 / close / 2 — two consecutive closes beyond EMA-55
| entry | exit | why | P/L |
|---|---|---|---|
| 06:10 SELL 4 oz @ 4390.37 (stop 4401.60) | 07:00 @ 4397.82 | reversal | −30.60 |
| 07:00 BUY 7 oz @ 4397.82 (stop 4391.22) | 07:25 @ 4391.22 | stop | −47.57 |
| 08:10 SELL 3 oz @ 4387.78 (stop 4399.55) | 08:30 @ 4399.55 | stop | −35.90 |
| 09:00 BUY 7 oz @ 4395.51 +4 @ 4400.44 | 10:10 @ 4396.54 | profit lock | −10.59 |
| 11:45 SELL 4 oz @ 4395.61 | (open at cut) | — | (−3.64 floating) |
**Session closed P/L: −124.66** (4 losses, 0 wins) — every whipsaw still taken, one bar later and at a worse price; the later BUY entry also turned the 08:45 winner into a small loser.

### 55 / open / 1 — "candle opens beyond EMA"
| entry | exit | why | P/L |
|---|---|---|---|
| 06:05 SELL 3 oz @ 4389.91 (stop 4401.69) | 06:55 @ 4397.16 | reversal | −22.35 |
| 06:55 BUY 7 oz @ 4397.16 (stop 4391.18) | 07:25 @ 4391.18 | stop | −43.25 |
| 07:55 SELL 5 oz @ 4390.83 | 08:30 @ 4399.50 | stop | −44.36 |
| 08:45 BUY 5+3+2 oz | 10:50 @ 4404.32 | profit lock | +33.54 |
| 11:40 SELL 5 oz @ 4396.34 | (open at cut) | — | (−0.90) |
**Session closed P/L: −76.42.** The ONLY difference from baseline is the first SELL: on the 05:50 bar the close was 4390.43 and the 05:55 open was 4390.44 — a 1-cent tick gap that straddled the EMA, so this mode fired three bars later. That is noise, not a rule (see §3).

### 50 / close / 1 — EMA 50, current confirm
Identical trade list to baseline (same 5 entries, same exits): **−81.79**. EMA-50 vs EMA-55 sat on the same side of every decisive close this morning.

### 50 / close / 2 — EMA 50, two closes
| entry | exit | why | P/L |
|---|---|---|---|
| 06:10 SELL 4 oz @ 4390.37 | 07:00 @ 4397.82 | reversal | −30.60 |
| 07:00 BUY 6 oz @ 4397.82 | 07:25 @ 4391.22 | stop | −40.77 |
| 08:00 SELL 7 oz @ 4392.99 +4 @ 4387.78 | 08:20 @ 4396.23 | stop | −58.67 |
| 09:00 BUY 7 oz @ 4395.51 +4 @ 4400.44 | 10:10 @ 4396.54 | profit lock | −10.59 |
| 11:45 SELL 4 oz @ 4395.61 | (open at cut) | — | (−3.64) |
**Session closed P/L: −140.63** — the worst of the set.

### 50 / open / 1
Identical to 50/close/1 (**−81.79**); 1 tick-gap decision bar, no trade changed.

Morning summary (closed P/L, 04:00–12:00):

| variant | trades | P/L | whipsaws avoided? |
|---|---|---|---|
| 55/close/1 (today) | 4 closed + 1 open | −81.79 | none |
| 55/close/2 | 4 + 1 | −124.66 | none (later, worse fills; winner lost too) |
| 55/open/1 | 4 + 1 | −76.42 | none (1-cent tick-gap artefact) |
| 50/close/1 | 4 + 1 | −81.79 | none (identical to today) |
| 50/close/2 | 4 + 1 | −140.63 | none |
| 50/open/1 | 4 + 1 | −81.79 | none (identical) |

None of the variants skip the box. The 05:00–07:50 range was 4388–4399 with
EMA-55 running through the middle of it; every rule that keys off "close (or
open) beyond the EMA" gets a fresh confirmation each time price crosses the
midline, and HalfTrend flipped three times in that box.

## 2. Last 30 days (07-20 01:00 → 08-17 13:20 server, 21 trading days)

All rows: `--balance 4500 --days 30` (ADX ≥ 10, expo 360, risk 1%, stop pad
0.75 ATR, ADR mode, target-exit).

| variant | trades | win % | net P/L | max DD (closed) | max open-equity valley | avg win / avg loss |
|---|---|---|---|---|---|---|
| **55/close/1 (today)** | 111 | 46.8 | **+949.47** | 316.09 | 387.33 | +65.97 / −42.05 |
| 55/close/2 | 101 | 42.6 | +643.11 | 334.16 | 369.83 | +62.31 / −35.11 |
| 55/open/1 | 111 | 46.8 | +947.39 | 316.09 | 387.33 | +65.97 / −42.09 |
| 55/open/2 | 101 | 42.6 | +643.11 | 334.16 | 369.83 | same as 55/close/2 |
| 50/close/1 | 117 | 47.0 | +736.02 | 374.22 | 452.10 | +60.38 / −41.69 |
| 50/close/2 | 99 | 42.4 | +331.95 | 351.38 | 377.63 | +57.17 / −36.30 |
| 50/open/1 | 117 | 47.0 | +736.02 | 374.22 | 452.10 | identical to 50/close/1 |

Entry-regime breakdown (service classifier), 30 days:

| variant | trend (n / net) | range (n / net) | high-vol (n / net) |
|---|---|---|---|
| 55/close/1 | 19 / −19 | 64 / +528 | 28 / +441 |
| 55/close/2 | 15 / +346 | 57 / +121 | 29 / +176 |
| 50/close/1 | 20 / +93 | 67 / +433 | 30 / +210 |
| 50/close/2 | 14 / +270 | 57 / −6 | 28 / +67 |

Confirm=2 does what it advertises — smaller average loser (−35 vs −42), fewer
range-tagged trades — but it also lands one bar later on every real move; over
30 days that gave up ~$300 (55) to ~$400 (50) of net and did NOT lower the
drawdown. EMA-50 is simply a bit noisier than EMA-55 (6 more trades, lower
net, higher DD).

## 3. Is "open beyond EMA" (variant 2) actually a different rule? No.

Code path checked in both places:

- EA `HalfTrendEma.mqh::ProcessClosedBar`: on the flip bar the counters are
  reset to 0 and THEN the same bar's close is counted (`m_consecAbove++`), and
  `Evaluate()` fires when `m_consecAbove >= m_confirm`. So with ConfirmCloses=1
  the flip bar itself can fire — there is no extra bar of delay after a flip.
- `scripts/backtest.py::run`: identical (reset on flip, then count this bar).

The EA only acts on closed bars. The "open" of the forming bar IS the close of
the bar that just closed (M5, same tick, except for a rare tick gap of a cent
or two). So "BUY when HalfTrend is up and a candle opens above the EMA" is
decided at exactly the same instant, on exactly the same number, as "the bar
just closed above the EMA" — it is the current rule with a different name.
The `--confirm-mode open` implementation tests `open[i+1] > EMA[i]` at the
decision instant instead of `close[i] > EMA[i]`; over 30 days it changed the
decision on 10 bars out of ~5,600 (tick gaps straddling the EMA) and moved
net P/L by $2. Not a different strategy.

The only way an "open" rule fires EARLIER is if the EA acted intrabar (on
ticks) — that would be a "price crosses the EMA" rule, which is strictly
noisier than close confirmation and is not what any of the variants above
model.

## 4. Caveats

- Replay simplifications as usual: close fills ± spread $0.20/oz, no slippage,
  no daily-loss brake, no news blackout, integer-oz sizing; balance compounds
  inside a window (30-day rows start at $4,500).
- 30 days is context, not a calibration set: 100–117 trades, and the ordering
  55 > 50 and confirm-1 > confirm-2 could flip in a different month.
  The 17-month sweeps in the earlier reports remain the reference for
  structural changes.
- The morning slice starts Friday 08-14 for indicator warm-up; Friday's +$148
  makes Monday's lots 4/7/5 oz vs the live 4/8/5.
