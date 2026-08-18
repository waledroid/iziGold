import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app.analysis import analyze_forecast
from app.chart_cmd import merge_forming_bar
from app.config import settings
from app.db import SignalDb, profile_completion
from app.forecaster import get_forecaster
from app.indicators import bollinger, ema, halftrend
from app.models import (AnalyzeRequest, AnalyzeResponse, HeartbeatRequest,
                        HeartbeatResponse, NotifyRequest, ProposalResultRequest,
                        TradeEventRequest)
from app.regime import classify_regime, last_atr
from app.render import render_snapshot_chart, render_trade_chart
from app.telegram import (BRAKE_RESET_DONE_TEXT, BRAKE_RESET_KB, EXIT_KB,
                          EXIT_NOW_KB, PROPOSAL_KB, TelegramClient,
                          format_proposal, handle_callback, handle_channel_post,
                          handle_command, pinned_tick, set_active_client)
from app.ticker import TickerState, load_ticker_state, ticker_tick
from app.verdict import combine

_SCREENSHOT_RETENTION = 500

# /ui/candles and /ui/overlays serve from an accumulator, not the latest
# /analyze payload alone -- the EA only resends its own rolling window each
# post, so without merging, the dashboard chart could never scroll past what
# fit in a single post. Capped so memory/response size stay bounded.
_CANDLE_WINDOW_CAP = 2000


def _merge_candle_window(existing: list, incoming: list) -> list:
    """Merge `incoming` candles into `existing` keyed by `.t`: overlapping
    timestamps are replaced by the incoming (fresher) candle, new timestamps
    append. Result is sorted by `t` and capped to the most recent
    _CANDLE_WINDOW_CAP entries."""
    merged = {c.t: c for c in existing}
    for c in incoming:
        merged[c.t] = c
    return [merged[t] for t in sorted(merged)][-_CANDLE_WINDOW_CAP:]


# An approved-but-not-yet-dispatched proposal older than this is expired
# rather than delivered on the next heartbeat -- a stale "yes" tapped
# minutes ago (EA offline, terminal closed, etc.) should not suddenly fire.
PROPOSAL_APPROVAL_TTL_S = 120

# A dispatched command the EA never confirmed (no /proposal-result) within
# this window is reconciled to 'blocked' so it stops showing as in-flight.
# Approximation: the proposals table has no dispatched_ts column, and
# pop_approved_command() intentionally doesn't touch decided_ts (only the
# 'approved'/'skipped'/'expired'/'blocked' transitions do -- see
# set_proposal_status), so decided_ts still holds the *approval* time, which
# predates the actual dispatch. Reusing it as a lower bound on dispatch time
# means this window is deliberately generous (180s, vs. the 120s approval
# TTL above) to avoid mistaking a slow-but-fine dispatch for a lost one.
COMMAND_RESULT_TTL_S = 180


async def telegram_poller(app: FastAPI):
    """Long-poll Telegram for commands and reply. Fail-open: any exception
    just backs off and continues.

    Every TelegramClient call is dispatched via asyncio.to_thread so the
    (synchronous, up-to-30s-blocking) HTTP call runs off the event loop —
    otherwise it would stall the entire FastAPI app for the duration of
    each long-poll, and task.cancel() would be unable to interrupt an
    in-flight call since the block isn't at an await point."""
    offset = 0
    # Filter on the ACTIVE client's chat id, not settings/.env directly --
    # credentials (and thus chat_id) can come from the profile instead, and
    # _apply_telegram restarts this task on every apply, so the value read
    # here at task start always reflects whichever client is current.
    chat_id = str(getattr(app.state.telegram, "chat_id", "") or settings.telegram_chat_id)
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
                text = message.get("text") or ""
                msg_chat_id = str(message.get("chat", {}).get("id"))
                if text.startswith("/") and msg_chat_id == chat_id:
                    if text.strip().split()[0].lower() == "/chart":
                        try:
                            await _send_chart_snapshot(app)
                        except Exception:
                            pass  # fail-open: /chart must never kill the poller
                        continue
                    reply = handle_command(text, app)
                    if isinstance(reply, tuple):
                        await asyncio.to_thread(app.state.telegram.send_message,
                                                reply[0], reply[1])
                    elif reply is not None:
                        await asyncio.to_thread(app.state.telegram.send_message, reply)
                    chan_text = _mirror_command_text(text, app)
                    if chan_text is not None:
                        await _mirror(app, text=chan_text)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def pinned_editor(app: FastAPI):
    """Every 300 s, create-pin-once the static command-reference message,
    self-healing if it was deleted/unpinned. The interval is relaxed from
    the old 60s live-status cadence because the content is now static --
    `pinned_tick` itself no-ops (no Telegram call) once the pinned message
    exists and its stored version is current, so this loop mostly just
    checks in. Fail-open: any exception is swallowed and the loop just
    retries next tick. The (synchronous, blocking) TelegramClient calls
    happen inside `pinned_tick`, dispatched via asyncio.to_thread so they
    run off the event loop -- same reasoning as `telegram_poller` above."""
    while True:
        try:
            await asyncio.to_thread(pinned_tick, app, app.state.telegram)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(300)


