"""The EA's law: after a HalfTrend flip, wait one closed bar, and enter on the
next bar only if it opens beyond EMA-55. Otherwise the signal is dead until
the next flip. The replay must default to that law."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def test_strict_window_is_the_default():
    bt = _load_bt()
    assert bt.STRICT_WINDOW is True


def test_loose_window_flag_turns_it_off():
    bt = _load_bt()
    args = bt.build_parser().parse_args(["--loose-window"])
    assert args.loose_window is True


def test_strict_window_flag_still_parses_as_a_noop():
    bt = _load_bt()
    args = bt.build_parser().parse_args(["--strict-window"])
    assert args.loose_window is False


def test_strict_takes_fewer_entries_than_loose():
    """Strict can only ever refuse entries loose would take, never add any."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.STRICT_WINDOW = True
    strict = bt.run(candles, 4000.0, False)[0]
    bt.STRICT_WINDOW = False
    loose = bt.run(candles, 4000.0, False)[0]
    assert len(strict) < len(loose), "strict must filter something in this slice"
