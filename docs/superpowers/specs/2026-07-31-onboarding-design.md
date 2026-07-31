# Client Onboarding Page — Design Spec

**Date:** 2026-07-31
**Status:** Approved in conversation
**Builds on:** [2026-07-31-ui-design.md](2026-07-31-ui-design.md)

## 1. Goal

A simple, quick, **nonblocking** onboarding first page for the system:
collects only vital setup/KYC information, every field optional, completable
later. Built for the single local operator today, but stored as a
client-shaped profile record so real client onboarding can grow from it
without rework (stepping-stone model).

## 2. Decisions

- **Form:** dedicated page at `GET /ui/onboarding`, served by the existing
  FastAPI app, same dark styling as the dashboard. One scrollable form,
  four sections, buttons "Save" and "Skip for now" (both navigate to `/ui`).
- **Nonblocking:** no required fields. First visit to `/ui` redirects to
  `/ui/onboarding` only when NO profile row exists; both Save and Skip
  create the row, so the redirect fires at most once ever.
- **Field groups (all nullable):**
  1. *Identity & contact* — name, email, phone.
  2. *Telegram setup* — telegram_bot_token, telegram_chat_id. Applied
     **live** on save (see §4).
  3. *Risk profile* — risk_per_trade_pct, max_drawdown_pct,
     profit_target_pct, prefilled with safe recommended defaults
     (0.5 / 10 / 2, matching the EA defaults). Declared preferences only;
     the EA inputs remain the enforcer. (Window hours were removed from
     the form 2026-07-31 for simplicity; columns remain in the schema.)
  4. *Account & consent* — broker_name, account_login, account_type
     (demo/live), risk_ack (checkbox; stores boolean + timestamp when
     first checked). (experience_level removed from the form for
     simplicity; column remains.)
     Plain-language acknowledgment of automated-trading risk.

## 3. Storage

New `profile` table in the same SQLite db (single row, id=1 upsert):
all §2.3 columns nullable, plus `created_ts`, `updated_ts`,
`risk_ack_ts`. `SignalDb` methods: `get_profile() -> dict | None`,
`save_profile(partial: dict) -> dict` (only provided keys update; returns
the full row). Completion percent is computed (fields set / fields total,
excluding timestamps), not stored.

## 4. Endpoints and live Telegram apply

- `GET /ui/onboarding` → the form page (static HTML like the dashboard).
- `GET /ui/profile` → `{profile: {...} | null, completion_pct: int}`.
- `POST /ui/profile` → partial update; body contains only changed fields.
  When the saved token+chat differ from the currently active Telegram
  credentials: rebuild `app.state.telegram`, cancel and restart the poller
  and pinned-editor tasks with the new client. Profile credentials
  override `.env` at startup (lifespan reads profile first, falls back to
  settings). Empty strings clear back to the `.env` fallback.
- `GET /ui` gains the redirect-once check (server-side: profile row absent
  → 307 to `/ui/onboarding`).

## 5. Dashboard tie-in

Header line on `/ui`: client name when set; completion badge
("profile NN%") linking to `/ui/onboarding`; risk-mismatch hint when the
latest heartbeat's EA-enforced values differ from declared preferences
(compare risk % is not in heartbeat — compare what is available: the hint
covers only values present in the heartbeat, currently exposure/window;
extend as heartbeat grows). Display-only — this page never touches
trading, the kill switch, or order flow.

## 6. Safety / non-goals

- Everything fail-open; a broken profile or Telegram apply never affects
  `/analyze`, `/heartbeat`, or trading.
- No auth (localhost tool); no multi-client list, no admin UI (future C).
- No writing to `.env`; profile is db-state, `.env` is fallback.

## 7. Testing

- Profile CRUD: partial updates only touch sent fields; upsert semantics;
  risk_ack_ts set once.
- Redirect-once: no row → 307; after Skip (empty POST) → 200.
- Telegram live-apply with fake transport: save credentials → new client
  active + tasks restarted; empty strings → fallback restored.
- Completion computation; `/ui/profile` shape; onboarding page serves and
  references `/ui/profile`.
