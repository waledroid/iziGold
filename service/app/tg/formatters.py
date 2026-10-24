"""Telegram message formatting and helpers for XAU Assistant."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.asset_registry import get_asset
from app.tg.keyboards import BTC_STRATEGY_LANES, STRATEGY_LANES, kb

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


def format_report(req, resp) -> str:
    if getattr(resp, "grade_pending", False):
        ai = "grading…"
    else:
        ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
              if resp.ai_available else "AI unavailable ❌")
    sym = getattr(req, "symbol", "XAUUSD")
    badge = get_asset(sym).badge
    return (f"{badge} {sym} {req.timeframe} — {req.signal}\n"
            f"Strategy: {req.signal}\n"
            f"AI: {ai}\n"
            f"Regime: {resp.regime}\n"
            f"Mode: {resp.mode}")


def format_proposal(kind, direction, price, resp, symbol: str = "XAUUSD") -> str:
    if getattr(resp, "grade_pending", False):
        ai = "grading…"
    else:
        ai = (f"{resp.direction} {resp.confidence:.0%} — {resp.verdict} {_ICON[resp.verdict]}"
              if resp.ai_available else "AI unavailable ❌")
    head = "📥 Entry proposal" if kind == "entry" else "📤 Exit proposal"
    badge = get_asset(symbol).badge
    sym_tag = f" [{badge} {symbol}]" if symbol != "XAUUSD" else ""
    body = (f"{head}{sym_tag}: {direction} @ {price}\n"
            f"AI: {ai}\nRegime: {resp.regime}")
    if kind == "entry":
        return f"🕒 {market_session()}\n{body}"
    return body


_EA_CONNECTED_MAX_AGE_S = 30


def _ea_connection_line(app) -> str:
    """First line of /status: EA connection state derived from
    app.state.latest_heartbeats | app.state.latest_heartbeat."""
    hbs = getattr(app.state, "latest_heartbeats", {})
    if len(hbs) > 1:
        parts = []
        for sym, (ts, _) in sorted(hbs.items()):
            age = time.time() - ts
            badge = f"{get_asset(sym).badge} "
            icon = "🟢" if age <= _EA_CONNECTED_MAX_AGE_S else "🔴"
            parts.append(f"{badge}{sym} {icon} ({int(age)}s ago)" if age <= _EA_CONNECTED_MAX_AGE_S else f"{badge}{sym} {icon} ({int(age // 60)}m ago)")
        return f"EA: 🟢 connected · " + " · ".join(parts)

    latest = getattr(app.state, "latest_heartbeat", None)
    if latest is None:
        return "EA: 🔴 never connected"
    ts, _ = latest
    age = time.time() - ts
    if age <= _EA_CONNECTED_MAX_AGE_S:
        return f"EA: 🟢 connected ({int(age)}s ago)"
    return f"EA: 🔴 disconnected (last seen {int(age // 60)}m ago)"


def _miniapp_healthz_url() -> str:
    from app.config import settings
    return (settings.miniapp_health_url or
            f"http://127.0.0.1:{settings.miniapp_port}/healthz")


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
    tg = sys.modules.get("app.telegram")
    hz_func = getattr(tg, "_miniapp_healthz", _miniapp_healthz) if tg else _miniapp_healthz
    hz = hz_func()
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


def _ai_status_line(app) -> str:
    db = getattr(app.state, "db", None)
    ai_on = db.ai_enabled() if db is not None and hasattr(db, "ai_enabled") else False
    if not ai_on:
        return "🤖 AI: OFF (Strategy Sovereign)"
    agent = getattr(app.state, "agent", None)
    from app.config import settings as _cfg
    model = getattr(_cfg, "agent_model", "trading-llm")
    if agent is None or not getattr(_cfg, "agent_api_key", None):
        return f"⚠️ AI: DEGRADED ({model} missing API key)"
    acc_str = ""
    try:
        sc = db.agent_scorecard() if hasattr(db, "agent_scorecard") else {}
        overall = sc.get("overall", {})
        acc = overall.get("accuracy_pct")
        res = overall.get("resolved", 0)
        if acc is not None and res > 0:
            acc_str = f" · Acc: {acc:.1f}% (n={res})"
    except Exception:
        pass
    return f"🤖 AI: Ready · {model}{acc_str}"


def _format_status(app, redacted=False) -> str:
    latest = getattr(app.state, "latest_heartbeat", None)
    hbs = getattr(app.state, "latest_heartbeats", {})
    connection = _ea_connection_line(app)
    session_line = f"🕒 {market_session()}"
    miniapp_line = _miniapp_line()
    ai_line = _ai_status_line(app)
    if latest is None and not hbs:
        return f"{session_line}\n{connection}\n{miniapp_line}\n{ai_line}\nno heartbeat yet"

    if len(hbs) > 1:
        _, primary_hb = latest if latest is not None else next(iter(hbs.values()))
        db = getattr(app.state, "db", None)
        mode = db.exec_mode().upper() if db is not None and hasattr(db, "exec_mode") else "?"
        lines = [session_line, connection, miniapp_line]
        if any(getattr(h, "algo_trading", True) is False for _, h in hbs.values()):
            lines.append("⚠️ ALGO TRADING OFF — MT5 cannot execute trades")
        lines.append(ai_line)
        if not redacted:
            total_floating = sum(getattr(h, "floating_pl", 0.0) for _, h in hbs.values())
            lines.append(f"💰 {primary_hb.equity} equity · {primary_hb.balance} balance "
                         f"· {total_floating:+g} floating")
            money = _money_line(app)
            if money:
                lines.append(money)

        all_positions = []
        for sym, (_, h) in sorted(hbs.items()):
            badge = "₿" if "BTC" in sym.upper() else "🥇"
            st = h.active_strategy
            pending = app.state.pending_switch
            if pending and pending != st:
                st = f"{st} → {pending}"
            is_paused = db.entries_paused(sym) if db else False
            from app.config import is_weekend_market
            if "XAU" in sym.upper() and is_weekend_market():
                state_label = " · 🌙 CLOSED (Weekend — reopens Sun 22:00 UTC)"
            elif is_paused:
                state_label = " · 🔴 STOPPED"
            elif "BTC" in sym.upper():
                state_label = " · ⚡ RUNNING (24/7)"
            else:
                state_label = " · 🟢 RUNNING"
            if h.kill_switch:
                lines.append(f"⛔ [{badge} {sym}] KILL SWITCH TRIPPED — trading halted")
            else:
                prot = f"🛡 [{badge} {sym}] Protection armed"
                if h.hwm and not redacted:
                    dd = max(0.0, (1 - h.equity / h.hwm) * 100)
                    prot += f" · DD {dd:.1f}%"
                daily_loss_pct = getattr(h, "daily_loss_pct", 0.0) or 0.0
                if daily_loss_pct > 0:
                    prot += f" · daily loss {daily_loss_pct:.0f}%"
                lines.append(prot)
            price_val = getattr(h, "bar_c", 0.0) or 0.0
            price_str = f" · ${price_val:,.1f}" if "BTC" in sym.upper() else f" · ${price_val:.2f}"
            lines.append(f"🎯 [{badge} {sym}] {st} · {mode}{price_str}{state_label}")
            for p in h.positions:
                all_positions.append((sym, p))

        if all_positions:
            lines.append(f"📬 Positions ({len(all_positions)}):")
            for sym, p in all_positions:
                badge = "₿" if "BTC" in sym.upper() else "🥇"
                lines.append(f"  #{p.ticket} {p.direction} [{badge} {sym}] {p.lots} @ {p.open_price} "
                            f"· P/L {p.profit:+g}")
        else:
            lines.append("📭 No open positions")
        return "\n".join(lines)

    _, hb = latest if latest is not None else next(iter(hbs.values()))
    pending = app.state.pending_switch
    strategy = hb.active_strategy
    if pending and pending != strategy:
        strategy = f"{strategy} → {pending}"
    if hb.kill_switch:
        protection = "⛔ KILL SWITCH TRIPPED — trading halted"
    else:
        protection = "🛡 Protection armed"
        if hb.hwm and not redacted:
            dd = max(0.0, (1 - hb.equity / hb.hwm) * 100)
            protection += f" · drawdown {dd:.1f}%"
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
    lines.append(ai_line)
    if not redacted:
        lines.append(f"💰 {hb.equity} equity · {hb.balance} balance "
                     f"· {hb.floating_pl:+g} floating")
        money = _money_line(app)
        if money:
            lines.append(money)
    from app.config import is_weekend_market
    sym_name = getattr(hb, "symbol", "XAUUSD")
    if "XAU" in sym_name.upper() and is_weekend_market():
        state_label = " · 🌙 CLOSED (Weekend — reopens Sun 22:00 UTC)"
    else:
        state_label = ""
    lines += [
        protection,
        f"🎯 {strategy} · {mode}{state_label}",
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


def _format_report(app) -> tuple:
    """Single-source Daily, Weekly, and Monthly multi-asset performance report."""
    from app.reports import build_periodic_summary
    from app.tg.keyboards import REPORT_INLINE_KB
    db = getattr(app.state, "db", None)
    conn = getattr(db, "conn", None) if db is not None else None
    if conn is None:
        return ("📊 Performance Report\n\nNo database connection.", REPORT_INLINE_KB())
    periods = build_periodic_summary(conn)
    lines = ["📊 Performance Report"]
    for label, key in (("📅 Today", "today"), ("🗓 This Week", "week"), ("📆 This Month", "month")):
        p = periods[key]
        if p["trades"] == 0:
            lines.append(f"\n{label}: $0.00 (0 trades)")
        else:
            lines.append(
                f"\n{label}: {_fmt_money(p['pl'])} "
                f"({p['wins']}W / {p['losses']}L · {p['win_pct']:.0f}%)"
            )
            g = p["gold"]
            b = p["btc"]
            lines.append(
                f"  🥇 Gold: {_fmt_money(g['pl'])} ({g['trades']})  ·  "
                f"₿ BTC: {_fmt_money(b['pl'])} ({b['trades']})"
            )
    return ("\n".join(lines), REPORT_INLINE_KB())


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
    trades table, or None when the db can't answer."""
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
    """/bal reply: balance, equity, floating P/L from latest heartbeat."""
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


