# Brake & kill-switch awareness — implementation report (2026-08-18)

Spec: `docs/superpowers/specs/2026-08-18-brake-awareness-design.md` · branch `feat/brake-awareness`

## EA (mt5/)
- `RiskManager.mqh`: `DailyLossUsedPct()` (0–100+), `DailyLossUsedUsd()/DailyLossThresholdUsd()` (dollar
  figures for the messages), `DrawdownPct()`, `BrakeResetToday()`, `ResetDailyBrake()` (writes
  `XAU_BRAKE_RESET_<login>_<symbol>` = server date YYYYMMDD and `XAU_BRAKE_BASE_<login>_<symbol>` = realized
  P/L now; drops the read cache first). `DailyLossBreached()` now measures `TodayRealized() − BrakeBase()`;
  `BrakeBase()` returns 0 unless BRAKE_RESET == today (rollover clears implicitly; missing/stale global
  fails open). `PollAwareness(text, button)`: four latches — `XAU_BRAKE_WARN70`/`XAU_BRAKE_TRIPPED`
  (dated, re-arm on rollover or when the metric drops back), `XAU_DD80` (flag, 80% of MaxDrawdownPct,
  1-pt hysteresis on re-arm so a 5 s equity poll can't spam), `XAU_KILLWARN` (flag, re-arms when KILL is
  cleared). Messages exactly per spec; the kill switch is not resettable from Telegram.
- `UiApi.mqh`: `PostNotify(text, string button = "")` — "exit" also sends the legacy `exit_button:true`;
  heartbeat JSON gains `daily_loss_pct` + `brake_reset` (defaulted params, old call sites unaffected).
- `XauAssistant.mq5`: FIXED target alert passes "exit"; OnTimer loops `PollAwareness` → `PostNotify`;
  `cmd == "reset_brake"` → `ResetDailyBrake()` + `PostProposalResult(ok, "brake reset for today")`.
  Guard level mirrors close_all (no live-account check — a reset opens no order; every entry path still
  runs AllowLiveTrading + CanEnter).
- `XauMaintenance.mq5`: interpret-only rows for the six new keys (no reset checkboxes).
- Compile (MetaEditor CLI, all changed files copied to the data folder):
  EA `Result: 0 errors, 0 warnings, 3096 ms elapsed`; XauMaintenance `Result: 0 errors, 0 warnings, 494 ms elapsed`.

## Service (service/app)
- `models.py`: `NotifyRequest.button: str = ""` (keeps `exit_button`); `HeartbeatRequest.daily_loss_pct`,
  `brake_reset` (defaults → old EA payloads validate).
- `telegram.py`: `BRAKE_RESET_KB()` (`brakereset:1`), `BRAKE_RESET_DONE_TEXT`; `handle_callback(data, app,
  message_id=None)` + `brakereset:` branch (pre-approved `reset_brake` proposal, guarded pending/approved/
  dispatched, stores the tapped message id); `/status` protection line ` · daily loss N%` + ` (brake reset
  today)` (not redacted).
- `main.py`: poller passes `message_id`; heartbeat maps kind `reset_brake` → `{"cmd":"reset_brake"}`;
  `/proposal-result` edits the tapped notice to the confirmation (or `🚫 brake reset failed: …`); `/notify`
  button selector; TTL sweep edit handles reset_brake rows.
- Tests appended: `test_entry_mode.py` (button mapping ×4), `test_exit_button.py` (brakereset round trip,
  failure edit, old/new heartbeat fields), `test_telegram_commands.py` (/status ×3).
  Full fast suite: 434 passed, 1 deselected.

## izi.md
§3 risk gates (awareness + reset semantics + new keys), §4 commands (brakereset), §6 runbook (XauMaintenance
key list), §7 history entry.

## Concerns / notes
- Live-account guard: close_all has none, so reset_brake has none either (per "same as close_all"); safe
  because a reset never opens an order and every entry path keeps its own guards.
- DD warning uses a 1-pt hysteresis on re-arm (spec says "drops back below"): deliberate anti-spam for the
  5 s equity poll; brake latches re-arm exactly at the line (their metric only moves on closed deals).
- A dashboard `/ui/state` will briefly list a `reset_brake` proposal row while in flight (harmless).

## Review round 1 (reviewer findings) — addressed in follow-up commit
- I-1: stale [Reset] button — EA `reset_brake` branch now refuses unless `DailyLossUsedPct() >= 70`
  (`/proposal-result` ok=false "brake at N% — nothing to reset" → renders `🚫 brake reset failed: …` on the
  tapped message); service `handle_callback brakereset:` pre-checks the latest heartbeat's `daily_loss_pct`
  (< 70, no heartbeat, or old EA → toast "brake at N% — nothing to reset", no proposal). Tests for both layers.
- M-1: success detail from the EA "Brake reset for today — re-arms after another 3.0%" (MaxDailyLossPct);
  service renders `🔓 <detail>` (BRAKE_RESET_DONE_TEXT constant removed).
- M-2: /status → ` · daily loss N% since reset` when brake_reset.
- M-3: OnTimer awareness loop bounded to 4 iterations.
- M-5: BrakeBase() clamps to ≤0 and treats a base deeper than the balance as no reset.
- Gates: EA compile `Result: 0 errors, 0 warnings, 3316 ms elapsed`; pytest 435 passed, 1 deselected;
  main service restarted, /health ok.
