import datetime as dt
from app.config import get_market_status, is_weekend_market


def test_is_weekend_market_boundaries():
    # Friday 21:59 UTC -> False (Gold open)
    fri_open = dt.datetime(2026, 10, 23, 21, 59, tzinfo=dt.timezone.utc)
    assert is_weekend_market(fri_open) is False

    # Friday 22:00 UTC -> True (Gold closes)
    fri_close = dt.datetime(2026, 10, 23, 22, 0, tzinfo=dt.timezone.utc)
    assert is_weekend_market(fri_close) is True

    # Saturday 12:00 UTC -> True
    sat = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)
    assert is_weekend_market(sat) is True

    # Sunday 21:59 UTC -> True
    sun_closed = dt.datetime(2026, 10, 25, 21, 59, tzinfo=dt.timezone.utc)
    assert is_weekend_market(sun_closed) is True

    # Sunday 22:00 UTC -> False (Gold reopens)
    sun_open = dt.datetime(2026, 10, 25, 22, 0, tzinfo=dt.timezone.utc)
    assert is_weekend_market(sun_open) is False

    # Wednesday 14:00 UTC -> False
    wed = dt.datetime(2026, 10, 28, 14, 0, tzinfo=dt.timezone.utc)
    assert is_weekend_market(wed) is False


def test_get_market_status():
    fri_night = dt.datetime(2026, 10, 23, 23, 0, tzinfo=dt.timezone.utc)
    wed_day = dt.datetime(2026, 10, 28, 14, 0, tzinfo=dt.timezone.utc)

    # Gold on weekend
    gold_weekend = get_market_status("XAUUSD", fri_night)
    assert gold_weekend["open"] is False
    assert gold_weekend["reason"] == "weekend_closed"
    assert gold_weekend["is_crypto"] is False
    assert "Sunday 22:00 UTC" in gold_weekend["reopens_at"]

    # Gold on weekday
    gold_weekday = get_market_status("XAUUSD", wed_day)
    assert gold_weekday["open"] is True
    assert gold_weekday["reason"] == "normal_trading"

    # Crypto (always 24/7)
    btc_weekend = get_market_status("BTCUSD", fri_night)
    assert btc_weekend["open"] is True
    assert btc_weekend["reason"] == "crypto_24_7"
    assert btc_weekend["is_crypto"] is True


def test_api_state_includes_weekend_and_market_status(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_weekend.db"))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        resp = client.get("/api/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_weekend" in data
        assert isinstance(data["is_weekend"], bool)
        assert "market_status" in data
        assert "XAUUSD" in data["market_status"]
        assert "BTCUSD" in data["market_status"]
        assert data["market_status"]["BTCUSD"]["open"] is True