def _format_mode(app) -> tuple:
    mode = app.state.db.exec_mode()
    emode = app.state.db.entry_mode()
    latest_hbs = getattr(app.state, "latest_heartbeats", {})
    has_btc = ("BTCUSD" in latest_hbs) or (app.state.latest_heartbeat and "bit" in getattr(app.state.latest_heartbeat[1], "active_strategy", "").lower())

    gold_active = "iziGold15"
    btc_active = "iziBit15"
    if "XAUUSD" in latest_hbs:
        gold_active = latest_hbs["XAUUSD"][1].active_strategy
    elif app.state.latest_heartbeat and "bit" not in app.state.latest_heartbeat[1].active_strategy.lower():
        gold_active = app.state.latest_heartbeat[1].active_strategy

    if "BTCUSD" in latest_hbs:
        btc_active = latest_hbs["BTCUSD"][1].active_strategy
    elif app.state.latest_heartbeat and "bit" in app.state.latest_heartbeat[1].active_strategy.lower():
        btc_active = app.state.latest_heartbeat[1].active_strategy

    pending = getattr(app.state, "pending_switch", None)

    from app.config import STRATEGY_ALIASES, calculate_adaptive_tier
    norm_gold = STRATEGY_ALIASES.get(gold_active, gold_active)
    norm_btc = STRATEGY_ALIASES.get(btc_active, btc_active)

    curr_eq = (app.state.latest_heartbeat[1].equity if app.state.latest_heartbeat else 1000.0)
    at_info = calculate_adaptive_tier(curr_eq)
    hybrid_note = f"\n⚡ Adaptive Sizing: {at_info['tier'].upper()} ({at_info['description']})" if emode == "hybrid" else ""

    tier_note = f" · {at_info['tier'].upper()} (≤{at_info['max_lots']:.2f}L)" if emode == "hybrid" else ""

    def mark(label, is_active):
        return ("● " + label) if is_active else label

    if has_btc:
        text = (f"⚙️ Settings\n"
                f"• Execution mode: {mode.upper()} (AUTO executes · MANUAL proposes)\n"
                f"• Entry mode: {emode.upper()}{tier_note}\n"
                f"• 🥇 Gold: {gold_active}\n"
                f"• ₿ Bitcoin: {btc_active}")
        kb_rows = [
            [(mark("🤖 AUTO", mode == "auto"), "mode:auto"),
             (mark("👤 MANUAL", mode == "manual"), "mode:manual")],
            [(mark("📊 ADR", emode == "adr"), "tmode:adr"),
             (mark("🎯 FIXED", emode == "fixed"), "tmode:fixed"),
             (mark("⚡ HYBRID", emode == "hybrid"), "tmode:hybrid")],
            [(mark(f"🥇 {label}", sid == norm_gold or sid == gold_active), f"strat:{sid}")
             for label, sid in STRATEGY_LANES],
            [(mark(label, sid == norm_btc or sid == btc_active), f"strat:{sid}")
             for label, sid in BTC_STRATEGY_LANES],
        ]
    else:
        active_label = (app.state.latest_heartbeat[1].active_strategy if app.state.latest_heartbeat else "")
        norm_active = STRATEGY_ALIASES.get(active_label, active_label)
        text = (f"⚙️ Settings\n"
                f"• Execution mode: {mode.upper()} (AUTO executes · MANUAL proposes)\n"
                f"• Entry mode: {emode.upper()}{tier_note}\n"
                f"• Strategy: {active_label or '?'}")
        kb_rows = [
            [(mark("🤖 AUTO", mode == "auto"), "mode:auto"),
             (mark("👤 MANUAL", mode == "manual"), "mode:manual")],
            [(mark("📊 ADR", emode == "adr"), "tmode:adr"),
             (mark("🎯 FIXED", emode == "fixed"), "tmode:fixed"),
             (mark("⚡ HYBRID", emode == "hybrid"), "tmode:hybrid")],
            [(mark(label, sid == norm_active or sid == active_label), f"strat:{sid}")
             for label, sid in STRATEGY_LANES],
        ]

    if pending and (has_btc or pending != (app.state.latest_heartbeat[1].active_strategy if app.state.latest_heartbeat else "")):
        text += f"\npending: {pending} (applies on the next heartbeat)"
    return (text, kb(kb_rows))