def _linked_channel(app) -> str | None:
    try:
        return app.state.db.get_kv("channel_id") or None
    except Exception:
        return None


async def _mirror(app, text: str | None = None,
                  photo_bytes: bytes | None = None, caption: str = "") -> None:
    """Mirror one already-sent owner message to the linked channel.
    Owner-first ordering is the caller's job (call this after the owner
    send). Fail-open: never raises, no-op when unlinked/no client."""
    cid = _linked_channel(app)
    tg = getattr(app.state, "telegram", None)
    if cid is None or tg is None:
        return
    try:
        if photo_bytes is not None:
            await asyncio.to_thread(tg.send_photo_to, cid, caption, photo_bytes)
        elif text:
            await asyncio.to_thread(tg.send_message_to, cid, text)
    except Exception:
        pass


_CHART_HB_FRESH_S = 60


async def _send_chart_snapshot(app) -> None:
    """/chart: render closed candles + the heartbeat's forming bar and send
    as a photo (owner first, channel mirror after). Every failure path
    replies with text instead; never raises into the poller.

    When a mini-app public URL is configured, /chart instead opens the live
    mini app (owner: text + web_app button; channel: plain URL text line,
    no markup) rather than rendering a static PNG."""
    tg = app.state.telegram
    if settings.miniapp_public_url:
        kb = {"inline_keyboard": [[
            {"text": "📈 Live Chart",
             "web_app": {"url": settings.miniapp_public_url}}
        ]]}
        await asyncio.to_thread(tg.send_message, "📈 Live chart:", kb)
        chan_link = settings.miniapp_direct_link or settings.miniapp_public_url
        await _mirror(app, text=f"👤 /chart\n📈 Live chart: {chan_link}")
        return
    rc = app.state.recent_candles
    if not rc or not rc.get("candles"):
        await asyncio.to_thread(
            tg.send_message, "no candles yet — waiting for the first bar post")
        return
    latest = app.state.latest_heartbeat
    hb = latest[1] if latest is not None else None
    stale = (latest is None or (time.time() - latest[0]) > _CHART_HB_FRESH_S
             or not getattr(hb, "bar_t", 0))
    candles = rc["candles"]
    if not stale:
        candles = merge_forming_bar(candles, hb)
    out = str(app.state.screenshot_dir / "chart_cmd.png")
    positions = hb.positions if hb is not None else []
    ok = await asyncio.to_thread(render_snapshot_chart, candles, out, positions)
    if not ok:
        await asyncio.to_thread(tg.send_message, "chart render failed")
        return
    caption = (f"📈 {rc['symbol']} {rc['timeframe']} — {candles[-1].c:g} "
               f"(as of {time.strftime('%H:%M:%S')})")
    if stale:
        caption += " · closed bars only"
    png = await asyncio.to_thread(Path(out).read_bytes)
    await asyncio.to_thread(tg.send_photo, caption, png)
    await _mirror(app, photo_bytes=png, caption=f"👤 /chart\n{caption}")


def _mirror_command_text(text: str, app) -> str | None:
    """Channel rendition of an owner command: '👤 /cmd' + the redacted
    reply. None when the command is unknown or owner-only."""
    if text.split()[0].lower() == "/channel":
        return None  # link management is owner-only housekeeping
    reply = handle_command(text, app, redacted=True)
    if reply is None:
        return None
    body = reply[0] if isinstance(reply, tuple) else reply
    return f"👤 {text}\n\n{body}"


