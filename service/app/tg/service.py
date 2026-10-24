"""Telegram Bot Background Service Loops & Command Dispatch for XAU Assistant.

Manages the long-polling loop, callback routing, pinned command cheat-sheet,
manual PDF transmission, and chart snapshot replies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app import config
from app.chart_cmd import merge_forming_bar
from app.render import render_snapshot_chart
from app.sr_zones import sr_zones_from_db
from app.tg.callbacks import handle_callback
from app.tg.client import pinned_tick
from app.tg.commands import BUTTON_COMMAND_MAP, handle_channel_post, handle_command

log = logging.getLogger("uvicorn.error")

_CHART_HB_FRESH_S = 60
_MANUAL_PDF = Path(__file__).resolve().parents[3] / "docs" / "izi_manual.pdf"


def mirror_command_text(text: str, app) -> str | None:
    """Channel rendition of an owner command: '👤 /cmd' + the redacted
    reply. None when the command is unknown or owner-only."""
    if text.split()[0].lower() == "/channel":
        return None  # link management is owner-only housekeeping
    reply = handle_command(text, app, redacted=True)
    if reply is None:
        return None
    body = reply[0] if isinstance(reply, tuple) else reply
    return f"👤 {text}\n\n{body}"


async def send_manual(app) -> None:
    """/manual: send docs/izi_manual.pdf into the owner chat as a document."""
    tg = app.state.telegram
    import sys
    pdf_path = _MANUAL_PDF
    main_mod = sys.modules.get("app.main")
    if main_mod is not None and hasattr(main_mod, "_MANUAL_PDF"):
        pdf_path = getattr(main_mod, "_MANUAL_PDF")
    if not pdf_path.is_file():
        await asyncio.to_thread(
            tg.send_message,
            "manual not found — run scripts/build_manual.py and restart")
        return
    data = pdf_path.read_bytes()
    await asyncio.to_thread(
        tg.send_document, "izi — operator manual", data, "izi_manual.pdf")


async def send_photo_snapshot(app, with_miniapp_btn: bool = False, target_symbol: str | None = None) -> None:
    """Render closed candles + the heartbeat's forming bar and send as photo."""
    from app.routers.ea import _mirror
    from app.config import is_weekend_market
    tg = app.state.telegram
    weekend = is_weekend_market()

    sym = (target_symbol or "").strip().upper()
    if not sym:
        sym = "BTCUSD" if weekend else "XAUUSD"

    candles = None
    timeframe = "M15"
    cbs = getattr(app.state, "candles_by_series", {})
    for (s_name, tf), c_list in cbs.items():
        if s_name.upper() == sym and c_list:
            candles = c_list
            timeframe = tf
            sym = s_name
            break

    rc = getattr(app.state, "recent_candles", None)
    if not candles and rc and rc.get("candles"):
        if not target_symbol or rc.get("symbol", "").upper() == sym:
            candles = rc["candles"]
            timeframe = rc.get("timeframe", "M15")
            sym = rc.get("symbol", sym)

    if not candles:
        await asyncio.to_thread(
            tg.send_message, f"no candles yet for {sym} — waiting for the first bar post")
        return

    hbs = getattr(app.state, "latest_heartbeats", {})
    hb_pair = hbs.get(sym) or getattr(app.state, "latest_heartbeat", None)
    hb = hb_pair[1] if hb_pair is not None else None
    stale = (hb_pair is None or (time.time() - hb_pair[0]) > _CHART_HB_FRESH_S
             or not getattr(hb, "bar_t", 0))

    if not stale and hb is not None:
        candles = merge_forming_bar(candles, hb)
    out = str(app.state.screenshot_dir / "chart_cmd.png")
    positions = hb.positions if hb is not None else []
    zones = await asyncio.to_thread(sr_zones_from_db, app.state.db, app.state.sr_cache)
    ok = await asyncio.to_thread(render_snapshot_chart, candles, out, positions, zones)
    if not ok:
        await asyncio.to_thread(tg.send_message, "chart render failed")
        return

    caption = (f"📈 {sym} {timeframe} — {candles[-1].c:g} "
               f"(as of {time.strftime('%H:%M:%S')})")
    if stale:
        caption += " · closed bars only"
    if weekend and "BTC" in sym.upper():
        caption += " · ⚡ Weekend Crypto Active"
    png = await asyncio.to_thread(Path(out).read_bytes)

    kb = None
    if with_miniapp_btn and config.settings.miniapp_public_url:
        kb = {"inline_keyboard": [[
            {"text": "📈 Open Live Chart",
             "web_app": {"url": config.settings.miniapp_public_url}}
        ]]}

    await asyncio.to_thread(tg.send_photo, caption, png, reply_markup=kb)
    chan_link = config.settings.miniapp_direct_link or config.settings.miniapp_public_url
    mirror_caption = f"👤 /chart\n{caption}"
    if chan_link:
        mirror_caption += f"\n📈 {chan_link}"
    await _mirror(app, photo_bytes=png, caption=mirror_caption)


