"""Command specifications and handlers for Telegram bot with button prioritization."""

from __future__ import annotations

import sys
import time
import types

from app.tg.formatters import (
    _EA_CONNECTED_MAX_AGE_S,
    _cmd_ai as _format_cmd_ai,
    _fmt_countdown,
    _format_agree,
    _format_balance,
    _format_channel,
    _format_config,
    _format_history,
    _format_mode,
    _format_report,
    _format_stats,
    _format_status,
    format_pinned_help,
)
from app.tg.keyboards import TRADE_KB, kb


def _cmd_start(app, parts, redacted):
    from app.tg.keyboards import MAIN_MENU_REPLY_KB
    db = getattr(app.state, "db", None)
    xau_p = db.entries_paused("XAUUSD") if db else False
    btc_p = db.entries_paused("BTCUSD") if db else False
    st = _format_status(app, redacted=redacted)
    welcome = (
        "⚡ IZIGOLD COCKPIT\n\n"
        f"{st}"
    )
    return (welcome, MAIN_MENU_REPLY_KB(xau_paused=xau_p, btc_paused=btc_p))


def _handle_asset_toggle(app, symbol: str, stop: bool):
    from app.tg.keyboards import MAIN_MENU_REPLY_KB
    db = getattr(app.state, "db", None)
    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    hb_data = latest_hbs.get(symbol)
    hb = hb_data[1] if hb_data is not None else None
    latest_single_hb = getattr(app.state, "latest_heartbeat", None)
    if not hb and latest_single_hb:
        primary_sym = getattr(latest_single_hb[1], "symbol", "XAUUSD")
        if primary_sym == symbol:
            hb = latest_single_hb[1]

    is_btc = "BTC" in symbol.upper()
    badge = "₿ BTCUSD" if is_btc else "🥇 XAUUSD"
    btn_label = "₿ BTC" if is_btc else "🥇 Gold"
    strat_default = "iziBit5" if is_btc else "iziGold15"
    strat_id = getattr(hb, "active_strategy", strat_default) or strat_default

    if stop:
        if db:
            db.set_entries_paused(True, symbol=symbol)

        # Check if positions exist to liquidate
        open_positions = getattr(hb, "positions", []) if hb else []
        liquidated_msg = ""
        if open_positions:
            last_entry = db.last_executed_entry() if db else None
            dir_str = last_entry["direction"] if last_entry else "BUY"
            price = getattr(hb, "bar_c", 0.0) or 0.0
            pid = db.create_proposal("exit", dir_str, strat_id, price, None) if db else 0
            if pid and db:
                db.set_proposal_status(pid, "approved", expected="pending")
            liquidated_msg = f"• Liquidating {len(open_positions)} open position(s)\n"
        else:
            liquidated_msg = "• Positions: Already flat (0 open)\n"

        xau_p = db.entries_paused("XAUUSD") if db else False
        btc_p = db.entries_paused("BTCUSD") if db else False

        text = (
            f"⏹️ {badge} HALTED & SECURED\n"
            f"{liquidated_msg}"
            f"• Safety: Auto-entries PAUSED\n\n"
            f"Tap [ {btn_label}: ▶️ PLAY ] to resume."
        )
        return (text, MAIN_MENU_REPLY_KB(xau_paused=xau_p, btc_paused=btc_p))
    else:
        if db:
            db.set_entries_paused(False, symbol=symbol)

        xau_p = db.entries_paused("XAUUSD") if db else False
        btc_p = db.entries_paused("BTCUSD") if db else False

        text = (
            f"▶️ {badge} RESUMED\n"
            f"• Strategy: {strat_id}\n"
            f"• Mode: AUTO · Entries active & armed"
        )
        return (text, MAIN_MENU_REPLY_KB(xau_paused=xau_p, btc_paused=btc_p))


def _cmd_stop_xau(app, parts, redacted):
    return _handle_asset_toggle(app, "XAUUSD", stop=True)


def _cmd_play_xau(app, parts, redacted):
    return _handle_asset_toggle(app, "XAUUSD", stop=False)