def _effective_telegram(app: FastAPI) -> tuple[str, str]:
    """Profile credentials when non-empty, else settings (.env) values, both
    stripped. Profile is only consulted once the row exists (get_profile can
    be None before any /ui/profile POST -- Save or Skip -- has happened)."""
    profile = app.state.db.get_profile() or {}
    token = str(profile.get("telegram_bot_token") or "").strip()
    chat_id = str(profile.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        token = str(settings.telegram_bot_token or "").strip()
        chat_id = str(settings.telegram_chat_id or "").strip()
    return token, chat_id


async def _apply_telegram(app: FastAPI) -> None:
    """Single owner of the Telegram client/task lifecycle: cancels whatever
    tasks currently exist, then rebuilds app.state.telegram (and its two
    background tasks) from the effective credentials. Called at lifespan
    startup and live from POST /ui/profile. Never raises -- fail-open.

    Serialized on app.state.telegram_lock: two overlapping callers (e.g. two
    rapid POST /ui/profile requests) must not both read the old task, both
    decide it needs replacing, and each spawn a fresh pair -- that would
    leak a poller/pinned-editor task that nothing ever cancels again."""
    async with app.state.telegram_lock:
        try:
            for attr in ("telegram_task", "pinned_task"):
                task = getattr(app.state, attr, None)
                if task is not None:
                    task.cancel()
                    try:
                        # See the lifespan shutdown comment: the underlying
                        # TelegramClient call runs in a worker thread which
                        # task.cancel() cannot interrupt mid-call, so bound the
                        # wait instead of hanging on an in-flight long-poll.
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

            token, chat_id = _effective_telegram(app)
            app.state.telegram = TelegramClient(token, chat_id) if token and chat_id else None
            # Keep app.telegram's module-level active client in sync so
            # send_alert (still unit-tested directly, but no longer wired
            # into the /analyze path -- that alert diet now flows through
            # maybe_propose's per-proposal messages) reaches whichever
            # client is live -- including profile-only credentials that never
            # appear in `settings`.
            set_active_client(app.state.telegram)
            app.state.telegram_task = (
                asyncio.create_task(telegram_poller(app)) if app.state.telegram else None)
            app.state.pinned_task = (
                asyncio.create_task(pinned_editor(app)) if app.state.telegram else None)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    app.state.latest_heartbeat = None
    app.state.ticker = load_ticker_state(app)   # resume a mid-trade LIVE message across restarts
    app.state.ticker_busy = False
    app.state.ticker_task = None
    app.state.report_tasks = set()
    app.state.pending_switch = None
    app.state.pending_channel = None
    app.state.last_candles = None
    app.state.recent_candles = None
    app.state.screenshot_dir = Path(settings.screenshot_dir)
    app.state.screenshot_dir.mkdir(parents=True, exist_ok=True)
    app.state.telegram = None
    app.state.telegram_task = None
    app.state.pinned_task = None
    app.state.telegram_lock = asyncio.Lock()
    await _apply_telegram(app)
    yield
    for task in (app.state.telegram_task, app.state.pinned_task):
        if task is not None:
            task.cancel()
            try:
                # The underlying TelegramClient call runs in a worker thread
                # (asyncio.to_thread) which task.cancel() cannot interrupt
                # mid-call, so an in-flight ~30s long-poll would otherwise
                # make a bare `await task` hang shutdown for up to 30s.
                # Bound the wait instead and move on regardless.
                await asyncio.wait_for(task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
    set_active_client(None)


app = FastAPI(title="XAU Assistant AI Service", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok",
            "forecaster": type(app.state.forecaster).__name__,
            "db": settings.db_path}


def maybe_propose(req: AnalyzeRequest, resp: AnalyzeResponse) -> None:
    """Proposal lifecycle + alert diet. Never raises (telegram fail-open;
    db errors are logged by db layer conventions) -- the caller wraps this
    in try/except as an extra belt-and-braces guard."""
    db = app.state.db
    tg = getattr(app.state, "telegram", None)

    def edit(pid_row, suffix):
        if tg is None or pid_row["tg_message_id"] is None:
            return
        tg.edit_message(pid_row["tg_message_id"],
                        f"{'📥' if pid_row['kind']=='entry' else '📤'} "
                        f"{pid_row['direction']} @ {pid_row['price']} — {suffix}")

    def is_stale(row) -> bool:
        return (
            (row["kind"] == "entry" and (
                req.signal == "EXIT" or
                (req.signal in ("BUY", "SELL") and req.signal != row["direction"])))
            or (row["kind"] == "exit" and req.signal in ("BUY", "SELL"))
        )

    # 1. expiry: does the active strategy still hold the pending stance?
    pending = db.pending_proposal()
    if pending is not None and is_stale(pending):
        # Guarded: a concurrent Telegram tap (or the /heartbeat TTL sweep)
        # may have already moved this row off 'pending' between our read
        # and this write -- e.g. approved right as the stance broke. If so,
        # skip the ⌛ edit (whatever landed the other transition owns the
        # message now) but still drop `pending` below so the create-new-
        # proposal step isn't blocked by a row that is no longer pending.
        if db.set_proposal_status(pending["id"], "expired", expected="pending"):
            edit(pending, "⌛ expired (strategy stance changed)")
        pending = None

    # 1b. same stance-expiry check for an APPROVED-but-not-yet-dispatched
    # proposal (I1a): a Telegram "Take trade" tap doesn't freeze the
    # strategy's stance -- it can still flip before the next heartbeat
    # dispatches the command.
    approved = db.pending_proposal(status="approved")
    if approved is not None and is_stale(approved):
        if db.set_proposal_status(approved["id"], "expired", expected="approved"):
            edit(approved, "⌛ expired before execution (stance changed)")

    # 2. new proposals: manual mode only, entry/exit signals only
    if req.signal not in ("BUY", "SELL", "EXIT"):
        return
    if db.exec_mode() != "manual":
        return
    kind = "exit" if req.signal == "EXIT" else "entry"
    if kind == "exit":
        # M1: only propose an exit when the latest heartbeat actually shows
        # an open position -- otherwise there's nothing for "Exit now" to
        # close, and close_all would be a silent no-op.
        latest = app.state.latest_heartbeat
        if latest is None or not latest[1].positions:
            return
    price = req.candles[-1].c if req.candles else 0.0
    if pending is not None and pending["kind"] == kind and \
       (kind == "exit" or pending["direction"] == req.signal):
        return  # one pending proposal per stance
    if kind == "entry":
        direction = req.signal
    else:
        # An exit proposal's direction is informational only (the EA's
        # close_all closes the whole basket regardless of what we display
        # here). Best-effort source: the newest proposal row with
        # kind='entry' and status='executed'; fall back to "BUY".
        last = db.last_executed_entry()
        direction = last["direction"] if last else "BUY"
    pid = db.create_proposal(kind, direction, req.strategy_id, price, None)
    if tg is not None:
        markup = (PROPOSAL_KB(pid) if kind == "entry" else EXIT_KB(pid))
        sent = tg.send_message(format_proposal(kind, direction, price, resp),
                               reply_markup=markup)
        if sent and sent.get("result", {}).get("message_id"):
            db.set_proposal_message(pid, sent["result"]["message_id"])
        cid = _linked_channel(app)
        if cid is not None:
            try:
                tg.send_message_to(
                    cid, format_proposal(kind, direction, price, resp))
            except Exception:
                pass


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    app.state.last_candles = req.candles
    rc = app.state.recent_candles
    if rc is not None and rc["symbol"] == req.symbol and rc["timeframe"] == req.timeframe:
        candles = _merge_candle_window(rc["candles"], req.candles)
    else:
        # First post, or the chart attached to a different symbol/timeframe
        # -- start the accumulator over from this payload rather than
        # merging incompatible series.
        candles = _merge_candle_window([], req.candles)
    app.state.recent_candles = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "candles": candles,
    }
    regime = classify_regime(req.candles)
    atr_value = last_atr(req.candles)
    closes = [x.c for x in req.candles]
    try:
        fc = app.state.forecaster.forecast(closes, settings.horizon)
        direction, confidence = analyze_forecast(fc, closes[-1], atr_value)
        ai_available = True
    except Exception:
        direction, confidence, ai_available = "neutral", 0.0, False

    resp = AnalyzeResponse(
        direction=direction, confidence=confidence, regime=regime,
        verdict=combine(req.signal, direction, confidence, settings.confirm_threshold),
        mode=settings.mode, ai_available=ai_available)

    app.state.db.resolve_outcomes(req.candles, settings.horizon)
    # Spread telemetry (ea-scope spec §3): archive the closed bar's spread
    # aggregates. An all-zero triple means "no samples / old EA" -- skip it
    # rather than store a meaningless row.
    if req.spread_min > 0 or req.spread_avg > 0 or req.spread_max > 0:
        app.state.db.upsert_spread(
            bar_time=req.candles[-1].t, spread_min=req.spread_min,
            spread_avg=req.spread_avg, spread_max=req.spread_max)
    if req.signal != "NONE":
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=req.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, verdict=resp.verdict, mode=settings.mode,
            ai_available=ai_available, strategy_id=req.strategy_id, is_active=True,
            timeframe=req.timeframe)
    try:
        maybe_propose(req, resp)
    except Exception:
        pass
    for shadow in req.shadows:
        if shadow.signal == "NONE":
            continue
        app.state.db.insert_signal(
            bar_time=req.candles[-1].t, symbol=req.symbol, signal=shadow.signal,
            price=closes[-1], direction=direction, confidence=confidence,
            regime=regime, mode=settings.mode, ai_available=ai_available,
            verdict=combine(shadow.signal, direction, confidence,
                            settings.confirm_threshold),
            strategy_id=shadow.strategy_id, is_active=False,
            timeframe=req.timeframe)
    return resp


