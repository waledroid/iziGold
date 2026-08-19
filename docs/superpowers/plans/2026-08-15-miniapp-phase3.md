# Mini App Phase 3 — Auth + ngrok + Telegram Wiring Implementation Plan

> **Historical note (2026-08-19):** every `9001` in this plan is the
> port as it was when the plan ran. The mini-app port is now
> `MINIAPP_PORT` in `service/.env` (default **9101**).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The mini app goes live end-to-end: Telegram-signed viewer auth (owner + linked-channel members), the ngrok static-domain tunnel, and the [📈 Live Chart] button + `/chart` repoint — with the existing bot untouched otherwise.

**Architecture:** `viewer_allowed()` gets its real body (initData HMAC per Telegram's algorithm + owner/membership authorization with a 10-min cache); the WS call site passes its query params. ngrok runs in WSL against 127.0.0.1:9001, started by an idempotent setup phase. The main service's ticker/`/chart` gain a `web_app` inline button (private chat — carries initData natively; no BotFather needed); the channel copy gets a `t.me` direct-link text line once the owner registers the app with BotFather (until then, channel text says "use /chart").

**Tech Stack:** hmac/hashlib (stdlib) for initData validation; httpx for Bot API `getChatMember`; ngrok v3 binary (WSL, ~/.local/bin); existing Telegram plumbing.

**Spec:** `docs/superpowers/specs/2026-08-14-live-chart-miniapp-design.md` (Phase 3 + the ngrok amendment + the Phase 1/2 checklists in izi §8)

## Global Constraints

- Auth default-deny: no initData → 403/4403; bad hash → 403; stale `auth_date` (>24 h) → 403; valid signature but neither owner nor channel member → 403. `MINIAPP_DEV_BYPASS` keeps working but stays inline-env-only (never `.env`) — izi already documents why.
- Phase 1/2 checklist items land here (from izi §8): `docs_url=None, redoc_url=None`; auth-free `GET /healthz` (`{"ok": true}`) replacing setup's `/openapi.json` liveness probe; REST initData moves to header `X-Telegram-Init-Data` (page sends it; `?initData=` query stays accepted for the WS only).
- Secrets: bot token via the same resolution the main service uses (profile db read-only, `.env` fallback); `NGROK_AUTHTOKEN` + `MINIAPP_PUBLIC_URL` already in `service/.env` (present; never committed). ngrok config never in git.
- Owner id = the configured Telegram chat id; channel id = kv `channel_id` (unlinked ⇒ owner-only). `getChatMember` results cached 10 min (both grants and denials); Bot API failure ⇒ fail-CLOSED for members, owner still admitted (owner check is local).
- Main-bot invariants unchanged: channel sends never carry reply_markup (structural); ticker/proposal/other flows untouched except the added button; `/chart` PNG fallback when `MINIAPP_PUBLIC_URL` unset.
- Branch `feat/miniapp-phase3` from `main`; izi per task; suite green (known flake rule); each git command its own Bash call.

---

### Task 1: Real viewer auth (initData HMAC + membership) + hardening checklist

**Files:**
- Create: `service/app/miniapp_auth.py`
- Modify: `service/app/miniapp.py` (FastAPI app kwargs `docs_url=None, redoc_url=None`; `GET /healthz`; `viewer_allowed(source)` body + WS call site passes `ws.query_params` / REST dependency reads header first then query; keep dev bypass first)
- Modify: `service/app/config.py` if a `miniapp_auth_max_age_s: int = 86400` setting helps testability
- Test: `service/tests/test_miniapp_auth.py` (create)

**Interfaces:**
- Produces: `validate_init_data(init_data: str, bot_token: str, max_age_s: int = 86400) -> dict | None` — Telegram's documented algorithm: parse querystring; pop `hash`; data-check-string = sorted `k=v` joined by `\n`; secret = HMAC_SHA256(key=b"WebAppData", msg=bot_token); valid iff hexdigest(HMAC_SHA256(secret, dcs)) == hash (compare via `hmac.compare_digest`) AND `auth_date` fresh. Returns the parsed user dict (`{"id": int, ...}` from the `user` JSON field) or None.
- Produces: `viewer_ok(init_data: str | None) -> bool` — dev bypass → True; else validate → user id == owner id → True; else linked channel + `getChatMember(channel_id, uid).status ∈ {creator, administrator, member}` (httpx, 5 s timeout, result cached 10 min per uid, denials cached too) → True; else False. Bot token/owner id resolved like the main service (profile db read-only via `SignalDb`-free sqlite open or reuse; document the choice); Bot API errors → False for non-owners.
- Tests MUST include: a known-vector initData (construct one in-test by signing with a test token — the validator must accept it, and reject: tampered hash, missing hash, stale auth_date, empty string, None); owner-id admit; member admit via mocked Bot API; non-member deny; Bot-API-down deny for member but owner still admitted; cache hit avoids a second Bot API call; header-vs-query precedence on the REST dependency; WS closes 4403 without valid initData; `/healthz` 200 auth-free; `/docs` and `/openapi.json` now 404.

- [ ] TDD: failing tests → implement → suites (`test_miniapp_auth.py`, `test_miniapp.py`, full once) → izi §8 auth paragraph update (real algorithm now live; bypass unchanged) → commit `feat(miniapp): Telegram initData auth — owner + channel members`.

---

### Task 2: ngrok install + setup phase + live tunnel verification

**Files:**
- Modify: `scripts/setup.sh` (new idempotent phase after the miniapp phase: install ngrok v3 into `~/.local/bin` if absent — download the linux-amd64 tgz from ngrok's official URL; write authtoken from `service/.env`'s `NGROK_AUTHTOKEN` via `ngrok config add-authtoken` only when unconfigured; start `nohup ngrok http --url=<domain from MINIAPP_PUBLIC_URL> 9001 --log /tmp/ngrok.log &` with skip-if-running (pgrep -f "ngrok http") and SKIP-if-unconfigured)
- Modify: `.claude/agents/izi.md` (tunnel section: start/stop/log, the interstitial note, the "only 9001 is ever exposed" invariant, upgrade path)

**Steps:**
- [ ] Implement the phase per setup.sh conventions; `bash -n` clean.
- [ ] Run it live: ngrok installs, tunnel comes up; verify from WSL: `curl -s -H "ngrok-skip-browser-warning: 1" https://<domain>/healthz` → `{"ok": true}` AND `curl -s -H "ngrok-skip-browser-warning: 1" "https://<domain>/api/history?tf=M5"` → 403 (auth now enforced through the tunnel — the security proof; record both outputs). Re-run phase → SKIP (idempotency).
- [ ] Commit `feat(setup): ngrok static-domain tunnel phase`.

---

### Task 3: Telegram wiring — button, /chart repoint, channel line, drill

**Files:**
- Modify: `service/app/config.py` (`miniapp_public_url: str = ""` — reads MINIAPP_PUBLIC_URL)
- Modify: `service/app/ticker.py` (owner LIVE message: when `settings.miniapp_public_url` set, attach inline keyboard `[[{"text": "📈 Live Chart", "web_app": {"url": <public url>}}]]` — extend `kb()` usage or build the dict directly; owner send only — the channel ticker copy is untouched (structural no-markup rule); note: `send_message`'s reply_markup path already exists)
- Modify: `service/app/main.py` (`_send_chart_snapshot` → when `miniapp_public_url` set, reply text "📈 Live chart:" with the same web_app button instead of rendering the PNG; PNG path stays as fallback when unset)
- Modify: `service/app/telegram.py` (pinned help `/chart` line → "open the live chart"; PINNED_HELP_VERSION → "7"; channel mirror of /chart: text link line `settings.miniapp_public_url` — plain URL text, no markup)
- Test: extend `service/tests/test_ticker.py` + `test_chart_cmd.py`: button present on owner ticker when url set, absent when unset; channel ticker copy carries NO markup either way; /chart replies button+text when set, PNG fallback when unset; pinned v7 assertions updated (grep `== "6"`).

**Steps:**
- [ ] TDD per above → suites → izi (button flow, /chart behavior, BotFather step pointer) → commit `feat(telegram): Live Chart button + /chart opens the mini app`.
- [ ] Live drill (record outputs): restart main service; confirm `/chart` in Telegram returns the button; tap-test is the OWNER's acceptance (web_app button opens the tunnel URL inside Telegram with initData → authorized). Controller relays the BotFather `/newapp` instructions to the owner separately (needed only for the channel's t.me link — owner chat works without it).

---

## Self-Review Notes (applied)

- Spec Phase 3 scope ↔ tasks: auth (T1), tunnel (T2), Telegram (T3); izi checklists from Phases 1–2 all land in T1 (docs_url, healthz, WS call-site params, initData header) and T2 (liveness probe swap).
- web_app buttons carry initData in private chats without BotFather registration — the owner path works day one; the channel direct link is the only BotFather-gated piece and degrades to "use /chart" text.
- Auth failure modes: owner path never depends on the Bot API; member path fails closed. Dev bypass precedence documented and inline-only.
- PINNED_HELP_VERSION "7" — the `== "6"` assertions are named for update in T3.
