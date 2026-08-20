# Would skipping AI-disagreed signals have been more profitable?

Analysis date: 2026-08-13. Data: `service/xau_assistant.db` (signals 2026-08-03 01:05 → 2026-08-12 22:45 server time, M5 rows only — M15 rows from the 08-11 timeframe incident were deliberately purged). Analysis only; nothing in live rules, EA, or service was changed.

## TL;DR

**No — on executed trades, skipping every AI-disagreed entry would have made $86.67 LESS ($373.84 → $287.17).** But the forward-looking stats on all resolved signals lean the other way (AI-disagreed signals moved against the trade on average). The samples are far too small to enable veto mode; the divergence itself is the most informative result: trade management (pyramids, profit target, stops) converts entries the raw 16-bar move would call "bad" into winners.

## Method

### Data joins

- `trades.ts` is UTC; `signals.bar_time` is broker server time (GMT+3 summer). A signal's bar closes at `bar_time + 300 s`; the matching trade open lands at `bar_time + 300 − 3 h` in UTC (or later, up to ~40 min for MANUAL-mode Telegram approvals, e.g. the 08-07 02:16 UTC open matching signal #60 at 04:35 server).
- Baskets were reconstructed from the trades table: each `open` starts a basket; subsequent `add`/`close` rows attach until the next `open`. Basket realized P/L = sum of its close rows' `profit`.
- Each basket entry was matched to the nearest preceding active-strategy BUY/SELL signal with the same direction within a −60 s…+2 h window. All matches were hand-verified.

### Data caveats (stated up front)

