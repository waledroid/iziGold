# Telegram broadcast channel + live trade ticker

**Date:** 2026-08-11 · **Status:** user-approved design (picker decisions in-session)

Two service-side features, one build. **No EA/MQL5 changes** — everything is
driven by data the service already receives (the 5 s `/heartbeat` carries
equity, balance, floating P/L, and the full position list).

## User decisions (locked)

1. **Live ticker:** one self-editing message per trade cycle (not periodic
   new messages). Silent edits, no notification spam.
2. **Channel scope:** trades only — **no account figures**. Privacy filter:
   hide balance / equity / drawdown / high-water mark everywhere; keep
   trade-level info (prices, lots, per-leg + basket floating P/L in dollars,
   realized P/L per trade).
3. **Command mirroring:** yes — the owner's commands and the bot's replies
   are mirrored to the channel, passed through the same privacy filter.

## Feature 1 — Live trade ticker

State machine in the `/heartbeat` handler (service-side only, in-memory
`app.state.live_ticker`):

- **flat → open** (previous heartbeat had no positions, this one does):
  build the LIVE text, `send_message` to the owner chat, remember
  `(message_id, last_text, last_edit_ts)`. If a channel is linked, send the
  redacted variant there too and remember its message id separately.
- **open → open**: rebuild the text; if it differs from `last_text` and
  ≥ `TICKER_MIN_EDIT_S` (5 s) since the last edit, `edit_message` in place.
  Skipping identical text avoids Telegram's "message is not modified" 400
  and keeps us far from rate limits. Owner and channel messages are edited
  independently (each has its own last_text).
- **open → flat**: final edit freezes the message with a `CLOSED` stamp and
  the last known floating P/L. The existing trade-close report (realized
  P/L) remains the authoritative final word — the ticker does not duplicate
  or replace it.
- **Service restart mid-trade**: ticker state is in-memory only. After
  restart the first heartbeat with positions looks like flat → open, so a
  fresh LIVE message is posted; the old one simply stops updating. Fail-open,
  no persisted state to corrupt.

Owner-chat format (channel variant drops the Equity line):

```
📊 LIVE — SELL basket (auto)
Equity     $4,785.18
Floating   +$65.40

SELL 0.02 @ 4391.60   +$54.02
SELL 0.01 @ 4377.08   +$12.49
SELL 0.01 @ 4363.48   −$1.11

updated 14:32:05
```

Direction/lots/price/per-leg P/L come from `HeartbeatRequest.positions`;
basket floating from `floating_pl`; equity from `equity`. Timestamp is the
service clock (server-agnostic wall time, HH:MM:SS).

All ticker Telegram calls are fail-open: a failed send/edit is logged and
skipped; the next heartbeat retries naturally. Ticker work must never block
or delay the heartbeat response (commands ride on it).

## Feature 2 — Broadcast channel

### Linking (one-time, owner-approved)

- The user creates a Telegram channel and adds the bot as **admin with post
  rights**, then posts anything in the channel.
- `getUpdates` then delivers a `channel_post` update. The poller (currently
  ignores these) captures `chat.id` + `chat.title` and — if no channel is
  linked and no link offer is pending — sends a confirmation to the **owner
  chat**: "Link channel «title» (id)?" with ✅ Link / ❌ Ignore buttons
  (callback data `chan:link:<id>` / `chan:ignore:<id>`; the pending offer is
  held in `app.state`, one at a time).
- Only the owner's ✅ (existing `from_id == chat_id` filter) stores the id in
  the DB kv (`channel_id`). A stranger's channel can never self-link.
- `/channel` command: shows link state; `/channel unlink` clears the kv.
  Added to the pinned command reference (bump `PINNED_HELP_VERSION`).

### Mirroring

- `TelegramClient` gains channel-aware sends: `send_channel(text)`,
  `send_channel_photo(caption, png)`, `edit_channel_message(...)` — same
  transport, `chat_id` overridden with the linked channel id. The channel id
  is passed in by callers from the DB kv (client stays credential-only).
- Every outbound owner message/photo gets an explicit **channel variant**
  provided at the call site (proposals, trade open/close reports + charts,
  rejections, mode/strategy changes, ticker). No regex scrubbing — each
  message type builds its redacted text deliberately. A `None` channel
  variant means "owner-only" (e.g. onboarding, channel-link confirmations,
  pinned help).
- **Command mirroring:** when the poller handles an owner command, it also
  posts to the channel: `👤 /status` followed by the reply's channel
  variant. `handle_command` returns gain an optional channel-safe text —
  produced by the same redaction helpers.
- **Privacy filter (redaction rules):** balance, equity, drawdown %, HWM →
  replaced with `•••` or the line dropped. Kept: prices, lots, direction,
  per-leg/basket floating P/L, realized per-trade P/L, session label,
  regime, strategy, mode, EA connection state. `/bal`'s mirror is therefore
  mostly `•••` by design.
- **No controls in the channel, ever:** channel sends never attach
  `reply_markup`. Approve/exit buttons exist only in the owner chat.
  Channel posts by members (if any) are never parsed as commands — the
  existing `msg_chat_id == chat_id` command filter already guarantees this;
  the only new channel_post handling is the link-capture above.
- **Fail-open:** channel send/edit failures are logged and dropped; they
  never delay or block owner delivery (owner send happens first, channel
  second).

## Storage / config

- DB kv: `channel_id` (empty/absent = no channel linked). No `.env` change,
  no schema migration (kv table exists).
- Constants: `TICKER_MIN_EDIT_S = 5` in `main.py` next to the other TTLs.

## Testing

Fake-transport unit tests (existing pattern):

- Ticker: flat→open posts exactly one LIVE message per cycle; open→open
  edits only on text change and respects min-edit interval; open→flat
  freezes with CLOSED; restart mid-trade posts a fresh message; send
  failure doesn't break the heartbeat response.
- Channel: link flow (channel_post → owner confirm → kv set; stranger
  callback ignored; `/channel unlink` clears); mirroring sends owner-first
  then channel; redaction of each message type (status, bal, proposal,
  open/close report, ticker) — assert no balance/equity/HWM figures appear
  in any channel text; no reply_markup ever in channel payloads; channel
  send failure leaves owner delivery intact.
- Contract: `/heartbeat` response shape unchanged.

Suite stays green; known flake note unchanged.

## izi.md

Same-branch update: new `/channel` command, link/unlink procedure, ticker
behavior, privacy-filter rules, and the "no controls in channel" invariant.
