#!/usr/bin/env bash
# xau-watchdog.sh — keeps the LIVE CHART chain (and the main service process)
# up: checks every link every INTERVAL seconds and restarts ONLY the failed
# one. Born 2026-08-17 after the mini-app served two-day-old code (a deploy
# forgot the restart) and a restart window produced tunnel 502s.
#
# Supervises PROCESSES only — never trading decisions. Routine self-heals
# and redeploys are SILENT (owner request 2026-08-18: log-only); the ONLY
# Telegram message is the alarm — a link still DOWN after MAX_FAILS
# restarts, i.e. something the watchdog cannot fix by itself.
#
# Links:  main service :9000 /health   | miniapp :<MINIAPP_PORT> /healthz
#         ngrok tunnel  (agent API domain match + public /healthz)
#         Windows bridge (feed freshness: /feed/push seen in the miniapp log
#                        within FEED_STALE_S)
#         MT5 EA        (heartbeat age via /api/state; a detached/silent EA
#                        while MT5 runs -> restart MT5, whose start config
#                        [StartUp] re-attaches the EA — 2026-08-24, born from
#                        the 17:44 silent-detach that left the EA off every
#                        chart for hours)
# Stale-code guard: a service whose process is OLDER than its code files on
#         disk is restarted (the exact 08-17 failure).
# Backoff: a link that fails MAX_FAILS restarts in a row is left alone for
#         COOLDOWN_S (and reported), so a truly broken component doesn't loop.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVC="$REPO/service"
INTERVAL="${WATCHDOG_INTERVAL:-30}"
FEED_STALE_S="${WATCHDOG_FEED_STALE_S:-90}"
EA_STALE_S="${WATCHDOG_EA_STALE_S:-180}"
MAX_FAILS=3
COOLDOWN_S=600
LOG="/tmp/xau-watchdog.log"
source "$REPO/scripts/lib/stale-code.sh"
# Singleton. setup.sh guards with pgrep, which loses a race: on 2026-08-19 two
# runs seconds apart left TWO supervisors alive, each restarting the same link
# and each alarming. flock is race-free -- a second instance exits quietly, so
# "run the launcher twice" can never fan out into duplicate supervisors.
# The lock lives IN THE REPO (not /tmp): on 2026-08-2x a /tmp sweep deleted
# the old /tmp/xau-watchdog.lock, the next open() recreated it with a fresh
# inode, and a second watchdog acquired that new file's flock while the
# first still held the original inode -- two supervisors running at once.
# .run/ is gitignored and never cleaned by anything but the owner.
LOCK_DIR="$REPO/.run"
mkdir -p "$LOCK_DIR"
LOCK="$LOCK_DIR/xau-watchdog.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date '+%F %T') another watchdog holds $LOCK -- exiting" >> "$LOG"
  exit 0
fi
declare -A fails cooldown_until
env_get() { grep -oP "^$1=\K.+" "$SVC/.env" 2>/dev/null | tail -1 | tr -d '"'"'" ; }
PUBLIC_URL="$(env_get MINIAPP_PUBLIC_URL)"; TUNNEL_DOMAIN="${PUBLIC_URL#https://}"
NGROK_TOKEN="$(env_get NGROK_AUTHTOKEN)"
# Mini-app port: .env is the single source of truth (moved 9001 -> 9101 on
# 2026-08-19 — a Docker mosquitto owns 9001 on this machine). Probing or
# restarting on a hard-coded port would supervise the WRONG process.
MINIAPP_PORT="$(env_get MINIAPP_PORT)"; MINIAPP_PORT="${MINIAPP_PORT:-9101}"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
notify() { curl -s -m 5 -X POST http://127.0.0.1:9000/notify \
              -H 'Content-Type: application/json' \
              -d "{\"text\":\"♻️ watchdog: $1\"}" >/dev/null 2>&1 || true; }

