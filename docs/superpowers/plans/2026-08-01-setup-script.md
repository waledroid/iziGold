# One-shot Setup Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single idempotent bash script `scripts/setup.sh` that takes a fresh WSL machine to a fully wired system — venv, tests, running service, Telegram, EA compiled into MT5 — ending with a heartbeat-verified handoff.

**Architecture:** One bash file, seven sequential phases, each printing `[N/7] name` then `OK/SKIP/FAIL` lines. Helper functions and detected paths are defined once at the top; every phase re-detects done-state so re-runs SKIP. The script grows phase-by-phase across tasks; after each task the script is runnable end-to-current-phase on the dev machine.

**Tech Stack:** bash (`set -euo pipefail`), curl, the service venv's python for all JSON work (no jq dependency), `MetaEditor64.exe` CLI via WSL interop, `wslpath`, `iconv` (UTF-16 compile logs).

**Spec:** `docs/superpowers/specs/2026-08-01-setup-script-design.md`

## Global Constraints

- Script path: `scripts/setup.sh`; run from anywhere (paths derived from `BASH_SOURCE`).
- `set -euo pipefail` at top; `shellcheck` must pass with zero warnings after every task.
- Secrets: bot token read with `read -rs`, never echoed; only masked form `••••<last4>` printed.
- Idempotent: every phase detects already-done state → `SKIP`. Re-run after success must print no `FAIL` and change nothing.
- Service URL is always `http://127.0.0.1:9000` (variable `BASE_URL`).
- JSON parsing always via `"$VENV/bin/python"` — never jq, never regex-on-JSON in bash (exception: `.env` line greps).
- Verification is live-run based (this is glue code driving real external systems): after each task, run the script on the dev machine and eyeball the expected OK/SKIP lines. No bats suite (per spec).
- Commits: one per task, message prefix `feat(setup):`.

---

### Task 1: Skeleton, arg parsing, phase framework, Phase 1 (Preflight)

**Files:**
- Create: `scripts/setup.sh`

**Interfaces:**
- Produces (used by every later task): functions `phase N "Name"`, `ok [msg]`, `skip [msg]`, `fail msg` (prints and exits 1); variables `REPO_ROOT`, `SERVICE_DIR`, `VENV`, `BASE_URL`, `MT5_DIR` (terminal data dir, contains `MQL5/`), `METAEDITOR` (unix path to MetaEditor64.exe), `TOTAL=7`.

- [ ] **Step 1: Write the script skeleton with Phase 1**

