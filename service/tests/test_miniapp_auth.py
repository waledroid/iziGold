"""Phase 3 Task 1: real viewer auth -- Telegram initData HMAC validation,
owner/channel-membership authorization, and the FastAPI wiring around it
(header-first REST dependency, WS query-param fallback, /healthz, docs
routes disabled)."""
import hashlib
import hmac
import importlib
import json
import time
import urllib.parse

import pytest
from starlette.websockets import WebSocketDisconnect

TEST_BOT_TOKEN = "123456:AAtestbottokenAAAAAAAAAAAAAAAAAAAAA"


def _sign_fields(bot_token: str, fields: dict) -> str:
    """Sign an arbitrary field dict the same way Telegram's WebApp client
    would (data-check-string over the sorted fields, HMAC-SHA256 twice),
    then urlencode fields+hash. Lower-level than `_sign` -- lets a test
    build a validly-signed initData missing a field `_sign` always adds
    (e.g. `auth_date`)."""
    data = dict(fields)
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


def _sign(bot_token: str, user: dict, auth_date: int | None = None,
          extra: dict | None = None) -> str:
    """Build a validly-signed initData querystring the same way Telegram's
    WebApp client would, for use as a known-good test vector."""
    if auth_date is None:
        auth_date = int(time.time())
    data = {"auth_date": str(auth_date),
            "user": json.dumps(user, separators=(",", ":"))}
    if extra:
        data.update(extra)
    return _sign_fields(bot_token, data)


