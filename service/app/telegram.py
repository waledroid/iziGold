import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

_ICON = {"confirm": "✅", "conflict": "⚠️", "neutral": "➖"}

# Channel privacy filter: account-level figures are replaced with this
# marker in every channel-bound text (spec: members see how trades
# perform, never what the account is worth).
REDACTED = "•••"

_PARIS_TZ = ZoneInfo("Europe/Paris")


def market_session(dt: datetime | None = None) -> str:
    """Label the current market session by Europe/Paris local time (DST-proof
    via zoneinfo). `dt` (aware datetime) is injectable for tests; defaults to
    now."""
    if dt is None:
        dt = datetime.now(_PARIS_TZ)
    else:
        dt = dt.astimezone(_PARIS_TZ)
    minutes = dt.hour * 60 + dt.minute
    bands = [
        (60, 540, "Asian session"),            # 01:00-09:00
        (540, 600, "London open"),              # 09:00-10:00
        (600, 840, "London morning"),            # 10:00-14:00
        (840, 930, "London+NY overlap · US data window"),  # 14:00-15:30
        (930, 1080, "London+NY overlap"),        # 15:30-18:00
        (1080, 1200, "New York afternoon"),      # 18:00-20:00
        (1200, 1320, "Late New York"),           # 20:00-22:00
        (1320, 1380, "NY close / pre-rollover"),  # 22:00-23:00
    ]
    for start, end, label in bands:
        if start <= minutes < end:
            return label
    return "Rollover — thin market"  # 23:00-01:00, wraps midnight


# Short forms for table columns. Kept HERE, beside market_session(), so the
# band definitions have exactly one home -- a second table elsewhere would
# drift the moment the bands change.
SESSION_SHORT = {
    "Asian session": "Asia",
    "London open": "LDN open",
    "London morning": "London",
    "London+NY overlap \u00b7 US data window": "LDN+NY data",
    "London+NY overlap": "LDN+NY",
    "New York afternoon": "NY",
    "Late New York": "Late NY",
    "NY close / pre-rollover": "NY close",
    "Rollover \u2014 thin market": "Rollover",
}


def market_session_short(dt: datetime | None = None) -> str:
    """Column-width label for the same bands market_session() names."""
    return SESSION_SHORT.get(market_session(dt), "\u2014")


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


def EXIT_NOW_KB(trade_id):
    """Keyboard on trade-open notifications: close the basket immediately."""
    return kb([[("🔴 EXIT — close trade", f"exitnow:{trade_id}")]])


def BRAKE_RESET_KB():
    """Keyboard on the daily-loss-brake 70% / TRIPPED notices: reset the
    brake for today (owner-only callback -> pre-approved reset_brake
    proposal -> EA command on the next heartbeat)."""
    return kb([[("🔓 Reset brake for today", "brakereset:1")]])


def TARGET_KB():
    """Keyboard on the FIXED-ride target alert: exit the basket, or ratchet
    the stop to the current price to lock the gain and keep riding. The
    /proposal-result edit re-attaches this same keyboard, so [Move SL]
    stays reusable as the ride extends."""
    return kb([[("🔴 EXIT — close trade", "exitnow:0")],
               [("🔒 Move SL to here", "movesl:1")]])


def TRADE_KB():
    """Keyboard on the /trade prompt: one big button per row so the two
    directions can't be fat-fingered. The tap IS the confirmation."""
    return kb([[("🔵 BUY", "mtrade:BUY")], [("🔴 SELL", "mtrade:SELL")]])


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
    body = (f"{head}: {direction} @ {price}\n"
            f"AI: {ai}\nRegime: {resp.regime}\n"
            f"Valid while the strategy holds this stance.")
    if kind == "entry":
        return f"🕒 {market_session()}\n{body}"
    return body


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

    def send_photo(self, caption: str, png_bytes: bytes,
                   reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "caption": caption}
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
            "sendDocument", {"chat_id": self.chat_id, "caption": caption},
            {"document": (filename, file_bytes, "application/pdf")})

    def edit_message(self, message_id, text: str, reply_markup: dict | None = None):
        payload = {"chat_id": self.chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.transport(
            "editMessageText", payload, None)

    # Channel-addressed sends. Deliberately no reply_markup parameter:
    # the channel must never carry interactive controls (spec invariant),
    # so the restriction is structural, not a call-site convention.
    def send_message_to(self, chat_id, text):
        return self.transport("sendMessage",
                              {"chat_id": chat_id, "text": text}, None)

    def send_photo_to(self, chat_id, caption: str, png_bytes: bytes):
        return self.transport(
            "sendPhoto", {"chat_id": chat_id, "caption": caption},
            {"photo": ("chart.png", png_bytes, "image/png")})

    def edit_message_to(self, chat_id, message_id, text: str):
        return self.transport(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text}, None)

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


