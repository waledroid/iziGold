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
TOTAL=11
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

# Shared "process predates its code on disk" guard — also used by the
# watchdog (scripts/xau-watchdog.sh) so there's exactly one implementation.
source "$REPO_ROOT/scripts/lib/stale-code.sh"

# ---- phase bookkeeping (for the end-of-run summary) ------------------------
# Every phase gets a status: OK (something was done / verified), SKIP
# (already in place — the idempotent re-run case) or FAILED (soft_fail).
# `ok` wins over `skip` inside one phase; FAILED is sticky.
PHASE_IDX=-1
_phase_ok_seen=0
declare -a PHASE_NAMES=() PHASE_STATUS=() DOWN_NOTES=()

phase() {
  CURRENT_PHASE="$2"
  PHASE_IDX=$((PHASE_IDX + 1))
  PHASE_NAMES[$PHASE_IDX]="$2"
  PHASE_STATUS[$PHASE_IDX]="OK"
  _phase_ok_seen=0
  printf '\n[%d/%d] %s\n' "$1" "$TOTAL" "$2"
}
_set_status() {   # never downgrade a FAILED phase
  (( PHASE_IDX >= 0 )) || return 0
  [[ "${PHASE_STATUS[$PHASE_IDX]}" == FAILED ]] && return 0
  PHASE_STATUS[$PHASE_IDX]="$1"
  return 0
}
ok()    { _phase_ok_seen=1; _set_status OK
          printf '  %sOK%s %s\n' "$C_GREEN" "$C_RESET" "${1:-}"; }
skip()  { (( _phase_ok_seen == 1 )) || _set_status SKIP
          printf '  %sSKIP%s %s\n' "$C_YELLOW" "$C_RESET" "${1:-}"; }
