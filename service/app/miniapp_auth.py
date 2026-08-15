"""Real viewer authentication for the mini-app (Phase 3, Task 1): Telegram
initData HMAC validation + owner/channel-membership authorization.

The mini-app process (`app.miniapp`, port 9001) is deliberately isolated
from `app.main` (port 9000, the trading service) — it must not import
`app.main` and must not share `app.main`'s writable `SignalDb` instance
across processes (they're two separate uvicorn processes; there is no
shared Python object to reuse even if we wanted to). Credentials are
resolved by opening the SAME sqlite file `app.main` writes to
(`settings.db_path`) in a **read-only** connection (`file:...?mode=ro`
URI) — this module never writes to that db. We deliberately do NOT
reuse `app.db.SignalDb` here even though it's importable: its
`__init__` unconditionally runs `CREATE TABLE IF NOT EXISTS` for every
schema, which needs a writable connection — instantiating it against a
read-only URI would raise, and instantiating it read-write would grant
this public-facing process implicit write access to the trading db,
which is exactly what "read-only" here is meant to rule out.

Resolution mirrors `app.main._effective_telegram`: `profile.telegram_bot_token`
/ `profile.telegram_chat_id` win when both are non-empty, else fall back to
`settings.telegram_bot_token` / `settings.telegram_chat_id` (.env). The
linked channel (for "member" admission) is `kv['channel_id']`, the same
key `app.main`/`app.telegram` read/write via `db.get_kv`/`set_kv`.

Fail-closed, not fail-open: CLAUDE.md's "fail-open everywhere" rule
(non-negotiable #3) is about the AI grading path staying out of the
trade path — it does not apply to authentication. Here, any resolution
failure (no bot token configured, no channel linked, Bot API down/
network error) denies non-owner viewers. Only the owner's local id
comparison ever admits without a successful network round trip; Bot API
errors never do.
"""
import hashlib
import hmac
import json
import sqlite3
import time
import urllib.parse

import httpx

from app.config import settings

_CACHE_TTL_S = 600  # 10 minutes; admits AND denials are cached (brief: "denials cached too")
_membership_cache: dict[int, tuple[bool, float]] = {}


def validate_init_data(init_data: str, bot_token: str, max_age_s: int = 86400) -> dict | None:
    """Telegram's documented WebApp initData signature check:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Parse the querystring, pop `hash`, build the data-check-string from the
    remaining sorted `k=v` pairs joined by `\\n`, derive
    secret = HMAC_SHA256(key=b"WebAppData", msg=bot_token), and require
    hexdigest(HMAC_SHA256(secret, data_check_string)) == hash via
    `hmac.compare_digest` (constant-time). Also requires `auth_date` to be
    within `max_age_s` seconds of now. Returns the parsed `user` dict
    (`{"id": int, ...}`) on success, `None` on ANY failure — malformed
    input, missing/tampered hash, stale auth_date, missing/malformed user
    field. Never raises; callers treat `None` as deny.
    """
    if not init_data or not bot_token:
        return None
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"),
                             hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    try:
        auth_date = int(data.get("auth_date", ""))
    except (TypeError, ValueError):
        return None
    if time.time() - auth_date > max_age_s:
        return None
    user_raw = data.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


def _resolve_credentials() -> tuple[str, str, str | None]:
    """(bot_token, owner_chat_id, channel_id), read-only, same precedence
    as `app.main._effective_telegram`: profile db row wins when both
    fields are non-empty, else `settings` (.env). `channel_id` has no
    .env fallback (kv-only, matches `app.main._linked_channel`). Any
    sqlite error (db file missing, table missing, locked, etc.) is
    treated as "no profile row" and falls through to settings — this
    process must never raise or block on db trouble."""
    token = ""
    chat_id = ""
    channel_id = None
    try:
        uri = f"file:{urllib.parse.quote(settings.db_path, safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT telegram_bot_token, telegram_chat_id FROM profile WHERE id = 1"
            ).fetchone()
            if row:
                token = str(row[0] or "").strip()
                chat_id = str(row[1] or "").strip()
            kv_row = conn.execute(
                "SELECT value FROM kv WHERE key = 'channel_id'").fetchone()
            if kv_row and kv_row[0]:
                channel_id = str(kv_row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    if not token or not chat_id:
        token = str(settings.telegram_bot_token or "").strip()
        chat_id = str(settings.telegram_chat_id or "").strip()
    return token, chat_id, channel_id


def _check_membership(bot_token: str, channel_id: str, uid: int) -> bool:
    """getChatMember, 5 s timeout; status in {creator, administrator,
    member} admits, everything else (wrong status, non-200, network/
    timeout error, malformed body) denies. Result cached 10 min per uid,
    denials cached too, so a rejected viewer can't hammer the Bot API by
    reloading the page."""
    now = time.monotonic()
    cached = _membership_cache.get(uid)
    if cached is not None and cached[1] > now:
        return cached[0]
    allowed = False
    try:
        r = httpx.get(
            f"https://api.telegram.org/bot{bot_token}/getChatMember",
            params={"chat_id": channel_id, "user_id": uid}, timeout=5.0)
        if r.status_code == 200:
            status = (r.json().get("result") or {}).get("status")
            allowed = status in {"creator", "administrator", "member"}
    except Exception:
        allowed = False
    _membership_cache[uid] = (allowed, now + _CACHE_TTL_S)
    return allowed


def viewer_ok(init_data: str | None) -> bool:
    """Dev bypass -> True (mirrors `settings.miniapp_dev_bypass`, same as
    Phase 1). Else: `init_data` must validate (signature + freshness);
    the signed-in user's id matching the resolved owner chat id admits
    with NO network call; otherwise, if a channel is linked, admission
    depends on Bot API channel membership (cached). Anything else denies,
    including no bot token configured and no channel linked."""
    if settings.miniapp_dev_bypass:
        return True
    if not init_data:
        return False
    bot_token, owner_chat_id, channel_id = _resolve_credentials()
    if not bot_token:
        return False
    user = validate_init_data(init_data, bot_token, settings.miniapp_auth_max_age_s)
    if user is None:
        return False
    uid = user.get("id")
    if uid is None:
        return False
    if owner_chat_id and str(uid) == str(owner_chat_id):
        return True
    if not channel_id:
        return False
    return _check_membership(bot_token, channel_id, uid)
