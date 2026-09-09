# Mini-app port move + setup.sh hardening — 2026-08-19

Branch `feat/miniapp-port-and-launcher`, off `main`.

## Incident (each fact re-verified on this machine before changing anything)

| Claim | Verification |
|---|---|
| Docker owns 9001 | `docker ps` → `on-prem-mosquitto-1  0.0.0.0:9001->9001/tcp` (Up 7 hours). **Left running, never touched.** |
| Mini-app died on the bind | `service/miniapp.log` → `ERROR: [Errno 98] error while attempting to bind on address ('127.0.0.1', 9001): address already in use` |
| Phase 5 `fail` + `set -euo pipefail` aborted the rest | Only `uvicorn app.main:app` (port 9000, started 17:06) was alive; no miniapp, no ngrok, no watchdog process |
| Bridge down too | `Get-CimInstance Win32_Process` on Windows → no `mt5_feed.py` process |

Cost to the owner: no chart, no tunnel (ngrok edge 404, no agent), **and no watchdog** —
the component that would have healed all of it — for hours.

## (A) The port is now configuration, not a literal

`MINIAPP_PORT` in `service/.env`, **default 9101**. Single source of truth:

| Consumer | Change |
|---|---|
| `service/app/config.py` | `miniapp_port: int = 9101` |
| `service/app/telegram.py` | `_MINIAPP_HEALTHZ_URL` built by `_miniapp_healthz_url()` from `settings.miniapp_port` (urllib 0.5 s probe unchanged) |
| `bridge/mt5_feed.py` | `feed_key()`'s tolerant parser generalised to `env_value(key, default)` (last match, quote/BOM tolerant, now also missing-file tolerant); `PUSH_URL = f"http://127.0.0.1:{miniapp_port()}/feed/push"`. Still pure stdlib. |
| `scripts/setup.sh` | phase 5 fills `MINIAPP_PORT` into `.env` (in-place fill of a blank line, else append with the trailing-newline guard — the FEED_KEY discipline) and re-reads it; start + liveness, phase 7 ngrok forward target, all use `$MINIAPP_PORT` |
| `scripts/xau-watchdog.sh` | `MINIAPP_PORT` via the existing `env_get`; used in `miniapp_ok`, `feed_ok`, `restart_miniapp`, `restart_tunnel` |
| `service/.env.example` | documented `MINIAPP_PORT=9101` line + the reason |

Repo-wide `9001` sweep: every remaining hit is either an explanatory comment about
*why* the port moved, or a dated historical plan/spec document — those got a
"Historical note (2026-08-19)" banner at the top rather than a rewrite of the record.

Tests: `test_config.py::test_miniapp_port_default_and_override` (9101 default + env
override) and `test_telegram_commands.py::test_miniapp_healthz_url_follows_configured_port`
(the probe URL is asserted against `settings.miniapp_port`, never a literal).

## (B) setup.sh survives a single component failing

1. **Port-conflict detection** — before starting, if the target port is listening and
   `/healthz` is not ours, setup names the squatter (`docker ps` first, then
   `ss -ltnp`, then a "run this to find out" fallback), prints the remedy
   ("set MINIAPP_PORT=<free port> in service/.env"), warns not to stop a process
   that isn't ours, and soft-fails the phase only. Proven against the real box:
   `port 9001: LISTENING, owner = the Docker container 'on-prem-mosquitto-1'` /
   `port 9101: LISTENING, owner = the process 'uvicorn'` / `port 9999: free`.
2. **`soft_fail()`** — records the phase FAILED, appends a "still down + remedy + log
   path" note, and continues. CRITICAL phases (preflight, Python env, test gate, main
   service) keep the hard `fail`. Non-critical: mini-app, live-chart config, tunnel,
   watchdog, Telegram, MT5 compile, handoff.
3. **The watchdog always runs** — it sits after every soft-failing phase and before
   Telegram/MT5, so it starts even when the mini-app or tunnel did not, and its 30 s
   loop is what brings them back once the cause clears.