```bash
#!/usr/bin/env bash
# One-shot setup for the XAU assistant: venv -> tests -> service -> Telegram -> MT5 EA.
# Usage: scripts/setup.sh [--mt5-dir /mnt/c/Users/<you>/AppData/Roaming/MetaQuotes/Terminal/<id>]
#                         [--metaeditor '/mnt/c/Program Files/MetaTrader 5/MetaEditor64.exe']
# Spec: docs/superpowers/specs/2026-08-01-setup-script-design.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$REPO_ROOT/service"
VENV="$SERVICE_DIR/.venv"
BASE_URL="http://127.0.0.1:9000"
TOTAL=7
MT5_DIR=""
METAEDITOR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mt5-dir)    MT5_DIR="$2"; shift 2 ;;
    --metaeditor) METAEDITOR="$2"; shift 2 ;;
    -h|--help)    sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_RESET=$'\033[0m'
CURRENT_PHASE="startup"
phase() { CURRENT_PHASE="$2"; printf '\n[%d/%d] %s\n' "$1" "$TOTAL" "$2"; }
ok()    { printf '  %sOK%s %s\n' "$C_GREEN" "$C_RESET" "${1:-}"; }
skip()  { printf '  %sSKIP%s %s\n' "$C_YELLOW" "$C_RESET" "${1:-}"; }
fail()  { printf '  %sFAIL%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; exit 1; }
trap 'st=$?; if [[ $st -ne 0 ]]; then printf "%sABORTED%s during: %s\n" "$C_RED" "$C_RESET" "$CURRENT_PHASE" >&2; fi' EXIT

# ---------------------------------------------------------------- 1. Preflight
phase 1 "Preflight"
grep -qi microsoft /proc/version || fail "not running under WSL"
[[ -d /mnt/c ]] || fail "/mnt/c is not mounted"
[[ -d "$SERVICE_DIR" && -d "$REPO_ROOT/mt5" ]] || fail "repo layout wrong: expected service/ and mt5/ under $REPO_ROOT"
command -v curl >/dev/null || fail "curl not found (sudo apt install curl)"
command -v python3 >/dev/null || fail "python3 not found"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "python3 >= 3.11 required (found: $(python3 -V 2>&1))"

if [[ -z "$MT5_DIR" ]]; then
  for mql5 in /mnt/c/Users/*/AppData/Roaming/MetaQuotes/Terminal/*/MQL5; do
    [[ -d "$mql5" ]] || continue
    term="$(dirname "$mql5")"
    if [[ -z "$MT5_DIR" || "$term" -nt "$MT5_DIR" ]]; then MT5_DIR="$term"; fi
  done
fi
[[ -n "$MT5_DIR" && -d "$MT5_DIR/MQL5" ]] \
  || fail "MT5 data folder not found. In MT5: File > Open Data Folder, then re-run with --mt5-dir '<that path as /mnt/c/...>'"

if [[ -z "$METAEDITOR" ]]; then
  for cand in "/mnt/c/Program Files/MetaTrader 5/MetaEditor64.exe" \
              "/mnt/c/Program Files/MetaTrader 5 EXNESS/MetaEditor64.exe" \
              "/mnt/c/Program Files (x86)/MetaTrader 5/MetaEditor64.exe"; do
    if [[ -f "$cand" ]]; then METAEDITOR="$cand"; break; fi
  done
fi
[[ -n "$METAEDITOR" && -f "$METAEDITOR" ]] \
  || fail "MetaEditor64.exe not found — re-run with --metaeditor '/mnt/c/.../MetaEditor64.exe'"
ok "MT5 data folder: $MT5_DIR"
ok "MetaEditor:      $METAEDITOR"
```

- [ ] **Step 2: Make executable and shellcheck**

Run: `chmod +x scripts/setup.sh && shellcheck scripts/setup.sh`
Expected: no output (clean). If shellcheck is missing: `sudo apt-get install -y shellcheck`.

- [ ] **Step 3: Live-run Phase 1**

Run: `scripts/setup.sh`
Expected: `[1/7] Preflight` then two OK lines showing the detected `Terminal/D0E8209F77C8CF37AD8BF550E51FF075` data dir and MetaEditor path, exit 0, no ABORTED line.
Also run: `scripts/setup.sh --mt5-dir /nonexistent` → expected FAIL message about the data folder, exit 1, ABORTED line naming Preflight.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): script skeleton, arg parsing, preflight phase"
```

---

### Task 2: Phase 2 (Python environment)

**Files:**
- Modify: `scripts/setup.sh` (append after Phase 1 block)

**Interfaces:**
- Consumes: `phase/ok/skip/fail`, `SERVICE_DIR`, `VENV`.
- Produces: a populated `$VENV` and `$SERVICE_DIR/.env` that all later phases rely on.

- [ ] **Step 1: Append Phase 2**

```bash
# ------------------------------------------------------- 2. Python environment
phase 2 "Python environment"
if [[ -x "$VENV/bin/python" ]]; then
  skip ".venv exists"
else
  python3 -m venv "$VENV"
  ok "created service/.venv"
fi
"$VENV/bin/pip" install -q -r "$SERVICE_DIR/requirements.txt"
ok "core requirements installed"
if [[ -f "$SERVICE_DIR/.env" ]]; then
  skip ".env exists"
else
  cp "$SERVICE_DIR/.env.example" "$SERVICE_DIR/.env"
  ok "created .env from .env.example"
fi
if grep -q '^FORECASTER=chronos' "$SERVICE_DIR/.env" \
   && ! "$VENV/bin/python" -c 'import torch' >/dev/null 2>&1; then
  echo "  installing torch + chronos (first time can take several minutes)..."
  "$VENV/bin/pip" install -q -r "$SERVICE_DIR/requirements-model.txt"
  ok "model requirements installed"