def _cmd_stop_btc(app, parts, redacted):
    return _handle_asset_toggle(app, "BTCUSD", stop=True)


def _cmd_play_btc(app, parts, redacted):
    return _handle_asset_toggle(app, "BTCUSD", stop=False)


def _cmd_status(app, parts, redacted):
    return _format_status(app, redacted=redacted)


def _cmd_bal(app, parts, redacted):
    return _format_balance(app, redacted=redacted)


def _cmd_mode(app, parts, redacted):
    return _format_mode(app)


def _cmd_agree(app, parts, redacted):
    return _format_agree(app)


def _cmd_ai(app, parts, redacted):
    return _format_cmd_ai(app, parts, redacted)


def _cmd_config(app, parts, redacted):
    return _format_config(app, redacted=redacted)


def _cmd_stats(app, parts, redacted):
    return _format_stats(app)


def _cmd_report(app, parts, redacted):
    return _format_report(app)


def _cmd_history(app, parts, redacted):
    return _format_history(app)


def _cmd_help(app, parts, redacted):
    return format_pinned_help()


def _cmd_channel(app, parts, redacted):
    return _format_channel(app, parts[1:])


def _cmd_news(app, parts, redacted):
    """Upcoming high-impact USD calendar events (next 24 h), straight from
    the EA's own MT5 calendar feed — with fail-open fallback to external news
    feed if MT5 calendar has no data."""
    latest = app.state.latest_heartbeat
    events = []
    radius = 30
    if latest is None or time.time() - latest[0] > _EA_CONNECTED_MAX_AGE_S:
        return "EA not connected — no calendar feed"

    hb = latest[1]
    events = [e for e in getattr(hb, "news", []) if getattr(e, "in_s", 0) > 0]
    radius = getattr(hb, "news_blackout_min", 30)
    source = "MT5 calendar"
    if latest is not None and time.time() - latest[0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = latest[1]
        events = [e for e in getattr(hb, "news", []) if e.in_s > 0]
        radius = getattr(hb, "news_blackout_min", 30)

    import os
    if not events and not hasattr(hb, "news") and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from app.news_feed import fetch_upcoming_news
            ext_events = fetch_upcoming_news(horizon_s=86400)
            if ext_events:
                events = [types.SimpleNamespace(**ev) for ev in ext_events]
                source = "External calendar fallback"
        except Exception:
            pass

    if not events:
        if latest is None or time.time() - latest[0] > _EA_CONNECTED_MAX_AGE_S:
            return "EA not connected — no calendar feed"
        return ("no high-impact USD events in the next 24 h — "
                "no news blackouts ahead")
    lines = [f"📰 High-impact USD events ({source}, entries frozen ±{radius}m around each):"]
    for e in sorted(events, key=lambda e: e.in_s):
        lines.append(f"• in {_fmt_countdown(e.in_s)} — {e.name}")
    return "\n".join(lines)


def _manual_entry_guard(app, db, symbol: str = "") -> str | None:
    """Why a manual entry can't be queued right now, or None when it can."""
    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    now = time.time()
    has_btc = ("BTCUSD" in latest_hbs) or (app.state.latest_heartbeat and "bit" in getattr(app.state.latest_heartbeat[1], "active_strategy", "").lower())

    if symbol:
        hb_tuple = latest_hbs.get(symbol)
        if hb_tuple is None or now - hb_tuple[0] > _EA_CONNECTED_MAX_AGE_S:
            latest = app.state.latest_heartbeat
            if latest and now - latest[0] <= _EA_CONNECTED_MAX_AGE_S and (getattr(latest[1], "symbol", "") == symbol or not getattr(latest[1], "symbol", "")):
                hb_tuple = latest
            else:
                return f"{symbol} EA not connected — can't open a trade"
        hb = hb_tuple[1]
        if hb.positions:
            p = hb.positions[0]
            return f"already in a trade ({p.direction} {p.lots:g}) — exit it first"
        for st in ("pending", "approved", "dispatched"):
            prop = db.pending_proposal(kind="entry", status=st)
            if prop is not None:
                prop_strat = prop.get("strategy_id", "")
                if ("bit" in prop_strat.lower() and "BTC" in symbol) or ("bit" not in prop_strat.lower() and "XAU" in symbol):
                    return f"{symbol} entry already {st}"
        return None

    if not has_btc:
        latest = app.state.latest_heartbeat
        if latest is None or now - latest[0] > _EA_CONNECTED_MAX_AGE_S:
            return "EA not connected — can't open a trade"
        if latest[1].positions:
            p = latest[1].positions[0]
            return f"already in a trade ({p.direction} {p.lots:g}) — exit it first"
        for st in ("pending", "approved", "dispatched"):
            if db.pending_proposal(kind="entry", status=st) is not None:
                return f"entry already {st}"
        return None

    active_hbs = [h for ts, h in latest_hbs.values() if now - ts <= _EA_CONNECTED_MAX_AGE_S]
    if not active_hbs:
        latest = app.state.latest_heartbeat
        if latest is None or now - latest[0] > _EA_CONNECTED_MAX_AGE_S:
            return "EA not connected — can't open a trade"
    return None


def _cmd_trade(app, parts, redacted):
    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    has_btc = ("BTCUSD" in latest_hbs) or (app.state.latest_heartbeat and "bit" in getattr(app.state.latest_heartbeat[1], "active_strategy", "").lower())

    if not has_btc:
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
        return (text, TRADE_KB(multi_asset=False))

    why = _manual_entry_guard(app, app.state.db)
    if why:
        return why
    gold_price = 0.0
    btc_price = 0.0
    if "XAUUSD" in latest_hbs:
        gold_price = getattr(latest_hbs["XAUUSD"][1], "bar_c", 0.0) or 0.0
    if "BTCUSD" in latest_hbs:
        btc_price = getattr(latest_hbs["BTCUSD"][1], "bar_c", 0.0) or 0.0

    if not gold_price and app.state.latest_heartbeat:
        hb = app.state.latest_heartbeat[1]
        if getattr(hb, "symbol", "") == "BTCUSD":
            btc_price = getattr(hb, "bar_c", 0.0) or 0.0
        else:
            gold_price = getattr(hb, "bar_c", 0.0) or 0.0

    prices = []
    if gold_price > 0:
        prices.append(f"🥇 Gold: ${gold_price:.2f}")
    if btc_price > 0:
        prices.append(f"₿ BTC: ${btc_price:,.1f}")
    prices_line = "  ·  ".join(prices) if prices else "Real-time rates active"

    text = (f"⚡ Instant Manual Order Deck\n"
            f"{prices_line}\n\n"
            "Select asset & direction to execute on next heartbeat:")
    return (text, TRADE_KB(multi_asset=True))


class CommandSpec:
    """handler(app, parts, redacted) -> reply; arg_hint/help build the pinned
    help line as f"/{cmd}{arg_hint} — {help}"."""

    __slots__ = ("handler", "arg_hint", "help")

    def __init__(self, handler, help, arg_hint=""):
        self.handler = handler
        self.arg_hint = arg_hint
        self.help = help


COMMANDS: dict[str, CommandSpec] = {
    "/start": CommandSpec(_cmd_start, "interactive button menu"),
    "/status": CommandSpec(_cmd_status, "snapshot + EA connection state"),
    "/bal": CommandSpec(_cmd_bal, "balance, equity, floating P/L"),
    "/mode": CommandSpec(_cmd_mode, "execution (AUTO/MANUAL) + entry mode (ADR/FIXED) "
                                    "+ strategy lane (M5/M15)"),
    "/agree": CommandSpec(_cmd_agree,
                          "what confirms a trade: higher-timeframe (M5) enforce on M15/M30/H1, "
                          "and EMA-200 (M5+M15) enforce or check only"),
    "/ai": CommandSpec(_cmd_ai, "toggle supervisory AI (on/off) or view status"),
    "/trade": CommandSpec(_cmd_trade, "manual entry — tap 🔵 BUY or 🔴 SELL"),
    "/config": CommandSpec(_cmd_config, "current settings"),
    "/news": CommandSpec(_cmd_news, "upcoming high-impact USD events (blackout windows)"),
    "/report": CommandSpec(_cmd_report, "daily, weekly & monthly P/L breakdown (Gold & Bitcoin)"),
    "/stats": CommandSpec(_cmd_stats, "per-strategy signal hit-rates"),
    "/history": CommandSpec(_cmd_history, "last 10 trade events"),
    "/channel": CommandSpec(_cmd_channel, "link/unlink the broadcast channel"),
    "/help": CommandSpec(_cmd_help, "show this reference"),
    "/stop_xau": CommandSpec(_cmd_stop_xau, "halt gold trading & close positions"),
    "/play_xau": CommandSpec(_cmd_play_xau, "resume gold trading"),
    "/stop_btc": CommandSpec(_cmd_stop_btc, "halt bitcoin trading & close positions"),
    "/play_btc": CommandSpec(_cmd_play_btc, "resume bitcoin trading"),
}

_PINNED_EXTRA: dict[str, list[str]] = {
    "/config": ["/chart — open the live chart"],
    "/help": ["/manual — download the operator manual (PDF)"],
}

# Button text mapping allowing buttons to be pressed instead of typing slash commands
BUTTON_COMMAND_MAP: dict[str, str] = {
    "🥇 gold: ⏹️ stop": "/stop_xau",
    "🥇 gold: ▶️ play": "/play_xau",
    "₿ btc: ⏹️ stop": "/stop_btc",
    "₿ btc: ▶️ play": "/play_btc",
    "gold: stop": "/stop_xau",
    "gold: play": "/play_xau",
    "btc: stop": "/stop_btc",
    "btc: play": "/play_btc",
    "stop gold": "/stop_xau",
    "play gold": "/play_xau",
    "stop btc": "/stop_btc",
    "play btc": "/play_btc",
    "📊 live status": "/status",
    "📊 status": "/status",
    "status": "/status",
    "⚡ instant trade": "/trade",
    "⚡ quick trade": "/trade",
    "⚡ trade": "/trade",
    "trade": "/trade",
    "🤖 ai supervisor": "/ai",
    "🤖 ai": "/ai",
    "ai": "/ai",
    "⚙️ settings": "/mode",
    "settings": "/mode",
    "⚙️ mode & lane": "/mode",
    "⚙️ strategy & mode": "/mode",
    "⚙️ mode": "/mode",
    "mode": "/mode",
    "🛡 protection & rules": "/agree",
    "🛡 rules & confirms": "/agree",
    "🛡 rules": "/agree",
    "rules": "/agree",
    "agree": "/agree",
    "📈 performance": "/report",
    "📈 report": "/report",
    "📊 report": "/report",
    "📊 reports": "/report",
    "report": "/report",
    "reports": "/report",
    "📈 stats": "/stats",
    "stats": "/stats",
    "📰 news & calendar": "/news",
    "📰 news": "/news",
    "news": "/news",
    "📜 trade history": "/history",
    "📜 history": "/history",
    "history": "/history",
    "🌐 webapp chart": "/chart",
    "🌐 open webapp": "/chart",
    "🌐 web chart": "/chart",
    "📈 chart": "/chart",
    "chart": "/chart",
    "📷 snapshot": "/snap",
    "📷 chart": "/snap",
    "snapshot": "/snap",
    "snap": "/snap",
    "menu": "/start",
    "start": "/start",
}


def handle_command(text: str, app, redacted=False) -> str | tuple | None:
    """Pure function mapping a slash command or button text to a reply, or None if unknown."""
    cleaned = text.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    if lower in BUTTON_COMMAND_MAP:
        cleaned = BUTTON_COMMAND_MAP[lower]

    parts = cleaned.split()
    if not parts:
        return None
    cmd = parts[0].lower()
    tg = sys.modules.get("app.telegram")
    cmds = getattr(tg, "COMMANDS", COMMANDS) if tg else COMMANDS
    spec = cmds.get(cmd)
    if spec is None:
        return None
    return spec.handler(app, parts, redacted)


def handle_channel_post(post: dict, app):
    """A message posted in a channel the bot was added to."""
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
