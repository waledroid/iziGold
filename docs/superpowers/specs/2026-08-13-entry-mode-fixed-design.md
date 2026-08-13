# Entry modes — "ADR" (current) and "FIXED" (pure trend ride)

**Date:** 2026-08-13 · **Status:** user-approved concept (picker decisions
in-session; "/mode gets all 4 buttons" correction applied)

## Concept

Two entry modes, runtime-switchable, per-basket sticky:

| | **ADR** (today's behavior, default) | **FIXED** (new) |
|---|---|---|
| Entry size | 1% equity risk over stop distance | `FixedLots` input, default **0.05** |
| Stop | HalfTrend wick extreme ± 0.75×ATR pad (broker-side) | same |
| Pyramid adds | yes (shrinking, laddered) | **none** |
| Profit target (+2% cycle) | yes | **none** |
| Profit lock (50% of peak) | yes | **none** |
| Exit | target / lock / ladder / confirmed reversal | **confirmed reversal only** (flip + EMA-55 close) or the stop |

Safety rails identical in both modes (never mode-dependent): kill switch,
daily loss brake, news blackout, spread/ADX/window/exposure gates, 23:54
flatten, `AllowLiveTrading` guard. Owner is aware one FIXED loss at 0.05
lots ≈ 1.5–2% of the account (and chose an input so size is adjustable
without code).

## Mode selection & stickiness

- EA inputs: `EntryMode` (enum `ENTRY_ADR`/`ENTRY_FIXED`, default ADR) and
  `FixedLots = 0.05` (broker min/max/step clamped at use).
- Runtime switch follows the exec-mode pattern exactly: service kv
  `entry_mode` ("adr"/"fixed", default "adr"), delivered on the heartbeat
  response (`HeartbeatResponse.entry_mode`); EA reports its current value
  in the heartbeat (`HeartbeatRequest.entry_mode`, default "adr" — old
  EAs keep working).
- **A switch applies to the NEXT entry.** An open basket finishes under
  the mode it was opened with: `TradeManager` captures the basket's mode
  at entry and persists it in a per-symbol MT5 global
  (`XAU_BASKET_MODE_<login>_<symbol>`, 0=ADR 1=FIXED, written at open,
  meaningful only while a basket exists) so a mid-trade restart cannot
  turn a FIXED ride into ADR management or vice versa.
- `Manage()` consults the BASKET's mode: FIXED → no adds, no target, no
  lock (the reversal exit and shared stop live outside `Manage()` and are
  unchanged). Sizing consults the RUNTIME mode at entry.

## Telegram — all four buttons under `/mode`

`/mode` (existing command) now replies with BOTH states and four buttons
in two rows: `🤖 AUTO` `👤 MANUAL` / `📊 ADR` `🎯 FIXED`. New callbacks
`tmode:adr` / `tmode:fixed` set the kv (owner-only via the existing
filter); confirmation edit text names the new entry mode and that it
applies from the next trade. `/config` shows `entry mode:`. Pinned help's
`/mode` line becomes "execution + entry mode" (bump
`PINNED_HELP_VERSION` → "6"). Callback edits mirror to the channel as
usual.

## Bookkeeping

- `TradeEventRequest` gains optional `entry_mode: str = ""`; EA sends the
  basket's mode on open/add/close events. `trades` table gains an
  `entry_mode` column (guarded `ALTER TABLE` migration, same pattern as
  profit/render_path migrations). No stats UI yet — data collection first.

## Backtest first (phase 1 of the plan)

Extend `scripts/backtest.py`: `--entry-mode fixed --fixed-lots 0.05` —
fixed sizing, no adds/target/lock, exit on confirmed reversal or stop.
Run FIXED vs ADR over the full 17-month window AND the last 30 days;
report net P/L, valley, trade count, avg/max winner. Numbers go to the
owner before they switch live (the feature ships with ADR as default, so
implementation is safe to land regardless).

## Testing

Service: kv round-trip, /mode four-button reply + tmode callbacks
(owner-only, mirror), heartbeat contract old+new payloads, trades
migration + entry_mode persistence. MQL5: CLI compile 0/0 + adversarial
read (basket-mode stickiness across restart; FIXED skips adds/target/lock;
ADR behavior byte-identical when nothing switches). izi.md same branch.
