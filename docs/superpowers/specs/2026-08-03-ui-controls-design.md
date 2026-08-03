# Dashboard controls — mode toggle, proposals, close-all, risk panel

**Date:** 2026-08-03
**Goal:** give `/ui` control parity with the Telegram layer, reusing the same
service-side machinery (kv `exec_mode`, `proposals` table, heartbeat command
slot). Minimal build. EA untouched.

## Endpoints (`app/main.py`)

- `POST /ui/mode` `{mode}` → validate `auto|manual` (else 400),
  `db.set_exec_mode`; heartbeat delivers it. Returns `{mode}`.
- `POST /ui/proposal/{pid}` `{action: take|skip}` → guarded
  `set_proposal_status(pid, approved|skipped, expected="pending")`; a lost
  race returns `{ok: false, status: <actual>}` (200 — the UI shows what won).
  404 unknown pid, 400 bad action. Best-effort edit of the proposal's
  Telegram message ("via dashboard") so both surfaces agree; fail-open.
- `POST /ui/close-all` → 409 if an exit proposal is already
  pending/approved/dispatched; else create an exit proposal (direction from
  `last_executed_entry` fallback BUY, price from `recent_candles`, strategy
  from latest heartbeat) and immediately approve it → next heartbeat
  dispatches `close_all`.
- `GET /ui/state` gains `mode` (kv) and `proposal` — the newest proposal in
  pending, else approved, else dispatched status; null otherwise.

## Dashboard (`app/static/dashboard.html`)

- Control bar: AUTO/MANUAL segmented toggle (live value highlighted; click
  posts `/ui/mode`), Close-all button with `confirm()`, disabled when the
  heartbeat shows no open positions.
- Proposal card (visible when `/ui/state.proposal` set): kind, direction,
  price, strategy, status; Take/Skip buttons only while status=pending.
- Risk/status badges from existing heartbeat fields: kill-switch, drawdown
  (hwm vs equity), exposure minutes, window open/closed, spread.
- All wired into the existing `state()` poll; no new polling loops.

## Safety

Strategy stays sole decision-maker (buttons approve/skip only). Localhost
binding remains the auth boundary (same as `/ui/switch`). Telegram edits are
best-effort; their failure never blocks a UI action.

## Tests (`tests/test_ui_controls.py`)

Contract tests: mode set/invalid; proposal take/skip happy path + decided
race (`ok:false`); close-all creates approved exit + 409 when repeated;
`/ui/state` carries mode + proposal. Existing suite stays green.
