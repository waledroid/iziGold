import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app.analysis import analyze_forecast
from app.config import settings
from app.db import SignalDb, profile_completion
from app.forecaster import get_forecaster
from app.models import (AnalyzeRequest, AnalyzeResponse, HeartbeatRequest,
                        HeartbeatResponse, ProposalResultRequest, TradeEventRequest)
from app.regime import classify_regime, last_atr
from app.render import render_trade_chart
from app.telegram import (EXIT_KB, PROPOSAL_KB, TelegramClient, format_proposal,
                          handle_command, pinned_tick, set_active_client)
from app.verdict import combine

_SCREENSHOT_RETENTION = 500


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
                message = upd.get("message") or {}
                text = message.get("text") or ""
                msg_chat_id = str(message.get("chat", {}).get("id"))
                if text.startswith("/") and msg_chat_id == chat_id:
                    reply = handle_command(text, app)
                    if reply is not None:
                        await asyncio.to_thread(app.state.telegram.send_message, reply)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def pinned_editor(app: FastAPI):
    """Every 60 s, create-pin-or-edit the pinned live-status message.
    Fail-open: any exception is swallowed and the loop just retries next
    tick. The (synchronous, blocking) TelegramClient calls happen inside
    `pinned_tick`, dispatched via asyncio.to_thread so they run off the
    event loop -- same reasoning as `telegram_poller` above."""
    while True:
        try:
            await asyncio.to_thread(pinned_tick, app, app.state.telegram)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(60)


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
            # send_alert (used by the sync /analyze endpoint) reaches whichever
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
    app.state.pending_switch = None
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

    # 1. expiry: does the active strategy still hold the pending stance?
    pending = db.pending_proposal()
    if pending is not None:
        stale = (
            (pending["kind"] == "entry" and (
                req.signal == "EXIT" or
                (req.signal in ("BUY", "SELL") and req.signal != pending["direction"])))
            or (pending["kind"] == "exit" and req.signal in ("BUY", "SELL"))
        )
        if stale:
            db.set_proposal_status(pending["id"], "expired")
            edit(pending, "⌛ expired (strategy stance changed)")
            pending = None

    # 2. new proposals: manual mode only, entry/exit signals only
    if req.signal not in ("BUY", "SELL", "EXIT"):
        return
    if db.exec_mode() != "manual":
        return
    kind = "exit" if req.signal == "EXIT" else "entry"
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


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    app.state.last_candles = req.candles
    app.state.recent_candles = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "candles": req.candles[-300:],
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


@app.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(hb: HeartbeatRequest):
    app.state.latest_heartbeat = (time.time(), hb)
    app.state.db.insert_heartbeat({**hb.model_dump(exclude={"positions"}),
                                   "open_count": len(hb.positions)})
    if app.state.pending_switch and hb.active_strategy == app.state.pending_switch:
        app.state.pending_switch = None
    cmd_row = app.state.db.pop_approved_command()
    command = None
    if cmd_row is not None:
        if cmd_row["kind"] == "entry":
            command = {"cmd": "execute", "proposal_id": cmd_row["id"],
                       "direction": cmd_row["direction"]}
        else:
            command = {"cmd": "close_all", "proposal_id": cmd_row["id"]}
    return HeartbeatResponse(
        switch_to=app.state.pending_switch,
        mode=app.state.db.exec_mode(),
        command=command
    )


@app.post("/proposal-result")
async def proposal_result(res: ProposalResultRequest):
    db = app.state.db
    row = db.get_proposal(res.proposal_id)
    if row is None:
        return {"ok": False}
    db.set_proposal_status(res.proposal_id, "executed" if res.ok else "blocked")
    tg = getattr(app.state, "telegram", None)
    if tg is not None and row["tg_message_id"] is not None:
        mark = "✅ executed" if res.ok else "🚫 blocked"
        try:
            await asyncio.to_thread(
                tg.edit_message, row["tg_message_id"],
                f"{'📥' if row['kind']=='entry' else '📤'} {row['direction']} "
                f"@ {row['price']} — {mark}: {res.detail}")
        except Exception:
            pass
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


@app.get("/ui/state")
def ui_state():
    latest = app.state.latest_heartbeat
    hb, age = None, None
    if latest is not None:
        age = round(time.time() - latest[0], 1)
        hb = latest[1].model_dump()
    return {"age_s": age, "heartbeat": hb,
            "pending_switch": app.state.pending_switch,
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
    return {"profile": row, "completion_pct": completion_pct}


@app.post("/ui/profile")
async def ui_profile_save(body: dict):
    body = dict(body) if isinstance(body, dict) else {}
    # Belt and braces: the onboarding page never sends a masked value back,
    # but guard here too so a masked telegram_bot_token (from the GET
    # response, e.g. round-tripped by a stale client) can never overwrite
    # the real stored token.
    if str(body.get("telegram_bot_token", "")).startswith("•"):
        body.pop("telegram_bot_token", None)
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


@app.post("/trade-event")
async def trade_event(ev: TradeEventRequest):
    trade_id = app.state.db.insert_trade(ev.model_dump())
    if ev.event in ("open", "close") and app.state.last_candles:
        render_path = app.state.screenshot_dir / f"render_{trade_id}.png"
        try:
            ok = await asyncio.to_thread(
                render_trade_chart, app.state.last_candles, ev.model_dump(),
                str(render_path))
            if ok:
                app.state.db.set_render(trade_id, str(render_path))
                await asyncio.to_thread(_prune_screenshots, app.state.screenshot_dir)
                if app.state.telegram is not None:
                    caption = f"render: {ev.reason}"
                    await asyncio.to_thread(
                        _send_render_photo, app.state.telegram, caption, render_path)
        except Exception:
            pass
    return {"id": trade_id}


def _send_render_photo(telegram: TelegramClient, caption: str, path: Path) -> None:
    telegram.send_photo(caption, path.read_bytes())


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
                await asyncio.to_thread(app.state.telegram.send_photo, caption, body)
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