def _sweep_stale_proposals(app: FastAPI) -> None:
    """Runs at the top of every /heartbeat, before pop_approved_command.
    Two independent TTL reconciliations (I1b, I4), both fail-open and both
    guarded (expected=...) so a proposal that transitioned concurrently
    (e.g. the EA's /proposal-result landing in the same instant) is left
    alone rather than double-edited or double-transitioned."""
    db = app.state.db
    tg = getattr(app.state, "telegram", None)

    def edit(row, suffix):
        if tg is None or row["tg_message_id"] is None:
            return
        if row["kind"] == "reset_brake":
            tg.edit_message(row["tg_message_id"], f"🔓 brake reset — {suffix}")
            return
        tg.edit_message(row["tg_message_id"],
                        f"{'📥' if row['kind']=='entry' else '📤'} "
                        f"{row['direction']} @ {row['price']} — {suffix}")

    # I1b: approved-but-undispatched proposals older than the approval TTL
    # never reach the EA -- expire them instead of dispatching a stale "yes".
    for row in db.stale_approved(PROPOSAL_APPROVAL_TTL_S):
        if db.set_proposal_status(row["id"], "expired", expected="approved"):
            edit(row, "⌛ expired before execution (approval timed out)")

    # I4: dispatched commands the EA never confirmed are reconciled to
    # 'blocked' so they stop looking in-flight forever.
    for row in db.stale_dispatched(COMMAND_RESULT_TTL_S):
        if db.set_proposal_status(row["id"], "blocked", expected="dispatched"):
            edit(row, "🚫 no confirmation from EA — check the terminal")


