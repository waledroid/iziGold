from app.analysis import analyze_forecast
from app.forecaster import QuantileForecast


def _fc(move, band):
    q50 = [3000.0 + move]
    return QuantileForecast(q10=[q50[0] - band], q50=q50, q90=[q50[0] + band])


def test_deadband_neutral():
    assert analyze_forecast(_fc(0.1, 1.0), 3000.0, atr_value=3.0) == ("neutral", 0.0)


def test_bullish_tight_band_high_conf():
    d, c = analyze_forecast(_fc(6.0, 0.5), 3000.0, 3.0)
    assert d == "bullish" and c > 0.9


def test_bearish():
    d, _ = analyze_forecast(_fc(-6.0, 0.5), 3000.0, 3.0)
    assert d == "bearish"


def test_wide_band_low_conf():
    _, c = analyze_forecast(_fc(6.0, 20.0), 3000.0, 3.0)
    assert c < 0.4