def _format_agree(app) -> tuple:
    db = app.state.db
    cur = db.htf_enforce()
    on = cur != "off"
    e200 = db.ema200_enforce()
    e200_on = e200 == "on"
    rsi = db.rsi_enforce()
    rsi_on = rsi == "on"
    adds = db.m15_adds()
    adds_on = adds == "on"
    body = (
        "🎛️ Strategy Confirmation Gates & Filters\n\n"
        f"• HTF Bias (M5): {'ENFORCING on ' + cur if on else 'CHECK ONLY (off, reported)'}\n"
        f"• EMA-200 (M5/M15): {'ENFORCING' if e200_on else 'CHECK ONLY (off, reported)'}\n"
        f"• RSI 70/30 (M15 lane only): {'ENFORCING' if rsi_on else 'CHECK ONLY (off)'}\n"
        f"• Pyramid adds on the M15 lane: {'ON (ADR adds)' if adds_on else 'OFF (pure ride)'}"
    )
    row = [(("● " if cur == c else "") + ("Off (report only)" if c == "off" else c),
            f"agree:{c}") for c in db.HTF_CHOICES]
    e200_row = [(("● " if e200 == c else "")
                + ("Off (report only)" if c == "off" else "Enforcing"),
                f"e200:{c}") for c in db.EMA200_CHOICES]
    rsi_row = [(("● " if rsi == c else "")
               + ("RSI off (report only)" if c == "off" else "RSI enforcing"),
               f"rsi:{c}") for c in db.RSI_CHOICES]
    adds_row = [(("● " if adds == c else "")
                + ("M15 adds off (ride)" if c == "off" else "M15 adds on"),
                f"adds:{c}") for c in db.ADDS_CHOICES]
    return (body, kb([row[:2], row[2:], e200_row, rsi_row, adds_row]))


