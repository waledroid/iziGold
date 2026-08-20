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


# --- same-bar exit precedence -------------------------------------------
# One M5 bar's range can cover BOTH the stop and the target, and OHLC cannot
# say which price came first. The replay always books the loss. Nothing
# pinned that before: the frozen fixture's only two QuickFlip trades exit
# "qf expired" and "qf target", so reversing the precedence to target-first
# -- the classic backtest-inflation bug -- left every golden green while the
# 365-day QuickFlip net moved +354.56 -> +427.66 (+21%).

def _pos(direction, entry, stop, tp, expire_t=10 ** 12):
    return {"dir": direction, "entry": entry, "stop": stop, "tp": tp,
            "expire_t": expire_t}


def test_stop_beats_target_when_one_bar_covers_both_short():
    bt = _load_bt()
    # sold the sweep at 2000: stop 2005 above, target 1990 below. This bar
    # trades through both.
    pos = _pos("SELL", entry=2000.0, stop=2005.0, tp=1990.0)
    bar = {"t": 0, "o": 2000.0, "h": 2006.0, "l": 1989.0, "c": 1995.0}
    assert bt.qf_resolve(pos, bar) == (2005.0, "qf stop")


def test_stop_beats_target_when_one_bar_covers_both_long():
    bt = _load_bt()
    pos = _pos("BUY", entry=2000.0, stop=1995.0, tp=2010.0)
    bar = {"t": 0, "o": 2000.0, "h": 2011.0, "l": 1994.0, "c": 2005.0}
    assert bt.qf_resolve(pos, bar) == (1995.0, "qf stop")


def test_the_stop_does_not_win_when_it_was_never_touched():
    """Guard against the pin passing for the wrong reason: a resolver that
    always answered "qf stop" would satisfy the two tests above."""
    bt = _load_bt()
    bt_short = bt.qf_resolve(_pos("SELL", 2000.0, 2005.0, 1990.0),
                             {"t": 0, "o": 2000.0, "h": 2001.0,
                              "l": 1989.0, "c": 1991.0})
    assert bt_short == (1990.0, "qf target")
    bt_long = bt.qf_resolve(_pos("BUY", 2000.0, 1995.0, 2010.0),
                            {"t": 0, "o": 2000.0, "h": 2011.0,
                             "l": 1999.0, "c": 2009.0})
    assert bt_long == (2010.0, "qf target")


def test_expiry_never_pre_empts_a_stop_on_the_same_bar():
    """The window closing does not turn a losing trade into a close-price
    exit: stop first, expiry only if neither level was touched."""
    bt = _load_bt()
    pos = _pos("SELL", entry=2000.0, stop=2005.0, tp=1990.0, expire_t=0)
    bar = {"t": 0, "o": 2000.0, "h": 2006.0, "l": 1998.0, "c": 2002.0}
    assert bt.qf_resolve(pos, bar) == (2005.0, "qf stop")
    quiet = {"t": 0, "o": 2000.0, "h": 2001.0, "l": 1999.0, "c": 2000.5}
    assert bt.qf_resolve(pos, quiet) == (2000.5, "qf expired")


def test_an_untouched_position_before_expiry_resolves_to_nothing():
    bt = _load_bt()
    pos = _pos("BUY", entry=2000.0, stop=1995.0, tp=2010.0)
    bar = {"t": 0, "o": 2000.0, "h": 2001.0, "l": 1999.0, "c": 2000.5}
    assert bt.qf_resolve(pos, bar) is None
