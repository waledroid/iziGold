# Reconcile-on-reconnect — back-fill close reports for offline basket closes

**Date:** 2026-08-12 · **Status:** user-approved design

## Problem

Broker-side stops correctly protect the account when MT5/EA is down
(fail-open by design), but the close is silent: `OnTradeTransaction` never
fires (MT5 down) or its `/trade-event` post fails (service down) — no
Telegram report, no render, no db row. Observed 08-11: a 6.1 h MT5 blackout
during which the ladder stop closed a basket −$56.18 with no report.

## Design (EA-only; zero service changes)

**Watermark:** MT5 global variable `XAU_RECON_<login>_<symbol>` holds the
**ticket of the last closing deal successfully reported** to `/trade-event`.
Deal tickets increase monotonically per account, so "unreported" ≡
"ticket > watermark".

**Reconciler** (`ReconcileOfflineCloses()` in the EA):

- Runs at **OnInit** (MT5/machine restart — the blackout case) and **once
  per 60 s from OnTimer** (service-down case, where the live post was
  dropped).
- `HistorySelect` over own deals (symbol + magic) since watermark deal's
  time (minus a small overlap), filter `DEAL_ENTRY_OUT` closing deals with
  `ticket > watermark`, **oldest first**.
- For each: post the same `/trade-event` close that `OnTradeTransaction`
  would have sent live — real fill price, lots, direction, profit
  (profit + swap + commission), ticket; reason derived from
  `DEAL_REASON`: `DEAL_REASON_SL` → `"stop-loss (reconciled)"`,
  `DEAL_REASON_TP` → `"take-profit (reconciled)"`, else
  `"closed offline (reconciled)"`. `final` = true when no own position
  remains open after that deal (same semantics as the live path).
- Advance the watermark **only after a successful post**; stop the scan at
  the first failure (service still down → retry next minute). At-least-once
  delivery, in order, no duplicates.
- **Live path integration:** `OnTradeTransaction`'s successful close post
  also advances the watermark, so in normal operation the reconciler finds
  nothing.
- **First run / migration:** watermark key absent → seed to the newest own
  closing deal ticket WITHOUT reporting (no historical spam), print once.
- Fail-open throughout: reconciler errors are logged (throttled) and never
  block trading, exits, or the heartbeat.

Service side: reconciled events arrive as ordinary close events → normal
Telegram P/L report (reason shows `(reconciled)`), render, db row, channel
mirror — all through existing paths.

## Testing

MQL5: CLI compile 0/0 + review (no unit tests possible). Live drill after
deploy: with a position open, stop the service, close the position manually
in MT5, restart the service, confirm the `(reconciled)` close report
arrives within ~60 s. Service suite as regression gate (untouched).

## izi.md

Same branch: the watermark key shape, reconciler behavior, first-run
seeding, the live drill procedure, and the 08-11 silent −$56.18 close in
history-worth-knowing.