@app.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(hb: HeartbeatRequest):
    previous = app.state.latest_heartbeat
    prev_algo_trading = previous[1].algo_trading if previous is not None else True
    app.state.latest_heartbeat = (time.time(), hb)
    app.state.db.insert_heartbeat({**hb.model_dump(exclude={"positions"}),
                                   "open_count": len(hb.positions)})
    # Live ticker: fire-and-forget so three potential Telegram calls (10 s
    # timeout each) can never delay this response — the EA's commands ride
    # on it. ticker_busy collapses overlapping runs to at most one.
    if not app.state.ticker_busy and getattr(app.state, "telegram", None) is not None:
        app.state.ticker_busy = True
        hb_now = time.time()

        async def _ticker_bg(hb=hb, hb_now=hb_now, previous=previous):
            try:
                await asyncio.to_thread(ticker_tick, app, hb, hb_now, previous)
            except Exception:
                pass
            finally:
                app.state.ticker_busy = False

        app.state.ticker_task = asyncio.create_task(_ticker_bg())
    if prev_algo_trading != hb.algo_trading:
        tg = getattr(app.state, "telegram", None)
        if tg is not None:
            text = ("⚠️ MT5 Algo Trading is OFF — trades cannot execute until you enable it"
                    if not hb.algo_trading else "✅ Algo Trading back ON")
            try:
                await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
            await _mirror(app, text=text)
    if app.state.pending_switch and hb.active_strategy == app.state.pending_switch:
        app.state.pending_switch = None
    try:
        _sweep_stale_proposals(app)
    except Exception:
        pass  # fail-open: a sweep bug must never block command delivery
    cmd_row = app.state.db.pop_approved_command()
    command = None
    if cmd_row is not None:
        if cmd_row["kind"] == "entry":
            command = {"cmd": "execute", "proposal_id": cmd_row["id"],
                       "direction": cmd_row["direction"]}
        elif cmd_row["kind"] == "reset_brake":
            command = {"cmd": "reset_brake", "proposal_id": cmd_row["id"]}
        else:
            command = {"cmd": "close_all", "proposal_id": cmd_row["id"]}
    return HeartbeatResponse(
        switch_to=app.state.pending_switch,
        mode=app.state.db.exec_mode(),
        entry_mode=app.state.db.entry_mode(),
        command=command
    )


@app.post("/proposal-result")
async def proposal_result(res: ProposalResultRequest):
    db = app.state.db
    row = db.get_proposal(res.proposal_id)
    if row is None:
        return {"ok": False}
    # Guarded on the row still being 'dispatched': if the /heartbeat TTL
    # sweep already reconciled it to 'blocked' (I4) before this callback
    # arrived, don't let a late result silently overwrite that outcome.
    status = "executed" if res.ok else "blocked"
    if not db.set_proposal_status(res.proposal_id, status, expected="dispatched"):
        return {"ok": False}
    tg = getattr(app.state, "telegram", None)
    if row["kind"] == "reset_brake":
        # Brake reset (2026-08-18): edit the tapped notice into the
        # confirmation (or a failure), messageless -> fresh message.
        text = BRAKE_RESET_DONE_TEXT if res.ok else f"🚫 brake reset failed: {res.detail}"
        if tg is not None:
            try:
                if row["tg_message_id"] is not None:
                    await asyncio.to_thread(tg.edit_message, row["tg_message_id"], text)
                else:
                    await asyncio.to_thread(tg.send_message, text)
            except Exception:
                pass
            await _mirror(app, text=text)
        return {"ok": True}
    if tg is not None and row["tg_message_id"] is not None:
        mark = "✅ executed" if res.ok else "🚫 blocked"
        try:
            await asyncio.to_thread(
                tg.edit_message, row["tg_message_id"],
                f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
                f"@ {row['price']} — {mark}: {res.detail}")
        except Exception:
            pass
        await _mirror(app, text=(
            f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
            f"@ {row['price']} — {mark}: {res.detail}"))
    elif tg is not None and not res.ok:
        # Messageless quick-exits (dashboard close-all / exitnow button) have
        # no proposal message to edit — a failure must still reach the user.
        try:
            await asyncio.to_thread(
                tg.send_message, f"🚫 close failed: {res.detail}")
        except Exception:
            pass
        await _mirror(app, text=f"🚫 close failed: {res.detail}")
    return {"ok": True}


