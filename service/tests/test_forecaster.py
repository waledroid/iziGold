import pytest

from app.config import Settings
from app.forecaster import FakeForecaster, get_forecaster


def test_fake_shape_and_order():
    fc = FakeForecaster().forecast([1.0 * i for i in range(100)], horizon=16)
    assert len(fc.q50) == 16
    assert all(a <= b <= c for a, b, c in zip(fc.q10, fc.q50, fc.q90))


def test_fake_follows_trend():
    closes = [3000.0 + 2.0 * i for i in range(100)]
    fc = FakeForecaster().forecast(closes, 16)
    assert fc.q50[-1] > closes[-1]


def test_factory_fake():
    s = Settings(_env_file=None, forecaster="fake")
    assert isinstance(get_forecaster(s), FakeForecaster)


def test_factory_timemoe_not_implemented():
    s = Settings(_env_file=None, forecaster="timemoe")
    with pytest.raises(NotImplementedError):
        get_forecaster(s)


@pytest.mark.slow
def test_chronos_real():
    pytest.importorskip("chronos")
    s = Settings(_env_file=None, forecaster="chronos")
    fc = get_forecaster(s).forecast([3000.0 + i for i in range(200)], 16)
    assert len(fc.q50) == 16
