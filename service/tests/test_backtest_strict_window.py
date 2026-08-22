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


def test_apply_window_args_wires_the_cli_flag_to_the_runtime_flag():
    """Drives the real main()-path (parse -> apply_window_args -> global),
    not just the parsed Namespace or a hand-set module global. This is the
    only test that would catch a broken `not` on that assignment."""
    bt = _load_bt()

    bt.STRICT_WINDOW = False   # start from a known-wrong value
    bt.apply_window_args(bt.build_parser().parse_args([]))
    assert bt.STRICT_WINDOW is True, "no flag must leave STRICT_WINDOW True"

    bt.STRICT_WINDOW = False
    bt.apply_window_args(bt.build_parser().parse_args(["--loose-window"]))
    assert bt.STRICT_WINDOW is False, "--loose-window must turn it off"

    bt.STRICT_WINDOW = False
    bt.apply_window_args(bt.build_parser().parse_args(["--strict-window"]))
    assert bt.STRICT_WINDOW is True, "--strict-window must leave it on (no-op)"


def test_strict_takes_fewer_entries_than_loose():
    """The strict entry window refuses signals the loose latch would take.

    Measured with the M15 agreement filter OFF, deliberately. The two filters
    interact through the BALANCE: HalfTrend's profit target is a dollar amount
    taken from the balance at entry, so moving an entry by a bar moves the
    target, the exit, and which later signals a basket is free to take. With
    both filters on, strict actually ends up with MORE trades than loose on
    this fixture (43 vs 41) -- not because it refuses less, but because the
    two paths diverge. Isolating the window is the only way this test measures
    the window.
    """
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.BIAS_EMA = 0            # M15 agreement off -- isolate the entry window
    bt.STRICT_WINDOW = True
    strict = bt.run(candles, 4000.0, False)[0]
    bt.STRICT_WINDOW = False
    loose = bt.run(candles, 4000.0, False)[0]
    assert len(strict) < len(loose), "strict must filter something in this slice"


def test_cli_defaults_match_the_module_defaults():
    """A default set only as a module constant is NOT the shipped default:
    main() overwrites the globals with argparse's own defaults, so the two
    must agree or the CLI silently runs something the tests never see.

    Caught live on 2026-08-20: BIAS_EMA/BIAS_MODE/BIAS_TF were changed to the
    M15-agreement defaults as module constants, the golden pinned the new
    behaviour, every test passed -- and a plain `backtest.py` run still took
    counter-trend entries because --bias-ema still defaulted to 0.
    """
    bt = _load_bt()
    args = bt.build_parser().parse_args([])
    assert args.bias_ema == bt.BIAS_EMA
    assert args.bias_mode == bt.BIAS_MODE
    assert args.bias_tf == bt.BIAS_TF
    assert args.bias_buffer_atr == bt.BIAS_BUFFER_ATR
    assert args.chop_eff_max == bt.CHOP_EFF_MAX
    assert args.chop_eff_bars == bt.CHOP_EFF_BARS
    # --confirm/--ema-len use None as "leave the module constant alone"
    assert args.confirm is None
    assert args.ema_len is None