@app.post("/notify")
async def notify(req: NotifyRequest):
    """Fire-and-forget Telegram notice for events with no proposal/db row
    of their own (e.g. an AUTO entry the EA couldn't execute). Sends `text`
    verbatim; never raises (telegram fail-open); no db writes."""
    text = req.text.strip()
    if not text:
        return {"ok": False}
    tg = getattr(app.state, "telegram", None)
    if tg is not None:
        # FIXED-mode target alert: attach the EXIT button only while a
        # position is actually open — a late-arriving alert on a flat
        # account degrades to a plain notice. The channel mirror stays
        # text-only (no controls in the channel, ever).
        markup = None
        button = req.button or ("exit" if req.exit_button else "")
        if button == "exit":
            latest = app.state.latest_heartbeat
            if latest is not None and latest[1].positions:
                markup = EXIT_NOW_KB(0)
        elif button == "reset_brake":
            # Daily-loss-brake 70% / TRIPPED notice: owner-only [Reset
            # brake for today] (callback brakereset:) — channel stays text-only.
            markup = BRAKE_RESET_KB()
        try:
            await asyncio.to_thread(tg.send_message, text, markup)
        except Exception:
            pass
        await _mirror(app, text=text)
    return {"ok": True}


_STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@app.post("/ui/switch")
def ui_switch(body: dict):
    sid = str(body.get("strategy_id", "")).strip()
    if not sid:
        app.state.pending_switch = None
        return {"pending": None}
    if not _STRATEGY_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid strategy_id")
    app.state.pending_switch = sid
    return {"pending": sid}


def _edit_proposal_message(row, suffix):
    """Best-effort: keep the proposal's Telegram message in sync with a
    decision made on the dashboard. Fail-open — never raises."""
    tg = getattr(app.state, "telegram", None)
    if tg is None or row.get("tg_message_id") is None:
        return
    try:
        tg.edit_message(row["tg_message_id"],
                        f"{'📥' if row['kind'] == 'entry' else '📤'} "
                        f"{row['direction']} @ {row['price']} — {suffix}")
    except Exception:
        pass


@app.post("/ui/mode")
def ui_mode(body: dict):
    mode = str(body.get("mode", "")).strip().lower()
    if mode not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="mode must be auto|manual")
    app.state.db.set_exec_mode(mode)
    return {"mode": mode}


@app.post("/ui/proposal/{pid}")
def ui_proposal_decide(pid: int, body: dict):
    action = str(body.get("action", "")).strip().lower()
    if action not in ("take", "skip"):
        raise HTTPException(status_code=400, detail="action must be take|skip")
    db = app.state.db
    row = db.get_proposal(pid)
    if row is None:
        raise HTTPException(status_code=404, detail="no such proposal")
    status = "approved" if action == "take" else "skipped"
    # Same guarded transition the Telegram buttons use: if a concurrent tap
    # or the expiry sweep already decided this row, report what won instead
    # of overwriting it.
    if not db.set_proposal_status(pid, status, expected="pending"):
        return {"ok": False, "status": db.get_proposal(pid)["status"]}
    mark = "🟢 approved (dashboard)" if action == "take" else "🔴 skipped (dashboard)"
    _edit_proposal_message(row, mark)
    return {"ok": True, "status": status}


@app.post("/ui/close-all")
def ui_close_all():
    db = app.state.db
    for st in ("pending", "approved", "dispatched"):
        if db.pending_proposal(kind="exit", status=st) is not None:
            raise HTTPException(status_code=409, detail=f"close already {st}")
    last = db.last_executed_entry()
    direction = last["direction"] if last else "BUY"
    latest = app.state.latest_heartbeat
    strategy_id = latest[1].active_strategy if latest else "dashboard"
    rc = app.state.recent_candles
    price = rc["candles"][-1].c if rc else 0.0
    pid = db.create_proposal("exit", direction, strategy_id, price, None)
    db.set_proposal_status(pid, "approved", expected="pending")
    return {"ok": True, "proposal_id": pid}


@app.get("/ui/state")
def ui_state():
    latest = app.state.latest_heartbeat
    hb, age = None, None
    if latest is not None:
        age = round(time.time() - latest[0], 1)
        hb = latest[1].model_dump()
    proposal = None
    for st in ("pending", "approved", "dispatched"):
        proposal = app.state.db.pending_proposal(status=st)
        if proposal is not None:
            break
    return {"age_s": age, "heartbeat": hb,
            "pending_switch": app.state.pending_switch,
            "mode": app.state.db.exec_mode(),
            "proposal": proposal,
            "stats": app.state.db.stats()}


