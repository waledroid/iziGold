from app.indicators import ema, halftrend
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