fi
```

- [ ] **Step 2: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Live-run**

Run: `scripts/setup.sh`
Expected: Phase 1 OKs, then `[2/7] Python environment` with `SKIP .venv exists`, `OK core requirements installed`, `SKIP .env exists` (dev machine already set up; the torch branch is silently skipped because torch imports).

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): python environment phase"
```

---

### Task 3: Phase 3 (Test gate)

**Files:**
- Modify: `scripts/setup.sh` (append)

**Interfaces:**
- Consumes: `VENV`, `SERVICE_DIR`, `ok/fail/phase`.
- Produces: nothing downstream — a pure gate; later phases may assume the fast suite passed.

- [ ] **Step 1: Append Phase 3**

```bash
# ------------------------------------------------------------------ 3. Test gate
phase 3 "Test gate (fast pytest suite)"
pytest_log="$(mktemp)"
if (cd "$SERVICE_DIR" && FORECASTER=fake "$VENV/bin/pytest" -q >"$pytest_log" 2>&1); then
  ok "$(tail -1 "$pytest_log")"
else
  tail -25 "$pytest_log" >&2
  fail "tests failed — nothing was installed into MT5. Fix and re-run."
fi
```

- [ ] **Step 2: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Live-run**

Run: `scripts/setup.sh`
Expected: phases 1–2 as before, then `[3/7] Test gate` with an OK line ending in the pytest summary (e.g. `NN passed in X.XXs`).

- [ ] **Step 4: Verify the gate actually gates**

Run: `cd service && FORECASTER=fake .venv/bin/pytest -q tests/test_api.py -k nonexistent_test_name_zzz; cd ..`
Expected: pytest errors with "no tests ran" — this confirms the command shape; the script's failure path (tail + FAIL + exit 1) is exercised by construction (`if/else` around the same command). No forced-failure edit needed.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): pytest gate before any install"
```

---

### Task 4: Phase 4 (Service up + smoke)

**Files:**
- Modify: `scripts/setup.sh` (append)
- Modify: `.gitignore` (ensure `service/service.log` ignored)

**Interfaces:**
- Consumes: `VENV`, `SERVICE_DIR`, `BASE_URL`, helpers.
- Produces: function `health()`; a service answering on `BASE_URL` that phases 5 and 7 call.

- [ ] **Step 1: Append Phase 4**

```bash
# -------------------------------------------------------- 4. Service up + smoke
phase 4 "Service"
health() { curl -sf -m 3 "$BASE_URL/health" 2>/dev/null; }
if health >/dev/null; then
  skip "already running at $BASE_URL"
else
  (cd "$SERVICE_DIR" && nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 9000 \
      >>"$SERVICE_DIR/service.log" 2>&1 &)
  up=""
  for _ in $(seq 1 60); do
    if health >/dev/null; then up=yes; break; fi
    sleep 1
  done
  if [[ -z "$up" ]]; then
    tail -25 "$SERVICE_DIR/service.log" >&2
    fail "service did not come up in 60s — see service/service.log"
  fi
  ok "started in background (logs: service/service.log)"
fi

smoke_payload="$("$VENV/bin/python" - <<'PY'
import json
candles = [{"t": 1754000000 + i * 300, "o": 2400 + i * 0.1, "h": 2401 + i * 0.1,
            "l": 2399 + i * 0.1, "c": 2400.5 + i * 0.1, "v": 100.0} for i in range(200)]
print(json.dumps({"symbol": "XAUUSD", "timeframe": "M5", "signal": "NONE",
                  "strategy_id": "setup_smoke", "shadows": [], "candles": candles}))
PY
)"
echo "  smoke POST /analyze (first call may load the model — up to 3 min)..."
smoke_resp="$(curl -sf -m 180 -X POST "$BASE_URL/analyze" \
    -H 'Content-Type: application/json' -d "$smoke_payload")" \
  || fail "POST /analyze smoke call failed — see service/service.log"
echo "$smoke_resp" | "$VENV/bin/python" -c '
import json, sys
d = json.load(sys.stdin)
assert d["verdict"] in ("confirm", "neutral", "conflict"), d
assert d["direction"] in ("bullish", "bearish", "neutral"), d
print("  analyze ok: %s conf=%s regime=%s ai=%s"
      % (d["direction"], d["confidence"], d["regime"], d["ai_available"]))' \
  || fail "/analyze returned an unexpected body: $smoke_resp"