1. **4 of 19 baskets have no signal row** — their signals were among the purged M15 rows (08-11 SELL +23.96, 08-11 BUY, 08-12 05:45 SELL −41.97, 08-12 07:45 BUY −30.40). Their AI grade is unknowable; they count identically in both scenarios.
2. **The 08-11 BUY basket (11:00 UTC open) has no close row in the DB** — that is the known blackout close (−$56.18, broker-side, never reported; predates reconcile-on-reconnect's watermark). All dollar totals below are DB-realized only and exclude it; including it changes both scenarios equally since the entry is unclassifiable anyway.
3. One basket (08-04 BUY +94.81) had `ai_available=0` (service returned neutral) — unclassifiable, counts in both scenarios.
4. `outcome_move` is the close 16 bars (80 min) after the signal bar minus the signal price — a fixed-horizon proxy, NOT the system's P/L. It ignores pyramiding, the ATR-padded stop, profit target/lock, and exits. This mismatch is real and explains much of the divergence below.

## Definitions of "AI disagreed" tested

All verdicts in the DB are `neutral`. The live veto rule (`verdict='conflict'`) requires confidence ≥ 0.6 (`confirm_threshold` in `app/config.py`, used by `app/verdict.py`); the maximum confidence ever observed is **0.42**. So:

- **Def A — `verdict='conflict'` (the actual live rule):** fires on **0 of 86 signals**. As currently thresholded, enabling veto mode would change nothing at all.
- **Def B — AI direction opposite the signal, any confidence** (bearish vs BUY / bullish vs SELL, `ai_available=1`): the broadest sensible reading.
- **Def C — AI opposite with confidence ≥ 0.20** (upper half of the observed confidence range): "conviction disagreement".

("Not aligned including neutral" was also considered; only 2 resolved active signals were AI-neutral, so it adds nothing over Def B.)

## Part 1 — Executed baskets (realized dollars)

19 baskets total; DB-realized total **+$373.84**.

| Entry (UTC) | Dir | Basket P/L | AI said | Conf | Disagreed? |
|---|---|---|---|---|---|
| 08-03 18:45 | BUY | +102.82 | bearish | 0.04 | yes |
| 08-04 11:15 | BUY | +94.81 | (AI unavailable) | — | n/a |
| 08-04 18:30 | SELL | −23.17 | bullish | 0.03 | yes |
| 08-05 09:25 | SELL | −7.70 | bullish | 0.14 | yes |
| 08-05 11:10 | BUY | +91.59 | bullish | 0.32 | no |
| 08-05 16:40 | BUY | +26.53 | bullish | 0.19 | no |
| 08-06 02:50 | SELL | +19.11 | bullish | 0.18 | yes |
| 08-06 05:55 | BUY | −16.68 | bullish | 0.28 | no |
| 08-06 06:35 | SELL | −20.16 | bearish | 0.02 | no |
| 08-06 10:15 | SELL | +12.02 | bullish | 0.11 | yes |
| 08-07 02:16 | BUY | +9.47 | bearish | 0.03 | yes |
| 08-07 04:02 | BUY | +99.93 | bullish | 0.12 | no |
| 08-10 17:35 | BUY | +120.96 | bullish | 0.21 | no |
| 08-11 05:30 | SELL | +23.96 | (purged M15) | — | n/a |
| 08-11 11:00 | BUY | (no close row; −56.18 offline, off-DB) | (purged M15) | — | n/a |
| 08-12 05:45 | SELL | −41.97 | (purged M15) | — | n/a |
| 08-12 07:45 | BUY | −30.40 | (purged M15) | — | n/a |
| 08-12 09:20 | BUY | −61.40 | bullish | 0.18 | no |
| 08-12 11:15 | SELL | −25.88 | bullish | 0.17 | yes |

Results per definition:

| Definition | Baskets skipped | P/L removed | As-is → with skips | Verdict-would-have |
|---|---|---|---|---|
| A: verdict='conflict' (live rule) | 0 | $0.00 | $373.84 → $373.84 | no change |
| B: opposite, any conf | 7 | +$86.67 | $373.84 → **$287.17** | **$86.67 WORSE** |
| C: opposite, conf ≥ 0.20 | 0 | $0.00 | $373.84 → $373.84 | no change |

Def B detail: the 7 disagreed baskets went 4 winners / 3 losers, net +$86.67. The single biggest was +$102.82 — vetoed by a **0.04-confidence** "bearish" call, i.e. a coin-flip grade would have deleted the best trade of the whole log. All 7 executed disagreements had confidence ≤ 0.18, which is why Def C skips nothing.

## Part 2 — Forward-looking outcomes (all resolved signals)

Favorable move = `outcome_move` for BUY, `−outcome_move` for SELL, in $/oz points over the 16-bar horizon.

**Active strategy only (halftrend_ema_v1), 46 resolved with AI grade:**

| Group | n | Mean favorable move | ± SE | Sum | Win rate |
|---|---|---|---|---|---|
| AI agreed | 21 | +5.19 | 4.54 | +109.0 | 11/21 |
| AI disagreed | 23 | −3.20 | 2.80 | −73.6 | 10/23 |
| AI neutral | 2 | +1.82 | — | +3.6 | 1/2 |

Gap = +8.39 points, Welch t ≈ **1.57** — not significant at conventional levels.

**Active + shadow (boll_stochrsi_v1), 66 resolved:**

| Group | n | Mean | ± SE | Sum | Win rate |
|---|---|---|---|---|---|
| AI agreed | 34 | +4.53 | 3.19 | +154.1 | 17/34 |
| AI disagreed | 30 | −4.13 | 2.25 | −123.8 | 11/30 |

Gap = +8.66 points, t ≈ **2.22** — marginally significant (~p 0.03), but the shadow strategy never trades, and pooling two strategies to reach significance is exactly the kind of forking-paths move calibration is supposed to prevent.

High-conviction disagreements (conf ≥ 0.20): n = 6, mean −0.22, 3/6 wins — flat, no information either way at this sample size.

AI standalone directional accuracy: 57.0% over 79 graded+resolved signals; 61.9% over the 21 with conf ≥ 0.20.

## Reconciling the two views

The forward stat says AI-disagreed *entry bars* drift against the trade over the next 80 minutes. The executed record says those same entries netted +$86.67. Both are true because the system does not hold a naked 16-bar position: the ATR-padded stop caps disagreed losers small (−23.17, −7.70, −25.88), while pyramiding + profit target let disagreed winners run large (+102.82). Trade management is currently doing the job a veto would try to do, and doing it while keeping the winners.

Also note: every executed disagreement carried confidence ≤ 0.18. In this log, "the AI disagreed" almost always meant "the AI mildly leaned the other way with near-zero conviction". There is no evidence yet about what a *confident* disagreement means on executed trades, because none has coincided with an executed entry.

## Part 3 — Sample-size verdict

**Not enough data to enable veto mode — and the point estimate on executed trades says veto would have cost money.**

- Executed AI-disagreed baskets: **n = 7**. Directionally against veto (+$86.67 kept by trading through them).
- Forward-looking disagreed (active): n = 23, t ≈ 1.57. Suggestive for the AI, not significant.
- The live conflict rule (conf ≥ 0.6) has fired **zero times in 86 signals** — enabling veto mode today is a literal no-op, and any *effective* veto would require lowering `confirm_threshold` into a region (≤ 0.2) where the only executed evidence says it destroys profit.

**Concrete decision threshold** — revisit when ALL of:

1. ≥ 50 resolved AI-disagreed active-strategy signals (currently 23) with the forward gap still ≥ 2 SE, AND
2. ≥ 20 executed AI-disagreed baskets (currently 7) with their net P/L actually negative, AND
3. ≥ 20 resolved disagreements at conf ≥ 0.20 (currently 6) so a threshold, if any, can be placed where the AI has demonstrated conviction (its 61.9% accuracy at conf ≥ 0.20 vs 57% overall hints confidence is mildly calibrated).

At current signal rates (~5–6 active entry signals/day, ~50% disagreed, ~2 baskets/day), that is roughly **3–4 more weeks of collection**. Interim lean: *promising for the forecaster as a grader, negative for it as a gatekeeper* — keep MODE=grading, keep logging, re-run this exact analysis then.

## Reproduction

All queries run via `service/.venv/bin/python` + `sqlite3` against `service/xau_assistant.db`. Basket construction: sequential scan of `trades` ordered by ts (open starts basket, add/close attach). Signal matching: nearest preceding same-direction active BUY/SELL signal with trade-ts(server) − bar-close ∈ [−60 s, +2 h]. `ai_correct` semantics per `app/db.py::resolve_outcomes` (16-bar horizon, sign of move vs AI direction; neutral grades unresolved by design).
