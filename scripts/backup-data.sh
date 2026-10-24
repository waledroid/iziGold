#!/usr/bin/env bash
set -euo pipefail

# Creates a consistent SQLite backup from the running Compose container. The
# named volume remains untouched; the resulting file is copied to ./backups.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$ROOT/backups/xau-assistant-$STAMP.db"
TMP="/tmp/xau-assistant-$STAMP.db"

mkdir -p "$ROOT/backups"
docker compose exec -T main python -c \
  "import sqlite3; src=sqlite3.connect('/data/xau_assistant.db'); dst=sqlite3.connect('$TMP'); src.backup(dst); dst.close(); src.close()"
docker compose cp "main:$TMP" "$TARGET"
docker compose exec -T main rm -f "$TMP"
chmod 600 "$TARGET"
printf 'Backup created: %s\n' "$TARGET"
