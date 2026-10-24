#!/usr/bin/env bash
# Snapshot / restore MT5 terminal settings under Wine.
#
# WHY THIS EXISTS
# The WebRequest allowlist (Tools > Options > Expert Advisors) lives inside
# `Config/settings.ini`, which MT5 stores BINARY/obfuscated and only writes on a
# CLEAN exit. It cannot be set from scripts/mt5-start.ini — that command-line
# override configures the [StartUp] chart, not the security whitelist, and
# MetaQuotes keeps the whitelist GUI-only by design.
#
# So: set it ONCE in the GUI, close MT5 gracefully (scripts/stop-mt5.sh), then
# take a snapshot here. If anything ever force-kills the terminal and the
# allowlist is lost (symptom: `UiApi: WebRequest failed ... err=4014`), restore
# the snapshot instead of redoing the GUI steps.
#
# Usage:
#   scripts/mt5-settings.sh backup     # copy Config/*.ini into .run/mt5-settings/
#   scripts/mt5-settings.sh restore    # put the last snapshot back (MT5 stopped)
#   scripts/mt5-settings.sh status     # show what is saved and what is live

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}"
MT5_DIR="${MT5_DIR:-$WINEPREFIX/drive_c/Program Files/MetaTrader 5}"
CONFIG_DIR="$MT5_DIR/Config"
SNAP_DIR="$ROOT/.run/mt5-settings"
FILES=(settings.ini common.ini terminal.ini)

mt5_running() { pgrep -af 'terminal64.exe' >/dev/null 2>&1; }

case "${1:-status}" in
  backup)
    [[ -d "$CONFIG_DIR" ]] || { echo "Config dir not found: $CONFIG_DIR" >&2; exit 1; }
    mkdir -p "$SNAP_DIR"
    for f in "${FILES[@]}"; do
      [[ -f "$CONFIG_DIR/$f" ]] && cp -f "$CONFIG_DIR/$f" "$SNAP_DIR/$f"
    done
    if mt5_running; then
      echo "WARNING: MT5 is running — the snapshot may predate your latest settings."
      echo "         Prefer: scripts/stop-mt5.sh && scripts/mt5-settings.sh backup"
    fi
    echo "Saved to $SNAP_DIR:"
    ls -l "$SNAP_DIR" | tail -n +2
    ;;

  restore)
    [[ -d "$SNAP_DIR" ]] || { echo "No snapshot at $SNAP_DIR — nothing to restore." >&2; exit 1; }
    # Safety: never blindly overwrite a NEWER live config. The live file is
    # newer whenever the terminal saved more recently than the snapshot, which
    # means it may already contain a freshly-set (and working) allowlist —
    # restoring over it would destroy the very setting we are trying to get.
    if [[ -f "$CONFIG_DIR/settings.ini" && -f "$SNAP_DIR/settings.ini" \
          && "$CONFIG_DIR/settings.ini" -nt "$SNAP_DIR/settings.ini" \
          && "${FORCE:-0}" != "1" ]]; then
      echo "Refusing to restore: live settings.ini is NEWER than the snapshot." >&2
      echo "  live: $(stat -c %y "$CONFIG_DIR/settings.ini" | cut -d. -f1)" >&2
      echo "  snap: $(stat -c %y "$SNAP_DIR/settings.ini" | cut -d. -f1)" >&2
      echo "Set it in the GUI and run 'backup' instead, or force with FORCE=1." >&2
      exit 1
    fi
    if mt5_running; then
      echo "MT5 is running; closing it gracefully first..."
      WINEPREFIX="$WINEPREFIX" bash "$ROOT/scripts/stop-mt5.sh"
      if mt5_running; then
        echo "MT5 refused to close — stop it before restoring (a restore under a" >&2
        echo "running terminal would be overwritten on exit)." >&2
        exit 1
      fi
    fi
    # Keep the pre-restore state so nothing is ever irrecoverable.
    PRELIVE="$ROOT/.run/mt5-settings-prelive-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$PRELIVE"
    for f in "${FILES[@]}"; do
      [[ -f "$CONFIG_DIR/$f" ]] && cp -f "$CONFIG_DIR/$f" "$PRELIVE/$f"
    done
    echo "saved pre-restore copy to $PRELIVE"
    for f in "${FILES[@]}"; do
      [[ -f "$SNAP_DIR/$f" ]] && cp -f "$SNAP_DIR/$f" "$CONFIG_DIR/$f" && echo "restored $f"
    done
    echo "Restored. Start MT5 with: python3 launch.py"
    ;;

  status)
    echo "WINEPREFIX : $WINEPREFIX"
    echo "Config dir : $CONFIG_DIR"
    if [[ -d "$CONFIG_DIR" ]]; then
      for f in "${FILES[@]}"; do
        [[ -f "$CONFIG_DIR/$f" ]] && printf '  live  %-14s %s  %s\n' "$f" \
          "$(stat -c %y "$CONFIG_DIR/$f" | cut -d. -f1)" "$(stat -c %s "$CONFIG_DIR/$f")B"
      done
    else
      echo "  (missing)"
    fi
    if [[ -d "$SNAP_DIR" ]]; then
      echo "Snapshot:"
      for f in "${FILES[@]}"; do
        [[ -f "$SNAP_DIR/$f" ]] && printf '  snap  %-14s %s  %s\n' "$f" \
          "$(stat -c %y "$SNAP_DIR/$f" | cut -d. -f1)" "$(stat -c %s "$SNAP_DIR/$f")B"
      done
    else
      echo "Snapshot: none (run: scripts/mt5-settings.sh backup)"
    fi
    mt5_running && echo "MT5        : running" || echo "MT5        : stopped"
    ;;

  *)
    echo "usage: $0 {backup|restore|status}" >&2
    exit 2
    ;;
esac
