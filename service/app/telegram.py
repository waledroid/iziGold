import time

import httpx

_ICON = {"confirm": "✅", "conflict": "⚠️", "neutral": "➖"}


def format_report(req, resp) -> str:
    ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
          if resp.ai_available else "AI unavailable ❌")
    return (f"🥇 {req.symbol} {req.timeframe} — {req.signal}\n"
            f"Strategy: {req.signal}\n"
            f"AI: {ai}\n"
            f"Regime: {resp.regime}\n"
            f"Mode: {resp.mode}")


def send_alert(text: str, settings) -> bool:
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
        profit = t.get("profit")
        if profit is not None:
            line += f" P/L {profit}"
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