async def send_chart_snapshot(app, target_symbol: str | None = None) -> None:
    """/chart: render closed candles + the heartbeat's forming bar and send as photo,
    with an inline button to launch the live MiniApp if configured.
    """
    await send_photo_snapshot(app, with_miniapp_btn=True, target_symbol=target_symbol)


async def telegram_poller(app):
    """Long-poll Telegram for commands and reply. Fail-open: any exception
    just backs off and continues.
    """
    from app.routers.ea import _mirror
    offset = 0
    chat_id = str(getattr(app.state.telegram, "chat_id", "") or config.settings.telegram_chat_id)
    while True:
        try:
            updates = await asyncio.to_thread(app.state.telegram.get_updates, offset)
            for upd in updates:
                offset = upd.get("update_id", offset - 1) + 1
                cq = upd.get("callback_query")
                if cq is not None:
                    from_id = str((cq.get("from") or {}).get("id"))
                    if from_id == chat_id:
                        msg = cq.get("message") or {}
                        edit_text, toast = handle_callback(
                            cq.get("data", ""), app,
                            message_id=msg.get("message_id"))
                        await asyncio.to_thread(app.state.telegram.answer_callback,
                                                cq.get("id", ""), toast)
                        if edit_text and msg.get("message_id"):
                            nav_kb = None
                            data = cq.get("data", "")
                            if data.startswith("nav:"):
                                target = data.split(":")[1] if len(data.split(":")) > 1 else ""
                                if target == "status":
                                    from app.tg.keyboards import STATUS_INLINE_KB
                                    nav_kb = STATUS_INLINE_KB()
                                elif target == "trade":
                                    from app.tg.keyboards import TRADE_KB
                                    nav_kb = TRADE_KB()
                                elif target == "ai":
                                    from app.tg.formatters import _format_ai
                                    nav_kb = _format_ai(app)[1]
                                elif target == "mode":
                                    from app.tg.formatters import _format_mode
                                    nav_kb = _format_mode(app)[1]
                                elif target == "rules":
                                    from app.tg.formatters import _format_agree
                                    nav_kb = _format_agree(app)[1]
                                elif target == "stats":
                                    from app.tg.keyboards import PERF_INLINE_KB
                                    nav_kb = PERF_INLINE_KB()
                                elif target == "news":
                                    from app.tg.keyboards import NEWS_INLINE_KB
                                    nav_kb = NEWS_INLINE_KB()
                                elif target == "history":
                                    from app.tg.keyboards import HISTORY_INLINE_KB
                                    nav_kb = HISTORY_INLINE_KB()
                            try:
                                if nav_kb is not None:
                                    await asyncio.to_thread(app.state.telegram.edit_message,
                                                            msg["message_id"], edit_text, reply_markup=nav_kb)
                                else:
                                    await asyncio.to_thread(app.state.telegram.edit_message,
                                                            msg["message_id"], edit_text)
                            except TypeError:
                                await asyncio.to_thread(app.state.telegram.edit_message,
                                                        msg["message_id"], edit_text)
                            if not cq.get("data", "").startswith("chan:"):
                                await _mirror(app, text=edit_text)
                    continue
                ch_post = upd.get("channel_post")
                if ch_post is not None:
                    offer = handle_channel_post(ch_post, app)
                    if offer is not None:
                        await asyncio.to_thread(
                            app.state.telegram.send_message,
                            offer[0], offer[1])
                    continue
                message = upd.get("message") or {}
                raw_text = (message.get("text") or "").strip()
                msg_chat_id = str(message.get("chat", {}).get("id"))
                is_cmd = raw_text.startswith("/") or raw_text.lower() in BUTTON_COMMAND_MAP
                if is_cmd and msg_chat_id == chat_id:
                    norm_text = BUTTON_COMMAND_MAP.get(raw_text.lower(), raw_text)
                    tokens = norm_text.split()
                    first_token = tokens[0].lower()
                    target_arg = tokens[1] if len(tokens) > 1 else None
                    if first_token in ("/snap", "/snapshot", "/photo"):
                        try:
                            await send_photo_snapshot(app, target_symbol=target_arg)
                        except Exception:
                            log.exception("send_photo_snapshot failed")
                        continue
                    if first_token == "/chart":
                        try:
                            await send_chart_snapshot(app, target_symbol=target_arg)
                        except Exception:
                            log.exception("send_chart_snapshot failed")
                        continue
                    if first_token == "/manual":
                        try:
                            await send_manual(app)
                        except Exception:
                            log.exception("send_manual failed")
                        continue
                    reply = handle_command(norm_text, app)
                    if isinstance(reply, tuple):
                        await asyncio.to_thread(app.state.telegram.send_message,
                                                reply[0], reply[1])
                    elif reply is not None:
                        kb_to_attach = None
                        if first_token == "/status":
                            from app.tg.keyboards import STATUS_INLINE_KB
                            kb_to_attach = STATUS_INLINE_KB()
                        elif first_token == "/trade":
                            from app.tg.keyboards import TRADE_KB
                            kb_to_attach = TRADE_KB()
                        elif first_token == "/perf":
                            from app.tg.keyboards import PERF_INLINE_KB
                            kb_to_attach = PERF_INLINE_KB()
                        elif first_token in ("/rules", "/agree"):
                            from app.tg.formatters import _format_agree
                            kb_to_attach = _format_agree(app)[1]
                        elif first_token == "/ai":
                            from app.tg.formatters import _format_ai
                            kb_to_attach = _format_ai(app)[1]
                        elif first_token == "/news":
                            from app.tg.keyboards import NEWS_INLINE_KB
                            kb_to_attach = NEWS_INLINE_KB()
                        elif first_token == "/mode":
                            from app.tg.formatters import _format_mode
                            kb_to_attach = _format_mode(app)[1]
                        elif first_token in ("/history", "/deals"):
                            from app.tg.keyboards import HISTORY_INLINE_KB
                            kb_to_attach = HISTORY_INLINE_KB()
                        if kb_to_attach is not None:
                            try:
                                await asyncio.to_thread(app.state.telegram.send_message,
                                                        reply, reply_markup=kb_to_attach)
                            except TypeError:
                                await asyncio.to_thread(app.state.telegram.send_message, reply)
                        else:
                            await asyncio.to_thread(app.state.telegram.send_message, reply)
                    chan_text = mirror_command_text(norm_text, app)
                    if chan_text is not None:
                        await _mirror(app, text=chan_text)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def pinned_editor(app):
    """Every 300 s, create-pin-once the static command-reference message."""
    while True:
        try:
            await asyncio.to_thread(pinned_tick, app, app.state.telegram)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(300)
