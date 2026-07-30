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
