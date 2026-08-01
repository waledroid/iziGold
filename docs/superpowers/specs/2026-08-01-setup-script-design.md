# One-shot setup script — design

**Date:** 2026-08-01
**Goal:** a single robust script that takes a fresh machine from nothing to a fully
wired system — Python service, Telegram alerts, and the EA compiled and staged in
MetaTrader 5 — with a test gate before anything is installed into MT5, and clear
handoff for the two steps MT5 refuses to let us automate.

## Decisions (from brainstorming)

- **Form:** single bash script `scripts/setup.sh`, run from the repo in WSL.
  Bash because it has zero bootstrap dependencies (the Python env doesn't exist
  until the script creates it) and can invoke Windows executables directly.
- **MT5 depth:** copy files + compile via `MetaEditor64.exe` CLI + verify via EA
  heartbeat. The WebRequest allowlist is stored encrypted inside
  `config/common.ini` (`Environment=` blob) and **cannot** be scripted; chart
  attach via profile-file editing is fragile across builds. Both stay manual,
  with the script verifying the outcome instead.
- **Telegram:** interactive when unconfigured — prompt for bot token, validate
  via `getMe`, auto-detect chat ID by polling `getUpdates` after the user
  messages the bot, save via `POST /ui/profile` (live-applied, profile takes
  precedence over `.env`), send a test message. Skipped entirely when existing
  credentials validate.
- **Test gate:** fast pytest suite (`FORECASTER=fake`) plus a service smoke test
  (`/health` + synthetic `POST /analyze`) must pass before the MT5 install phase.
- **Service lifecycle:** left running in the background via `nohup`, logs to
  `service/service.log`; re-runs of the script detect and reuse it.

## Phases

Each phase prints `[N/7] <name> ... OK | SKIP | FAIL`. Any FAIL aborts with a
specific remedy. The script is idempotent: every phase detects already-done
state and reports SKIP.

### 1. Preflight
- Assert: running under WSL (`/proc/version` contains `microsoft`), `/mnt/c`
  mounted, repo layout present (`service/`, `mt5/`), `python3` ≥ 3.11, `curl`.
- Detect MT5 data folder: scan `/mnt/c/Users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5`;
  if several match, pick the most recently modified terminal; `--mt5-dir PATH`
  overrides. FAIL with instructions (`File → Open Data Folder`) if none found.
- Detect `MetaEditor64.exe` in standard install paths (`/mnt/c/Program Files/MetaTrader 5/`,
  same dir as the detected terminal's install); `--metaeditor PATH` overrides.

### 2. Python environment
- `python3 -m venv service/.venv` if missing; `pip install -r requirements.txt`
  (pip's own idempotency makes re-runs cheap).
- `cp .env.example .env` if `.env` missing.
- If `.env` has `FORECASTER=chronos` and `import torch` fails in the venv,
  install `requirements-model.txt`.

### 3. Test gate
- `FORECASTER=fake .venv/bin/pytest` in `service/` (fast suite; slow markers
  excluded by default via pyproject).
- Any test failure → FAIL, print pytest tail, stop before touching MT5.

### 4. Service up + smoke
- If `GET http://127.0.0.1:9000/health` already returns `status: ok` → SKIP
  (reuse running service).
- Else `nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9000 >> service/service.log 2>&1 &`,
  then poll `/health` up to 60 s.
- Smoke: `POST /analyze` with a synthetic 200-candle payload (signal NONE,
  matching `models.py` schema). Assert HTTP 200 and a parseable verdict. Works
  under both fake and chronos forecasters (chronos first-call model load can be
  slow — timeout 180 s, message explaining the wait).

### 5. Telegram
- Effective credentials = profile values when non-empty else `.env` (mirrors
  `_effective_telegram` in `app/main.py`). Profile read via `GET /ui/profile`
  (token comes back masked — masked-but-present counts as configured).
- If configured: validate by calling `getMe` only when the plain token is
  available (i.e. from `.env`); a masked profile token is trusted as-is → SKIP.
- If not configured:
  1. `read -s` the bot token (never echoed); validate via `getMe`; re-prompt on
     failure (3 attempts).
  2. Print the bot's `t.me/<username>` link, instruct the user to send it any
     message, poll `getUpdates` every 3 s up to 120 s for the first private-chat
     ID.
  3. `POST /ui/profile` with token + chat ID (service applies live, no restart).
  4. Send a "✅ setup: Telegram connected" message through the bot; FAIL if
     Telegram rejects it.

### 6. MT5 install + compile
- Copy `mt5/Experts/XauAssistant.mq5` → `<data>/MQL5/Experts/` and
  `mt5/Include/XauAssistant/` → `<data>/MQL5/Include/XauAssistant/` (full
  replace of the include dir so deleted files don't linger).
- Compile: `MetaEditor64.exe /compile:<windows path to XauAssistant.mq5> /log:<log>`
  (paths converted with `wslpath -w`). MetaEditor's exit code is unreliable —
  parse the log (UTF-16) for the `N errors, M warnings` result line.
- 0 errors required; warnings printed but non-fatal. FAIL prints the log tail.
- Verify `XauAssistant.ex5` now exists and is newer than the source.

### 7. Handoff + end-to-end verify
- Print the exactly-two manual steps:
  1. MT5 → Tools → Options → Expert Advisors → Allow WebRequest for
     `http://127.0.0.1:9000`
  2. Drag XauAssistant onto a XAUUSD M5 chart, Allow Algo Trading, OK.
- Poll `GET /ui/state` for up to 5 min waiting for a fresh heartbeat
  (`age_s` non-null and < 30). The EA heartbeats every 5 s independent of
  market hours, so this works on weekends too.
- Heartbeat seen → success banner (dashboard URL, Telegram confirmation,
  log locations, stop/restart commands).
- Timeout → non-zero exit with a troubleshooting list (allowlist exact string,
  Algo Trading button, Experts-tab errors, re-attach EA), and note that
  re-running the script resumes at this phase (everything before it SKIPs).

## Error handling

- `set -euo pipefail`; `trap` on ERR prints the failing phase and line.
- All state-changing operations target things this script created (venv, .env
  copy, MQL5 copies); the only overwrite of user data is the MQL5 include dir
  replace, which mirrors the repo — flagged in output.
- Secrets: token read with `read -s`, printed only masked (`••••` + last 4);
  `set -x` never enabled.

## Out of scope (YAGNI)

- Auto-editing the WebRequest allowlist (encrypted store).
- Auto-attaching the EA to a chart (profile-file surgery, build-fragile).
- systemd/boot persistence; Windows-native (non-WSL) layouts; multi-terminal
  MT5 selection UI beyond "newest wins + flag override".

## Verification of the script itself

- `shellcheck` clean.
- Full live run on the dev machine: phases 1–6 must complete for real; phase 7
  run to the handoff prompt (heartbeat verification exercised when the user
  completes the manual steps).
- Re-run immediately after: every phase must SKIP (idempotency proof).
