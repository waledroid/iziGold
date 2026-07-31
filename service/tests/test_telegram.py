from app import telegram as telegram_module
from app.config import Settings
from app.models import AnalyzeRequest, AnalyzeResponse
from app.telegram import TelegramClient, format_report, send_alert
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


def test_send_alert_uses_active_client_when_set():
    """Profile-only credentials never populate `settings`, so send_alert
    must reach the live client (set by _apply_telegram) directly rather
    than only building its own settings-based httpx call."""
    calls = []

    def transport(method, payload, files=None):
        calls.append((method, payload))
        return {"ok": True}

    client = TelegramClient("tok", "555", transport=transport)
    telegram_module.set_active_client(client)
    try:
        assert send_alert("hi", Settings(_env_file=None)) is True
        assert calls == [("sendMessage", {"chat_id": "555", "text": "hi"})]
    finally:
        telegram_module.set_active_client(None)


def test_send_alert_falls_back_to_settings_path_when_no_active_client():
    telegram_module.set_active_client(None)
    assert send_alert("hi", Settings(_env_file=None)) is False
