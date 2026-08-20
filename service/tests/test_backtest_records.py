"""The chart cannot draw what the engine does not record: entry times, the
stop as it actually moved, and the target price."""
import json
import pathlib

from tests.test_backtest_golden import BARS, _load_bt


def _trades():
    bt = _load_bt()
    return bt.run(json.loads(BARS.read_text()), 4000.0, False)[0]


def test_every_leg_carries_its_bar_time():
    for t in _trades():
        for leg in t["legs"]:
            assert isinstance(leg["t"], int) and leg["t"] > 0
        # legs fill in chronological order
        times = [leg["t"] for leg in t["legs"]]
        assert times == sorted(times)


def test_opened_t_mirrors_first_leg():
    for t in _trades():
        assert t["opened_t"] == t["legs"][0]["t"]


def test_stop_history_starts_at_entry_and_never_goes_backwards_in_time():
    for t in _trades():
        hist = t["stop_history"]
        assert hist, "every basket places a stop at entry"
        assert hist[0]["t"] == t["legs"][0]["t"]
        assert [h["t"] for h in hist] == sorted(h["t"] for h in hist)


def test_a_trade_with_adds_records_more_stop_moves_than_a_single_leg_trade():
    trades = _trades()
    multi = [t for t in trades if len(t["legs"]) > 1]
    assert multi, "the slice must contain at least one pyramided basket"
    # every add ladders the shared stop, so history grows past the initial one
    assert any(len(t["stop_history"]) > 1 for t in multi)


def test_tp_is_a_price_on_the_profitable_side_of_entry():
    for t in _trades():
        if t["tp"] is None:
            continue
        entry = t["legs"][0]["px"]
        if t["dir"] == "BUY":
            assert t["tp"] > entry
        else:
            assert t["tp"] < entry


def test_bal_after_chains_to_the_final_balance():
    bt = _load_bt()
    trades, bal, _dd, _v = bt.run(json.loads(BARS.read_text()), 4000.0, False)
    assert trades
    assert round(trades[-1]["bal_after"], 2) == round(bal, 2)