4. **End-of-run summary** — OK/SKIP/FAILED per phase, a "Still down / needs you" list,
   `exit 0` unless a CRITICAL phase failed. An abort now marks the phase it died in as
   FAILED instead of leaving it OK.
5. Existing conventions kept (`phase`/`ok`/`skip`/`fail`, idempotency, `bash -n` clean).
   Phase bodies that need an early return are wrapped in a function so `soft_fail` can
   `return` **without re-indenting** — several bodies contain heredocs whose terminators
   must stay column 0.

### One extra bug the live run exposed

Run 1 aborted in phase 9 anyway: the freshly started watchdog immediately restarted
`app.main` to deploy this commit's code (its stale-code guard), and phase 9's
`profile_has_tg="$(curl -sf .../ui/profile | python ...)"` read an empty body during
that ~25 s torch cold start → JSONDecodeError → abort, taking MT5 compile and handoff
with it. Phase 9 now waits up to 60 s for `/health`, tolerates an empty profile body,
and soft-fails instead of dying. (Confirmed in `/tmp/xau-watchdog.log`:
`23:13:09 main service running code older than disk — restarting`.)

## Live verification (this machine, 2026-08-19)

- **Mini-app**: `127.0.0.1:9101/healthz` → `{"ok":true,...}`; started by setup phase 5.
- **Tunnel**: `https://tribute-obscurity-monday.ngrok-free.dev/healthz`
  (`ngrok-skip-browser-warning: 1`) → `{"ok":true,"feed_age_s":null,"uptime_s":66.3}`.
- **Auth still enforced through the tunnel**: `/api/history?tf=M5` unauthenticated → **403**;
  with valid owner `X-Telegram-Init-Data` → **200, 500 candles**. `GET /` → 200, 53 518 bytes.
- **Watchdog**: running; log line `watchdog start (interval 30s, miniapp port=9101, tunnel=...)`.
- **Bridge**: restarted the watchdog's way (PowerShell kill of any `python*` running
  `mt5_feed.py`, then `timeout 25 cmd.exe /c start "" /B <abs pythonw.exe> <abs mt5_feed.py>`;
  the `timeout` exit 124 is the documented WSL-view hang, the process survives).
  Now PID 9184 `pythonw.exe`, pushing to the **new** port — `/healthz` `feed_age_s: 0.5`.
- **Docker mosquitto**: untouched, still `Up`, still owns 9001.
- **Run 2 (idempotency)**: mini-app / live-chart config / tunnel / watchdog / Telegram all
  **SKIP**; MT5 compiled `0 errors, 0 warnings`; EA heartbeat received; summary printed;
  `EXIT=0`.

## Tests

`437 passed, 1 deselected` (full `service` suite, run standalone and twice more inside
setup.sh's gate). Two poller tests (`test_poller_dispatches_client_calls_off_event_loop`,
`test_poller_filters_using_active_client_chat_id_not_settings`) failed the first time and
were **not** flakes: they give the poll cycle 100 ms while `/status` runs the real
mini-app probe, and they had only been passing because mosquitto answered instantly on
9001 — with the port free, urllib burned its full 0.5 s timeout. Both now stub
`_miniapp_healthz`; they test threading, not the chart probe.

## Concerns / follow-ups

- The `/status` mini-app line was not exercised against the live Telegram bot (that
  would message the owner). It is covered by unit tests and the service process was
  restarted onto the new code, so its probe targets 9101.
- 9101 is only "free on this machine today". The guard now makes a future collision a
  named, actionable soft failure instead of a silent total outage.
- If the mini-app port is squatted at boot, the watchdog will try 3 restarts, then cool
  down 10 min and send the Telegram alarm — the owner is told, but the chart stays down
  until the port is freed or `MINIAPP_PORT` is changed. That is the intended trade.
- Unexpected (unhandled) errors inside a non-critical phase still abort under
  `set -e`; only the known failure modes are soft. Phase 9's was the one such case found
  live and is now handled.
