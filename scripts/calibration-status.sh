#!/usr/bin/env bash
# Daily calibration status: summarizes the SQLite accuracy log and sends it
# to the configured Telegram chat. Run manually or from cron.
# Usage: scripts/calibration-status.sh [--print]   (--print: stdout only, no Telegram)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$REPO_ROOT/service"
VENV="$SERVICE_DIR/.venv"
DB="$SERVICE_DIR/xau_assistant.db"
BASE_URL="http://127.0.0.1:9000"
PRINT_ONLY="${1:-}"

msg="$("$VENV/bin/python" - "$DB" "$BASE_URL" <<'PY'
import json, sqlite3, sys, urllib.request

db_path, base = sys.argv[1], sys.argv[2]
db = sqlite3.connect(db_path)

def one(q, default=0):
    row = db.execute(q).fetchone()
    return row[0] if row and row[0] is not None else default

lines = ["📊 XAU calibration status"]

try:
    state = json.load(urllib.request.urlopen(base + "/ui/state", timeout=5))
    age = state.get("age_s")
    hb = state.get("heartbeat") or {}
    ea = f"EA: live ({age:.0f}s ago)" if age is not None and age < 60 else "EA: ⚠️ NO HEARTBEAT"
    lines.append(f"{ea} | balance {hb.get('balance', '?')} | spread {hb.get('spread_points', '?')}pt")
except Exception:
    lines.append("service: ⚠️ NOT RESPONDING on :9000")

total = one("SELECT COUNT(*) FROM signals")
resolved = one("SELECT COUNT(*) FROM signals WHERE ai_correct IS NOT NULL")
last24 = one("SELECT COUNT(*) FROM signals WHERE created_ts > strftime('%s','now') - 86400")
correct = one("SELECT COUNT(*) FROM signals WHERE ai_correct = 1")
lines.append(f"signals: {total} total, {last24} last 24h, {resolved} resolved")
if resolved:
    lines.append(f"AI hit-rate: {100.0 * correct / resolved:.0f}% ({correct}/{resolved})")

for sid, tf, n, res, hits in db.execute(
    """SELECT strategy_id, timeframe, COUNT(*),
              SUM(ai_correct IS NOT NULL), SUM(ai_correct = 1)
       FROM signals GROUP BY strategy_id, timeframe ORDER BY COUNT(*) DESC"""):
    res, hits = res or 0, hits or 0
    pct = f", {100.0 * hits / res:.0f}% hit" if res else ""
    lines.append(f"  {sid} @{tf}: {n} signals, {res} resolved{pct}")

trades = one("SELECT COUNT(*) FROM trades")
if trades:
    lines.append(f"trades: {trades}")
print("\n".join(lines))
PY
)"

echo "$msg"
if [[ "$PRINT_ONLY" == "--print" ]]; then exit 0; fi

creds="$("$VENV/bin/python" -c "
import sqlite3, sys
row = sqlite3.connect('$DB').execute(
    'SELECT telegram_bot_token, telegram_chat_id FROM profile LIMIT 1').fetchone()
print(row[0] or '', row[1] or '') if row else print('', '')")"
token="${creds%% *}"
chat="${creds##* }"
if [[ -z "$token" || -z "$chat" ]]; then
  echo "no Telegram credentials in profile; printed only" >&2
  exit 0
fi
curl -sf -m 10 -X POST "https://api.telegram.org/bot$token/sendMessage" \
  -d "chat_id=$chat" --data-urlencode "text=$msg" >/dev/null \
  && echo "(sent to Telegram)"
