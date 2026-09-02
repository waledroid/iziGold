from typing import Literal

from pydantic import BaseModel, Field


class Candle(BaseModel):
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class ShadowSignal(BaseModel):
    strategy_id: str
    signal: Literal["NONE", "BUY", "SELL", "EXIT"]


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str
    signal: Literal["NONE", "BUY", "SELL", "EXIT"]
    candles: list[Candle] = Field(min_length=50)
    strategy_id: str = "unknown"
    shadows: list[ShadowSignal] = []
    # Per-bar spread telemetry (points) sampled by the EA every timer tick
    # over the CLOSED bar; 0.0 = unknown / no samples (old EAs omit these).
    spread_min: float = 0.0
    spread_avg: float = 0.0
    spread_max: float = 0.0
    # True when the EA is inside a high-impact news blackout window (owner
    # 2026-09-01: the blackout no longer auto-trades OR silently blocks —
    # a BUY/SELL arriving with this set raises a Telegram proposal so the
    # owner decides the trade themselves; auto resumes when the window ends).
    news_blackout: bool = False


class AnalyzeResponse(BaseModel):
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    regime: Literal["trend", "range", "high_volatility"]
    verdict: Literal["confirm", "neutral", "conflict"]
    mode: Literal["grading", "veto"]
    ai_available: bool


class Position(BaseModel):
    ticket: int
    direction: Literal["BUY", "SELL"]
    lots: float
    open_price: float
    sl: float
    profit: float


class NewsEvent(BaseModel):
    """One upcoming high-importance USD calendar event, as the EA sees it.
    `in_s` is RELATIVE seconds until the event (computed EA-side from
    TimeCurrent), deliberately not an absolute timestamp: the MT5 server
    clock and the service clock disagree by hours, and relative time is
    immune to that."""
    in_s: int
    name: str = ""


class HeartbeatRequest(BaseModel):
    equity: float
    balance: float
    floating_pl: float
    positions: list[Position] = []
    kill_switch: bool = False
    hwm: float = 0.0
    exposure_min: int = 0
    window_open: bool = False
    spread_points: float = 0.0
    active_strategy: str = "unknown"
    algo_trading: bool = True
    # Forming (bar 0) OHLC carried by the EA every heartbeat so /chart can
    # render in real time without waiting for the bar to close. 0 = absent
    # (old EA, or CopyRates failure — fail-open).
    bar_t: int = 0
    bar_o: float = 0.0
    bar_h: float = 0.0
    bar_l: float = 0.0
    bar_c: float = 0.0
    entry_mode: str = "adr"
    # Brake awareness (2026-08-18): today's realized loss as % of the daily
    # loss brake threshold (0-100+, measured from the reset base when the
    # owner reset the brake today) and whether such a reset is in effect.
    # Defaults keep old EA payloads valid (fail-open).
    daily_loss_pct: float = 0.0
    brake_reset: bool = False
    # Upcoming high-impact USD events (next 24 h) + the EA's blackout
    # radius, for /news and the pre-blackout heads-up. Defaults keep old
    # EA payloads valid (fail-open).
    news: list[NewsEvent] = []
    news_blackout_min: int = 30


class HeartbeatResponse(BaseModel):
    switch_to: str | None = None
    mode: Literal["auto", "manual"] = "manual"
    entry_mode: Literal["adr", "fixed"] = "adr"
    # Higher-timeframe agreement module: the timeframe it ENFORCES on, or
    # "off" to check and report without blocking. The EA obeys this over its
    # own input, so the module can be toggled from Telegram at runtime.
    htf_enforce: str = "off"
    # EMA-200 (own-timeframe) agreement module, same idea, simpler shape --
    # no timeframe choice, just "off" (report only) or "on" (enforce).
    ema200_enforce: str = "off"
    command: dict | None = None


class TradeEventRequest(BaseModel):
    event: Literal["open", "add", "close"]
    strategy_id: str = "unknown"
    direction: Literal["BUY", "SELL"]
    lots: float
    price: float
    sl: float = 0.0
    tp: float = 0.0
    reason: str = ""
    ticket: int = 0
    profit: float = 0.0
    final: bool = True
    entry_mode: str = ""
    # Higher-timeframe agreement at entry, as the EA judged it:
    # 1 = agreed, 0 = disagreed, -1 = unknown (older EA builds).
    htf_agree: int = -1
    # EMA-200 (own-timeframe) agreement at entry, same shape.
    ema200_agree: int = -1
    # News blackout at entry (owner 2026-09-01: blackout is a WARNING, not a
    # block): 1 = entered inside a high-impact blackout window, 0 = clear,
    # -1 = unknown (older EA builds / non-open events).
    news_blackout: int = -1
    # RSI(14, lane timeframe) verdict at the confirm bar (owner 2026-09-02;
    # sweep: M15+RSI-70 better in both halves). Report-only: 1 = agreed
    # (BUY with RSI<70 / SELL with RSI>30), 0 = disagreed, -1 = unknown.
    rsi_agree: int = -1


class ProposalResultRequest(BaseModel):
    proposal_id: int
    ok: bool
    detail: str = ""


class NotifyRequest(BaseModel):
    text: str = Field(max_length=500)
    # FIXED-mode target alert: attach an EXIT button to this notice (only
    # honored while a position is actually open; the button reuses the
    # existing exitnow close machinery).
    exit_button: bool = False
    # Generalized button selector (2026-08-18): "" (none), "exit" (same as
    # exit_button=True) or "reset_brake" (owner-only [Reset brake for today]
    # → brakereset: callback). exit_button stays for old EA builds.
    button: str = ""
