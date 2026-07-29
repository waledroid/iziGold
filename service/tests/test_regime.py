from app.regime import classify_regime, last_atr
from tests.fixtures import range_candles, spike_candles, trend_candles


def test_trend():
    assert classify_regime(trend_candles()) == "trend"


def test_range():
    assert classify_regime(range_candles()) == "range"


def test_high_volatility():
    assert classify_regime(spike_candles()) == "high_volatility"


def test_last_atr_positive():
    assert last_atr(trend_candles()) > 0
