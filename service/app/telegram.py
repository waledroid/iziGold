import time

import httpx

_ICON = {"confirm": "✅", "conflict": "⚠️", "neutral": "➖"}

# The single "live" TelegramClient, kept in sync by app.main._apply_telegram
# whenever the effective Telegram credentials change (profile or .env).
# send_alert prefers this over building its own settings-based httpx call so
# profile-only credentials -- which never appear in `settings` -- still
# deliver /analyze alerts. None when Telegram isn't configured at all.
_active_client = None


def set_active_client(client) -> None:
    global _active_client
    _active_client = client


def format_report(req, resp) -> str:
    ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
          if resp.ai_available else "AI unavailable ❌")
    return (f"🥇 {req.symbol} {req.timeframe} — {req.signal}\n"
            f"Strategy: {req.signal}\n"
            f"AI: {ai}\n"
            f"Regime: {resp.regime}\n"
            f"Mode: {resp.mode}")


def send_alert(text: str, settings) -> bool:
    if _active_client is not None:
        # /analyze is a sync endpoint (runs in a worker thread via FastAPI),
        # so this direct synchronous send is event-loop-safe.
        result = _active_client.send_message(text)
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
        self.transport = transport or _default_transport(token)

    def send_message(self, text: str):
        return self.transport("sendMessage",
                              {"chat_id": self.chat_id, "text": text}, None)

    def send_photo(self, caption: str, png_bytes: bytes):
        return self.transport(
            "sendPhoto", {"chat_id": self.chat_id, "caption": caption},
            {"photo": ("chart.png", png_bytes, "image/png")})

    def edit_message(self, message_id, text: str):
        return self.transport(
            "editMessageText",
            {"chat_id": self.chat_id, "message_id": message_id, "text": text}, None)

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


def _format_status(app) -> str:
    latest = app.state.latest_heartbeat
    if latest is None:
        return "no heartbeat yet"
    _, hb = latest
    pending = app.state.pending_switch
    strategy = hb.active_strategy
    if pending and pending != strategy:
        strategy = f"{strategy} → {pending}"
    lines = [
        "📊 Status",
        f"Equity: {hb.equity} Balance: {hb.balance} Floating P/L: {hb.floating_pl}",
        f"Kill-switch: {'ON' if hb.kill_switch else 'off'}",
        f"Strategy: {strategy}",
        f"Window: {'open' if hb.window_open else 'closed'} Exposure: {hb.exposure_min}m",
    ]
    positions = hb.positions
    lines.append(f"Positions ({len(positions)}):")
    if positions:
        for p in positions:
            lines.append(f"  #{p.ticket} {p.direction} {p.lots}@{p.open_price} "
                        f"P/L {p.profit}")
    else:
        lines.append("  none")
    return "\n".join(lines)


def _format_stats(app) -> str:
    stats = app.state.db.stats()
    by_strategy = stats.get("by_strategy") or {}
    if not by_strategy:
        return "no stats yet"
    lines = ["📈 Stats by strategy:"]
    for sid, s in by_strategy.items():
        lines.append(f"{sid}: {s['signals']} signals, {s['resolved']} resolved, "
                     f"{s['hit_pct']}% hit, avg move {s['avg_move']}")
    return "\n".join(lines)


def _format_history(app) -> str:
    trades = app.state.db.recent_trades(10)
    if not trades:
        return "no trade history yet"
    lines = ["🕘 Last trades:"]
    for t in trades:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["ts"]))
        line = (f"{when} {t['event']} {t.get('strategy_id')} {t.get('direction')} "
                f"{t.get('lots')}@{t.get('price')}")
        reason = t.get("reason")
        if reason:
            line += f" ({reason})"
        if t.get("event") == "close":
            line += f" P/L {t.get('profit')}"
        lines.append(line)
    return "\n".join(lines)


def _format_switch(app, args: list) -> str:
    if not args:
        pending = app.state.pending_switch
        current = pending if pending else "none"
        return f"pending switch: {current} — use /switch <id>, /switch cancel"
    target = args[0]
    if target == "cancel":
        app.state.pending_switch = None
        return "pending switch cleared"
    app.state.pending_switch = target
    return f"switch to {target} queued — confirms on next EA heartbeat"


def format_live_status(app) -> str:
    latest = app.state.latest_heartbeat
    if latest is None:
        return "📌 Live status\nno heartbeat yet"
    ts, hb = latest
    if hb.kill_switch:
        ks = "⛔ TRIPPED"
    elif hb.hwm > 0:
        ks = f"{100 * (hb.equity / hb.hwm - 1):.1f}% off peak"
    else:
        ks = "ok"
    updated = time.strftime("%H:%M", time.localtime(ts))
    lines = [
        "📌 Live status",
        f"Equity: {hb.equity} Floating P/L: {hb.floating_pl}",
        f"Positions: {len(hb.positions)}",
        f"Kill-switch: {ks}",
        f"Strategy: {hb.active_strategy}",
        f"Updated: {updated}",
    ]
    return "\n".join(lines)


def pinned_tick(app, client: "TelegramClient") -> None:
    """Create-pin-or-edit the live-status message. Sync so tests can drive
    it directly with a fake transport; the async pinned_editor loop calls
    this via asyncio.to_thread. All failures are swallowed (fail-open).

    Self-healing: if the stored id can't be edited -- e.g. the pinned
    message was deleted server-side (edit_message returns None/an error),
    or the stored value isn't a valid numeric id -- the kv id is cleared
    so the *next* tick falls through to the create-and-pin path instead of
    retrying a dead id forever."""
    if app.state.latest_heartbeat is None:
        return
    text = format_live_status(app)
    pinned_id = app.state.db.get_kv("pinned_message_id")
    if pinned_id:
        try:
            numeric_id = int(pinned_id)
        except ValueError:
            app.state.db.set_kv("pinned_message_id", "")
            return
        result = client.edit_message(numeric_id, text)
        if result is None or not result.get("ok", True):
            app.state.db.set_kv("pinned_message_id", "")
        return
    result = client.send_message(text)
    if not result or not result.get("ok"):
        return
    message_id = (result.get("result") or {}).get("message_id")
    if message_id is None:
        return
    client.pin_message(message_id)
    app.state.db.set_kv("pinned_message_id", str(message_id))


def handle_command(text: str, app) -> str | None:
    """Pure function mapping a slash command to a reply, or None if unknown."""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower()
    if cmd == "/status":
        return _format_status(app)
    if cmd == "/stats":
        return _format_stats(app)
    if cmd == "/history":
        return _format_history(app)
    if cmd == "/switch":
        return _format_switch(app, parts[1:])
    return None
