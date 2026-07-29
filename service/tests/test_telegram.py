from app.config import Settings
from app.models import AnalyzeRequest, AnalyzeResponse
from app.telegram import format_report, send_alert
from tests.fixtures import trend_candles


def test_format_contains_essentials():
    req = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="BUY",
                         candles=trend_candles(50))
    resp = AnalyzeResponse(direction="bullish", confidence=0.82, regime="trend",
                           verdict="confirm", mode="grading", ai_available=True)
    text = format_report(req, resp)
    for token in ("XAUUSD", "BUY", "82%", "trend", "confirm"):
        assert token in text


def test_format_ai_unavailable():
    req = AnalyzeRequest(symbol="XAUUSD", timeframe="M15", signal="SELL",
                         candles=trend_candles(50))
    resp = AnalyzeResponse(direction="neutral", confidence=0.0, regime="range",
                           verdict="neutral", mode="grading", ai_available=False)
    assert "AI unavailable" in format_report(req, resp)


def test_send_noop_without_token():
    assert send_alert("hi", Settings(_env_file=None)) is False