ok "service healthy + /analyze smoke passed"
```

Note: the smoke call writes one `setup_smoke` NONE row into SQLite — harmless (NONE rows are the normal per-bar traffic) and clearly labeled.

- [ ] **Step 2: Ensure log file is git-ignored**

Check `.gitignore` at repo root; if `service/service.log` (or a covering pattern like `*.log`) is absent, append `service/service.log`.

- [ ] **Step 3: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 4: Live-run**

Run: `scripts/setup.sh`
Expected: `[4/7] Service` with `SKIP already running` (service is up on the dev machine), then the analyze line (`analyze ok: ... ai=True`) and final OK. If the dev service was stopped, the start path runs instead — both are valid.

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.sh .gitignore
git commit -m "feat(setup): service boot + /analyze smoke phase"
```

---

### Task 5: Phase 5 (Telegram)

**Files:**
- Modify: `scripts/setup.sh` (append)

**Interfaces:**
- Consumes: `VENV`, `SERVICE_DIR`, `BASE_URL`, helpers; service running (phase 4).
- Produces: Telegram credentials stored in the service profile (live-applied by `POST /ui/profile`).

- [ ] **Step 1: Append Phase 5**

```bash
# ----------------------------------------------------------------- 5. Telegram
phase 5 "Telegram"
profile_has_tg="$(curl -sf "$BASE_URL/ui/profile" | "$VENV/bin/python" -c '
import json, sys
p = json.load(sys.stdin).get("profile") or {}
print("yes" if p.get("telegram_bot_token") and p.get("telegram_chat_id") else "no")')"
env_token="$(grep -oP '^TELEGRAM_BOT_TOKEN=\K.+' "$SERVICE_DIR/.env" || true)"
env_chat="$(grep -oP '^TELEGRAM_CHAT_ID=\K.+' "$SERVICE_DIR/.env" || true)"

if [[ "$profile_has_tg" == yes ]]; then
  skip "credentials already in service profile"
elif [[ -n "$env_token" && -n "$env_chat" ]]; then
  curl -sf -m 10 "https://api.telegram.org/bot$env_token/getMe" >/dev/null \
    || fail ".env TELEGRAM_BOT_TOKEN was rejected by Telegram (getMe failed)"
  skip ".env credentials present and token valid"
else
  echo "  No Telegram credentials found. Create a bot with @BotFather (/newbot) first."
  token=""
  me_json=""
  for attempt in 1 2 3; do
    read -rsp "  Paste the bot token (input hidden, attempt $attempt/3): " token; echo
    me_json="$(curl -sf -m 10 "https://api.telegram.org/bot$token/getMe" || true)"
    if [[ -n "$me_json" ]]; then break; fi
    echo "  Telegram rejected that token."
    token=""
  done
  [[ -n "$token" ]] || fail "could not validate a bot token"
  bot_user="$(echo "$me_json" | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["result"]["username"])')"
  echo "  Open https://t.me/$bot_user and send the bot any message. Waiting up to 120s..."
  chat_id=""
  for _ in $(seq 1 40); do
    chat_id="$(curl -sf -m 10 "https://api.telegram.org/bot$token/getUpdates" | "$VENV/bin/python" -c '
import json, sys
for u in reversed(json.load(sys.stdin).get("result", [])):
    chat = (u.get("message") or {}).get("chat") or {}
    if chat.get("type") == "private":
        print(chat["id"]); break' || true)"
    [[ -n "$chat_id" ]] && break
    sleep 3
  done
  [[ -n "$chat_id" ]] || fail "the bot received no message in 120s — message it, then re-run (earlier phases will SKIP)"
  curl -sf -X POST "$BASE_URL/ui/profile" -H 'Content-Type: application/json' \
      -d "{\"telegram_bot_token\":\"$token\",\"telegram_chat_id\":\"$chat_id\"}" >/dev/null \
    || fail "saving credentials to the service profile failed"
  curl -sf -m 10 -X POST "https://api.telegram.org/bot$token/sendMessage" \
      -d "chat_id=$chat_id" --data-urlencode "text=✅ XAU Assistant setup: Telegram connected." >/dev/null \
    || fail "test message failed to send"
  ok "linked chat $chat_id (token ••••${token: -4}); test message sent"
fi
```