# ---- checks -----------------------------------------------------------
main_ok()    { curl -sf -m 4 http://127.0.0.1:9000/health >/dev/null; }
miniapp_ok() { curl -sf -m 4 "http://127.0.0.1:$MINIAPP_PORT/healthz" >/dev/null; }
tunnel_ok()  { [[ -n "$TUNNEL_DOMAIN" ]] || return 0   # unconfigured = not our job
               curl -sf -m 3 http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -q "$TUNNEL_DOMAIN" \
               && curl -sf -m 8 -H "ngrok-skip-browser-warning: 1" "https://$TUNNEL_DOMAIN/healthz" >/dev/null; }
feed_ok()    { # bridge alive = the miniapp saw a /feed/push recently. /healthz gives
               # feed_age_s (null = never since this process started) + uptime_s.
               # null is only excusable while the miniapp is YOUNG; a null older
               # than FEED_STALE_S means no bridge push has arrived at all -> dead.
               local hz; hz=$(curl -sf -m 4 "http://127.0.0.1:$MINIAPP_PORT/healthz" 2>/dev/null) || return 0  # miniapp down: not the bridge's fault
               local age up
               age=$(grep -oP '"feed_age_s":\s*\K[0-9.]+' <<<"$hz")
               up=$(grep -oP '"uptime_s":\s*\K[0-9.]+' <<<"$hz")
               if [[ -z "$age" ]]; then (( ${up%.*} < FEED_STALE_S )); return; fi
               (( ${age%.*} < FEED_STALE_S )); }
mt5_running() { tasklist.exe 2>/dev/null | grep -qi "terminal64.exe"; }
hb_age()      { curl -sf -m 4 http://127.0.0.1:9000/api/state 2>/dev/null \
                | grep -oP '"age_s":\s*\K[0-9.]+'; }
ea_ok()      { # EA heartbeat freshness, judged only when it can be judged:
               # service down -> main's problem, not the EA's; MT5 not running
               # -> the owner closed it (or the launcher hasn't started it) —
               # reopening the terminal on our own is NOT this watchdog's call.
               main_ok || return 0
               mt5_running || return 0
               local age; age="$(hb_age)"
               [[ -z "$age" ]] && return 1   # age_s null = no heartbeat since service start
               (( ${age%.*} < EA_STALE_S )); }
# stale-code: proc_started/newest_mtime/stale_code come from
# scripts/lib/stale-code.sh (sourced above), shared with setup.sh's Service
# phase so there is exactly one implementation of "process predates its code".
# Restart at most ONCE per distinct code mtime: the guard compares process
# start time to file mtime, but the main service takes ~25 s to boot (torch),
# and a file touched inside that window (a commit landing, an editor save)
# would otherwise read as "newer than the process" forever -> restart LOOP
# (seen live 2026-08-18: three restarts in three cycles). Remembering the
# mtime we already acted on (via stale_code's LAST_CODE_MTIME side effect)
# makes each code change cost exactly one restart.
declare -A acted_mtime
stale_code_once() {   # $1 = key into acted_mtime, $2 = pgrep pattern, $3.. = code paths
  local key="$1"; shift
  stale_code "$@" || return 1
  [[ "${acted_mtime[$key]:-}" == "$LAST_CODE_MTIME" ]] && return 1   # already restarted for this exact code
  acted_mtime[$key]="$LAST_CODE_MTIME"
  return 0
}

# ---- restarts ---------------------------------------------------------
# Log paths must match setup.sh's (service/service.log, service/miniapp.log).
# Until 2026-08-24 the watchdog appended to /tmp/xau-service.log and
# /tmp/miniapp.log instead: every watchdog restart silently moved the output
# somewhere nobody tails, so a crash loop looked like a log that just stopped.
# Same 20 MB one-generation rotation setup.sh applies before each start.
LOG_MAX_BYTES=$((20 * 1024 * 1024))
rotate_log() {   # $1 = log path
  local f="$1" size
  [[ -f "$f" ]] || return 0
  size="$(stat -c %s "$f" 2>/dev/null || echo 0)"
  (( size > LOG_MAX_BYTES )) || return 0
  mv -f "$f" "$f.1" 2>/dev/null || return 0
  log "rotated $(basename "$f") (was $((size / 1024 / 1024)) MB)"
}
restart_main()    { pkill -f "uvicorn app.main:app" || true; sleep 2
                    rotate_log "$SVC/service.log"
                    (cd "$SVC" && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9000 >> "$SVC/service.log" 2>&1 &) ; }
restart_miniapp() { pkill -f "uvicorn app.miniapp:app" || true; sleep 2
                    rotate_log "$SVC/miniapp.log"
                    (cd "$SVC" && nohup .venv/bin/uvicorn app.miniapp:app --host 127.0.0.1 --port "$MINIAPP_PORT" >> "$SVC/miniapp.log" 2>&1 &) ; }
restart_tunnel()  { pkill -f "ngrok http" || true; sleep 2
                    [[ -n "$NGROK_TOKEN" && -n "$TUNNEL_DOMAIN" ]] || return 0
                    nohup "$HOME/.local/bin/ngrok" http --url="$TUNNEL_DOMAIN" --inspect=false "$MINIAPP_PORT" --log /tmp/ngrok.log >/dev/null 2>&1 & }
restart_bridge()  { # Kill any old bridge, then launch a DETACHED hidden pythonw.
                    # Lessons (2026-08-17): a Start-Process from a WSL-invoked
                    # PowerShell dies with its wrapper; `cmd /c start "" /B` with
                    # ABSOLUTE Windows paths (not a /mnt/c cwd) survives — the
                    # wrapper itself may hang from WSL's view, hence `timeout`.
                    local win_repo; win_repo="$(wslpath -w "$REPO" 2>/dev/null)"
                    powershell.exe -NoProfile -Command \
                      '$p=Get-CimInstance Win32_Process | ? { $_.Name -like "python*" -and $_.CommandLine -like "*mt5_feed.py*" }; if($p){ $p | % { Stop-Process -Id $_.ProcessId -Force } }' >/dev/null 2>&1 || true
                    sleep 2
                    local py=""
                    for v in 312 311 313; do
                      local cand; cand="$(wslpath -u "$(cmd.exe /c "echo %LOCALAPPDATA%" 2>/dev/null | tr -d '\r')")/Programs/Python/Python$v/pythonw.exe"
                      [[ -f "$cand" ]] && { py="$(wslpath -w "$cand")"; break; }
                    done
                    [[ -n "$py" ]] || { log "bridge: no Windows pythonw found"; return 0; }
                    timeout 25 cmd.exe /c start "" /B "$py" "$win_repo\\bridge\\mt5_feed.py" >/dev/null 2>&1 || true; }

restart_mt5()     { # Gentle close -> wait -> force kill -> relaunch with the
                    # start config whose [StartUp] section re-attaches the EA.
                    # Then BLOCK (up to 4 min) until the heartbeat is fresh:
                    # supervise's immediate recheck must see the truth, not a
                    # terminal that is still booting — a fake early "ok" here
                    # would reset the fail counter and turn a truly broken MT5
                    # into a silent restart loop instead of an alarm.
                    log "EA heartbeat stale — restarting MT5"
                    timeout 20 taskkill.exe /IM terminal64.exe >/dev/null 2>&1 || true
                    local _t; for _t in {1..15}; do mt5_running || break; sleep 2; done
                    if mt5_running; then
                      timeout 20 taskkill.exe /F /IM terminal64.exe >/dev/null 2>&1 || true
                      sleep 3
                    fi
                    local ini; ini="$(wslpath -w "$REPO/scripts/mt5-start.ini")"
                    timeout 25 cmd.exe /c start "" "C:\\Program Files\\MetaTrader 5\\terminal64.exe" "/config:$ini" >/dev/null 2>&1 || true
                    for _t in {1..48}; do
                      sleep 5
                      local age; age="$(hb_age)"
                      if [[ -n "$age" ]] && (( ${age%.*} < 60 )); then
                        log "EA heartbeat back (age ${age}s)"; return 0
                      fi
                    done
                    log "EA heartbeat still absent 4 min after MT5 restart"; }

# ---- supervise one link ---------------------------------------------------
supervise() {   # $1 name  $2 check-fn  $3 restart-fn
  local name="$1" now; now=$(date +%s)
  if (( ${cooldown_until[$name]:-0} > now )); then return; fi
  if "$2"; then fails[$name]=0; return; fi
  fails[$name]=$(( ${fails[$name]:-0} + 1 ))
  log "$name DOWN (fail ${fails[$name]}/$MAX_FAILS) — restarting"
  "$3"; sleep 8
  if "$2"; then log "$name recovered"; fails[$name]=0   # silent: routine self-heal (owner: no Telegram for these, 2026-08-18)
  elif (( fails[$name] >= MAX_FAILS )); then
    log "$name still down after $MAX_FAILS restarts — cooling down ${COOLDOWN_S}s"
    notify "$name still DOWN after $MAX_FAILS restarts — pausing $((COOLDOWN_S/60)) min (check /tmp/xau-watchdog.log)"
    cooldown_until[$name]=$(( now + COOLDOWN_S ))
  fi
}

log "watchdog start (interval ${INTERVAL}s, miniapp port=${MINIAPP_PORT}, tunnel=${TUNNEL_DOMAIN:-none})"
while true; do
  # stale-code guard first (a restart here also clears any transient DOWN)
  if stale_code_once miniapp "uvicorn app.miniapp:app" "$SVC/app/miniapp.py" "$SVC/app/miniapp_auth.py" "$SVC/app/static/miniapp.html"; then
     log "miniapp running code older than disk — restarting"; restart_miniapp; sleep 6; fi   # silent redeploy
  if stale_code_once main "uvicorn app.main:app" "$SVC/app"; then
     log "main service running code older than disk — restarting"; restart_main; sleep 25; fi   # silent redeploy
  supervise main    main_ok    restart_main
  supervise miniapp miniapp_ok restart_miniapp
  supervise tunnel  tunnel_ok  restart_tunnel
  supervise bridge  feed_ok    restart_bridge
  supervise ea      ea_ok      restart_mt5
  sleep "$INTERVAL"
done
