"""Trade-event reporting: turns a raw /trade-event POST from the EA into the
basket-leg lookup, the rendered chart, and the Telegram P/L message. Split
out of app/main.py (which keeps only the /trade-event and /screenshot route
handlers) so main.py stays FastAPI wiring only.

Pure-ish module: no FastAPI app import (that would be circular -- app.main
imports from here). Anything that would otherwise come from `app.state` is
passed in explicitly by the caller instead."""
import asyncio
from pathlib import Path

from app.asset_registry import format_asset_badge
from app.db import SignalDb
from app.models import Candle
from app.render import render_trade_chart
from app.sr_zones import sr_zones_from_db
from app.telegram import TelegramClient

_SCREENSHOT_RETENTION = 500

# The trade screenshot follows the ACTIVE LANE's timeframe (owner 2026-09-02:
# an M15-lane trade was rendered on M5 candles, so flip->entry looked 9 bars
# apart instead of 3, mismatching the owner's MT5 M15 chart). The EA always
# sends M5 candles (AiApi TradeTimeframe), so a lane trading a higher TF has
# its candles resampled up here. Map: strategy_id -> (bucket_seconds, trade
# EMA length) — default M5/55; the M15 lane resamples to 900 s and draws its
# EMA-50 like its live overlay. A new higher-TF lane adds one row.
_LANE_RENDER = {
    "iziGold15": (900, 50),
    "iziGold5": (300, 55),
    "izi15": (900, 50),
    "izi5": (300, 55),
    "iziBit15": (900, 50),
    "iziBit5": (300, 55),
}


def _resample(candles: list, bucket_s: int) -> list:
    """M5 Candle objects -> higher-TF Candle objects aligned to bucket_s
    boundaries (same rule as main._resample_m15; the trailing partial
    bucket is kept). Volume summed. Returns Candle objects so render's
    attribute access (.t/.o/.h/.l/.c) is unchanged."""
    out = []
    for c in candles:
        b = c.t - (c.t % bucket_s)
        if out and out[-1].t == b:
            p = out[-1]
            out[-1] = Candle(t=b, o=p.o, h=max(p.h, c.h), l=min(p.l, c.l),
                             c=c.c, v=(p.v or 0) + (c.v or 0))
        else:
            out.append(Candle(t=b, o=c.o, h=c.h, l=c.l, c=c.c, v=c.v))
    return out


def _basket_legs(db: SignalDb, trade_id: int) -> list:
    """Rows belonging to the current basket, in entry order: every
    'open'/'add' trade row after the last previous FINAL 'close' row (there
    is no dedicated basket/ticket column, so basket boundaries are inferred
    from 'close' rows). A non-final close (a single leg stopping out while
    the rest of the basket survives -- see TradeEventRequest.final) does NOT
    end the basket, so it is excluded from the boundary search: legs from
    before AND after such a row still belong to the same basket and are both
    returned once the basket's eventual final close arrives.

    For a "close" event `trade_id` is the just-inserted close row itself --
    it's excluded automatically since its own event isn't 'open'/'add'. For
    "open"/"add" events, `trade_id`'s row already satisfies id > last_close
    and event IN ('open','add'), so it's included as the newest leg.

    TWIN WARNING: `app/reports.py`'s `_group_baskets` implements this same
    basket-boundary rule independently (it groups a whole fetched window at
    once instead of walking backward from one row id, because the mini-app
    needs every basket in a range, not just the one around a fresh insert).
    Both MUST agree on which rows are legs of a basket, in what order --
    that agreement is pinned by
    `tests/test_basket_twins.py::test_basket_legs_and_group_baskets_agree_
    on_the_same_legs`. They deliberately return different SHAPES: this
    function's legs carry `sl`/`tp` (needed to backfill/redraw the chart
    render) and no `ts`/`htf_agree`/`ema200_agree`; `_group_baskets`' entries
    carry `ts`/`htf_agree`/`ema200_agree` (needed for the report) and no
    `sl`/`tp` (the mini-app query never selects them). If you change the boundary rule here, change
    it there too, and vice versa."""
    cur = db.conn.execute("SELECT symbol FROM trades WHERE id = ?", (trade_id,)).fetchone()
    sym = cur[0] if cur and cur[0] else None
    if sym:
        last_close = db.conn.execute(
            "SELECT MAX(id) FROM trades WHERE event='close' AND final=1 AND symbol = ? AND id < ?",
            (sym, trade_id)).fetchone()[0] or 0
        rows = db.conn.execute(
            "SELECT price, lots, event, sl, tp FROM trades WHERE symbol = ? AND id > ? AND event IN"
            " ('open','add') ORDER BY id ASC", (sym, last_close)).fetchall()
    else:
        last_close = db.conn.execute(
            "SELECT MAX(id) FROM trades WHERE event='close' AND final=1 AND id < ?",
            (trade_id,)).fetchone()[0] or 0
        rows = db.conn.execute(
            "SELECT price, lots, event, sl, tp FROM trades WHERE id > ? AND event IN"
            " ('open','add') ORDER BY id ASC", (last_close,)).fetchall()
    return [{"price": r[0], "lots": r[1], "event": r[2], "sl": r[3], "tp": r[4]}
            for r in rows]


