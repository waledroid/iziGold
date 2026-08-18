# Brake & kill-switch awareness — Telegram warnings before they trip, with a [Reset] for the day

**Date:** 2026-08-18 · **Status:** owner-approved concept ("⚠️ Daily loss brake
at 70% … with a [Reset] button for that day"); bounded EA + service change.

## Messages (owner chat; channel gets the text WITHOUT buttons — structural)

1. `⚠️ Daily loss brake at 70% (−$100 of −$143) — one more loss ends the day`
   with inline button **[🔓 Reset brake for today]** (owner-only callback).
2. `🛑 Daily loss brake TRIPPED — no new entries until midnight (server)`
   with the same **[🔓 Reset brake for today]** button.
3. `⚠️ Drawdown 8.0% from peak — kill switch arms at 10%` (no button; the
   kill switch stays a manual, deliberate reset via XauMaintenance).
4. `⛔ KILL SWITCH TRIPPED — trading halted; reset via XauMaintenance` (exists
   in spirit today via /status; now pushed proactively).

Each fires ONCE per crossing (EA-side latches, reset when the metric falls
back below the threshold or at the daily rollover), through the existing
fail-open `/notify` path — trading logic untouched.

## The [Reset] semantics (the safety-rail part)

- Tapping = an owner-approved command riding the SAME rails as `close_all`:
  callback → pre-approved proposal (kind `reset_brake`) → next heartbeat
  delivers `{"cmd":"reset_brake"}` → EA sets a per-symbol MT5 global
  `XAU_BRAKE_RESET_<login>_<symbol>` = today's server date + the realized
  P/L at reset time (`XAU_BRAKE_BASE_…`).
- Effect: `DailyLossBreached()` measures today's loss **from the reset
  point** (realized − base) instead of from midnight — so the brake re-arms
  at a FURTHER 3% loss (a reset can't become unlimited bleeding), and the
  70% warning re-arms too. Midnight rollover clears both globals.
- Confirmation edit on the tapped message: `🔓 Brake reset for today —
  re-arms after another 3% (−$1xx)`; `/status`'s protection line shows
  `brake reset today (−$X since reset)`.
- Kill switch is NOT resettable from Telegram (deliberate: it's the last
  line; the maintenance script's explicit action stays the only way).

## Where it lives

- EA: `RiskManager` gains `BrakeUsedPct()` (0–100+), the reset-base globals,
  and the four latches; `OnTimer` (5 s) evaluates + notifies via
  `PostNotify(text, exitButton=false)` — the message needs a DIFFERENT
  button (reset, not exit) → `PostNotify` gains an optional `button`
  selector param ("exit" | "reset_brake" | ""); service `/notify` maps it
  to the right keyboard. Heartbeat gains `daily_loss_pct` + `brake_reset`
  (float, bool) for `/status`.
- Service: `NotifyRequest.button`; callback `brakereset:` → proposal kind
  `reset_brake` (owner-only via existing filter) → heartbeat command;
  `/status` protection line addition. Channel mirror text-only.
- Tests: notify button mapping, callback → proposal → heartbeat command
  round-trip, /status line. MQL5: compile 0/0 + adversarial read (latch
  once-per-crossing, rollover clears, reset math never loosens the kill
  switch, fail-open on missing globals).