_EA_CONNECTED_MAX_AGE_S = 30


def _ea_connection_line(app) -> str:
    """First line of /status: EA connection state derived from
    app.state.latest_heartbeat (ts, HeartbeatRequest) | None."""
    latest = app.state.latest_heartbeat
    if latest is None:
        return "EA: 🔴 never connected"
    ts, _ = latest
    age = time.time() - ts
    if age <= _EA_CONNECTED_MAX_AGE_S:
        return f"EA: 🟢 connected ({int(age)}s ago)"
    return f"EA: 🔴 disconnected (last seen {int(age // 60)}m ago)"


# Mini-app (live chart) liveness for /status. Same source of truth the
# watchdog uses: the miniapp's auth-free /healthz on 127.0.0.1:<MINIAPP_PORT>
# -- feed_age_s = seconds since the Windows bridge last pushed. Injectable
# for tests (monkeypatch _miniapp_healthz). Never raises; None = unreachable.
# The port comes from settings (MINIAPP_PORT), never a literal: it moved off
# 9001 on 2026-08-19 and every probe must follow it in lockstep.


def _miniapp_healthz_url() -> str:
    # Local import, matching the rest of this module: telegram.py never
    # holds a module-level settings reference.
    from app.config import settings
    return f"http://127.0.0.1:{settings.miniapp_port}/healthz"


_MINIAPP_HEALTHZ_URL = _miniapp_healthz_url()
_MINIAPP_FEED_STALE_S = 90


