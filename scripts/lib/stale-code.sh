#!/usr/bin/env bash
# scripts/lib/stale-code.sh — shared "does this running process predate its
# own code on disk" guard. Sourced by scripts/xau-watchdog.sh (which restarts
# on every stale detection, with its own memoization to avoid a restart loop)
# and scripts/setup.sh's Service phase (which only ever runs once per launch,
# so no memoization is needed there). One implementation, one bug surface —
# born from the 2026-08-2x incident where the service had been started 13
# minutes before a commit added a database field, and ran a full day
# silently dropping that field on every trade while /health stayed green.
#
# Not meant to be executed directly — source it:
#   source "$REPO_ROOT/scripts/lib/stale-code.sh"

# proc_started PATTERN — start time (epoch seconds) of the first process
# whose command line matches PATTERN (pgrep -f), or nothing if none is running.
proc_started() {
  local pid
  pid=$(pgrep -f "$1" | head -1)
  [[ -n "$pid" ]] && stat -c %Y "/proc/$pid" 2>/dev/null
}

# newest_mtime PATH... — newest mtime (epoch seconds) among the .py/.html
# files under the given paths, or nothing if none were found.
newest_mtime() {
  find "$@" -type f \( -name '*.py' -o -name '*.html' \) -printf '%T@\n' 2>/dev/null \
    | sort -n | tail -1 | cut -d. -f1
}

# stale_code PATTERN CODE_PATH... — true (0) when the process matching
# PATTERN (pgrep -f) started before the newest .py/.html mtime under
# CODE_PATH...; false (1) when the process is current, or when either the
# process or the code can't be found (nothing to compare, so "not stale").
# Side effect: sets LAST_CODE_MTIME to the code mtime just computed, so a
# caller that wants to memoize "already restarted for this exact code"
# (the watchdog) doesn't have to call newest_mtime a second time.
LAST_CODE_MTIME=""
stale_code() {
  local pattern="$1" started
  started=$(proc_started "$pattern") || true
  LAST_CODE_MTIME=$(newest_mtime "${@:2}")
  [[ -n "$LAST_CODE_MTIME" && -n "$started" ]] || return 1
  (( LAST_CODE_MTIME > started ))
}
