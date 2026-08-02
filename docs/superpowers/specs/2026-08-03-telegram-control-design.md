# Telegram control layer — proposals, buttons, mode, strategy, config

**Date:** 2026-08-03
**Goal:** turn the Telegram bot from a one-way alert firehose into the
assistant's remote control: quiet by default, interactive trade proposals
with approve/skip buttons, runtime AUTO/MANUAL toggle, strategy switching,
and a config readout.

## Decisions (from brainstorming)

- Buttons **approve or skip the strategy's proposal** — never pick a
  direction. The strategy stays sole decision-maker (spec rule 1).
- Proposal validity: **as long as the strategy still holds that stance**;
  it expires only when the active strategy signals EXIT or the opposite
  direction on a later bar (user's explicit rule). Message edits to ⌛.
- Mode (AUTO/MANUAL) lives service-side, toggled from Telegram, delivered
  to the EA via the heartbeat response. EA `AllowLiveTrading=false` guard
  is unchanged and unbypassable on real accounts.
- Telegram inline buttons cannot be colored; 🟢/🔴 emoji labels instead.
- Fail-open everywhere: Telegram down never blocks the EA or the service.

## Alert diet

`/analyze` stops calling `send_alert` per signal. Telegram sends only:
1. entry proposals (MANUAL) with buttons,
2. exit proposals (MANUAL) with buttons,
3. execution notifications (both modes; existing /trade-event screenshots),
4. command replies.
Shadow strategies never alert (already true — only the EA's active signal
reaches `req.signal`). Analysis grades remain in SQLite + dashboard.

## Execution mode

- Stored in the existing `kv` table, key `exec_mode`, values `auto|manual`;
  default `manual` when unset.
- `HeartbeatResponse` gains `mode: Literal["auto","manual"]` and
  `commands: list[Command]` (see below). EA parses both.
- EA: runtime mode from heartbeat **overrides** the `ExecutionMode` input
  (input becomes the pre-first-heartbeat default). In AUTO the EA executes
  at the bar as today; in MANUAL it does not execute — the service raises a
  proposal instead.
- `/mode` command → reply with current mode + inline buttons `AUTO`/
  `MANUAL`; tapping stores the kv value and edits the message. Mode changes
  also announced ("mode → AUTO").

## Proposals

New SQLite table `proposals`:
`id, created_ts, kind ('entry'|'exit'), direction ('BUY'|'SELL'),
strategy_id, price REAL, signal_id (FK signals.id, nullable),
status ('pending'|'approved'|'executed'|'skipped'|'expired'|'blocked'),
tg_message_id INTEGER, decided_ts, executed_ts`.

Lifecycle (all service-side, driven by the existing `/analyze` flow):
- MANUAL + active-strategy entry signal → insert proposal(kind=entry) +
  send Telegram message (direction, price, AI grade/verdict, regime) with
  buttons 🟢 Take trade / 🔴 Skip (`callback_data` = `prop:<id>:take` /
  `prop:<id>:skip`). Only ONE pending entry proposal at a time — a new
  entry signal in the same direction while one is pending refreshes
  nothing; opposite direction or EXIT expires it (below).
- MANUAL + EXIT signal while the EA has an open position → proposal
  (kind=exit) with 🔴 Exit now / ⏸ Hold. (`Hold` = skip.)
- Expiry check on every `/analyze` from the active strategy: a pending
  `entry BUY` expires when the signal is `EXIT` or `SELL`; mirror for SELL.
  A pending `exit` proposal expires when a new entry signal fires (stance
  changed). Expired → status + message edit ⌛.
- Callback handling in the poller: `callback_query` updates are dispatched
  like commands (same single-chat filter, via `from.id`); `take` on a
  pending proposal → status `approved` + command queued; `skip` → status
  `skipped`; anything on a non-pending proposal → answerCallbackQuery
  "already <status>". Always `answerCallbackQuery` to stop the spinner.
- Command queue: `commands` in the next heartbeat response, JSON list:
  `{"cmd":"execute","proposal_id":N,"direction":"BUY"}` or
  `{"cmd":"close_all","proposal_id":N}`. Delivered once (mark approved →
  dispatched when included in a heartbeat response; EA acks implicitly via
  /trade-event or an explicit `/proposal-result` POST `{proposal_id,
  ok, detail}`).
- EA execution: on heartbeat commands, `execute` → `TradeManager` opens in
  the given direction using the standard risk pipeline at current market
  price (risk checks may refuse → POST `/proposal-result ok=false detail=
  reason`); `close_all` → TradeManager CloseAll. Results update proposal
  status (`executed` / `blocked`) and edit the Telegram message
  (✅ executed @price / 🚫 blocked: reason).
- Restart-safe: proposals and mode live in SQLite; the pinned/poller tasks
  already restart with the app.

## Commands (poller `handle_command` additions)

- `/mode` — current mode + AUTO/MANUAL buttons.
- `/strategy` — buttons per registered strategy (ids from the latest
  heartbeat's `active_strategy` + the shadows list of the latest signals;
  simplest reliable source: distinct `strategy_id` from `signals` plus the
  heartbeat's active), active one marked ●; tap → same path as
  `/ui/switch` (pending switch applied by EA at next bar) + edit.
- `/config` — mode, active strategy, AI settings (forecaster, horizon,
  confirm threshold, service mode grading/veto), last-heartbeat risk state
  (balance, equity, kill switch, window open, spread, exposure minutes),
  and EA liveness (heartbeat age).
- `/status` — unchanged.

## Files touched

- `service/app/models.py` — HeartbeatResponse (mode, commands), new
  ProposalResult model.
- `service/app/db.py` — proposals table + CRUD, kv helpers for exec_mode.
- `service/app/telegram.py` — inline keyboards (sendMessage reply_markup,
  editMessageText, answerCallbackQuery), callback dispatch, new commands.
- `service/app/main.py` — /analyze proposal/expiry logic, alert-diet,
  /heartbeat command delivery, `/proposal-result` endpoint.
- `mt5/Include/XauAssistant/UiApi.mqh` — parse `mode` + `commands` array,
  POST /proposal-result.
- `mt5/Experts/XauAssistant.mq5` — runtime mode obedience, command
  execution hook in OnTimer (execution itself deferred to next tick/bar
  boundary safe path: execute immediately in OnTimer via TradeManager, as
  trade ops are allowed in timer context).
- Tests: proposals lifecycle (create/approve/skip/expire/block), mode kv,
  heartbeat contract with commands, callback dispatch (transport-mocked),
  alert-diet (no send on plain signals).

## Safety rails

- Single-chat filter for messages AND callbacks.
- One pending entry proposal max; commands delivered exactly once.
- EA-side risk manager remains the final gate on every execution.
- All Telegram sends best-effort; proposal rows exist regardless so the
  dashboard can show them later (out of scope now).
