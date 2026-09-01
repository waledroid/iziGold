"""Trade-event reporting: turns a raw /trade-event POST from the EA into the
basket-leg lookup, the rendered chart, and the Telegram P/L message. Split
out of app/main.py (which keeps only the /trade-event and /screenshot route
handlers) so main.py stays FastAPI wiring only.

Pure-ish module: no FastAPI app import (that would be circular -- app.main
imports from here). Anything that would otherwise come from `app.state` is
passed in explicitly by the caller instead."""
import asyncio
from pathlib import Path

from app.db import SignalDb
from app.render import render_trade_chart
from app.telegram import TelegramClient

_SCREENSHOT_RETENTION = 500


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
                               mirror) -> None:
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
            ok = await asyncio.to_thread(
                render_trade_chart, last_candles, trade_dict,
                str(render_path))
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
        try:
            await asyncio.to_thread(
                telegram.send_message,
                _pl_message(ev.profit, ev.direction, legs, ev.price))
        except Exception:
            pass
        await mirror(text=_pl_message(ev.profit, ev.direction,
                                      legs, ev.price))


def _pl_message(profit: float, direction: str = "", legs: list | None = None,
                exit_price: float = 0.0) -> str:
    if profit > 0:
        head = f"💰 Trade closed: +${profit:.2f} profit"
    elif profit < 0:
        head = f"🔻 Trade closed: -${abs(profit):.2f} loss"
    else:
        head = "⚖️ Trade closed: breakeven"
    # lot-weighted average entry across the basket's open/add legs, so
    # pyramided trades report the entry that actually determined the P/L
    if legs and exit_price:
        tot = sum(l.get("lots", 0) for l in legs)
        if tot > 0:
            avg = sum(l["price"] * l.get("lots", 0) for l in legs) / tot
            head += f" ({direction} {avg:.2f} → {exit_price:.2f})"
    return head




def _trade_caption(event, direction, lots, price, reason, profit,
                   htf_agree: int = -1, ema200_agree: int = -1,
                   news_blackout: int = -1) -> str:
    caption = f"{event} {direction} {lots}@{price} — {reason}"
    if event == "close":
        caption += f"; P/L {profit}"
    else:
        if htf_agree in (0, 1):
            # The M15 verdict is evaluated on EVERY entry, in every session,
            # and reported here even when the tape was trending and it was
            # not allowed to block (owner 2026-08-21). "no" on a live entry
            # therefore means: taken in a trend, against M15.
            caption += f"\nM15: {'agrees ✅' if htf_agree == 1 else 'DISAGREES ⚠️'}"
        if ema200_agree in (0, 1):
            # Same "always evaluated" rule as M15 above (owner 2026-08-22):
            # reported on every entry, both strategies, even when the
            # enforcement toggle is off and it could not have blocked.
            caption += (f"\nE200: {'agrees ✅' if ema200_agree == 1 else 'DISAGREES ⚠️'}")
        if news_blackout == 1:
            # Only worth a line when it actually applies (owner 2026-09-01):
            # this entry happened INSIDE a high-impact blackout window —
            # either owner-approved via the blackout proposal, or manual.
            caption += "\nNEWS BLACKOUT at entry ⚠️"
    return caption


def _prune_screenshots(dir_path: Path, keep: int = _SCREENSHOT_RETENTION) -> None:
    files = sorted(dir_path.glob("*.png"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for stale in files[keep:]:
        stale.unlink()
