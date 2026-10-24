from datetime import datetime, timezone
import pytest
from app.strategy_router import DynamicStrategyRouter
from app.models import Candle


def _make_candles(n=50, base=4200.0, trend=0.0, expanding=False):
    candles = []
    for i in range(n):
        p = base + i * trend
        span = (2.0 + (i * 0.2)) if expanding else 2.0
        candles.append(Candle(t=1700000000 + i * 300, o=p, h=p + span, l=p - span, c=p + 0.5, v=1000.0))
    return candles


def test_basket_open_locks_current_strategy():
    router = DynamicStrategyRouter()
    candles = _make_candles()
    # 09:30 Paris time is London open (which normally picks iziGold5)
    dt_london = datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc)
    strat, reason = router.evaluate_optimal_strategy(
        candles=candles,
        current_active="iziGold15",
        open_count=2,  # Basket is open
        dt=dt_london,
        force=True
    )
    assert strat == "iziGold15"
    assert "basket open" in reason


def test_session_routing_asia_vs_london():
    router = DynamicStrategyRouter()
    # Flat candles (no trend/ADX) -> router protects capital by holding iziGold15
    flat_candles = _make_candles(trend=0.0)
    dt_london = datetime(2026, 10, 12, 7, 15, tzinfo=timezone.utc) # 09:15 Paris
    strat_flat, reason = router.evaluate_optimal_strategy(
        candles=flat_candles,
        current_active="iziGold15",
        open_count=0,
        dt=dt_london,
        force=True
    )
    assert strat_flat == "iziGold15"
    assert "holding iziGold15" in reason

    # Strong trending expansion candles (ADX > 25, expanding ATR) -> promotes to iziGold5
    trending_candles = _make_candles(trend=1.5, expanding=True)
    strat_trend, reason = router.evaluate_optimal_strategy(
        candles=trending_candles,
        current_active="iziGold15",
        open_count=0,
        dt=dt_london,
        force=True
    )
    assert strat_trend == "iziGold5"
    assert ("high-velocity expansion" in reason or "volatility breakout spike" in reason)


def test_dwell_time_prevents_flapping():
    router = DynamicStrategyRouter()
    trending_candles = _make_candles(trend=1.5, expanding=True)

    dt_london = datetime(2026, 10, 12, 7, 15, tzinfo=timezone.utc)
    # Switch to iziGold5 during high-velocity London expansion
    strat1, _ = router.evaluate_optimal_strategy(
        candles=trending_candles,
        current_active="iziGold15",
        open_count=0,
        dt=dt_london,
        force=False
    )
    assert strat1 == "iziGold5"

    # Immediate next check with condition that would want iziGold15 (e.g., flat candles during Asia)
    flat_candles = _make_candles(trend=0.0)
    dt_asia = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)
    strat2, reason = router.evaluate_optimal_strategy(
        candles=flat_candles,
        current_active="iziGold5",
        open_count=0,
        dt=dt_asia,
        force=False
    )
    # Dwell lock prevents immediate switch back
    assert strat2 == "iziGold5"
    assert "dwell lock active" in reason