def _format_ai(app) -> tuple:
    db = app.state.db
    ai_on = db.ai_enabled() if db is not None and hasattr(db, "ai_enabled") else False
    st_text = "ENABLED (Ready)" if ai_on else "DISABLED (Off)"
    from app.config import settings as _cfg
    model = getattr(_cfg, "agent_model", "trading-llm")

    acc_text = ""
    try:
        sc = db.agent_scorecard() if hasattr(db, "agent_scorecard") else {}
        overall = sc.get("overall", {})
        acc = overall.get("accuracy_pct")
        resolved = overall.get("resolved", 0)
        if acc is not None and resolved > 0:
            acc_text = f" · Accuracy: {acc:.1f}% ({resolved} resolved)"
    except Exception:
        pass

    body = (
        "🤖 AI Supervisor\n"
        f"• Status: {st_text}\n"
        f"• Model: {model}\n"
        f"• Role: Risk & Context Supervision{acc_text}\n\n"
        "Use /ai on or /ai off to toggle."
    )
    btn_text = "🔴 Turn AI OFF" if ai_on else "🟢 Turn AI ON"
    btn_val = "ai:off" if ai_on else "ai:on"
    return (body, kb([[ (btn_text, btn_val) ]]))
    return (body, kb([[ (btn_text, btn_val) ]]))