def _miniapp_healthz():
    """0.5 s hard cap: /status must never wait on the chart service. Plain
    urllib on purpose: an httpx.get()/Client() first call costs ~700 ms of
    SSL-context/env setup even for loopback — enough to delay every /status
    reply — while urllib answers a local HTTP probe in ~1 ms. Never raises;
    None = unreachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(_MINIAPP_HEALTHZ_URL, timeout=0.5) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode() or "{}")
    except Exception:
        return None


def _miniapp_line() -> str:
    """Second line of /status, right under the EA line: chart service +
    data feed state in one glance. Infra state, never redacted."""
    hz = _miniapp_healthz()
    if not hz or not hz.get("ok"):
        return "Mini app: 🔴 down"
    age = hz.get("feed_age_s")
    if age is None:
        up = hz.get("uptime_s") or 0
        return ("Mini app: 🟡 up, waiting for data" if up < _MINIAPP_FEED_STALE_S
                else "Mini app: 🟡 up, no data (bridge?)")
    if age > _MINIAPP_FEED_STALE_S:
        return f"Mini app: 🟡 up, no data for {int(age // 60)}m (bridge?)"
    return f"Mini app: 🟢 connected (feed {age:.0f}s ago)"


def _format_status(app, redacted=False) -> str:
    latest = app.state.latest_heartbeat
    connection = _ea_connection_line(app)
    session_line = f"🕒 {market_session()}"
    miniapp_line = _miniapp_line()
    if latest is None:
        return f"{session_line}\n{connection}\n{miniapp_line}\nno heartbeat yet"
    _, hb = latest
    pending = app.state.pending_switch
    strategy = hb.active_strategy
    if pending and pending != strategy:
        strategy = f"{strategy} → {pending}"
    # "Kill-switch off" confused users -- phrase it as what the protection
    # is doing, not the raw flag.
    if hb.kill_switch:
        protection = "⛔ KILL SWITCH TRIPPED — trading halted"
    else:
        protection = "🛡 Protection armed"
        if hb.hwm and not redacted:
            dd = max(0.0, (1 - hb.equity / hb.hwm) * 100)
            protection += f" · drawdown {dd:.1f}%"
    # Daily-loss brake usage (2026-08-18): % of the brake threshold spent
    # today (from the reset base when reset). Infra/risk state, not an
    # account figure -> NOT redacted. getattr: old heartbeats lack it.
    daily_loss_pct = getattr(hb, "daily_loss_pct", 0.0) or 0.0
    brake_reset = getattr(hb, "brake_reset", False)
    if daily_loss_pct > 0 or brake_reset:
        protection += f" · daily loss {daily_loss_pct:.0f}%"
        if brake_reset:
            protection += " since reset"
    db = getattr(app.state, "db", None)
    mode = db.exec_mode().upper() if db is not None and hasattr(db, "exec_mode") else "?"
    lines = [
        session_line,
        connection,
        miniapp_line,
    ]
    if getattr(hb, "algo_trading", True) is False:
        lines.append("⚠️ ALGO TRADING OFF — MT5 cannot execute trades")
    if not redacted:
        lines.append(f"💰 {hb.equity} equity · {hb.balance} balance "
                     f"· {hb.floating_pl:+g} floating")
        money = _money_line(app)
        if money:
            lines.append(money)
    lines += [
        protection,
        f"🎯 {strategy} · {mode}",
    ]
    positions = hb.positions
    if positions:
        lines.append(f"📬 Positions ({len(positions)}):")
        for p in positions:
            lines.append(f"  #{p.ticket} {p.direction} {p.lots} @ {p.open_price} "
                        f"· P/L {p.profit:+g}")
    else:
        lines.append("📭 No open positions")
    return "\n".join(lines)


def _format_stats(app) -> str:
    stats = app.state.db.stats()
    by_strategy = stats.get("by_strategy") or {}
    if not by_strategy:
        return "no stats yet"
    # Money companion to the signal hit-rates: realized P/L per strategy
    # from actual close events (empty dict when none / db can't answer).
    pnl = (app.state.db.strategy_pnl()
           if hasattr(app.state.db, "strategy_pnl") else {})
    lines = ["📈 Stats by strategy:"]
    for sid, s in by_strategy.items():
        line = (f"{sid}: {s['signals']} signals, {s['resolved']} resolved, "
                f"{s['hit_pct']}% hit, avg move {s['avg_move']}")
        if sid in pnl:
            total, n = pnl[sid]
            line += f", P/L {_fmt_money(total)} over {n} trades"
        lines.append(line)
    return "\n".join(lines)


def _fmt_money(amount: float) -> str:
    """-3.2 -> '-$3.20', 12.5 -> '+$12.50' — signed, dollar, 2 decimals."""
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):.2f}"


def _period_starts() -> tuple:
    """(today_start, week_start) as epoch seconds, LOCAL time: today =
    local midnight, week = the most recent local Monday midnight."""
    now = time.localtime()
    today = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0,
                         0, 0, -1))
    return today, today - now.tm_wday * 86400


def _money_line(app) -> str | None:
    """'📅 Today: +$X (N trades) · Week: +$Y' from close events in the
    trades table, or None when the db can't answer (tests with a bare
    namespace, no db). Account figures — callers must skip it when
    redacted."""
    db = getattr(app.state, "db", None)
    if db is None or not hasattr(db, "realized_pnl"):
        return None
    today_start, week_start = _period_starts()
    today, n = db.realized_pnl(today_start)
    week, _ = db.realized_pnl(week_start)
    return (f"📅 Today: {_fmt_money(today)} ({n} trade{'s' if n != 1 else ''})"
            f" · Week: {_fmt_money(week)}")


def _format_history(app) -> str:
    trades = app.state.db.recent_trades(10)
    if not trades:
        return "no trade history yet"
    lines = ["🕘 Last trades:"]
    closed_total, closed_n = 0.0, 0
    for t in trades:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["ts"]))
        dot = {"BUY": "🟢", "SELL": "🔴"}.get(t.get("direction"), "▫️")
        line = (f"{dot} {when} {t['event']} {t.get('strategy_id')} "
                f"{t.get('direction')} {t.get('lots')}@{t.get('price')}")
        reason = t.get("reason")
        if reason:
            line += f" ({reason})"
        if t.get("event") == "close":
            profit = t.get("profit") or 0.0
            line += f" P/L {profit:+.2f}"
            closed_total += profit
            closed_n += 1
        lines.append(line)
    if closed_n:
        lines.append(f"Σ closed shown: {_fmt_money(closed_total)} ({closed_n})")
    return "\n".join(lines)


def _format_balance(app, redacted=False) -> str:
    """/bal reply: balance, equity, floating P/L from the latest heartbeat
    ((ts, HeartbeatRequest) tuple | None on app.state.latest_heartbeat)."""
    latest = app.state.latest_heartbeat
    if latest is None:
        return "no EA heartbeat yet"
    _, hb = latest
    sign = "+" if hb.floating_pl >= 0 else "-"
    floating = f"{sign}${abs(hb.floating_pl):.2f}"
    if redacted:
        return f"💰 Balance: {REDACTED} | Equity: {REDACTED} | Floating: {floating}"
    text = (f"💰 Balance: ${hb.balance:.2f} | Equity: ${hb.equity:.2f} | "
            f"Floating: {floating}")
    money = _money_line(app)
    return f"{text}\n{money}" if money else text


# The two strategy lanes the EA registers (Experts/XauAssistant.mq5 OnInit).
# /mode's third button row switches between them; the old typed /switch and
# the id-list /strategy were consolidated here 2026-08-26. Deliberately a
# hardcoded pair, not db.strategy_ids(): that list carries shadow ids (stub,
# boll_stochrsi_v1, ...) the owner should never activate by mis-tap.
STRATEGY_LANES = [("⏱ M5", "halftrend_ema_v1"), ("⏱ M15", "halftrend_m15_v1")]


def _format_mode(app) -> tuple:
    mode = app.state.db.exec_mode()
    emode = app.state.db.entry_mode()
    latest = app.state.latest_heartbeat
    active = latest[1].active_strategy if latest else ""
    pending = getattr(app.state, "pending_switch", None)

    # ● marks the currently-active choice, same convention as /agree.
    def mark(label, active):
        return ("● " + label) if active else label

    text = (f"Execution mode: {mode.upper()}\nAUTO executes signals "
            f"immediately; MANUAL sends proposals with buttons.\n"
            f"Entry mode: {emode.upper()}\nADR sizes by 1% risk with "
            f"pyramid adds and targets; FIXED rides a fixed lot until "
            f"the trend confirms a change.\n"
            f"Strategy: {active or '?'} — M5/M15 switches the HalfTrend "
            f"lane, applies at next bar.")
    if pending and pending != active:
        text += f"\npending: {pending} (applies on the next heartbeat)"
    return (text,
            kb([[(mark("🤖 AUTO", mode == "auto"), "mode:auto"),
                 (mark("👤 MANUAL", mode == "manual"), "mode:manual")],
                [(mark("📊 ADR", emode == "adr"), "tmode:adr"),
                 (mark("🎯 FIXED", emode == "fixed"), "tmode:fixed")],
                [(mark(label, sid == active), f"strat:{sid}")
                 for label, sid in STRATEGY_LANES]]))


def _format_agree(app) -> tuple:
    db = app.state.db
    cur = db.htf_enforce()
    on = cur != "off"
    e200 = db.ema200_enforce()
    e200_on = e200 == "on"
    body = (
        "What confirms a trade\n\n"
        "Higher-timeframe agreement (M5 only)\n"
        f"Currently: {'ENFORCING on ' + cur if on else 'CHECK ONLY (off)'}\n"
        "Runs on EVERY M5 entry either way and is reported on the trade "
        "and in the M15 column.\n"
        "Enforcing means it may also BLOCK an entry — and only while the "
        "tape is choppy; in a trend it never blocks.\n"
        "Off = report only, the trade decision is untouched.\n\n"
        "EMA-200 agreement (M5 and M15)\n"
        f"Currently: {'ENFORCING' if e200_on else 'CHECK ONLY (off)'}\n"
        "BUY agrees when price is above its own EMA-200, SELL when below. "
        "Runs on EVERY entry on BOTH strategies either way and is reported "
        "in the E200 column.\n"
        "Enforcing means it may also BLOCK an entry — no chop exception, "
        "it's on or off all day.\n"
        "Off = report only, the trade decision is untouched.")
    row = [(("● " if cur == c else "") + ("Off (report only)" if c == "off" else c),
            f"agree:{c}") for c in db.HTF_CHOICES]
    e200_row = [(("● " if e200 == c else "")
                + ("Off (report only)" if c == "off" else "Enforcing"),
                f"e200:{c}") for c in db.EMA200_CHOICES]
    return (body, kb([row[:2], row[2:], e200_row]))


def _format_config(app, redacted=False) -> str:
    db = app.state.db
    from app.config import settings
    latest = app.state.latest_heartbeat
    hb = latest[1] if latest else None
    return (
        "⚙️ Config\n"
        f"mode: {db.exec_mode()}\n"
        f"entry mode: {db.entry_mode()}\n"
        f"strategy: {hb.active_strategy if hb else '?'}\n"
        f"forecaster: {settings.forecaster} | horizon: {settings.horizon}\n"
        f"ai mode: {settings.mode} | confirm ≥ {settings.confirm_threshold}\n"
        f"balance: {REDACTED if redacted else (hb.balance if hb else '?')} | "
        f"equity: {REDACTED if redacted else (hb.equity if hb else '?')}\n"
        f"kill switch: {hb.kill_switch if hb else '?'} | "
        f"window open: {hb.window_open if hb else '?'}\n"
        f"spread: {hb.spread_points if hb else '?'}pt\n"
        f"confirms — HTF: {db.htf_enforce()} | EMA200: {db.ema200_enforce()}"
        f" (/agree to change)")


def _format_channel(app, args: list) -> str:
    if args and args[0].lower() == "unlink":
        app.state.db.set_kv("channel_id", "")
        return "🔗 channel unlinked — mirroring off"
    cid = app.state.db.get_kv("channel_id")
    if cid:
        return f"🔗 linked to channel {cid} — /channel unlink to stop"
    return ("no channel linked — add the bot as admin to your channel, "
            "post any message there, then approve the prompt that "
            "appears here")


# ---------------------------------------------------------------------------
# Command registry: single source of truth for both dispatch (handle_command)
# and the pinned help text (format_pinned_help). A command that exists but
# isn't listed here -- or a help line with no matching handler -- used to be
# possible because the two lived as separate hand-maintained lists; now the
# help is generated FROM this table so the two cannot drift.
#
# Each handler has signature (app, parts, redacted) -> str | tuple | None,
# where `parts` is the full whitespace-split command line (parts[0] is the
# command itself, matching how handle_command already sliced args) and
# `redacted` is only meaningful to handlers that surface account figures.
#
# /chart is deliberately NOT here: it renders a photo (or opens the mini
# app) and is special-cased in main.py's poller *before* handle_command is
# even called, because that needs an async send_photo / web_app button, not
# a text reply this pure function can return. It still needs a pinned-help
# line, so _PINNED_EXTRA below carries it verbatim at the right position.
# ---------------------------------------------------------------------------

def _cmd_status(app, parts, redacted):
    return _format_status(app, redacted=redacted)


def _cmd_bal(app, parts, redacted):
    return _format_balance(app, redacted=redacted)


def _cmd_mode(app, parts, redacted):
    return _format_mode(app)


def _cmd_agree(app, parts, redacted):
    return _format_agree(app)


def _cmd_config(app, parts, redacted):
    return _format_config(app, redacted=redacted)


def _cmd_stats(app, parts, redacted):
    return _format_stats(app)


def _cmd_history(app, parts, redacted):
    return _format_history(app)


def _cmd_help(app, parts, redacted):
    # Typing /help used to be silently ignored (unknown command -> None);
    # replying with the same reference the pinned message carries costs
    # nothing and never goes stale (both render from COMMANDS).
    return format_pinned_help()


def _cmd_channel(app, parts, redacted):
    return _format_channel(app, parts[1:])


def _fmt_countdown(in_s: int) -> str:
    h, m = in_s // 3600, (in_s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _cmd_news(app, parts, redacted):
    """Upcoming high-impact USD calendar events (next 24 h), straight from
    the EA's own MT5 calendar feed — the same data the news guard blocks
    on, so this list and the guard can never disagree. Rendered as
    countdowns because event times arrive relative (see NewsEvent)."""
    latest = app.state.latest_heartbeat
    if latest is None or time.time() - latest[0] > _EA_CONNECTED_MAX_AGE_S:
        return "EA not connected — no calendar feed"
    hb = latest[1]
    events = [e for e in getattr(hb, "news", []) if e.in_s > 0]
    radius = getattr(hb, "news_blackout_min", 30)
    if not events:
        return ("no high-impact USD events in the next 24 h — "
                "no news blackouts ahead")
    lines = [f"📰 High-impact USD events (entries frozen ±{radius}m around each):"]
    for e in sorted(events, key=lambda e: e.in_s):
        lines.append(f"• in {_fmt_countdown(e.in_s)} — {e.name}")
    return "\n".join(lines)


def _manual_entry_guard(app, db) -> str | None:
    """Why a manual entry can't be queued right now, or None when it can.
    Shared by /trade and the mtrade: callback -- old buttons stay tappable
    forever, so the tap must re-check everything the command checked. The
    EA still re-runs its own gates (AllowLiveTrading, CanEnter, brake,
    kill switch) authoritatively; these are UX pre-checks, and the
    in-flight check doubles as clash prevention with the strategy's own
    proposals (any live 'entry' blocks a manual one)."""
    latest = app.state.latest_heartbeat
    if latest is None or time.time() - latest[0] > _EA_CONNECTED_MAX_AGE_S:
        return "EA not connected — can't open a trade"
    if latest[1].positions:
        p = latest[1].positions[0]
        return f"already in a trade ({p.direction} {p.lots:g}) — exit it first"
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="entry", status=st) is not None:
            return f"entry already {st}"
    return None


def _cmd_trade(app, parts, redacted):
    why = _manual_entry_guard(app, app.state.db)
    if why:
        return why
    _, hb = app.state.latest_heartbeat
    price = getattr(hb, "bar_c", 0.0) or 0.0
    mode = (getattr(hb, "entry_mode", "") or "?").upper()
    text = (f"📥 Manual entry — XAUUSD @ {price:.2f}\n"
            f"{hb.active_strategy} · {mode} mode\n"
            "Tap a direction — opens on the next heartbeat, then managed "
            "like any EA trade (stop, exits, alerts).")
    return (text, TRADE_KB())


class CommandSpec:
    """handler(app, parts, redacted) -> reply; arg_hint/help build the pinned
    help line as f"/{cmd}{arg_hint} — {help}"."""

    __slots__ = ("handler", "arg_hint", "help")

    def __init__(self, handler, help, arg_hint=""):
        self.handler = handler
        self.arg_hint = arg_hint
        self.help = help


# Order here is the order the pinned help lists commands in.
COMMANDS: dict[str, CommandSpec] = {
    "/status": CommandSpec(_cmd_status, "snapshot + EA connection state"),
    "/bal": CommandSpec(_cmd_bal, "balance, equity, floating P/L"),
    "/mode": CommandSpec(_cmd_mode, "execution (AUTO/MANUAL) + entry mode (ADR/FIXED) "
                                    "+ strategy lane (M5/M15)"),
    "/agree": CommandSpec(_cmd_agree,
                          "what confirms a trade: higher-timeframe (M5) enforce on M15/M30/H1, "
                          "and EMA-200 (M5+M15) enforce or check only"),
    "/trade": CommandSpec(_cmd_trade, "manual entry — tap 🔵 BUY or 🔴 SELL"),
    "/config": CommandSpec(_cmd_config, "current settings"),
    "/news": CommandSpec(_cmd_news, "upcoming high-impact USD events (blackout windows)"),
    "/stats": CommandSpec(_cmd_stats, "per-strategy signal hit-rates"),
    "/history": CommandSpec(_cmd_history, "last 10 trade events"),
    "/channel": CommandSpec(_cmd_channel, "link/unlink the broadcast channel"),
    "/help": CommandSpec(_cmd_help, "show this reference"),
}

# Pinned-help-only lines inserted after a given registered command's line.
# See the COMMANDS docstring above for why /chart lives here instead of in
# COMMANDS itself.
_PINNED_EXTRA: dict[str, list[str]] = {
    "/config": ["/chart — open the live chart"],
    "/help": ["/manual — download the operator manual (PDF)"],
}


# Bumped whenever format_pinned_help()'s text changes. pinned_tick compares
# this against the kv-stored "pinned_help_version" to decide whether the
# pinned message needs rewriting -- an unrelated deploy/restart with no
# content change must not re-edit (or even hit Telegram) every tick.
PINNED_HELP_VERSION = "13"


def format_pinned_help() -> str:
    """Static command reference pinned in the chat. Not live status --
    content only changes when this text (and PINNED_HELP_VERSION) changes.

    Generated from COMMANDS (plus _PINNED_EXTRA for the one command that
    bypasses handle_command) so this can never list a command that isn't
    dispatched, or dispatch one that isn't listed."""
    lines = ["📌 Command reference"]
    for cmd, spec in COMMANDS.items():
        lines.append(f"{cmd}{spec.arg_hint} — {spec.help}")
        lines.extend(_PINNED_EXTRA.get(cmd, []))
    lines += [
        "🟢 Take / 🔴 Skip on a proposal to act on it.",
        "Valid while the strategy holds this stance.",
    ]
    return "\n".join(lines)


