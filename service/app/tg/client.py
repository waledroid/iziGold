"""Telegram client transport and pinned message maintenance for XAU Assistant."""

from __future__ import annotations

import json
import re
import sys
import httpx

_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9_-]*(?:\s+[^>]*)?>")


def strip_html_tags(text: str) -> str:
    """Strip unrendered HTML/XML tags (e.g. <b>, <code>) so Telegram renders clean text without tag leakage."""
    if not text or "<" not in text:
        return text
    return _HTML_TAG_RE.sub("", text)


# The single "live" TelegramClient, kept in sync by app.main._apply_telegram
# whenever the effective Telegram credentials change (profile or .env).
_active_client = None


def set_active_client(client) -> None:
    global _active_client
    _active_client = client
    # Also update app.telegram if imported
    tg = sys.modules.get("app.telegram")
    if tg is not None and tg is not sys.modules.get(__name__):
        tg._active_client = client


def send_alert(text: str, settings) -> bool:
    tg = sys.modules.get("app.telegram")
    client = getattr(tg, "_active_client", None) if tg else _active_client
    if client is None:
        client = _active_client
    if client is not None:
        # /analyze is a sync endpoint (runs in a worker thread via FastAPI),
        # so this direct synchronous send is event-loop-safe.
        result = client.send_message(text)
        return bool(result and result.get("ok", True))
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=5.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _default_transport(token: str):
    """Fail-open httpx transport: any exception or non-200 -> None."""

    def transport(method: str, payload: dict, files: dict | None = None):
        url = f"https://api.telegram.org/bot{token}/{method}"
        # getUpdates long-polls server-side up to `timeout` seconds (25s by
        # convention here); give the HTTP client enough slack for that.
        request_timeout = 30.0 if method == "getUpdates" else 10.0
        try:
            if files is None:
                r = httpx.post(url, json=payload, timeout=request_timeout)
            else:
                r = httpx.post(url, data=payload, files=files, timeout=request_timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    return transport


class TelegramClient:
    """Two-way Telegram transport. `transport` is injectable for tests."""

    def __init__(self, token: str, chat_id: str, transport=None):
        self.token = token
        self.chat_id = chat_id
        if transport is None:
            tg = sys.modules.get("app.telegram")
            factory = getattr(tg, "_default_transport", _default_transport) if tg else _default_transport
            self.transport = factory(token)
        else:
            self.transport = transport

    def send_message(self, text: str, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "text": strip_html_tags(str(text))}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.transport("sendMessage", payload, None)

    def send_photo(self, caption: str, png_bytes: bytes,
                   reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "caption": strip_html_tags(str(caption))}
        if reply_markup is not None:
            # sendPhoto goes out as multipart form data, where reply_markup
            # must be a JSON-serialized string, not a nested object
            payload["reply_markup"] = json.dumps(reply_markup)
        return self.transport(
            "sendPhoto", payload,
            {"photo": ("chart.png", png_bytes, "image/png")})

    def send_document(self, caption: str, file_bytes: bytes, filename: str):
        # /manual: same multipart shape as sendPhoto, different field name.
        return self.transport(
            "sendDocument", {"chat_id": self.chat_id, "caption": strip_html_tags(str(caption))},
            {"document": (filename, file_bytes, "application/pdf")})

    def edit_message(self, message_id, text: str, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "message_id": message_id, "text": strip_html_tags(str(text))}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.transport(
            "editMessageText", payload, None)

    # Channel-addressed sends. Deliberately no reply_markup parameter:
    # the channel must never carry interactive controls (spec invariant),
    # so the restriction is structural, not a call-site convention.
    def send_message_to(self, chat_id, text):
        return self.transport("sendMessage",
                              {"chat_id": chat_id, "text": strip_html_tags(str(text))}, None)

    def send_photo_to(self, chat_id, caption: str, png_bytes: bytes):
        return self.transport(
            "sendPhoto", {"chat_id": chat_id, "caption": strip_html_tags(str(caption))},
            {"photo": ("chart.png", png_bytes, "image/png")})

    def edit_message_to(self, chat_id, message_id, text: str):
        return self.transport(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": strip_html_tags(str(text))}, None)

    def answer_callback(self, callback_id: str, text: str = "") -> dict | None:
        return self.transport(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": strip_html_tags(str(text))}, None)

    def pin_message(self, message_id) -> bool:
        result = self.transport(
            "pinChatMessage",
            {"chat_id": self.chat_id, "message_id": message_id}, None)
        return bool(result and result.get("ok"))

    def get_updates(self, offset) -> list:
        result = self.transport(
            "getUpdates", {"offset": offset, "timeout": 25}, None)
        if not result:
            return []
        return result.get("result") or []


def pinned_tick(app, client: TelegramClient) -> None:
    """Create-pin-once the static command-reference message, self-healing if
    it was deleted/unpinned. Sync so tests can drive it directly with a fake
    transport; the async pinned_editor loop calls this via
    asyncio.to_thread. All failures are swallowed (fail-open)."""
    from app.tg.formatters import format_pinned_help, PINNED_HELP_VERSION

    text = format_pinned_help()
    pinned_id = app.state.db.get_kv("pinned_message_id")
    if pinned_id:
        if app.state.db.get_kv("pinned_help_version") == PINNED_HELP_VERSION:
            return
        try:
            numeric_id = int(pinned_id)
        except ValueError:
            app.state.db.set_kv("pinned_message_id", "")
            return
        result = client.edit_message(numeric_id, text)
        if result is None or not result.get("ok", True):
            app.state.db.set_kv("pinned_message_id", "")
            return
        # Re-pin after a successful edit: the message may have been manually
        # unpinned since creation, and editing alone leaves it unpinned
        # forever once the version matches again.
        client.pin_message(numeric_id)
        app.state.db.set_kv("pinned_help_version", PINNED_HELP_VERSION)
        return
    result = client.send_message(text)
    if not result or not result.get("ok"):
        return
    message_id = (result.get("result") or {}).get("message_id")
    if message_id is None:
        return
    client.pin_message(message_id)
    app.state.db.set_kv("pinned_message_id", str(message_id))
    app.state.db.set_kv("pinned_help_version", PINNED_HELP_VERSION)