- [ ] **Step 2: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Live-run (SKIP path)**

Run: `scripts/setup.sh`
Expected: `[5/7] Telegram` → `SKIP credentials already in service profile` (dev machine has them).

- [ ] **Step 4: Live-run (interactive path, sandboxed)**

Verify the extraction one-liners standalone (the full interactive path needs a fresh profile, verified in Task 8's cold-ish rehearsal):
Run: `curl -sf "https://api.telegram.org/bot$(service/.venv/bin/python -c "import sqlite3;print(sqlite3.connect('service/xau_assistant.db').execute('select telegram_bot_token from profile').fetchone()[0])")/getUpdates" | service/.venv/bin/python -c "import json,sys
for u in reversed(json.load(sys.stdin).get('result', [])):
    chat=(u.get('message') or {}).get('chat') or {}
    if chat.get('type')=='private': print(chat['id']); break"`
Expected: prints `5318349042`. (Table/column names: check `service/app/db.py` for the actual profile table name first; adjust the ad-hoc query if it differs — this query is test-only, not part of the script.)

- [ ] **Step 5: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): telegram enrolment with auto chat-id detection"
```

---

### Task 6: Phase 6 (MT5 install + compile)

**Files:**
- Modify: `scripts/setup.sh` (append)

**Interfaces:**
- Consumes: `MT5_DIR`, `METAEDITOR`, `REPO_ROOT`, helpers.
- Produces: `$MT5_DIR/MQL5/Experts/XauAssistant.ex5` — the artifact the user attaches in phase 7.

- [ ] **Step 1: Append Phase 6**

```bash
# ------------------------------------------------------ 6. MT5 install + compile
phase 6 "MT5 install + compile"
mql5="$MT5_DIR/MQL5"
mkdir -p "$mql5/Experts" "$mql5/Include"
cp "$REPO_ROOT/mt5/Experts/XauAssistant.mq5" "$mql5/Experts/"
rm -rf "$mql5/Include/XauAssistant"
cp -r "$REPO_ROOT/mt5/Include/XauAssistant" "$mql5/Include/XauAssistant"
ok "sources copied into $mql5 (Include/XauAssistant fully replaced)"

compile_log="$mql5/Experts/XauAssistant.setup-compile.log"
: >"$compile_log"   # must exist before wslpath -w can translate it
win_src="$(wslpath -w "$mql5/Experts/XauAssistant.mq5")"
win_log="$(wslpath -w "$compile_log")"
echo "  compiling via MetaEditor CLI..."
"$METAEDITOR" /compile:"$win_src" /log:"$win_log" || true   # exit code is unreliable by design
read_log() { iconv -f UTF-16LE -t UTF-8 "$compile_log" 2>/dev/null || cat "$compile_log"; }
result_line="$(read_log | grep -iE '[0-9]+ error' | tail -1 || true)"
[[ -n "$result_line" ]] || { read_log >&2; fail "could not find a result line in the compile log"; }
errors="$(echo "$result_line" | grep -oiE '[0-9]+ error' | grep -oE '[0-9]+')"
if [[ "$errors" != 0 ]]; then
  read_log | tail -30 >&2
  fail "compilation failed: $result_line"
fi
[[ -f "$mql5/Experts/XauAssistant.ex5" ]] || fail "compile reported success but XauAssistant.ex5 is missing"
ok "compiled: ${result_line#"${result_line%%[![:space:]]*}"}"
```

- [ ] **Step 2: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Live-run**

Run: `scripts/setup.sh`
Expected: `[6/7] MT5 install + compile` → sources copied, then `OK compiled: ... 0 errors, 0 warnings ...`, and `$MT5_DIR/MQL5/Experts/XauAssistant.ex5` freshly timestamped (`ls -la` it).
If MetaEditor pops a dialog or hangs: kill it, and note the failure verbatim in the task report — do not silently work around.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): MT5 file install + MetaEditor CLI compile with log parse"
```

---

### Task 7: Phase 7 (Handoff + end-to-end verify)

**Files:**
- Modify: `scripts/setup.sh` (append)

