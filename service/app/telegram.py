import time

import httpx

_ICON = {"confirm": "✅", "conflict": "⚠️", "neutral": "➖"}


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    """Build an inline keyboard from rows of (text, callback_data) tuples.

    Args:
        rows: list of rows, each row is list of (text, callback_data) tuples

    Returns:
        dict with "inline_keyboard" key containing the structure expected by Telegram API
    """
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def PROPOSAL_KB(pid):
    """Keyboard for taking or skipping a trade proposal."""
    return kb([[("🟢 Take trade", f"prop:{pid}:take"), ("🔴 Skip", f"prop:{pid}:skip")]])


def EXIT_KB(pid):
    """Keyboard for exiting or holding a position."""
    return kb([[("🔴 Exit now", f"prop:{pid}:take"), ("⏸ Hold", f"prop:{pid}:skip")]])

# The single "live" TelegramClient, kept in sync by app.main._apply_telegram
# whenever the effective Telegram credentials change (profile or .env).
# send_alert (still unit-tested directly, but no longer called from the
# /analyze path -- proposal messages via maybe_propose replaced it there)
# prefers this over building its own settings-based httpx call so
# profile-only credentials -- which never appear in `settings` -- still
# work. None when Telegram isn't configured at all.
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


def format_proposal(kind, direction, price, resp) -> str:
    ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
          if resp.ai_available else "AI unavailable ❌")
    head = "📥 Entry proposal" if kind == "entry" else "📤 Exit proposal"
    return (f"{head}: {direction} @ {price}\n"
            f"AI: {ai}\nRegime: {resp.regime}\n"
            f"Valid while the strategy holds this stance.")


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

    def send_message(self, text: str, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.transport("sendMessage", payload, None)

    def send_photo(self, caption: str, png_bytes: bytes):
        return self.transport(
            "sendPhoto", {"chat_id": self.chat_id, "caption": caption},
            {"photo": ("chart.png", png_bytes, "image/png")})

    def edit_message(self, message_id, text: str, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.transport(
            "editMessageText", payload, None)

    def answer_callback(self, callback_id: str, text: str = "") -> dict | None:
        return self.transport(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text}, None)

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
    if cmd == "/mode":
        mode = app.state.db.exec_mode()
        return (f"Execution mode: {mode.upper()}\nAUTO executes signals "
                f"immediately; MANUAL sends proposals with buttons.",
                kb([[("🤖 AUTO", "mode:auto"), ("👤 MANUAL", "mode:manual")]]))
    if cmd == "/strategy":
        rows = app.state.db.strategy_ids()
        latest = app.state.latest_heartbeat
        active = latest[1].active_strategy if latest else ""
        buttons = [[(("● " if s == active else "") + s, f"strat:{s}")] for s in rows]
        return ("Switch active strategy (applies at next bar):",
                kb(buttons) if buttons else None)
    if cmd == "/config":
        db = app.state.db
        from app.config import settings
        latest = app.state.latest_heartbeat
        hb = latest[1] if latest else None
        return (
            "⚙️ Config\n"
            f"mode: {db.exec_mode()}\n"
            f"strategy: {hb.active_strategy if hb else '?'}\n"
            f"forecaster: {settings.forecaster} | horizon: {settings.horizon}\n"
            f"ai mode: {settings.mode} | confirm ≥ {settings.confirm_threshold}\n"
            f"balance: {hb.balance if hb else '?'} | equity: {hb.equity if hb else '?'}\n"
            f"kill switch: {hb.kill_switch if hb else '?'} | "
            f"window open: {hb.window_open if hb else '?'}\n"
            f"spread: {hb.spread_points if hb else '?'}pt")
    return None


def handle_callback(data: str, app) -> tuple:
    """Pure function mapping a callback_query's data to (edit_text_or_None,
    toast). The poller edits the tapped message when edit_text is not None
    and always answers the callback with toast (fail-open UX)."""
    db = app.state.db
    parts = data.split(":")
    if parts[0] == "mode" and len(parts) > 1 and parts[1] in ("auto", "manual"):
        db.set_exec_mode(parts[1])
        return (f"Execution mode → {parts[1].upper()}", f"mode: {parts[1]}")
    if parts[0] == "strat" and len(parts) > 1:
        sid = parts[1]
        app.state.pending_switch = sid
        return (f"Switching to {sid} at next bar.", f"→ {sid}")
    if parts[0] == "prop" and len(parts) == 3 and parts[1].isdigit():
        pid, action = int(parts[1]), parts[2]
        row = db.get_proposal(pid)
        if row is None or row["status"] != "pending":
            return (None, f"already {row['status'] if row else 'gone'}")
        status = "approved" if action == "take" else "skipped"
        # Guarded on the row still being 'pending': it read as pending just
        # above, but a concurrent /analyze stance-expiry (maybe_propose) or
        # the /heartbeat TTL sweep may have moved it in between. If the
        # guarded UPDATE lost that race, re-read the row and report whatever
        # it actually became instead of silently "succeeding".
        if not db.set_proposal_status(pid, status, expected="pending"):
            current = db.get_proposal(pid)
            return (None, f"already {current['status'] if current else 'gone'}")
        if action == "take":
            return (f"{row['direction']} @ {row['price']} — 👍 approved, "
                    f"executing on next heartbeat…", "approved")
        return (f"{row['direction']} @ {row['price']} — ❌ skipped", "skipped")
    return (None, "unknown")
