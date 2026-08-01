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
on_exit() {
  local st=$?
  if [[ $st -ne 0 ]]; then
    printf '%sABORTED%s during: %s\n' "$C_RED" "$C_RESET" "$CURRENT_PHASE" >&2
  fi
}
trap on_exit EXIT

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

# ------------------------------------------------------------------ 3. Test gate
phase 3 "Test gate (fast pytest suite)"
pytest_log="$(mktemp)"
if (cd "$SERVICE_DIR" && FORECASTER=fake "$VENV/bin/pytest" -q >"$pytest_log" 2>&1); then
  ok "$(tail -1 "$pytest_log")"
else
  tail -25 "$pytest_log" >&2
  fail "tests failed — nothing was installed into MT5. Fix and re-run."
fi

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
