"""Telegram inline button callback handlers and registry for XAU Assistant."""

from __future__ import annotations

import sys
import time

from app.tg.commands import _manual_entry_guard
from app.tg.formatters import _EA_CONNECTED_MAX_AGE_S


def _cb_mode(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in ("auto", "manual"):
        db.set_exec_mode(parts[1])
        return (f"Execution mode → {parts[1].upper()}", f"mode: {parts[1]}")
    return (None, "unknown")


def _cb_tmode(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in ("adr", "fixed", "hybrid"):
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


def _cb_rsi(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in db.RSI_CHOICES:
        db.set_rsi_enforce(parts[1])
        if parts[1] == "off":
            return ("RSI 70/30 agreement → CHECK ONLY. It is still evaluated "
                    "and reported on every entry, but will not block one.",
                    "rsi: off")
        return ("RSI 70/30 agreement → ENFORCING on the M15 lane. An "
                "overbought BUY or oversold SELL is now refused there; the "
                "M5 lane keeps reporting only.", "rsi: on")
    return (None, "unknown")


def _cb_adds(parts, app, db, message_id):
    if len(parts) > 1 and parts[1] in db.ADDS_CHOICES:
        db.set_m15_adds(parts[1])
        if parts[1] == "off":
            return ("M15 pyramid adds → OFF. The M15 lane rides one position "
                    "to the reversal or its stop (the validated recipe); an "
                    "open basket keeps its legs, it just gets no new ones.",
                    "m15 adds: off")
        return ("M15 pyramid adds → ON. The M15 lane adds and ratchets like "
                "ADR again — note this was never part of its measured edge.",
                "m15 adds: on")
    return (None, "unknown")


def _cb_strat(parts, app, db, message_id):
    if len(parts) > 1:
        sid = parts[1]
        app.state.pending_switch = sid
        pending_switches = getattr(app.state, "pending_switches", None)
        if pending_switches is None:
            pending_switches = app.state.pending_switches = {}
        if "bit" in sid.lower():
            pending_switches["BTCUSD"] = sid
            db.set_kv("active_strategy_btcusd", sid)
            label = "₿ Bitcoin"
        else:
            pending_switches["XAUUSD"] = sid
            db.set_kv("active_strategy", sid)
            label = "🥇 Gold"
        return (f"Switching {label} to {sid} at next bar.", f"→ {sid}")
    return (None, "unknown")


def _cb_prop(parts, app, db, message_id):
    if len(parts) == 3 and parts[1].isdigit():
        pid, action = int(parts[1]), parts[2]
        row = db.get_proposal(pid)
        if row is None or row["status"] != "pending":
            return (None, f"already {row['status'] if row else 'gone'}")
        status = "approved" if action == "take" else "skipped"
        if not db.set_proposal_status(pid, status, expected="pending"):
            current = db.get_proposal(pid)
            return (None, f"already {current['status'] if current else 'gone'}")
        if action == "take":
            return (f"{row['direction']} @ {row['price']} — 👍 approved, "
                    f"executing on next heartbeat…", "approved")
        return (f"{row['direction']} @ {row['price']} — ❌ skipped", "skipped")
    return (None, "unknown")


def _cb_exitnow(parts, app, db, message_id):
    if len(parts) == 2:
        trade_id_str = parts[1]
        strategy_id = None
        symbol = None
        direction = "BUY"
        if trade_id_str.isdigit() and int(trade_id_str) > 0:
            trade_row = db.conn.execute(
                "SELECT symbol, direction, strategy_id FROM trades WHERE id=?",
                (int(trade_id_str),)).fetchone()
            if trade_row:
                symbol = trade_row[0]
                direction = trade_row[1]
                strategy_id = trade_row[2]

        latest_hbs = getattr(app.state, "latest_heartbeats", {})
        hb = None
        if symbol and symbol in latest_hbs:
            hb = latest_hbs[symbol][1]
        elif app.state.latest_heartbeat:
            hb = app.state.latest_heartbeat[1]

        if hb is None or not hb.positions:
            has_pos = any(h[1].positions for h in latest_hbs.values()) if latest_hbs else False
            if not has_pos:
                return (None, "already flat")

        for st in ("pending", "approved", "dispatched"):
            if db.pending_proposal(kind="exit", status=st) is not None:
                return (None, f"close already {st}")

        if not strategy_id:
            strategy_id = hb.active_strategy if hb else "dashboard"
        if hb and hb.positions:
            direction = hb.positions[0].direction

        pid = db.create_proposal("exit", direction, strategy_id, 0.0, None)
        db.set_proposal_status(pid, "approved", expected="pending")
        return (None, "closing on next heartbeat…")
    return (None, "unknown")


def _cb_mtrade(parts, app, db, message_id):
    if len(parts) == 2 and parts[1] in ("BUY", "SELL"):
        symbol = "XAUUSD"
        direction = parts[1]
    elif len(parts) == 3 and parts[2] in ("BUY", "SELL"):
        symbol = parts[1].upper()
        direction = parts[2]
    else:
        return (None, "unknown")

    why = _manual_entry_guard(app, db, symbol=symbol)
    if why:
        return (None, why)

    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    now = time.time()
    hb = None
    if symbol in latest_hbs and now - latest_hbs[symbol][0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = latest_hbs[symbol][1]
    elif app.state.latest_heartbeat and now - app.state.latest_heartbeat[0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = app.state.latest_heartbeat[1]

    if hb is None:
        return (None, f"{symbol} EA not connected — can't open a trade")

    price = getattr(hb, "bar_c", 0.0) or 0.0
    strategy_id = hb.active_strategy
    pid = db.create_proposal("entry", direction, strategy_id, price, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    badge = "₿ BTCUSD" if "BTC" in symbol else "🥇 XAUUSD"
    return (f"📥 {badge} {direction} @ {price:.2f} — 👍 queued, opening on next heartbeat…",
            f"{direction} {symbol} queued")


def _cb_movesl(parts, app, db, message_id):
    is_be = (len(parts) > 1 and parts[1] == "be")
    trade_id_str = parts[2] if (is_be and len(parts) > 2) else (parts[1] if len(parts) > 1 else "")
    strategy_id = None
    symbol = None
    if trade_id_str.isdigit() and int(trade_id_str) > 0:
        row = db.conn.execute("SELECT symbol, direction, strategy_id FROM trades WHERE id=?",
                              (int(trade_id_str),)).fetchone()
        if row:
            symbol, _, strategy_id = row

    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    now = time.time()
    hb = None
    if symbol and symbol in latest_hbs and now - latest_hbs[symbol][0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = latest_hbs[symbol][1]
    elif app.state.latest_heartbeat and now - app.state.latest_heartbeat[0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = app.state.latest_heartbeat[1]

    if hb is None:
        return (None, "EA not connected — can't move the stop")
    if not hb.positions:
        return (None, "nothing open — stop can't be moved")

    kind = "move_sl_be" if is_be else "move_sl"
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind=kind, status=st) is not None:
            return (None, f"move already {st}")
    direction = hb.positions[0].direction
    price = getattr(hb, "bar_c", 0.0) or 0.0
    strat = strategy_id or hb.active_strategy
    pid = db.create_proposal(kind, direction, strat, price, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    toast = "moving SL to BE on next heartbeat…" if is_be else "moving SL on next heartbeat…"
    return (None, toast)


def _cb_closehalf(parts, app, db, message_id):
    trade_id_str = parts[1] if len(parts) > 1 else ""
    strategy_id = None
    symbol = None
    if trade_id_str.isdigit() and int(trade_id_str) > 0:
        row = db.conn.execute("SELECT symbol, direction, strategy_id FROM trades WHERE id=?",
                              (int(trade_id_str),)).fetchone()
        if row:
            symbol, _, strategy_id = row

    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    now = time.time()
    hb = None
    if symbol and symbol in latest_hbs and now - latest_hbs[symbol][0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = latest_hbs[symbol][1]
    elif app.state.latest_heartbeat and now - app.state.latest_heartbeat[0] <= _EA_CONNECTED_MAX_AGE_S:
        hb = app.state.latest_heartbeat[1]

    if hb is None:
        return (None, "EA not connected — cannot close")
    if not hb.positions:
        return (None, "nothing open — cannot close")

    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="close_half", status=st) is not None:
            return (None, f"close already {st}")
    direction = hb.positions[0].direction
    price = getattr(hb, "bar_c", 0.0) or 0.0
    strat = strategy_id or hb.active_strategy
    pid = db.create_proposal("close_half", direction, strat, price, None)
    if message_id:
        db.set_proposal_message(pid, message_id)
    db.set_proposal_status(pid, "approved", expected="pending")
    return (None, "closing 50% on next heartbeat…")


def _cb_brakereset(parts, app, db, message_id):
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
    if len(parts) == 3:
        if parts[2] != getattr(app.state, "pending_channel", None):
            return (None, "offer expired")
        app.state.pending_channel = None
        if parts[1] == "link":
            db.set_kv("channel_id", parts[2])
            return (f"🔗 Channel linked ({parts[2]}) — mirroring on.", "linked")
        return ("Channel ignored.", "ignored")
    return (None, "unknown")


def _cb_ai(parts, app, db, message_id):
    if len(parts) == 2:
        val = parts[1].lower() == "on"
        db.set_ai_enabled(val)
        status_str = "ENABLED" if val else "DISABLED"
        return (f"🤖 AI Supervisor {status_str}.", status_str.lower())
    return (None, "unknown")


def _cb_nav(parts, app, db, message_id):
    """Button navigation router allowing instant screen transitions."""
    if len(parts) > 1:
        target = parts[1]
        if target == "status":
            from app.tg.formatters import _format_status
            return (_format_status(app), "status")
        elif target == "trade":
            from app.tg.commands import _cmd_trade
            reply = _cmd_trade(app, ["/trade"], False)
            if isinstance(reply, tuple):
                return (reply[0], "trade")
            return (reply, "trade")
        elif target == "ai":
            from app.tg.formatters import _format_ai
            reply = _format_ai(app)
            return (reply[0], "ai")
        elif target == "mode":
            from app.tg.formatters import _format_mode
            reply = _format_mode(app)
            return (reply[0], "mode")
        elif target == "rules":
            from app.tg.formatters import _format_agree
            reply = _format_agree(app)
            return (reply[0], "rules")
        elif target == "report":
            from app.tg.formatters import _format_report
            reply = _format_report(app)
            return (reply[0], "report")
        elif target == "stats":
            from app.tg.formatters import _format_stats
            return (_format_stats(app), "stats")
        elif target == "news":
            from app.tg.commands import _cmd_news
            return (_cmd_news(app, ["/news"], False), "news")
        elif target == "history":
            from app.tg.formatters import _format_history
            return (_format_history(app), "history")
    return (None, "unknown")


CALLBACKS = {
    "mode": _cb_mode,
    "tmode": _cb_tmode,
    "agree": _cb_agree,
    "ai": _cb_ai,
    "e200": _cb_e200,
    "rsi": _cb_rsi,
    "adds": _cb_adds,
    "strat": _cb_strat,
    "prop": _cb_prop,
    "exitnow": _cb_exitnow,
    "closehalf": _cb_closehalf,
    "mtrade": _cb_mtrade,
    "movesl": _cb_movesl,
    "brakereset": _cb_brakereset,
    "chan": _cb_chan,
    "nav": _cb_nav,
}


def handle_callback(data: str, app, message_id: int | None = None) -> tuple:
    """Pure function mapping a callback_query's data to (edit_text_or_None, toast)."""
    db = app.state.db
    parts = data.split(":")
    tg = sys.modules.get("app.telegram")
    callbacks = getattr(tg, "CALLBACKS", CALLBACKS) if tg else CALLBACKS
    handler = callbacks.get(parts[0])
    if handler is None:
        return (None, "unknown")
    return handler(parts, app, db, message_id)
