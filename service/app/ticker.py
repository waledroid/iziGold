"""Live trade ticker: one self-editing Telegram message per trade cycle.

Driven from /heartbeat (positions/equity/floating arrive every ~5 s).
State is in-memory only (app.state.ticker) — after a service restart the
first open-position heartbeat simply starts a fresh LIVE message and the
old one stops updating. Every Telegram call is fail-open: a failed send
or edit is dropped and the next heartbeat retries naturally.
"""
import time
from dataclasses import dataclass

TICKER_MIN_EDIT_S = 5


def _live_chart_kb() -> dict | None:
    """Inline web_app button opening the mini app, or None when no public
    URL is configured. Owner-only (see ticker_tick) -- the channel ticker
    copy must stay markup-free (structural no-markup rule).

    Imports settings lazily (not at module level) so tests that
    importlib.reload(app.config) without also reloading this module still
    see the current value -- same convention as telegram.py's /config
    handler."""
    from app.config import settings
    if not settings.miniapp_public_url:
        return None
    return {"inline_keyboard": [[
        {"text": "📈 Live Chart", "web_app": {"url": settings.miniapp_public_url}}
    ]]}


@dataclass
class TickerState:
    owner_msg_id: int | None = None
    owner_text: str = ""
    channel_msg_id: int | None = None
    channel_text: str = ""
    last_edit_ts: float = 0.0


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def format_ticker(hb, mode: str, ts_str: str, closed=False,
                  redacted=False) -> str:
    direction = hb.positions[0].direction if hb.positions else "?"
    head = "📊 CLOSED" if closed else "📊 LIVE"
    lines = [f"{head} — {direction} basket ({mode})"]
    if not redacted:
        lines.append(f"Equity     ${hb.equity:,.2f}")
    lines.append(f"Floating   {_money(hb.floating_pl)}")
    lines.append("")
    for p in hb.positions:
        lines.append(f"{p.direction} {p.lots:g} @ {p.open_price:g}   "
                     f"{_money(p.profit)}")
    lines.append("")
    if closed:
        lines.append(f"closed {ts_str} — final P/L in the close report")
    else:
        lines.append(f"updated {ts_str}")
    return "\n".join(lines)


def _channel_id(app) -> str | None:
    try:
        return app.state.db.get_kv("channel_id") or None
    except Exception:
        return None


def ticker_tick(app, hb, now: float, previous=None) -> None:
    """One heartbeat's worth of ticker work. Sync (call via to_thread or
    directly in tests); never raises."""
    tg = getattr(app.state, "telegram", None)
    if tg is None:
        return
    st = app.state.ticker
    ts_str = time.strftime("%H:%M:%S", time.localtime(now))
    try:
        mode = app.state.db.exec_mode()
    except Exception:
        mode = "?"
    cid = _channel_id(app)

    if hb.positions and st.owner_msg_id is None:
        # flat -> open: post the LIVE message(s)
        text = format_ticker(hb, mode, ts_str)
        try:
            sent = tg.send_message(text, reply_markup=_live_chart_kb())
        except Exception:
            sent = None
        msg_id = (sent or {}).get("result", {}).get("message_id")
        if msg_id is None:
            return  # retry the open on the next heartbeat
        st.owner_msg_id, st.owner_text = msg_id, text
        st.last_edit_ts = now
        if cid:
            ch_text = format_ticker(hb, mode, ts_str, redacted=True)
            try:
                ch_sent = tg.send_message_to(cid, ch_text)
            except Exception:
                ch_sent = None
            ch_id = (ch_sent or {}).get("result", {}).get("message_id")
            if ch_id is not None:
                st.channel_msg_id, st.channel_text = ch_id, ch_text
        return

    if hb.positions and st.owner_msg_id is not None:
        # open -> open: silent in-place edit, throttled, only on change.
        # The timestamp line alone always differs, so compare without it —
        # otherwise every heartbeat would count as "changed".
        if now - st.last_edit_ts < TICKER_MIN_EDIT_S:
            return
        text = format_ticker(hb, mode, ts_str)
        if _body(text) == _body(st.owner_text):
            return
        try:
            edit_result = tg.edit_message(st.owner_msg_id, text)
        except Exception:
            edit_result = None
        if (edit_result or {}).get("ok", False):
            st.owner_text = text
            st.last_edit_ts = now
        else:
            return  # retry on next tick if edit failed
        if cid and st.channel_msg_id is not None:
            ch_text = format_ticker(hb, mode, ts_str, redacted=True)
            try:
                ch_edit_result = tg.edit_message_to(cid, st.channel_msg_id, ch_text)
            except Exception:
                ch_edit_result = None
            if (ch_edit_result or {}).get("ok", False):
                st.channel_text = ch_text
        return

    if not hb.positions and st.owner_msg_id is not None:
        # open -> flat: freeze with CLOSED (never throttled), reset state.
        # The frozen numbers are the LAST OPEN snapshot (this heartbeat is
        # already flat); the close report remains the authoritative P/L.
        snapshot = (previous[1] if previous is not None and previous[1].positions else hb)
        text = format_ticker(snapshot, mode, ts_str, closed=True)
        try:
            tg.edit_message(st.owner_msg_id, text)
        except Exception:
            pass
        if cid and st.channel_msg_id is not None:
            try:
                tg.edit_message_to(
                    cid, st.channel_msg_id,
                    format_ticker(snapshot, mode, ts_str, closed=True,
                                  redacted=True))
            except Exception:
                pass
        app.state.ticker = TickerState()


def _body(text: str) -> str:
    """Ticker text minus its trailing 'updated HH:MM:SS' line."""
    return text.rsplit("\n", 1)[0]