def _cmd_ai(app, parts, redacted):
    db = app.state.db
    if len(parts) >= 2:
        arg = parts[1].strip().lower()
        if arg in ("on", "1", "true", "enable", "start"):
            db.set_ai_enabled(True)
            return "🤖 AI Supervisor turned ON.\nModel will grade signals and supervise risk."
        elif arg in ("off", "0", "false", "disable", "stop"):
            db.set_ai_enabled(False)
            return "🤖 AI Supervisor turned OFF.\nSystem is running on 100% deterministic strategy rules."
    return _format_ai(app)


def _format_config(app, redacted=False) -> str:
    db = app.state.db
    latest = app.state.latest_heartbeat
    hb = latest[1] if latest else None
    bal = REDACTED if redacted else (f"${hb.balance:.2f}" if hb else "?")
    eq = REDACTED if redacted else (f"${hb.equity:.2f}" if hb else "?")
    return (
        "⚙️ Config\n"
        f"• Mode: {db.exec_mode()} | entry mode: {db.entry_mode()}\n"
        f"• Strategy: {hb.active_strategy if hb else '?'}\n"
        f"• Balance: {bal} | Equity: {eq}\n"
        f"• Window: {'OPEN' if hb and hb.window_open else 'CLOSED'} · Guard: {'TRIPPED' if hb and hb.kill_switch else 'OK'}\n"
        f"• HTF: {db.htf_enforce()} | EMA200: {db.ema200_enforce()} | RSI: {db.rsi_enforce()} | Adds: {db.m15_adds()}"
    )


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


def _fmt_countdown(in_s: int) -> str:
    h, m = in_s // 3600, (in_s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


# Bumped whenever format_pinned_help()'s text changes.
PINNED_HELP_VERSION = "15"


def format_pinned_help() -> str:
    """Static command reference pinned in the chat."""
    from app.tg.commands import COMMANDS, _PINNED_EXTRA
    tg = sys.modules.get("app.telegram")
    cmds = getattr(tg, "COMMANDS", COMMANDS) if tg else COMMANDS
    pinned_extra = getattr(tg, "_PINNED_EXTRA", _PINNED_EXTRA) if tg else _PINNED_EXTRA

    lines = ["📌 Command reference"]
    for cmd, spec in cmds.items():
        lines.append(f"{cmd}{spec.arg_hint} — {spec.help}")
        lines.extend(pinned_extra.get(cmd, []))
    lines += [
        "🟢 Take / 🔴 Skip on a proposal to act on it.",
        "Valid while the strategy holds this stance.",
    ]
    return "\n".join(lines)