def pinned_tick(app, client: "TelegramClient") -> None:
    """Create-pin-once the static command-reference message, self-healing if
    it was deleted/unpinned. Sync so tests can drive it directly with a fake
    transport; the async pinned_editor loop calls this via
    asyncio.to_thread. All failures are swallowed (fail-open).

    Content is static, so a stored kv `pinned_help_version` guards rewrite:
    once a pinned message exists and its version matches
    PINNED_HELP_VERSION, this is a no-op (no Telegram call at all). Rewrite
    only happens when the version differs -- either PINNED_HELP_VERSION was
    bumped, or (upgrade path) no version was ever stored for an
    already-pinned message.

    Self-healing: if the stored id can't be edited -- e.g. the pinned
    message was deleted server-side (edit_message returns None/an error),
    or the stored value isn't a valid numeric id -- the kv id is cleared so
    the *next* tick falls through to the create-and-pin path instead of
    retrying a dead id forever."""
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


def handle_command(text: str, app, redacted=False) -> str | None:
    """Pure function mapping a slash command to a reply, or None if unknown."""
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower()
    spec = COMMANDS.get(cmd)
    if spec is None:
        return None
    return spec.handler(app, parts, redacted)


def handle_channel_post(post: dict, app):
    """A message posted in a channel the bot was added to. If no channel is
    linked and no offer is pending, stage this channel and return the
    owner-chat confirmation (text, keyboard); otherwise None. Only the
    owner's ✅ callback (chan:link) actually stores the id — a stranger's
    channel can never self-link."""
    chat = post.get("chat") or {}
    cid = str(chat.get("id") or "")
    if not cid:
        return None
    if app.state.db.get_kv("channel_id"):
        return None
    if getattr(app.state, "pending_channel", None) is not None:
        return None
    title = chat.get("title") or "channel"
    app.state.pending_channel = cid
    text = (f"🔗 Link channel «{title}» ({cid})?\n"
            f"Members will see trade activity — never account figures.")
    keyboard = kb([[("✅ Link", f"chan:link:{cid}"),
                    ("❌ Ignore", f"chan:ignore:{cid}")]])
    return (text, keyboard)


