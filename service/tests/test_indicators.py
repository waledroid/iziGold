from app.indicators import bollinger, ema, halftrend
from tests.fixtures import trend_candles


def test_ema_of_constant_series_is_constant():
    closes = [100.0] * 30
    result = ema(closes, 9)
    settled = [v for v in result if v is not None]
    assert len(settled) == 30 - 9 + 1
    assert all(abs(v - 100.0) < 1e-9 for v in settled)


def test_ema_warmup_prefix_is_none():
    closes = [float(i) for i in range(20)]
    result = ema(closes, 9)
    assert result[:8] == [None] * 8
    assert result[8] is not None


def test_ema_handles_short_input_without_raising():
    assert ema([], 9) == []
    assert ema([1.0, 2.0], 9) == [None, None]


def test_ema_handles_bad_period_without_raising():
    assert ema([1.0, 2.0, 3.0], 0) == [None, None, None]
    assert ema([1.0, 2.0, 3.0], -5) == [None, None, None]


def test_halftrend_on_rising_data_is_mostly_up_and_below_closes():
    candles = trend_candles(200)
    result = halftrend(candles, amplitude=4)

    settled = [(i, v) for i, v in enumerate(result) if v is not None]
    assert len(settled) > 150

    trends = [trend for _, (line, trend) in settled]
    up_fraction = trends.count(0) / len(trends)
    assert up_fraction > 0.9  # steadily rising data should stay in the up state

    for i, (line, trend) in settled:
        assert line <= candles[i].c + 1e-9


def test_halftrend_handles_short_input_without_raising():
    assert halftrend([], amplitude=4) == []
    short = trend_candles(3)
    result = halftrend(short, amplitude=4)
    assert len(result) == 3
    assert all(v is None for v in result)


def test_halftrend_handles_bad_amplitude_without_raising():
    candles = trend_candles(20)
    result = halftrend(candles, amplitude=0)
    assert result == [None] * 20


def test_bollinger_of_constant_series_has_upper_equal_mid_equal_lower():
    closes = [100.0] * 30
    upper, mid, lower = bollinger(closes, period=20, dev=2.0)
    settled = [(u, m, l) for u, m, l in zip(upper, mid, lower) if m is not None]
    assert len(settled) == 30 - 20 + 1
    assert all(abs(u - 100.0) < 1e-9 and abs(l - 100.0) < 1e-9 and abs(m - 100.0) < 1e-9
               for u, m, l in settled)


def test_bollinger_warmup_prefix_is_none():
    closes = [float(i) for i in range(25)]
    upper, mid, lower = bollinger(closes, period=20, dev=2.0)
    assert upper[:19] == [None] * 19 and mid[:19] == [None] * 19 and lower[:19] == [None] * 19
    assert upper[19] is not None and mid[19] is not None and lower[19] is not None


def test_bollinger_handles_short_input_without_raising():
    assert bollinger([], period=20) == ([], [], [])
    upper, mid, lower = bollinger([1.0, 2.0], period=20)
    assert upper == [None, None] and mid == [None, None] and lower == [None, None]


def test_bollinger_handles_bad_period_without_raising():
    upper, mid, lower = bollinger([1.0, 2.0, 3.0], period=0)
    assert upper == [None] * 3 and mid == [None] * 3 and lower == [None] * 3