# fail = CRITICAL phase failed (preflight, venv, test gate, main service):
# there is no useful run left, abort.
fail()  { printf '  %sFAIL%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; exit 1; }
# soft_fail = a NON-CRITICAL phase failed (mini-app, live-chart config,
# tunnel, watchdog, Telegram, MT5 compile, handoff). Records the phase as
# FAILED, notes what is still down + where to look, and RETURNS so the run
# continues. Rationale (incident 2026-08-19): one occupied port made phase 5
# abort the whole script, so the owner lost the chart, the tunnel AND the
# watchdog — one optional component must never cost the trader everything
# that comes after it. Callers must follow it with an explicit `return 0`
# when the rest of the phase body no longer makes sense.
soft_fail() {
  printf '  %sFAIL%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
  (( PHASE_IDX >= 0 )) && PHASE_STATUS[$PHASE_IDX]="FAILED"
  [[ -n "${2:-}" ]] && DOWN_NOTES+=("$2")
  return 0
}
print_summary() {
  printf '\n%s\n' "──────────────── Setup summary ────────────────"
  local i st colour
  for i in "${!PHASE_NAMES[@]}"; do
    st="${PHASE_STATUS[$i]}"
    case "$st" in
      OK)     colour="$C_GREEN" ;;
      SKIP)   colour="$C_YELLOW" ;;
      *)      colour="$C_RED" ;;
    esac
    printf '  %s%-6s%s %s\n' "$colour" "$st" "$C_RESET" "${PHASE_NAMES[$i]}"
  done
  if (( ${#DOWN_NOTES[@]} > 0 )); then
    printf '\n  %sStill down / needs you:%s\n' "$C_RED" "$C_RESET"
    local n
    for n in "${DOWN_NOTES[@]}"; do printf '   - %s\n' "$n"; done
    printf '   (the watchdog retries the process links every 30 s: /tmp/xau-watchdog.log)\n'
  fi
}
on_exit() {
  local st=$?
  # A hard abort (or an unexpected error) leaves the current phase
  # unfinished — never let the summary call it OK.
  if [[ $st -ne 0 ]] && (( PHASE_IDX >= 0 )); then
    PHASE_STATUS[$PHASE_IDX]="FAILED"
  fi
  print_summary
  if [[ $st -ne 0 ]]; then
    printf '%sABORTED%s during: %s\n' "$C_RED" "$C_RESET" "$CURRENT_PHASE" >&2
  fi
}
trap on_exit EXIT

# ---- mini-app port helpers -------------------------------------------------
# MINIAPP_PORT lives in service/.env and is the single source of truth for the
# mini-app process, the ngrok forward target, the watchdog and the Windows
# bridge. Phase 5 fills it in and re-reads it; the default is only the
# fallback for a .env written before this setting existed.
MINIAPP_PORT_DEFAULT=9101
MINIAPP_PORT="$MINIAPP_PORT_DEFAULT"
MINIAPP_URL="http://127.0.0.1:$MINIAPP_PORT"

port_listening() {   # any listener at all on $1 (0.0.0.0 counts: it shadows 127.0.0.1)
  ss -ltn 2>/dev/null | awk -v p="$1" '$4 ~ ("[:.]" p "$") {found=1} END {exit !found}'
}
port_owner_hint() {  # best effort: name the squatter so the message is actionable
  local port="$1" proc dk
  dk="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -m1 ":${port}->" | awk '{print $1}' || true)"
  if [[ -n "$dk" ]]; then echo "the Docker container '$dk'"; return 0; fi
  proc="$(ss -ltnp 2>/dev/null | awk -v p="$port" '$4 ~ ("[:.]" p "$") {print $NF}' \
          | grep -oP 'users:\(\("\K[^"]+' | head -1 || true)"
  if [[ -n "$proc" ]]; then echo "the process '$proc'"; return 0; fi
  echo "another process (identify it with: sudo ss -ltnp | grep :$port)"
}

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

# config/strategy.json is the single source of truth for the parameters
# mirrored between the EA and scripts/backtest.py (both registered
# strategies, halftrend_ema_v1 and halftrend_m15_v1, read their block from
# it). backtest.py raises at import time when it's missing, which otherwise
# surfaces many phases later as an opaque pytest collection error in the
# Test gate — catch it here instead, plainly, while it's still cheap to fix.
[[ -f "$REPO_ROOT/config/strategy.json" ]] \
  || fail "config/strategy.json is missing — required by the backtest and the EA/replay parameter mirror. Restore it (git checkout -- config/strategy.json) and re-run."
ok "config/strategy.json present"

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
start_service() {
  (cd "$SERVICE_DIR" && nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 9000 \
      >>"$SERVICE_DIR/service.log" 2>&1 &)
  up=""
  for _ in $(seq 1 60); do
    if health >/dev/null; then up=yes; break; fi
    sleep 1
  done
  [[ -n "$up" ]]
}
if health >/dev/null; then
  # stale-code guard (incident 2026-08-2x): the service was started 13
  # minutes before a commit added a DB field and ran a full day silently
  # dropping it from every trade while /health stayed green. The watchdog
  # already catches this every 30s once it's running, but this phase runs
  # BEFORE the watchdog starts (phase 8) and on every manual re-run, so it
  # must not wait for the watchdog to notice. Same check the watchdog uses
  # (scripts/lib/stale-code.sh) — one implementation, no second copy to drift.
  if stale_code "uvicorn app.main:app" "$SERVICE_DIR/app"; then
    echo "  running service predates its code on disk — restarting"
    pkill -f "uvicorn app.main:app" || true
    for _ in $(seq 1 10); do health >/dev/null || break; sleep 1; done
    if start_service; then
      ok "restarted (was running stale code; logs: service/service.log)"
    else
      tail -25 "$SERVICE_DIR/service.log" >&2
      fail "service did not come back up after the stale-code restart — see service/service.log"
    fi
  else
    skip "already running at $BASE_URL (code up to date)"
  fi
else
  if start_service; then
    ok "started in background (logs: service/service.log)"
  else
    tail -25 "$SERVICE_DIR/service.log" >&2
    fail "service did not come up in 60s — see service/service.log"
  fi
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

# ---------------------------------------------------- 5. Mini-app feed service
phase 5 "Mini-app feed service"
# Wrapped in a function purely so a soft failure can `return` out of the
# phase without re-indenting (and without aborting the whole run).
phase5_miniapp() {

# MINIAPP_PORT: the single source of truth for the mini-app port, read here
# and reused by the tunnel + watchdog phases below. Same in-place-fill /
# append discipline as FEED_KEY: .env.example ships a MINIAPP_PORT= line, but
# a .env created before this setting existed has none at all.
if grep -q '^MINIAPP_PORT=.\+' "$SERVICE_DIR/.env"; then
  skip "MINIAPP_PORT already set"
elif grep -q '^MINIAPP_PORT=$' "$SERVICE_DIR/.env"; then
  sed -i "s/^MINIAPP_PORT=\$/MINIAPP_PORT=$MINIAPP_PORT_DEFAULT/" "$SERVICE_DIR/.env"
  ok "filled blank MINIAPP_PORT in .env ($MINIAPP_PORT_DEFAULT)"
else
  if [[ -s "$SERVICE_DIR/.env" ]] && [[ "$(tail -c1 "$SERVICE_DIR/.env" | wc -l)" -eq 0 ]]; then
    printf '\n' >> "$SERVICE_DIR/.env"
  fi
  printf 'MINIAPP_PORT=%s\n' "$MINIAPP_PORT_DEFAULT" >> "$SERVICE_DIR/.env"
  ok "added MINIAPP_PORT=$MINIAPP_PORT_DEFAULT to .env"
fi
MINIAPP_PORT="$(grep -oP '^MINIAPP_PORT=\K.+' "$SERVICE_DIR/.env" | tail -1 | tr -d '"'"'" || true)"
[[ "$MINIAPP_PORT" =~ ^[0-9]+$ ]] || MINIAPP_PORT="$MINIAPP_PORT_DEFAULT"
MINIAPP_URL="http://127.0.0.1:$MINIAPP_PORT"
miniapp_alive() { curl -sf -m 3 "$MINIAPP_URL/healthz" >/dev/null 2>&1; }

feed_key_changed=0
if grep -q '^FEED_KEY=.\+' "$SERVICE_DIR/.env"; then
  skip "FEED_KEY already set"
else
  feed_key="$(openssl rand -hex 24 2>/dev/null || true)"
  [[ -n "$feed_key" ]] || feed_key="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(24))')"
  if [[ -z "$feed_key" ]]; then
    soft_fail "could not generate FEED_KEY" \
              "mini-app (live chart) — no FEED_KEY could be generated; set one by hand in service/.env"
    return 0
  fi
  if grep -q '^FEED_KEY=$' "$SERVICE_DIR/.env"; then
    # .env.example ships a blank FEED_KEY= line (so it's documented and
    # diff-visible); fill that line in place rather than appending, or a
    # second FEED_KEY= line would exist and the bridge's (pre-fix) first-
    # match parser would keep reading the blank one forever.
    sed -i "s/^FEED_KEY=\$/FEED_KEY=$feed_key/" "$SERVICE_DIR/.env"
    ok "filled blank FEED_KEY in .env"
  else
    # No FEED_KEY= line at all: append one, guarding against a
    # hand-truncated .env whose last line has no trailing newline (which
    # would otherwise concatenate onto that line instead of adding a new
    # one).
    if [[ -s "$SERVICE_DIR/.env" ]] && [[ "$(tail -c1 "$SERVICE_DIR/.env" | wc -l)" -eq 0 ]]; then
      printf '\n' >> "$SERVICE_DIR/.env"
    fi
    printf 'FEED_KEY=%s\n' "$feed_key" >> "$SERVICE_DIR/.env"
    ok "generated FEED_KEY into .env"
  fi
  feed_key_changed=1
fi

if [[ "$feed_key_changed" == 1 ]] && miniapp_alive; then
  echo "  FEED_KEY changed — restarting mini-app so it picks up the new key"
  pkill -f "uvicorn app.miniapp:app" 2>/dev/null || true
  for _ in $(seq 1 10); do
    miniapp_alive || break
    sleep 1
  done
  if miniapp_alive; then
    soft_fail "could not stop the stale mini-app process (still holding the old FEED_KEY)" \
              "mini-app (live chart) — stale process on $MINIAPP_URL still holds the old FEED_KEY; pkill -f 'uvicorn app.miniapp:app' and re-run"
    return 0
  fi
  ok "stopped stale mini-app process"
fi

# Port-conflict guard (incident 2026-08-19): 9001 — the old default — is
# bound by a Docker mosquitto WebSocket listener on this machine, so uvicorn
# died with "address already in use" and the run aborted before the tunnel
# and the watchdog ever started. Detect the squatter BEFORE starting, name
# it, and soft-fail this phase only. A listener that answers our own
# /healthz is our mini-app, not a conflict.
if ! miniapp_alive && port_listening "$MINIAPP_PORT"; then
  soft_fail "port $MINIAPP_PORT is already in use by $(port_owner_hint "$MINIAPP_PORT") — mini-app NOT started" \
            "mini-app (live chart) — port $MINIAPP_PORT taken; set MINIAPP_PORT=<free port> in service/.env and re-run scripts/setup.sh (log: service/miniapp.log)"
  echo "  Remedy: set MINIAPP_PORT=<a free port> in service/.env, then re-run scripts/setup.sh."
  echo "  (Do NOT stop the other process unless you know it is yours to stop.)"
  return 0
fi

if miniapp_alive; then
  skip "already running at $MINIAPP_URL"
else
  (cd "$SERVICE_DIR" && nohup "$VENV/bin/uvicorn" app.miniapp:app --host 127.0.0.1 --port "$MINIAPP_PORT" \
      >>"$SERVICE_DIR/miniapp.log" 2>&1 &)
  up=""
  for _ in $(seq 1 30); do
    if miniapp_alive; then up=yes; break; fi
    sleep 1
  done
  if [[ -z "$up" ]]; then
    tail -25 "$SERVICE_DIR/miniapp.log" >&2
    soft_fail "mini-app did not come up in 30s — see service/miniapp.log" \
              "mini-app (live chart) — did not start on $MINIAPP_URL (log: service/miniapp.log)"
    return 0
  fi
  ok "started in background on port $MINIAPP_PORT (logs: service/miniapp.log)"
fi
}
phase5_miniapp

# -------------------------------------- 6. Live chart config (profile -> .env)
phase 6 "Live chart config (profile -> .env)"
# The onboarding page (/onboarding) collects ngrok_authtoken, ngrok_domain
# and miniapp_direct_link into the service profile alongside the Telegram
# fields. This phase syncs whatever's in the profile into service/.env
# BEFORE the ngrok phase below runs, mirroring the Telegram phase's
# profile-first-then-.env precedence. GET /api/profile masks ngrok_authtoken
# the same way it masks telegram_bot_token (see app/main.py's
# _mask_secret) -- "****" + last 4 chars -- so the raw token cannot come
# from that endpoint. It's read directly from the sqlite profile row
# instead (read-only URI connection; never opened for writes).
db_path="$(grep -oP '^DB_PATH=\K.+' "$SERVICE_DIR/.env" || true)"
[[ -n "$db_path" ]] || db_path="xau_assistant.db"
[[ "$db_path" = /* ]] || db_path="$SERVICE_DIR/$db_path"

profile_json="$(curl -sf "$BASE_URL/api/profile" || true)"
raw_ngrok_token=""
if [[ -f "$db_path" ]]; then
  raw_ngrok_token="$("$VENV/bin/python" - "$db_path" <<'PY' || true
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    row = conn.execute("SELECT ngrok_authtoken FROM profile WHERE id=1").fetchone()
    print((row[0] or "") if row else "")
except sqlite3.OperationalError:
    print("")
PY
)"
fi
profile_domain="$(echo "$profile_json" | "$VENV/bin/python" -c '
import json, sys
p = (json.load(sys.stdin).get("profile") or {})
print(p.get("ngrok_domain") or "")' 2>/dev/null || true)"
profile_link="$(echo "$profile_json" | "$VENV/bin/python" -c '
import json, sys
p = (json.load(sys.stdin).get("profile") or {})
print(p.get("miniapp_direct_link") or "")' 2>/dev/null || true)"

# Upsert KEY=VAL into a .env file (default: service/.env; a third arg lets
# tests point this at a scratch copy instead). Replaces an existing
# KEY=... line in place; appends a new line if absent, guarding a missing
# trailing newline first (the FEED_KEY in-place/append lessons -- a
# hand-truncated .env's last line must not get concatenated onto). Returns
# 1 (no-op) when the stored value already matches, so callers can tell
# "changed" from "already correct" for the apply-hint below. The token
# itself is never echoed anywhere -- only a ••••last4 form reaches stdout.
#
# The in-place replace is done in Python, not sed: sed's replacement text
# treats `&` (whole match) and `\` as special, so a value containing either
# (e.g. a t.me deep link with `?startapp=...&x=y`) would silently corrupt
# the line on a second run if spliced into a sed `s|...|...|` script. The
# value is passed as a plain argv element (never interpolated into a shell
# string that gets re-parsed or eval'd), so no escaping class of bug
# applies here at all.
env_upsert() {
  local key="$1" val="$2" target="${3:-$SERVICE_DIR/.env}"
  local existing
  existing="$(grep -oP "^${key}=\K.*" "$target" 2>/dev/null || true)"
  [[ "$existing" == "$val" ]] && return 1
  if grep -q "^${key}=" "$target" 2>/dev/null; then
    "$VENV/bin/python" - "$target" "$key" "$val" <<'PY'
import sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
prefix = key + "="
with open(path) as f:
    lines = f.readlines()
out = []
replaced = False
for line in lines:
    if not replaced and line.startswith(prefix):
        out.append(f"{key}={val}\n")
        replaced = True
    else:
        out.append(line)
with open(path, "w") as f:
    f.writelines(out)
PY
  else
    if [[ -s "$target" ]] && [[ "$(tail -c1 "$target" | wc -l)" -eq 0 ]]; then
      printf '\n' >>"$target"
    fi
    printf '%s=%s\n' "$key" "$val" >>"$target"
  fi
  return 0
}

livechart_changed=0
if [[ -n "$raw_ngrok_token" ]]; then
  if env_upsert NGROK_AUTHTOKEN "$raw_ngrok_token"; then
    ok "NGROK_AUTHTOKEN synced from profile (••••${raw_ngrok_token: -4})"
    livechart_changed=1
  else
    skip "NGROK_AUTHTOKEN already up to date"
  fi
elif grep -q '^NGROK_AUTHTOKEN=.\+' "$SERVICE_DIR/.env"; then
  skip "NGROK_AUTHTOKEN already set in .env"
else
  skip "no ngrok authtoken in profile or .env -- add it at $BASE_URL/onboarding"
fi

if [[ -n "$profile_domain" ]]; then
  public_url="https://$profile_domain"
  if env_upsert MINIAPP_PUBLIC_URL "$public_url"; then
    ok "MINIAPP_PUBLIC_URL synced from profile ($public_url)"
    livechart_changed=1
  else
    skip "MINIAPP_PUBLIC_URL already up to date"
  fi
elif grep -q '^MINIAPP_PUBLIC_URL=.\+' "$SERVICE_DIR/.env"; then
  skip "MINIAPP_PUBLIC_URL already set in .env"
else
  skip "no ngrok domain in profile or .env -- add it at $BASE_URL/onboarding"
fi

if [[ -n "$profile_link" ]]; then
  if env_upsert MINIAPP_DIRECT_LINK "$profile_link"; then
    ok "MINIAPP_DIRECT_LINK synced from profile"
    livechart_changed=1
  else
    skip "MINIAPP_DIRECT_LINK already up to date"
  fi
elif grep -q '^MINIAPP_DIRECT_LINK=.\+' "$SERVICE_DIR/.env"; then
  skip "MINIAPP_DIRECT_LINK already set in .env"
else
  skip "no mini-app direct link in profile or .env yet (set it after the BotFather /newapp step) -- add it at $BASE_URL/onboarding"
fi

# Settings are read once at process startup (app/config.py's Settings()).
# A changed value here won't take effect until the MAIN service restarts --
# but this script never auto-restarts trading-critical processes (same
# stance as the FEED_KEY-changed handling in the mini-app phase above,
# which restarts only the mini-app, never app.main), so just say so.
if [[ "$livechart_changed" == 1 ]]; then
  ok "live chart config changed -- restart the service to apply"
fi

# ------------------------------------------------------- 7. ngrok tunnel
phase 7 "ngrok static-domain tunnel"
# Wrapped for soft_fail early-return (see phase 5): a tunnel that will not
# come up must not cost the owner the watchdog phase below.
phase7_tunnel() {
env_ngrok_token="$(grep -oP '^NGROK_AUTHTOKEN=\K.+' "$SERVICE_DIR/.env" || true)"
env_miniapp_url="$(grep -oP '^MINIAPP_PUBLIC_URL=\K.+' "$SERVICE_DIR/.env" || true)"

if [[ -z "$env_ngrok_token" || -z "$env_miniapp_url" ]]; then
  skip "NGROK_AUTHTOKEN / MINIAPP_PUBLIC_URL not set in .env — tunnel not started"
else
  tunnel_domain="${env_miniapp_url#https://}"
  tunnel_domain="${tunnel_domain#http://}"
  ngrok_bin="$HOME/.local/bin/ngrok"

  if [[ -x "$ngrok_bin" ]]; then
    skip "ngrok binary already installed"
  else
    mkdir -p "$HOME/.local/bin"
    ngrok_tmpdir="$(mktemp -d)"
    # bin.equinox.io is ngrok's own CDN for release binaries; if it's ever
    # unreachable, ngrok also publishes an apt repo (apt.ngrok.com) as a
    # fallback — not used here so this stays a no-sudo, no-package-manager
    # install into the user's own ~/.local/bin.
    if ! curl -sfL -o "$ngrok_tmpdir/ngrok.tgz" "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz"; then
      rm -rf "$ngrok_tmpdir"
      soft_fail "ngrok download failed" "ngrok tunnel — download failed; re-run when the network is back"
      return 0
    fi
    # Extract into the temp dir and check tar's own exit status — extracting
    # straight into ~/.local/bin with only a presence check afterward would
    # let a truncated/corrupt download become the permanently "installed"
    # binary (a later re-run would see -x true and SKIP forever). mv is
    # atomic on the same filesystem, so ngrok_bin only ever points at a
    # binary that extracted cleanly.
    if ! tar xzf "$ngrok_tmpdir/ngrok.tgz" -C "$ngrok_tmpdir" ngrok; then
      rm -rf "$ngrok_tmpdir"
      soft_fail "ngrok extract failed" "ngrok tunnel — the downloaded archive did not extract; re-run"
      return 0
    fi
    if [[ ! -x "$ngrok_tmpdir/ngrok" ]]; then
      rm -rf "$ngrok_tmpdir"
      soft_fail "ngrok binary missing after extract" "ngrok tunnel — extracted archive had no ngrok binary; re-run"
      return 0
    fi
    mv -f "$ngrok_tmpdir/ngrok" "$ngrok_bin"
    rm -rf "$ngrok_tmpdir"
    if [[ ! -x "$ngrok_bin" ]]; then
      soft_fail "ngrok binary missing after install" "ngrok tunnel — install to $ngrok_bin failed; re-run"
      return 0
    fi
    ok "installed ngrok to $ngrok_bin"
  fi

  if grep -q '^ *authtoken:' "$HOME/.config/ngrok/ngrok.yml" 2>/dev/null; then
    skip "ngrok authtoken already configured"
  else
    if ! "$ngrok_bin" config add-authtoken "$env_ngrok_token" >/dev/null; then
      soft_fail "ngrok config add-authtoken failed" "ngrok tunnel — authtoken rejected; check NGROK_AUTHTOKEN in service/.env"
      return 0
    fi
    ok "ngrok authtoken configured"
  fi

  # Verify by DOMAIN, not by "some ngrok http process exists" — pgrep -f
  # "ngrok http" would be satisfied by any unrelated tunnel on the box and
  # SKIP without ever exposing the mini-app. Query the local agent API for the
  # configured domain; if 4040 isn't answering at all, treat that as
  # not-running too (never mistake "can't tell" for "already up"). This
  # keeps working with --inspect=false below — confirmed empirically
  # (2026-08-15): the agent/tunnels API (port 4040) stays up and still
  # reports the tunnel's domain/config; only the separate request-capture
  # buffer (/api/requests/http) goes empty. No need to fall back to
  # probing the tunnel domain's /healthz directly.
  tunnel_running() {
    curl -sf -m 3 "http://127.0.0.1:4040/api/tunnels" 2>/dev/null | grep -q "$tunnel_domain"
  }

  if tunnel_running; then
    skip "tunnel already running for $tunnel_domain"
  else
    # --inspect=false (security-review fix, 2026-08-15): with inspection
    # on (ngrok's default), the local 4040 web UI/API retains a rolling
    # buffer of full request/response captures — including raw request
    # URIs and headers, which for this app means a viewer's initData
    # (sent as ?initData= on the WS path, or replayed via the REST
    # header) sits there fully replayable to anything with access to
    # 127.0.0.1:4040. Only the mini-app (MINIAPP_PORT) is ever meant to be
    # reachable from outside this box; the inspection buffer must not
    # become a second, higher-privilege leak of the same secret.
    nohup "$ngrok_bin" http --url="$tunnel_domain" --inspect=false "$MINIAPP_PORT" --log /tmp/ngrok.log >/dev/null 2>&1 &
    up=""
    for _ in $(seq 1 20); do
      if tunnel_running; then up=yes; break; fi
      sleep 1
    done
    if [[ -z "$up" ]]; then
      tail -25 /tmp/ngrok.log >&2
      soft_fail "tunnel did not come up in 20s — see /tmp/ngrok.log" \
                "ngrok tunnel — https://$tunnel_domain not live (log: /tmp/ngrok.log)"
      return 0
    fi
    ok "tunnel live at https://$tunnel_domain (logs: /tmp/ngrok.log)"
  fi
fi
}
phase7_tunnel

# ------------------------------------------------------- 7b. Watchdog (supervisor)
# ALWAYS reached: every phase above either succeeds or soft-fails, so the
# self-healing net starts even when the mini-app or the tunnel did not. It
# re-checks each link every 30 s, which is what brings up whatever failed
# above once the cause is gone (e.g. a port freed, the network back).
phase 8 "Watchdog (keeps main/miniapp/tunnel/bridge up)"
if pgrep -f "scripts/xau-watchdog.sh" >/dev/null 2>&1; then
  skip "watchdog already running (log: /tmp/xau-watchdog.log)"
else
  nohup bash "$REPO_ROOT/scripts/xau-watchdog.sh" >/dev/null 2>&1 &
  sleep 1
  if pgrep -f "scripts/xau-watchdog.sh" >/dev/null 2>&1; then
    ok "watchdog started — restarts any dead link every 30 s, reports to Telegram (log: /tmp/xau-watchdog.log)"
  else
    soft_fail "watchdog failed to start" \
              "watchdog — not running; start it by hand: nohup bash scripts/xau-watchdog.sh >/dev/null 2>&1 &"
  fi
fi

# ----------------------------------------------------------------- 9. Telegram
phase 9 "Telegram"
# Wrapped for soft_fail early-return (see phase 5): Telegram credentials are
# not worth aborting the MT5 install over.
phase9_telegram() {
# Wait for the main service before reading the profile. The watchdog started
# in phase 8 deploys any newer code by restarting app.main — a ~25 s window
# (torch cold start) in which /api/profile answers nothing. Reading an empty
# body used to kill the whole run right here (live, 2026-08-19), taking the
# MT5 compile and the handoff with it.
svc_up=""
for _ in $(seq 1 60); do
  if curl -sf -m 3 "$BASE_URL/health" >/dev/null 2>&1; then svc_up=yes; break; fi
  sleep 1
done
if [[ -z "$svc_up" ]]; then
  soft_fail "main service not answering after 60s — cannot read the Telegram profile" \
            "Telegram — service unreachable at $BASE_URL (log: service/service.log)"
  return 0
fi
profile_json="$(curl -sf -m 10 "$BASE_URL/api/profile" || true)"
if [[ -z "$profile_json" ]]; then
  soft_fail "could not read the service profile (empty /api/profile response)" \
            "Telegram — profile unreadable; check $BASE_URL/onboarding"
  return 0
fi
profile_has_tg="$(printf '%s' "$profile_json" | "$VENV/bin/python" -c '
import json, sys
try:
    p = json.load(sys.stdin).get("profile") or {}
except Exception:
    p = {}
print("yes" if p.get("telegram_bot_token") and p.get("telegram_chat_id") else "no")' 2>/dev/null || echo no)"
env_token="$(grep -oP '^TELEGRAM_BOT_TOKEN=\K.+' "$SERVICE_DIR/.env" || true)"
env_chat="$(grep -oP '^TELEGRAM_CHAT_ID=\K.+' "$SERVICE_DIR/.env" || true)"

if [[ "$profile_has_tg" == yes ]]; then
  skip "credentials already in service profile"
elif [[ -n "$env_token" && -n "$env_chat" ]]; then
  if ! curl -sf -m 10 "https://api.telegram.org/bot$env_token/getMe" >/dev/null; then
    soft_fail ".env TELEGRAM_BOT_TOKEN was rejected by Telegram (getMe failed)" \
              "Telegram — bot token in service/.env is invalid or the network is down"
    return 0
  fi
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
  if [[ -z "$token" ]]; then
    soft_fail "could not validate a bot token" "Telegram — no valid bot token; re-run scripts/setup.sh to retry"
    return 0
  fi
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
  if [[ -z "$chat_id" ]]; then
    soft_fail "the bot received no message in 120s" \
              "Telegram — message your bot once, then re-run scripts/setup.sh (earlier phases SKIP)"
    return 0
  fi
  if ! curl -sf -X POST "$BASE_URL/api/profile" -H 'Content-Type: application/json' \
      -d "{\"telegram_bot_token\":\"$token\",\"telegram_chat_id\":\"$chat_id\"}" >/dev/null; then
    soft_fail "saving credentials to the service profile failed" \
              "Telegram — credentials not saved; add them at $BASE_URL/onboarding"
    return 0
  fi
  if ! curl -sf -m 10 -X POST "https://api.telegram.org/bot$token/sendMessage" \
      -d "chat_id=$chat_id" --data-urlencode "text=✅ XAU Assistant setup: Telegram connected." >/dev/null; then
    soft_fail "test message failed to send" "Telegram — credentials saved but the test message failed"
    return 0
  fi
  ok "linked chat $chat_id (token ••••${token: -4}); test message sent"
fi
}
phase9_telegram

# ------------------------------------------------------ 9. MT5 install + compile
phase 10 "MT5 install + compile"
# Wrapped for soft_fail early-return (see phase 5): a compile problem is
# real, but the service side of the system stays up and reported.
phase10_mt5() {
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
if [[ -z "$result_line" ]]; then
  read_log >&2
  soft_fail "could not find a result line in the compile log" \
            "MT5 EA — compile result unknown (log: $compile_log)"
  return 0
fi
errors="$(echo "$result_line" | grep -oiE '[0-9]+ error' | grep -oE '[0-9]+')"
if [[ "$errors" != 0 ]]; then
  read_log | tail -30 >&2
  soft_fail "compilation failed: $result_line" \
            "MT5 EA — compilation failed, the terminal still runs the previous .ex5 (log: $compile_log)"
  return 0
fi
if [[ ! -f "$mql5/Experts/XauAssistant.ex5" ]]; then
  soft_fail "compile reported success but XauAssistant.ex5 is missing" \
            "MT5 EA — no .ex5 produced; compile once by hand in MetaEditor"
  return 0
fi
ok "compiled: ${result_line#"${result_line%%[![:space:]]*}"}"
}
phase10_mt5

# ------------------------------------------ 10. Handoff + end-to-end verification
phase 11 "Handoff + end-to-end verify"
# Wrapped for soft_fail early-return (see phase 5): "no EA heartbeat yet" is
# a checklist item for the owner, not a reason to exit non-zero — the
# summary below says so plainly.
phase11_handoff() {
cat <<'EOF'

  Two manual steps remain in MetaTrader 5 (MT5 stores these encrypted; no script can set them):

    1. Tools > Options > Expert Advisors:
         tick "Allow WebRequest for listed URL" and add exactly:  http://127.0.0.1:9000
    2. Drag XauAssistant (Navigator > Expert Advisors) onto a XAUUSD chart,
         tick "Allow Algo Trading" in the dialog, press OK.
       (If the EA was already on the chart, remove and re-attach it.)

  Two strategies are registered and BOTH are evaluated on every bar; only the
  active one places trades, the other shadow-logs its signals so the two can be
  compared on the same live market:

    halftrend_ema_v1   M5   <- active by default
    halftrend_m15_v1   M15  shadow (3 waiting bars, EMA-200 confirmation)

  Switch with /strategy in Telegram — it applies on the next bar, no recompile.
  The chart's own timeframe is display only; each strategy trades its own.

  Waiting up to 5 minutes for the EA heartbeat (fires every 5s, even with markets closed)...
EOF
deadline=$((SECONDS + 300))
beat=""
while (( SECONDS < deadline )); do
  # A heartbeat only proves the EA is RUNNING. It can run perfectly while
  # unable to place a single trade -- the AutoTrading button off, the kill
  # switch latched, or the service holding it in MANUAL. Reporting "setup
  # complete" in that state is the failure that costs money quietly, so the
  # verify reads the trading-capability fields the heartbeat already carries
  # (nested under "heartbeat"; no service change was needed for this).
  beat="$(curl -sf -m 3 "$BASE_URL/api/state" | "$VENV/bin/python" -c '
import json, sys
d = json.load(sys.stdin)
a = d.get("age_s")
if a is None or a >= 30:
    print("")
else:
    hb = d.get("heartbeat") or {}
    print("|".join([
        "yes",
        "on"   if hb.get("algo_trading") else "OFF",
        "clear" if not hb.get("kill_switch") else "TRIPPED",
        str(hb.get("active_strategy") or "?"),
        str(d.get("mode") or "?"),
    ]))' || true)"
  [[ -n "$beat" ]] && break
  sleep 5
done
if [[ -n "$beat" ]]; then
  IFS='|' read -r _b algo kill active exmode <<<"$beat"
  ok "EA heartbeat received — end-to-end wiring confirmed"
  echo "     active strategy : $active   (execution mode: $exmode)"
  # Each of these is a state where the EA is alive and CANNOT trade. They are
  # reported separately because the fix differs for each.
  if [[ "$algo" != "on" ]]; then
    soft_fail "EA is running but Algo Trading is OFF" \
              "MT5 — the EA cannot trade: switch the toolbar Algo Trading button ON (green)"
  fi
  if [[ "$kill" != "clear" ]]; then
    soft_fail "EA is running but the kill switch is TRIPPED" \
              "Risk — the kill switch is latched: no new trades until it is reset (see /status)"
  fi
  if [[ "$exmode" != "auto" ]]; then
    echo "     note: execution mode is $exmode — signals raise Telegram proposals instead of trading."
  fi
  cat <<EOF

  ✅ Setup complete.
     Dashboard:   $BASE_URL/
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
  Re-run scripts/setup.sh afterwards — phases 1-7 will SKIP and the wait restarts.
EOF
  soft_fail "EA heartbeat not observed" \
            "MT5 EA — no heartbeat: attach the EA to a XAUUSD chart and allow the WebRequest URL (checklist above)"
  return 0
fi
}
phase11_handoff

# Candle-history notice (non-fatal, no phase status, no effect on the exit
# code). The `candles` table is what the Backtest page replays; /analyze
# fills it one closed bar at a time, so a fresh install has nothing to
# replay for months. setup.sh cannot run the backfill itself — the pull
# needs WINDOWS python plus a running terminal (MetaTrader5 package) — so
# it prints the two-step runbook instead. Read through the venv python on a
# READ-ONLY uri connection rather than the sqlite3 CLI (not installed on
# this machine, and a `command -v` guard would silently never fire here);
# any error (no db, no table) counts as empty and prints the hint.
candle_rows="$("$VENV/bin/python" - "$db_path" <<'PY' 2>/dev/null || true
import sqlite3, sys
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    print(conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0])
except Exception:
    print(0)
PY
)"
if [[ -z "$candle_rows" || "$candle_rows" == "0" ]]; then
  echo "  NOTE: the candles table is empty — the Backtest page has no history to replay."
  echo "        1) Windows: python.exe scripts/dump_bars.py 75000 bars_max.json"
  echo "        2) WSL:     cd service && python3 ../scripts/backfill_candles.py ../bars_max.json"
fi

# Exit code contract: non-zero ONLY when a CRITICAL phase failed (those call
# `fail`, which exits immediately). Soft failures are reported by the summary
# that the EXIT trap prints, and leave the exit status 0 so a launcher does
# not treat "the chart is down" as "setup did not run".
exit 0
