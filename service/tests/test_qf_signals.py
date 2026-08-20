"""QuickFlip setups are pure geometry: they depend on candles only, never on
the account. That is what lets the lane be precomputed and then executed
inside the existing balance-aware loop."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _sigs():
    bt = _load_bt()
    return bt, bt.qf_signals(json.loads(BARS.read_text()))


def test_defaults_match_the_spec():
    bt = _load_bt()
    assert (bt.QF_HOUR, bt.QF_MINUTE) == (13, 30)
    assert bt.QF_ATR_PCT == 5.0
    assert bt.QF_WINDOW_MIN == 90
    assert bt.QF_RISK_PCT == 0.25


def test_signals_are_pure_of_the_account():
    """Called twice, the same candles must give identical setups."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    assert bt.qf_signals(candles) == bt.qf_signals(candles)


def test_stop_sits_beyond_entry_and_target_on_the_other_side():
    _bt, sigs = _sigs()
    for s in sigs:
        if s["dir"] == "SELL":
            assert s["stop"] > s["entry"] and s["tp"] < s["entry"]
        else:
            assert s["stop"] < s["entry"] and s["tp"] > s["entry"]


def test_entry_index_points_at_the_entry_bar():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    for s in bt.qf_signals(candles):
        assert candles[s["i"]]["t"] == s["entry_t"]


def test_expiry_is_within_the_window_of_the_box():
    _bt, sigs = _sigs()
    for s in sigs:
        assert s["expire_t"] > s["entry_t"]


def test_one_setup_per_server_day():
    _bt, sigs = _sigs()
    days = [s["entry_t"] // 86400 for s in sigs]
    assert len(days) == len(set(days))


def test_threshold_filters_setups_out():
    """A 90% ATR threshold must leave far fewer setups than a 0% one --
    proof the qualifier is actually applied."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.QF_ATR_PCT = 0.0
    loose = len(bt.qf_signals(candles))
    bt.QF_ATR_PCT = 90.0
    strict = len(bt.qf_signals(candles))
    assert strict < loose


def test_atr_boundary_does_not_leak_the_last_days_close():
    """Regression for the wraparound bug in the brief's qf_daily_atr(): with
    the off-by-one bug, keys[j - 1] hits j == 0 on the first eligible day and
    Python wraps to keys[-1] -- the LAST server day in the whole dataset --
    so the first computed ATR silently depends on the final candle's close.
    Mutating ONLY the last candle's close must leave the FIRST computed ATR
    unchanged. A bare `> 0` assertion would not catch this: the leaked value
    is wrong but still positive.
    """
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    atr_before = bt.qf_daily_atr(candles)
    first_day_before = sorted(atr_before)[0]
    value_before = atr_before[first_day_before]

    mutated = [dict(x) for x in candles]
    mutated[-1] = dict(mutated[-1])
    mutated[-1]["c"] = mutated[-1]["c"] + 10_000.0

    atr_after = bt.qf_daily_atr(mutated)
    first_day_after = sorted(atr_after)[0]
    value_after = atr_after[first_day_after]

    assert first_day_after == first_day_before
    assert value_after == value_before