async def _report_trade_event(ev, trade_id: int, legs: list, *,
                               last_candles, screenshot_dir: Path,
                               db: SignalDb, telegram: TelegramClient | None,
                               mirror, sr_cache: dict | None = None) -> None:
    """Render/photo/P&L message for a trade event, OFF the response path.
    Render/photo only for opens and FINAL closes -- 'add' legs are still
    recorded (so the eventual close chart can draw their A-lines via
    _basket_legs) but must not themselves trigger a render/Telegram photo,
    and a non-final close (a single leg stopping out mid-basket) is
    telemetry-only, not a basket-ending event worth a chart/P&L message.
    Fail-open: every failure is swallowed."""
    should_render = ev.event == "open" or (ev.event == "close" and ev.final)
    if should_render and last_candles:
        render_path = screenshot_dir / f"render_{trade_id}.png"
        try:
            trade_dict = ev.model_dump()
            trade_dict["legs"] = legs
            if ev.event == "close":
                # Real close events (broker-side SL/TP touches) carry
                # sl=0/tp=0 -- the EA has no per-position SL/TP snapshot to
                # resend at close time. Backfill from the basket's own
                # stored legs instead of relying on the EA: sl from the
                # basket's first ('open') leg (the original protective
                # stop), tp from the latest non-zero tp seen across the
                # basket's legs (a pyramided add can move the target).
                if not trade_dict.get("sl") and legs:
                    trade_dict["sl"] = legs[0]["sl"]
                if not trade_dict.get("tp"):
                    for leg in reversed(legs):
                        if leg.get("tp"):
                            trade_dict["tp"] = leg["tp"]
                            break
            # Resample to the active lane's timeframe so the chart matches
            # the lane the owner actually watches (see _LANE_RENDER).
            bucket_s, ema_len = _LANE_RENDER.get(ev.strategy_id, (0, 55))
            render_candles = _resample(last_candles, bucket_s) if bucket_s \
                else last_candles
            zones = []
            if sr_cache is not None:
                try:
                    zones = await asyncio.to_thread(sr_zones_from_db, db, sr_cache)
                except Exception:
                    zones = []
            ok = await asyncio.to_thread(
                render_trade_chart, render_candles, trade_dict,
                str(render_path), ema_len, zones)
            if ok:
                db.set_render(trade_id, str(render_path))
                await asyncio.to_thread(_prune_screenshots, screenshot_dir)
                # The PNG is kept ON DISK for the dashboard's trade list; it
                # is no longer sent to Telegram (owner request 2026-08-17 —
                # the live ticker + [📈 Live Chart] mini app cover the
                # visual, and the extra "render:" photos were noise).
        except Exception:
            pass
    # No open-time Telegram message here at all: the EA's own chart
    # screenshot (POST /screenshot, caption "open BUY 0.09@4399.17 — signal
    # BUY") already arrives with the EXIT button — that is the ONE chart the
    # owner wants per entry (2026-08-17). Anything sent here would be a
    # duplicate.
    if ev.event == "close" and ev.final and telegram is not None:
        sym = getattr(ev, "symbol", "XAUUSD")
        text = _pl_message(ev.profit, ev.direction, legs, ev.price, sym, getattr(ev, "reason", ""))
        try:
            await asyncio.to_thread(telegram.send_message, text)
        except Exception:
            pass
        await mirror(text=text)