# ---------------------------------------------------------------------------
# Callback registry: dispatch on parts[0] (the prefix before the first ":")
# into a dict instead of an if/elif chain. The prefixes take different
# arity/shape (mode:auto vs prop:123:take vs chan:link:-100…), so each
# handler keeps its own signature-independent validation and simply returns
# (None, "unknown") when its own arity/value check fails -- exactly what the
# old chain fell through to when a condition didn't match.
#
# Handler signature: (parts, app, db, message_id) -> (edit_text_or_None, toast)
# ---------------------------------------------------------------------------

def _cb_mode(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in ("auto", "manual"):
        db.set_exec_mode(parts[1])
        return (f"Execution mode → {parts[1].upper()}", f"mode: {parts[1]}")
    return (None, "unknown")


def _cb_tmode(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in ("adr", "fixed"):
        db.set_entry_mode(parts[1])
        return (f"Entry mode → {parts[1].upper()} — applies from the next trade.",
                f"entry mode: {parts[1]}")
    return (None, "unknown")


def _cb_agree(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in db.HTF_CHOICES:
        db.set_htf_enforce(parts[1])
        if parts[1] == "off":
            return ("Higher-timeframe agreement → CHECK ONLY. It is still "
                    "evaluated and reported on every trade, but will not block "
                    "an entry.", "agreement: off")
        return (f"Higher-timeframe agreement → ENFORCING on {parts[1]} "
                f"(in choppy tape only; a trend is never blocked).",
                f"agreement: {parts[1]}")
    return (None, "unknown")


def _cb_e200(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in db.EMA200_CHOICES:
        db.set_ema200_enforce(parts[1])
        if parts[1] == "off":
            return ("EMA-200 agreement → CHECK ONLY. It is still evaluated "
                    "and reported on every entry (both strategies), but "
                    "will not block one.", "ema200: off")
        return ("EMA-200 agreement → ENFORCING. A disagreeing entry is now "
                "blocked on both strategies (no chop exception).",
                "ema200: on")
    return (None, "unknown")


def _cb_strat(parts, app, db, message_id):
    if len(parts) > 1:
        sid = parts[1]
        app.state.pending_switch = sid
        return (f"Switching to {sid} at next bar.", f"→ {sid}")
    return (None, "unknown")


def _cb_prop(parts, app, db, message_id):
    if len(parts) == 3 and parts[1].isdigit():
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


def _cb_exitnow(parts, app, db, message_id):
    # EXIT button on a trade-open notification: queue an immediate basket
    # close (dispatched as close_all on the next heartbeat).
    if len(parts) == 2:
        latest = app.state.latest_heartbeat
        if latest is None or not latest[1].positions:
            return (None, "already flat")
        for st in ("pending", "approved", "dispatched"):
            if db.pending_proposal(kind="exit", status=st) is not None:
                return (None, f"close already {st}")
        direction = latest[1].positions[0].direction
        pid = db.create_proposal("exit", direction,
                                 latest[1].active_strategy, 0.0, None)
        db.set_proposal_status(pid, "approved", expected="pending")
        return (None, "closing on next heartbeat…")
    return (None, "unknown")


def _cb_mtrade(parts, app, db, message_id):
    # /trade direction button: the tap is the confirmation. Same rails as
    # every remote command (pre-approved 'entry' proposal -> next heartbeat
    # -> EA 'execute' with all risk gates -> /proposal-result edits this
    # message with the outcome). Editing the tapped message also drops the
    # keyboard, so a queued entry can't be double-tapped.
    if len(parts) != 2 or parts[1] not in ("BUY", "SELL"):
        return (None, "unknown")
    why = _manual_entry_guard(app, db)
    if why:
        return (None, why)
    _, hb = app.state.latest_heartbeat
    price = getattr(hb, "bar_c", 0.0) or 0.0
    pid = db.create_proposal("entry", parts[1], hb.active_strategy, price, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    return (f"📥 {parts[1]} @ {price:.2f} — 👍 queued, opening on next heartbeat…",
            f"{parts[1]} queued")


def _cb_movesl(parts, app, db, message_id):
    # [🔒 Move SL to here] on the target alert: queue a move_sl command on
    # the same rails as every remote command. The EA does the actual move
    # (authoritative, tighten-only, broker stops-level aware); these are UX
    # pre-checks for the stale-button case — the alert message and its
    # buttons stay tappable forever.
    latest = app.state.latest_heartbeat
    if latest is None or time.time() - latest[0] > _EA_CONNECTED_MAX_AGE_S:
        return (None, "EA not connected — can't move the stop")
    if not latest[1].positions:
        return (None, "nothing open — stop can't be moved")
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="move_sl", status=st) is not None:
            return (None, f"move already {st}")
    direction = latest[1].positions[0].direction
    price = getattr(latest[1], "bar_c", 0.0) or 0.0
    pid = db.create_proposal("move_sl", direction,
                             latest[1].active_strategy, price, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    return (None, "moving SL on next heartbeat…")


def _cb_brakereset(parts, app, db, message_id):
    # [Reset brake for today] on a daily-loss-brake notice: queue an
    # owner-approved reset_brake command (same rails as close_all:
    # pre-approved proposal -> next heartbeat -> EA -> /proposal-result
    # edits the tapped message). Guarded like exitnow: one in flight.
    # UX pre-check (the EA re-checks authoritatively): a stale [Reset]
    # button stays tappable forever — don't queue a command when the brake
    # isn't actually ≥70% spent per the latest heartbeat.
    latest = app.state.latest_heartbeat
    pct = float(getattr(latest[1], "daily_loss_pct", 0.0) or 0.0) if latest else 0.0
    if pct < 70.0:
        return (None, f"brake at {pct:.0f}% — nothing to reset")
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="reset_brake", status=st) is not None:
            return (None, f"reset already {st}")
    strategy = latest[1].active_strategy
    pid = db.create_proposal("reset_brake", "-", strategy, 0.0, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    return (None, "resetting brake on next heartbeat…")


def _cb_chan(parts, app, db, message_id):
    # parts[2] is the channel id; ids are negative ("-100..."), but the
    # split on ":" is safe — callback data is built as chan:<action>:<id>
    # and the id contains no colon.
    if len(parts) == 3:
        # Only honor a tap that matches the current pending offer -- a stale
        # button from a superseded/ignored offer, still sitting in chat
        # history, must not silently re-link (or ignore) some other
        # channel's offer. This is what keeps "one pending offer at a time"
        # actually true rather than just true at offer-creation time.
        if parts[2] != getattr(app.state, "pending_channel", None):
            return (None, "offer expired")
        app.state.pending_channel = None
        if parts[1] == "link":
            db.set_kv("channel_id", parts[2])
            return (f"🔗 Channel linked ({parts[2]}) — mirroring on.", "linked")
        return ("Channel ignored.", "ignored")
    return (None, "unknown")


CALLBACKS = {
    "mode": _cb_mode,
    "tmode": _cb_tmode,
    "agree": _cb_agree,
    "e200": _cb_e200,
    "strat": _cb_strat,
    "prop": _cb_prop,
    "exitnow": _cb_exitnow,
    "mtrade": _cb_mtrade,
    "movesl": _cb_movesl,
    "brakereset": _cb_brakereset,
    "chan": _cb_chan,
}


def handle_callback(data: str, app, message_id: int | None = None) -> tuple:
    """Pure function mapping a callback_query's data to (edit_text_or_None,
    toast). The poller edits the tapped message when edit_text is not None
    and always answers the callback with toast (fail-open UX).
    `message_id` (the tapped message, when the poller knows it) lets a
    callback that queues a deferred command remember which message to edit
    once the EA reports the outcome (brakereset:)."""
    db = app.state.db
    parts = data.split(":")
    handler = CALLBACKS.get(parts[0])
    if handler is None:
        return (None, "unknown")
    return handler(parts, app, db, message_id)
