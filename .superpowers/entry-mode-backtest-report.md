# Entry-mode backtest: current system (ADR) vs FIXED lots

Date: 2026-08-13. Data: `bars_max.json` — 99,999 M5 bars, 2025-03-14 16:25 to
2026-08-12 17:45 server time (~17 months). Params for every run: balance
$4,700, risk 1%, ADX 10, exposure 360 min/day, exit scheme target-exit.
Replayed with `scripts/backtest.py --entry-mode {adr,fixed}`.

## Plain-language summary

**What was tested.** The current system ("ADR") sizes each trade at 1% risk,
adds into winners, takes profit at 2%, and locks in half of the peak profit.
The new "FIXED" idea always trades the same size (0.05 lots), never adds,
never takes profit early — it rides every trade until the trend confirms a
reversal, the stop is hit, or the end-of-day flatten.

**The last 30 days (the market the system was tuned for):** both make money,
but the current system makes more — **+$1,132 vs +$645 for FIXED**. The
current system also wins more often (46% vs 34%) with a similar worst dip
(~$470 vs ~$504).

**The full 17 months:** both lose money, and the losses are big either way.
The current system loses −$3,100; FIXED(0.05) loses less, −$1,006, but its
worst peak-to-trough dip is deeper (−$6,040 vs −$4,717). Honestly, **both
dips are bigger than the $4,700 account** — over the full window either
version would have busted the account at its worst stretch. The strategy's
current settings were tuned on recent weeks; the long window includes market
regimes it was never calibrated for.

**FIXED at 0.10 lots (the original size): do not.** On paper it ends the 17
months at −$2,013, but along the way the balance goes to **−$2,579 — below
zero**. A real account would have been margin-called and wiped out long before
the "final" number. The worst dip is $12,080 on a $4,700 account.

**What FIXED actually changes:** fewer trades (1,308 vs 2,161), each held
about twice as long (~2.2h vs ~1h), much bigger average winners ($91 vs $41)
and a monster best trade (+$1,138 vs +$238) — but a lower win rate (27% vs
36%) because winners are never banked; many rides that would have hit the
profit target give the gain back before the reversal confirms.

**Bottom line:** on the recent, tuned-for market the current ADR system beats
FIXED on profit, win rate, and drawdown. FIXED's one genuine edge shows only
over the long ugly window (smaller total loss), and it comes with deeper
dips. FIXED at 0.10 lots is account-ending on this history.

## Results matrix

| Run | Net P/L | Trades | Win % | Max open-equity valley | Avg winner | Max winner | Avg hold |
|---|---|---|---|---|---|---|---|
| ADR — full 17mo | −$3,100.21 | 2,161 | 36.2% | $4,717.23 | $41.12 | $237.75 | 58 min |
| ADR — last 30d | +$1,132.33 | 116 | 45.7% | $468.13 | $76.05 | $238.36 | 68 min |
| FIXED 0.05 — full 17mo | −$1,006.41 | 1,308 | 26.9% | $6,040.13 | $90.68 | $1,137.70 | 132 min |
| FIXED 0.05 — last 30d | +$644.74 | 65 | 33.8% | $503.62 | $85.18 | $397.25 | 154 min |
| FIXED 0.10 — full 17mo | −$2,012.82 | 1,308 | 26.9% | $12,080.27 | $181.37 | $2,275.40 | 132 min |

Extra detail:

- Exit mix, ADR full: 1,113 stop / 415 profit target / 396 profit lock /
  190 reversal / 47 flatten. FIXED full: 670 stop / 606 reversal / 32
  flatten (no target, no lock, no adds — by design).
- FIXED baskets verified single-leg everywhere (5 oz at 0.05 lots, 10 oz at
  0.10); FIXED 0.10 is exactly 2x FIXED 0.05 trade-for-trade, as expected
  with no balance-dependent sizing.
- Balance path minimum, full window: FIXED 0.05 bottoms at $1,061;
  FIXED 0.10 bottoms at **−$2,579** (account wiped in reality).
- Max realized drawdown (closed-balance): ADR full $4,661; FIXED 0.05 full
  $5,897; FIXED 0.10 full $11,793.

## Caveats (same as every replay with this script)

- Bar-close granularity, flat $0.20/oz spread, no slippage, no margin model
  — the FIXED 0.10 run especially would have hit margin limits long before
  the numbers above.
- The daily loss brake (MaxDailyLossPct) and the news blackout (NewsGuard)
  are NOT modeled — live results would refuse some of these entries, so the
  replay is optimistic around losing days and high-impact events.
- The safety rails above apply identically to both modes; the comparison
  between modes is like-for-like.

## Reproduce

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --balance 4700 --risk 1 --adx 10 \
    --expo 360 --source bars_max.json                      # ADR full
... --days 30                                              # ADR 30d
... --entry-mode fixed --fixed-lots 0.05                   # FIXED full
... --entry-mode fixed --fixed-lots 0.05 --days 30         # FIXED 30d
... --entry-mode fixed --fixed-lots 0.10                   # FIXED 0.10 full
```