@app.get("/ui/equity")
def ui_equity():
    return {"series": app.state.db.equity_series()}


@app.get("/ui/stats")
def ui_stats():
    return app.state.db.stats()


@app.get("/ui/signals")
def ui_signals(limit: int = 50):
    return {"signals": app.state.db.recent_signals(limit)}


@app.get("/ui/candles")
def ui_candles():
    rc = app.state.recent_candles
    if not rc:
        return {"symbol": "", "timeframe": "", "candles": []}
    return {"symbol": rc["symbol"], "timeframe": rc["timeframe"],
            "candles": [c.model_dump() for c in rc["candles"]]}


@app.get("/ui/overlays")
def ui_overlays(strategy: str = ""):
    """Chart-overlay series for the dashboard's price chart, aligned 1:1
    (same length, None for warmup) with the candle list /ui/candles
    returns from the same app.state.recent_candles snapshot. Unknown
    strategy or no candles yet -> {} (nothing to draw)."""
    rc = app.state.recent_candles
    if not rc or not rc["candles"]:
        return {}
    candles = rc["candles"]
    closes = [c.c for c in candles]
    if strategy == "halftrend_ema_v1":
        ht = halftrend(candles, amplitude=4)
        return {
            "halftrend": [list(v) if v is not None else None for v in ht],
            "ema55": ema(closes, 55),
            "ema9": ema(closes, 9),
            "ema21": ema(closes, 21),
            "ema200": ema(closes, 200),
        }
    if strategy == "boll_stochrsi_v1":
        upper, mid, lower = bollinger(closes, period=20, dev=2.0)
        return {"bb_upper": upper, "bb_mid": mid, "bb_lower": lower}
    return {}


def _mask_secret(value: str) -> str:
    """Never return the full secret: "****" + last 4 chars (or all dots if
    shorter than that)."""
    tail = value[-4:] if len(value) >= 4 else value
    return "••••" + tail


@app.get("/ui/profile")
def ui_profile_get():
    row = app.state.db.get_profile()
    completion_pct = profile_completion(row)
    if row is not None:
        row = dict(row)
        token = row.get("telegram_bot_token")
        if token:
            row["telegram_bot_token"] = _mask_secret(str(token))
        ngrok_token = row.get("ngrok_authtoken")
        if ngrok_token:
            row["ngrok_authtoken"] = _mask_secret(str(ngrok_token))
    return {"profile": row, "completion_pct": completion_pct}


@app.post("/ui/profile")
async def ui_profile_save(body: dict):
    body = dict(body) if isinstance(body, dict) else {}
    # Belt and braces: the onboarding page never sends a masked value back,
    # but guard here too so a masked telegram_bot_token/ngrok_authtoken
    # (from the GET response, e.g. round-tripped by a stale client) can
    # never overwrite the real stored secret.
    if str(body.get("telegram_bot_token", "")).startswith("•"):
        body.pop("telegram_bot_token", None)
    if str(body.get("ngrok_authtoken", "")).startswith("•"):
        body.pop("ngrok_authtoken", None)
    row = app.state.db.save_profile(body)
    if "telegram_bot_token" in body or "telegram_chat_id" in body:
        try:
            await _apply_telegram(app)
        except Exception:
            pass
    return {"profile": row, "completion_pct": profile_completion(row)}


@app.get("/ui")
def ui_page():
    try:
        has_profile = app.state.db.get_profile() is not None
    except Exception:
        has_profile = True  # fail open to the dashboard, not a redirect/500
    if not has_profile:
        return RedirectResponse("/ui/onboarding", status_code=307)
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html",
                        media_type="text/html")


@app.get("/ui/onboarding")
def ui_onboarding():
    return FileResponse(Path(__file__).parent / "static" / "onboarding.html",
                        media_type="text/html")


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
    and event IN ('open','add'), so it's included as the newest leg."""
    last_close = db.conn.execute(
        "SELECT MAX(id) FROM trades WHERE event='close' AND final=1 AND id < ?",
        (trade_id,)).fetchone()[0] or 0
    rows = db.conn.execute(
        "SELECT price, lots, event, sl, tp FROM trades WHERE id > ? AND event IN"
        " ('open','add') ORDER BY id ASC", (last_close,)).fetchall()
    return [{"price": r[0], "lots": r[1], "event": r[2], "sl": r[3], "tp": r[4]}
            for r in rows]