def _reload_auth(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    return miniapp_auth


def _reload_app(monkeypatch, **env):
    """Reload config -> miniapp_auth -> miniapp in dependency order so
    every module's `from app.config import settings` (and miniapp's
    `from app import miniapp_auth`) sees the freshly-patched env."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config, miniapp, miniapp_auth
    importlib.reload(config)
    importlib.reload(miniapp_auth)
    importlib.reload(miniapp)
    return miniapp, miniapp_auth


# ---------------------------------------------------------------- validate_init_data

def test_validate_init_data_accepts_known_vector():
    from app.miniapp_auth import validate_init_data
    init_data = _sign(TEST_BOT_TOKEN, {"id": 555, "first_name": "Ada"})
    user = validate_init_data(init_data, TEST_BOT_TOKEN)
    assert user is not None
    assert user["id"] == 555


def test_validate_init_data_rejects_tampered_hash():
    from app.miniapp_auth import validate_init_data
    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    tampered = init_data[:-1] + ("0" if init_data[-1] != "0" else "1")
    assert validate_init_data(tampered, TEST_BOT_TOKEN) is None


def test_validate_init_data_rejects_missing_hash():
    from app.miniapp_auth import validate_init_data
    qs = urllib.parse.urlencode({
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 555}),
    })
    assert validate_init_data(qs, TEST_BOT_TOKEN) is None


def test_validate_init_data_rejects_stale_auth_date():
    from app.miniapp_auth import validate_init_data
    stale = int(time.time()) - 90000  # > default 86400s max age
    init_data = _sign(TEST_BOT_TOKEN, {"id": 555}, auth_date=stale)
    assert validate_init_data(init_data, TEST_BOT_TOKEN) is None
    # but within a widened max_age_s it validates
    assert validate_init_data(init_data, TEST_BOT_TOKEN, max_age_s=100000) is not None


def test_validate_init_data_rejects_empty_string():
    from app.miniapp_auth import validate_init_data
    assert validate_init_data("", TEST_BOT_TOKEN) is None


def test_validate_init_data_rejects_none():
    from app.miniapp_auth import validate_init_data
    assert validate_init_data(None, TEST_BOT_TOKEN) is None


def test_validate_init_data_rejects_wrong_bot_token():
    from app.miniapp_auth import validate_init_data
    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert validate_init_data(init_data, "999999:wrongtoken") is None


def test_validate_init_data_rejects_missing_auth_date():
    from app.miniapp_auth import validate_init_data
    # Sign a field set that never includes auth_date at all -- the hash
    # itself is valid (over exactly the fields present), so this exercises
    # the auth_date-missing branch specifically, not a hash mismatch.
    init_data = _sign_fields(TEST_BOT_TOKEN, {"user": json.dumps({"id": 555})})
    assert validate_init_data(init_data, TEST_BOT_TOKEN) is None


def test_validate_init_data_rejects_non_ascii_hash_without_raising():
    """Security-review finding: a crafted hash containing non-ASCII bytes
    (e.g. `hash=%C3%A9abc`) used to raise TypeError out of
    hmac.compare_digest when comparing two `str` operands -- must instead
    just deny (str.encode() before comparing, whole function catch-all)."""
    from app.miniapp_auth import validate_init_data
    init_data = urllib.parse.urlencode({
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 555}),
        "hash": "éabc",  # "éabc" -- non-ASCII, deliberately not a real hex digest
    })
    assert validate_init_data(init_data, TEST_BOT_TOKEN) is None


# ---------------------------------------------------------------- viewer_ok

def test_viewer_ok_dev_bypass_admits_without_init_data(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="true")
    assert miniapp_auth.viewer_ok(None) is True
    assert miniapp_auth.viewer_ok("") is True
    assert miniapp_auth.viewer_ok("garbage") is True


def test_viewer_ok_owner_admits_without_network_call(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")

    def _boom(*a, **k):
        raise AssertionError("owner check must not touch the network")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "555", None))
    monkeypatch.setattr(miniapp_auth.httpx, "get", _boom)

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is True


def test_viewer_ok_member_admits_via_bot_api(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", "-100123"))

    calls = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"status": "member"}}

    def _get(url, params=None, timeout=None):
        calls.append(params)
        return _Resp()
    monkeypatch.setattr(miniapp_auth.httpx, "get", _get)

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is True
    assert len(calls) == 1
    assert calls[0]["user_id"] == 555


def test_viewer_ok_non_member_denied(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", "-100123"))

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"status": "left"}}
    monkeypatch.setattr(miniapp_auth.httpx, "get",
                        lambda url, params=None, timeout=None: _Resp())

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is False


def test_viewer_ok_bot_api_down_denies_member_but_owner_still_admitted(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", "-100123"))

    def _get(url, params=None, timeout=None):
        raise ConnectionError("bot api unreachable")
    monkeypatch.setattr(miniapp_auth.httpx, "get", _get)

    member_init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(member_init_data) is False

    owner_init_data = _sign(TEST_BOT_TOKEN, {"id": 999})
    assert miniapp_auth.viewer_ok(owner_init_data) is True


def test_viewer_ok_no_channel_linked_denies_non_owner(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", None))

    def _boom(*a, **k):
        raise AssertionError("no channel linked -> no Bot API call to make")
    monkeypatch.setattr(miniapp_auth.httpx, "get", _boom)

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is False


def test_viewer_ok_no_bot_token_configured_denies(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials", lambda: ("", "", None))
    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is False


def test_viewer_ok_membership_cache_hit_avoids_second_bot_api_call(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", "-100123"))

    calls = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"status": "member"}}
    monkeypatch.setattr(miniapp_auth.httpx, "get",
                        lambda url, params=None, timeout=None: calls.append(1) or _Resp())

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is True
    assert miniapp_auth.viewer_ok(init_data) is True
    assert len(calls) == 1  # second call served from the 10-min cache


def test_viewer_ok_denial_is_cached_too(monkeypatch):
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    monkeypatch.setattr(miniapp_auth, "_resolve_credentials",
                        lambda: (TEST_BOT_TOKEN, "999", "-100123"))

    calls = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"status": "left"}}
    monkeypatch.setattr(miniapp_auth.httpx, "get",
                        lambda url, params=None, timeout=None: calls.append(1) or _Resp())

    init_data = _sign(TEST_BOT_TOKEN, {"id": 555})
    assert miniapp_auth.viewer_ok(init_data) is False
    assert miniapp_auth.viewer_ok(init_data) is False
    assert len(calls) == 1


def test_membership_cache_keyed_by_channel_and_uid_not_uid_alone(monkeypatch):
    """Security-review finding: caching by uid alone means a /channel
    unlink+relink to a DIFFERENT channel could serve a grant that was only
    ever checked against the OLD channel. Keying by (channel_id, uid)
    forces a fresh Bot API check whenever the channel changes, while still
    caching repeat checks against the SAME channel."""
    miniapp_auth = _reload_auth(monkeypatch, MINIAPP_DEV_BYPASS="false")
    calls = []

    class _Resp:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"status": "member"}}
    monkeypatch.setattr(miniapp_auth.httpx, "get",
                        lambda url, params=None, timeout=None: calls.append(params) or _Resp())

    assert miniapp_auth._check_membership(TEST_BOT_TOKEN, "-100111", 555) is True
    assert len(calls) == 1
    # Same channel, same uid -> served from cache, no second call.
    assert miniapp_auth._check_membership(TEST_BOT_TOKEN, "-100111", 555) is True
    assert len(calls) == 1
    # Different channel (simulates unlink+relink), same uid -> re-checked.
    assert miniapp_auth._check_membership(TEST_BOT_TOKEN, "-100222", 555) is True
    assert len(calls) == 2


# ---------------------------------------------------------------- _resolve_credentials (real sqlite)

def test_resolve_credentials_reads_profile_and_kv_from_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "xau.db")
    from app.db import SignalDb
    db = SignalDb(db_path)
    db.conn.execute(
        "INSERT INTO profile (id, telegram_bot_token, telegram_chat_id, "
        "created_ts, updated_ts) VALUES (1, ?, ?, 0, 0)",
        ("db-token", "424242"))
    db.conn.execute("INSERT INTO kv (key, value) VALUES ('channel_id', ?)",
                    ("-100999",))
    db.conn.commit()
    db.conn.close()

    miniapp_auth = _reload_auth(monkeypatch, DB_PATH=db_path,
                                TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    token, owner, channel = miniapp_auth._resolve_credentials()
    assert token == "db-token"
    assert owner == "424242"
    assert channel == "-100999"


def test_resolve_credentials_falls_back_to_settings_when_db_missing(tmp_path, monkeypatch):
    missing = str(tmp_path / "does_not_exist.db")
    miniapp_auth = _reload_auth(monkeypatch, DB_PATH=missing,
                                TELEGRAM_BOT_TOKEN="env-token",
                                TELEGRAM_CHAT_ID="777")
    token, owner, channel = miniapp_auth._resolve_credentials()
    assert token == "env-token"
    assert owner == "777"
    assert channel is None


def test_resolve_credentials_caches_for_60s(tmp_path, monkeypatch):
    """Security-review finding: this function runs on every unvalidated
    request (it resolves the bot token needed to validate). Without a
    cache, a flood of garbage initData would each cost a sqlite open."""
    db_path = str(tmp_path / "xau.db")
    from app.db import SignalDb
    db = SignalDb(db_path)
    db.conn.execute(
        "INSERT INTO profile (id, telegram_bot_token, telegram_chat_id, "
        "created_ts, updated_ts) VALUES (1, ?, ?, 0, 0)",
        ("db-token", "424242"))
    db.conn.commit()
    db.conn.close()

    miniapp_auth = _reload_auth(monkeypatch, DB_PATH=db_path,
                                TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
    connect_calls = []
    orig_connect = miniapp_auth.sqlite3.connect

    def _counting_connect(*a, **k):
        connect_calls.append((a, k))
        return orig_connect(*a, **k)
    monkeypatch.setattr(miniapp_auth.sqlite3, "connect", _counting_connect)

    first = miniapp_auth._resolve_credentials()
    second = miniapp_auth._resolve_credentials()
    assert first == second == ("db-token", "424242", None)
    assert len(connect_calls) == 1  # second call served from the 60s cache
    assert connect_calls[0][1].get("timeout") == 1.0  # lock contention fails fast


# ---------------------------------------------------------------- FastAPI wiring

def test_require_viewer_prefers_header_over_query(monkeypatch):
    miniapp, miniapp_auth = _reload_app(monkeypatch, FEED_KEY="sekret",
                                        MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    captured = []
    monkeypatch.setattr(miniapp.miniapp_auth, "viewer_ok",
                        lambda d: (captured.append(d), True)[1])
    with TestClient(miniapp.app) as c:
        r = c.get("/api/history", params={"tf": "M5", "initData": "from-query"},
                  headers={"X-Telegram-Init-Data": "from-header"})
        assert r.status_code == 200
    assert captured == ["from-header"]


def test_require_viewer_falls_back_to_query_without_header(monkeypatch):
    miniapp, miniapp_auth = _reload_app(monkeypatch, FEED_KEY="sekret",
                                        MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    captured = []
    monkeypatch.setattr(miniapp.miniapp_auth, "viewer_ok",
                        lambda d: (captured.append(d), True)[1])
    with TestClient(miniapp.app) as c:
        r = c.get("/api/history", params={"tf": "M5", "initData": "from-query"})
        assert r.status_code == 200
    assert captured == ["from-query"]


def test_ws_closes_4403_without_valid_init_data(monkeypatch):
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    with TestClient(miniapp.app) as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4403


def test_healthz_ok_without_viewer_auth(monkeypatch):
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    with TestClient(miniapp.app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


def test_docs_and_openapi_json_404(monkeypatch):
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="true")
    from fastapi.testclient import TestClient
    with TestClient(miniapp.app) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def _non_ascii_hash_init_data():
    return urllib.parse.urlencode({
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 5}),
        "hash": "éabc",
    })


def test_history_rejects_non_ascii_hash_with_403_not_500(monkeypatch):
    """Security-review finding: this used to 500 (unhandled TypeError out
    of hmac.compare_digest) instead of the intended 403 deny."""
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    with TestClient(miniapp.app, raise_server_exceptions=True) as c:
        r = c.get("/api/history", params={"tf": "M5"},
                  headers={"X-Telegram-Init-Data": _non_ascii_hash_init_data()})
        assert r.status_code == 403


def test_ws_rejects_non_ascii_hash_with_4403_not_crash(monkeypatch):
    """Security-review finding: this used to crash the handshake with an
    unhandled TypeError instead of closing 4403."""
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="false")
    from fastapi.testclient import TestClient
    url = "/ws?initData=" + urllib.parse.quote(_non_ascii_hash_init_data(), safe="")
    with TestClient(miniapp.app, raise_server_exceptions=True) as c:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect(url):
                pass
        assert exc_info.value.code == 4403


def test_ws_feed_runs_viewer_allowed_via_asyncio_to_thread(monkeypatch):
    """Security-review finding: viewer_allowed() is sync and can do real
    blocking work (sqlite open + a 5s httpx.get on a membership-cache
    miss) -- it must be run off the event loop, not called inline inside
    the `async def ws_feed`. Asserts the to_thread call shape (function +
    args), the cheapest reliable thing to check under TestClient's
    synchronous test transport."""
    miniapp, _ = _reload_app(monkeypatch, FEED_KEY="sekret",
                             MINIAPP_DEV_BYPASS="true")
    from fastapi.testclient import TestClient
    calls = []
    orig_to_thread = miniapp.asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return await orig_to_thread(func, *args, **kwargs)
    monkeypatch.setattr(miniapp.asyncio, "to_thread", _spy_to_thread)

    with TestClient(miniapp.app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()  # snapshot

    assert len(calls) == 1
    assert calls[0][0] is miniapp.viewer_allowed