**Interfaces:**
- Consumes: `BASE_URL`, `VENV`, helpers; `/ui/state` returns `{"age_s": <float|null>, ...}` where `age_s` is seconds since last EA heartbeat.

- [ ] **Step 1: Append Phase 7**

```bash
# ------------------------------------------- 7. Handoff + end-to-end verification
phase 7 "Handoff + end-to-end verify"
cat <<'EOF'

  Two manual steps remain in MetaTrader 5 (MT5 stores these encrypted; no script can set them):

    1. Tools > Options > Expert Advisors:
         tick "Allow WebRequest for listed URL" and add exactly:  http://127.0.0.1:9000
    2. Drag XauAssistant (Navigator > Expert Advisors) onto a XAUUSD M5 chart,
         tick "Allow Algo Trading" in the dialog, press OK.
       (If the EA was already on the chart, remove and re-attach it.)

  Waiting up to 5 minutes for the EA heartbeat (fires every 5s, even with markets closed)...
EOF
deadline=$((SECONDS + 300))
beat=""
while (( SECONDS < deadline )); do
  beat="$(curl -sf -m 3 "$BASE_URL/ui/state" | "$VENV/bin/python" -c '
import json, sys
d = json.load(sys.stdin)
a = d.get("age_s")
print("yes" if a is not None and a < 30 else "")' || true)"
  [[ -n "$beat" ]] && break
  sleep 5
done
if [[ -n "$beat" ]]; then
  ok "EA heartbeat received — end-to-end wiring confirmed"
  cat <<EOF

  ✅ Setup complete.
     Dashboard:   $BASE_URL/ui
     Service log: service/service.log
     Stop:        pkill -f 'uvicorn app.main:app'
     Restart:     re-run scripts/setup.sh (completed phases SKIP)
     Signals & Telegram alerts flow on closed M5 bars once the market is open.
EOF
else
  cat <<EOF >&2

  No heartbeat within 5 minutes. Checklist:
    - Options > Expert Advisors: URL is exactly http://127.0.0.1:9000 (no trailing slash)
    - The toolbar "Algo Trading" button is ON (green) and the chart smiley is smiling
    - MT5 Toolbox > Experts tab: look for "WebRequest error 4014" or similar
    - Remove the EA from the chart and re-attach it (options load at EA init)
  Re-run scripts/setup.sh afterwards — phases 1-6 will SKIP and the wait restarts.
EOF
  fail "EA heartbeat not observed"
fi
```

- [ ] **Step 2: Shellcheck**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Live-run**

Run: `scripts/setup.sh`
Expected: phases 1–6 OK/SKIP, then the handoff text and the wait loop. If the EA is already attached with the allowlist set (heartbeats flowing), the success banner appears within ~10s. If the user hasn't done the manual steps yet, the 5-minute timeout with the checklist and exit 1 is ALSO a correct outcome for this task — record which of the two you observed.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.sh
git commit -m "feat(setup): handoff instructions + heartbeat end-to-end verification"
```

---

### Task 8: Full verification pass + docs pointer

**Files:**
- Modify: `CLAUDE.md` (Commands section: one line pointing at the script)

**Interfaces:**
- Consumes: the complete script.

- [ ] **Step 1: Idempotency proof**

Run: `scripts/setup.sh` twice back-to-back.
Expected on the second run: phases 1–5 all OK/SKIP with no state changes (`git -C . status --short` unchanged, `.venv` untouched); phase 6 recompiles (by design — cheap and keeps the .ex5 fresh); phase 7 reaches the heartbeat wait.

- [ ] **Step 2: Shellcheck final**

Run: `shellcheck scripts/setup.sh`
Expected: clean.

- [ ] **Step 3: Add the one-liner to CLAUDE.md Commands section**

In the `## Commands` section of `CLAUDE.md`, after the uvicorn line, add:

```markdown
`scripts/setup.sh` — one-shot idempotent setup (venv → tests → service → Telegram → MT5 compile); safe to re-run any time.
```

- [ ] **Step 4: Commit and push**

```bash
git add scripts/setup.sh CLAUDE.md
git commit -m "feat(setup): idempotency verification + CLAUDE.md pointer"
git push
```
