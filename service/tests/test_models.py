import pytest
from pydantic import ValidationError

from app.models import AnalyzeRequest, AnalyzeResponse, Candle


def mk_candles(n=50):
    return [Candle(t=1700000000 + i * 900, o=1.0, h=2.0, l=0.5, c=1.5, v=10) for i in range(n)]


def test_valid_request():
    r = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="BUY", candles=mk_candles())
    assert r.signal == "BUY"


def test_rejects_bad_signal():
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="HOLD", candles=mk_candles())


def test_rejects_short_history():
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="NONE", candles=mk_candles(10))


def test_response_bounds():
    with pytest.raises(ValidationError):
        AnalyzeResponse(direction="bullish", confidence=1.5, regime="trend",
                        verdict="confirm", mode="grading", ai_available=True)
