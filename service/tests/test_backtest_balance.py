"""Below ~$4,000 the 1% risk rule stops being obeyed: sizing clamps to the
0.01 minimum lot and over-risks rather than skipping the trade. Measured over
12 months of M5: 51% of entries clamp at $1,200, 89% at $500."""
import json

import pytest

from tests.test_backtest_golden import BARS, _load_bt


def test_below_minimum_balance_refuses_to_run():
    bt = _load_bt()
    with pytest.raises(SystemExit):
        bt.validate_balance(300.0)


def test_small_balance_warns_but_runs():
    bt = _load_bt()
    msg = bt.validate_balance(1200.0)
    assert msg is not None and "0.01" in msg


def test_healthy_balance_is_silent():
    bt = _load_bt()
    assert bt.validate_balance(10000.0) is None


def test_sizing_stats_are_recorded_after_a_run():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.run(candles, 10000.0, False)
    s = bt.run.sizing
    assert s["entries"] > 0
    assert 0.0 <= s["clamp_pct"] <= 100.0
    assert s["risk_median"] > 0.0


def test_a_small_account_clamps_far_more_than_a_large_one():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.run(candles, 800.0, False)
    small = bt.run.sizing["clamp_pct"]
    bt.run(candles, 25000.0, False)
    large = bt.run.sizing["clamp_pct"]
    assert small > large + 20.0, (
        f"expected a large clamp gap, got {small:.1f}% vs {large:.1f}%")