@app.post("/trade-event")
async def trade_event(ev: TradeEventRequest):
    # Idempotent receiver for the EA's at-least-once close delivery: deal
    # tickets are unique, so a close re-delivered with the same nonzero
    # ticket (the EA timed out before seeing our response and retried —
    # the reconciler does this every 60 s) must get the ORIGINAL row id
    # back, with no re-insert and no re-report.
    if ev.event == "close" and ev.ticket:
        row = app.state.db.conn.execute(
            "SELECT MIN(id) FROM trades WHERE event='close' AND ticket=?",
            (ev.ticket,)).fetchone()
        if row is not None and row[0] is not None:
            return {"id": row[0]}
    trade_id = app.state.db.insert_trade(ev.model_dump())
    # legs computed before responding: _basket_legs is a quick db read, and
    # the background report needs the basket bounded as of THIS row.
    try:
        legs = _basket_legs(app.state.db, trade_id)
    except Exception:
        legs = []
    # Respond immediately — the render + Telegram + channel-mirror work for
    # a FINAL close takes multiple seconds, far beyond the EA's 1 s
    # WebRequest timeout. Holding the response made the EA treat every
    # slow final close as FAILED and re-deliver it forever (the 2026-08-13
    # reconcile spam). The report runs as a background task instead; task
    # refs are held on app.state so they can't be garbage-collected.
    task = asyncio.create_task(_report_trade_event(ev, trade_id, legs))
    app.state.report_tasks.add(task)
    task.add_done_callback(app.state.report_tasks.discard)
    return {"id": trade_id}


async def _report_trade_event(ev: TradeEventRequest, trade_id: int,
                              legs: list) -> None:
    """Render/photo/P&L message for a trade event, OFF the response path.
    Render/photo only for opens and FINAL closes -- 'add' legs are still
    recorded (so the eventual close chart can draw their A-lines via
    _basket_legs) but must not themselves trigger a render/Telegram photo,
    and a non-final close (a single leg stopping out mid-basket) is
    telemetry-only, not a basket-ending event worth a chart/P&L message.
    Fail-open: every failure is swallowed."""
    should_render = ev.event == "open" or (ev.event == "close" and ev.final)
    if should_render and app.state.last_candles:
        render_path = app.state.screenshot_dir / f"render_{trade_id}.png"
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
                render_trade_chart, app.state.last_candles, trade_dict,
                str(render_path))
            if ok:
                app.state.db.set_render(trade_id, str(render_path))
                await asyncio.to_thread(_prune_screenshots, app.state.screenshot_dir)
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
    if ev.event == "close" and ev.final and app.state.telegram is not None:
        try:
            await asyncio.to_thread(
                app.state.telegram.send_message,
                _pl_message(ev.profit, ev.direction, legs, ev.price))
        except Exception:
            pass
        await _mirror(app, text=_pl_message(ev.profit, ev.direction,
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


def _send_render_photo(telegram: TelegramClient, caption: str, path: Path,
                       reply_markup: dict | None = None) -> None:
    telegram.send_photo(caption, path.read_bytes(), reply_markup)


@app.post("/screenshot")
async def screenshot(event: int, request: Request):
    body = await request.body()
    dir_path = app.state.screenshot_dir
    file_path = dir_path / f"{event}.png"
    # Dispatch the blocking file write and directory scan/prune via
    # asyncio.to_thread so they run off the event loop instead of stalling
    # every other request while they hit disk.
    await asyncio.to_thread(file_path.write_bytes, body)
    app.state.db.set_screenshot(event, str(file_path))
    await asyncio.to_thread(_prune_screenshots, dir_path)
    if app.state.telegram is not None:
        try:
            row = app.state.db.conn.execute(
                "SELECT event, direction, lots, price, reason, profit"
                " FROM trades WHERE id=?", (event,)).fetchone()
            if row is not None:
                caption = _trade_caption(*row)
                markup = EXIT_NOW_KB(event) if row[0] == "open" else None
                await asyncio.to_thread(
                    app.state.telegram.send_photo, caption, body, markup)
        except Exception:
            pass
    return {"saved": str(file_path)}


def _trade_caption(event, direction, lots, price, reason, profit) -> str:
    caption = f"{event} {direction} {lots}@{price} — {reason}"
    if event == "close":
        caption += f"; P/L {profit}"
    return caption


def _prune_screenshots(dir_path: Path, keep: int = _SCREENSHOT_RETENTION) -> None:
    files = sorted(dir_path.glob("*.png"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    for stale in files[keep:]:
        stale.unlink()


@app.get("/ui/trades")
def ui_trades(limit: int = 50):
    return {"trades": app.state.db.recent_trades(limit)}


@app.get("/ui/screenshot/{trade_id}")
def ui_screenshot(trade_id: int):
    row = app.state.db.conn.execute(
        "SELECT screenshot_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "screenshot not found"})
    return FileResponse(path, media_type="image/png")


@app.get("/ui/render/{trade_id}")
def ui_render(trade_id: int):
    row = app.state.db.conn.execute(
        "SELECT render_path FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or not row[0]:
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    path = Path(row[0])
    if not path.exists():
        return JSONResponse(status_code=404, content={"detail": "render not found"})
    return FileResponse(path, media_type="image/png")
