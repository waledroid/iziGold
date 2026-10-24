import asyncio
import datetime as dt
import json
import logging
import math
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import backtest_runner
from app.agent import AgentClient, AgentError, build_context
from app.analysis import analyze_forecast
from app.basket import derive_basket_band
from app.chart_cmd import merge_forming_bar
from app.config import settings
from app.db import SignalDb, profile_completion
from app.forecaster import get_forecaster
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    NotifyRequest,
    ProposalResultRequest,
    TradeEventRequest,
)
from app.regime import classify_regime, last_atr
from app.render import render_snapshot_chart
from app import chart_spec, overlays, preview
from app.sr_zones import sr_zones_from_db
from app.telegram import (
    BRAKE_RESET_KB,
    EXIT_KB,
    EXIT_NOW_KB,
    PROPOSAL_KB,
    TARGET_KB,
    TelegramClient,
    format_proposal,
    handle_callback,
    handle_channel_post,
    handle_command,
    pinned_tick,
    set_active_client,
)
from app.ticker import TickerState, load_ticker_state, ticker_tick
from app.trade_report import (
    _basket_legs,
    _prune_screenshots,
    _report_trade_event,
    _trade_caption,
)
from app.verdict import combine

# Sub-routers & Services
from app.agent_supervisor import agent_supervisor
from app.candle_buffer import CandleBufferManager
from app.routers.backtest import router as backtest_router
from app.routers.stream import router as stream_router
from app.routers.ea import router as ea_router, _CANDLE_WINDOW_CAP
from app.routers.ui import router as ui_router, _OVERLAY_BUILDERS
from app.tg.service import (
    pinned_editor,
    telegram_poller,
)

log = logging.getLogger("uvicorn.error")

_CHART_HB_FRESH_S = 60
_MANUAL_PDF = Path(__file__).resolve().parents[2] / "docs" / "izi_manual.pdf"


def _effective_telegram(app: FastAPI) -> tuple[str, str]:
    """Profile credentials when non-empty, else settings (.env) values."""
    profile = app.state.db.get_profile() or {}
    token = str(profile.get("telegram_bot_token") or "").strip()
    chat_id = str(profile.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        token = str(settings.telegram_bot_token or "").strip()
        chat_id = str(settings.telegram_chat_id or "").strip()
    return token, chat_id


async def _apply_telegram(app: FastAPI) -> None:
    """Single owner of the Telegram client/task lifecycle."""
    async with app.state.telegram_lock:
        try:
            for attr in ("telegram_task", "pinned_task"):
                task = getattr(app.state, attr, None)
                if task is not None:
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=3.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

            token, chat_id = _effective_telegram(app)
            app.state.telegram = TelegramClient(token, chat_id) if token and chat_id else None
            set_active_client(app.state.telegram)
            app.state.telegram_task = (
                asyncio.create_task(telegram_poller(app)) if app.state.telegram else None)
            app.state.pinned_task = (
                asyncio.create_task(pinned_editor(app)) if app.state.telegram else None)
        except Exception:
            log.exception("rebuilding the Telegram client/tasks failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.forecaster = get_forecaster(settings)
    app.state.db = SignalDb(settings.db_path)
    app.state.latest_heartbeat = None
    app.state.chart_subscribers = set()
    app.state.ticker = load_ticker_state(app, "XAUUSD")
    app.state.tickers = {"XAUUSD": app.state.ticker, "BTCUSD": load_ticker_state(app, "BTCUSD")}
    app.state.ticker_busy = False
    app.state.ticker_busy_symbols = set()
    app.state.ticker_task = None
    app.state.report_tasks = set()
    app.state.pending_switch = None
    app.state.pending_channel = None
    app.state.last_candles = None
    app.state.recent_candles = None
    app.state.candles_cache = None
    app.state.overlays_cache = {}
    app.state.sr_cache = {}
    app.state.candle_buffer = CandleBufferManager(window_cap=_CANDLE_WINDOW_CAP)
    app.state.candle_buffer.seed_from_db(app.state.db)
    app.state.candles_by_series = app.state.candle_buffer.series_dict
    for (sym, tf), c_list in app.state.candles_by_series.items():
        if app.state.recent_candles is None or sym == "XAUUSD":
            app.state.recent_candles = {"symbol": sym, "timeframe": tf, "candles": c_list}
            if sym == "XAUUSD":
                app.state.last_candles = c_list
    try:
        app.state.db.conn.execute(
            "UPDATE backtest_runs SET status='failed',"
            " error='interrupted by service restart' WHERE status='running'")
        app.state.db.conn.commit()
    except Exception:
        log.exception("reconciling interrupted backtest runs failed")
    app.state.screenshot_dir = Path(settings.screenshot_dir)
    app.state.screenshot_dir.mkdir(parents=True, exist_ok=True)
    app.state.telegram = None
    app.state.telegram_task = None
    app.state.pinned_task = None
    app.state.telegram_lock = asyncio.Lock()
    app.state.loop = asyncio.get_running_loop()
    app.state.agent = AgentClient(settings) if settings.agent_enabled else None
    app.state.agent_grade_cache = {}
    app.state.agent_inflight = set()
    app.state.agent_lock = threading.Lock()
    app.state.agent_task = None
    if settings.agent_enabled:
        app.state.agent_task = asyncio.create_task(agent_supervisor(app))
    await _apply_telegram(app)
    yield
    if app.state.agent_task is not None:
        app.state.agent_task.cancel()
        try:
            await asyncio.wait_for(app.state.agent_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    for task in (app.state.telegram_task, app.state.pinned_task):
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
    set_active_client(None)


app = FastAPI(title="XAU Assistant AI Service", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"),
          name="static")
app.include_router(stream_router)
app.include_router(backtest_router)
app.include_router(ea_router)
app.include_router(ui_router)

# Backward compatibility exports for existing tests and external callers
from app.routers.ea import analyze
from app.routers.ui import ui_candles, ui_overlays
from app.tg.service import (
    _MANUAL_PDF,
    mirror_command_text as _mirror_command_text,
    send_chart_snapshot as _send_chart_snapshot,
    send_manual as _send_manual,
)
