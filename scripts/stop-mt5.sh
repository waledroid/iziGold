#!/usr/bin/env bash
# Gracefully stop MT5 under Wine so terminal settings (WebRequest allowlist, charts) persist.
set -euo pipefail

WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}"
export WINEPREFIX

# wineserver is NOT on PATH on Debian/Ubuntu (it lives in /usr/lib/<arch>/wine/),
# so the old bare `wineserver -k` here silently failed and left MT5 running —
# which then looked like "the close did not work". Resolve it like launch.py does.
WINESERVER="$(command -v wineserver 2>/dev/null || true)"
if [ -z "$WINESERVER" ]; then
    WINESERVER="$(ls /usr/lib/*/wine/wineserver 2>/dev/null | head -1 || true)"
fi

if pgrep -af "terminal64.exe" >/dev/null 2>&1; then
    echo "Sending graceful close to MT5..."
    wine "$WINEPREFIX/drive_c/windows/system32/taskkill.exe" /IM terminal64.exe >/dev/null 2>&1 || true
    # MT5 under Wine routinely needs >10 s to shut down while it flushes its
    # config; force-killing too early can truncate Config/settings.ini (which
    # holds the WebRequest allowlist). Give it 30 s before the last resort.
    for _ in {1..30}; do
        if ! pgrep -af "terminal64.exe" >/dev/null 2>&1; then
            echo "MT5 exited cleanly. Settings (including WebRequest allowlist) persisted to disk."
            exit 0
        fi
        sleep 1
    done
    echo "Warning: MT5 did not exit after 30 seconds. Force stopping wineserver..."
    if [ -n "$WINESERVER" ]; then "$WINESERVER" -k || true; else echo "wineserver not found; MT5 may still be running" >&2; fi
else
    echo "MT5 is not running."
fi