def _format_exit_reason(reason: str) -> str:
    r = (reason or "").strip().lower()
    if "reversal" in r:
        return "Reversal Exit"
    if "stop-loss" in r or "sl" in r:
        return "Stop Loss Hit"
    if "profit target" in r or "tp" in r:
        return "Take Profit Hit"
    if "profit lock" in r or "trailing" in r:
        return "Trailing Stop"
    if "stagnation" in r:
        return "Stagnation Exit"
    if "pre-break" in r or "flatten" in r:
        return "Session Flatten"
    if "remote exit" in r or "exit" in r:
        return "Manual Close"
    return reason or "Exit"


def _pl_message(profit: float, direction: str = "", legs: list | None = None,
                exit_price: float = 0.0, symbol: str = "XAUUSD",
                reason: str = "") -> str:
    badge = format_asset_badge(symbol)
    if profit > 0:
        head = f"💰 {badge} Trade closed: +${profit:.2f} profit"
    elif profit < 0:
        head = f"🔻 {badge} Trade closed: -${abs(profit):.2f} loss"
    else:
        head = f"⚖️ {badge} Trade closed: breakeven"

    lines = [head]
    if legs and exit_price:
        tot = sum(l.get("lots", 0) for l in legs)
        if tot > 0:
            avg = sum(l["price"] * l.get("lots", 0) for l in legs) / tot
            lines.append(f"{direction} {avg:.2f} → {exit_price:.2f}")
    elif direction and exit_price:
        lines.append(f"{direction} {exit_price:.2f}")

    if reason:
        lines.append(f"Reason: {_format_exit_reason(reason)}")

    return "\n".join(lines)


def _trade_caption(event, direction, lots, price, reason, profit,
                   htf_agree: int = -1, ema200_agree: int = -1,
                   news_blackout: int = -1, rsi_agree: int = -1,
                   sr_agree: int = -1, sr_tier: str = "",
                   symbol: str = "XAUUSD", strategy_id: str = "",
                   sl: float = 0.0, tp: float = 0.0) -> str:
    badge = format_asset_badge(symbol)
    if event == "close":
        clean_reason = _format_exit_reason(reason)
        if profit > 0:
            head = f"💰 {badge} Closed: +${profit:.2f} profit"
        elif profit < 0:
            head = f"🔻 {badge} Closed: -${abs(profit):.2f} loss"
        else:
            head = f"⚖️ {badge} Closed: breakeven"
        return f"{head}\n{direction} {lots} @ {price:.2f}\nReason: {clean_reason}"

    # For open / add:
    dir_icon = "🟢" if (direction or "").upper() == "BUY" else "🔴"
    head = f"{dir_icon} {(direction or '').upper()} {lots} {badge} @ {price:.2f}"
    lines = [head]

    if sl > 0 and tp > 0:
        risk = abs(price - sl)
        reward = abs(tp - price)
        rr_str = f" (1:{reward / risk:.2f} RR)" if risk > 0 else ""
        lines.append(f"🛑 SL: {sl:.2f} | 🎯 TP: {tp:.2f}{rr_str}")
    elif sl > 0:
        lines.append(f"🛑 SL: {sl:.2f}")
    elif tp > 0:
        lines.append(f"🎯 TP: {tp:.2f}")

    if strategy_id:
        tf = "M15" if "15" in strategy_id else ("M5" if "5" in strategy_id else "")
        lines.append(f"⚡ Lane: {tf} ({strategy_id})" if tf else f"⚡ Strategy: {strategy_id}")

    # Key warnings only — no repetitive 'agrees' checklist
    if news_blackout == 1:
        lines.append("⚠️ News Blackout at entry")
    if sr_agree == 0:
        lines.append(f"⚠️ Opposing S/R Wall ({sr_tier or '?'})")
    if htf_agree == 0:
        lines.append("⚠️ M15 Disagrees")
    if ema200_agree == 0:
        lines.append("⚠️ E200 Disagrees")
    if rsi_agree == 0:
        lines.append("⚠️ RSI Disagrees")

    return "\n".join(lines)


def _prune_screenshots(dir_path: Path, keep: int = _SCREENSHOT_RETENTION) -> None:
    files = sorted(dir_path.glob("*.png"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for stale in files[keep:]:
        stale.unlink()
