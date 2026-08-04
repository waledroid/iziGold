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


class HeartbeatResponse(BaseModel):
    switch_to: str | None = None
    mode: Literal["auto", "manual"] = "manual"
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


class ProposalResultRequest(BaseModel):
    proposal_id: int
    ok: bool
    detail: str = ""


class NotifyRequest(BaseModel):
    text: str = Field(max_length=500)
