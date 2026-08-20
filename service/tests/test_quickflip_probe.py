"""The probe is the only evidence behind the spec's numbers, so it is pinned.

Deliberately includes a guard on the TIME CONVENTION: candle `t` is server
wall-clock and must be read with no offset. A +3h shift is what invalidated
the first two spikes and inflated the result that was nearly built live.
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BARS = pathlib.Path(__file__).parent / "data" / "bars_slice.json"


def _probe():
    spec = importlib.util.spec_from_file_location(
        "qfp", ROOT / "scripts" / "quickflip_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_server_hour_zero_is_the_market_break():
    """The convention guard: read candle t with NO offset and server hour 00
    is empty, because that is the daily break. If this fails, someone has
    reintroduced a timezone shift.

    This must exercise the PROBE's own conversion (`qfp._server`), the exact
    function `setups_at()` calls to bucket candles into the box -- not a
    hand-rolled duplicate of the correct conversion, which would pass even
    if `_server()` itself were broken.
    """
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    hours = {qfp._server(c["t"]).hour for c in candles}
    assert 0 not in hours, "server hour 00 must be empty (the daily break)"


def test_daily_atr_warms_up_and_is_positive():
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    assert atr, "no daily ATR computed"
    assert all(v > 0 for v in atr.values())


def test_daily_atr_does_not_leak_the_last_day_into_the_first():
    """Regression guard for the i == ATR_DAYS wraparound bug: the inner
    loop's `keys[j - 1]` hit j == 0 for the first eligible day, and
    Python's negative-index wraparound made keys[-1] -- the LAST day in
    the whole dataset -- stand in as "yesterday's close" for the FIRST
    computed ATR. That leaked months of future price into the earliest
    day's true range.

    Prove it is fixed by mutating only the last day's close to an absurd
    value: a correct implementation never looks at it while computing the
    first day's ATR, so the first computed value must be unchanged.
    """
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr_before = qfp.daily_atr(candles)
    first_day = min(atr_before)

    mutated = json.loads(BARS.read_text())
    mutated[-1]["c"] += 10_000.0  # absurd future close, deliberately huge

    atr_after = qfp.daily_atr(mutated)

    assert first_day in atr_after
    assert atr_after[first_day] == atr_before[first_day], (
        "the first computed ATR changed when only the LAST day's close "
        "changed -- it is reading a future/wrapped day into its true "
        "range sum"
    )


def test_setups_have_a_stop_on_the_losing_side_of_entry():
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    got = qfp.setups_at(candles, 13, 30, atr)
    for s in got:
        if s["green"]:      # sold the sweep: stop above, target below
            assert s["stop"] > s["tp"]
        else:
            assert s["stop"] < s["tp"]


def test_at_most_one_setup_per_day():
    import datetime as dt
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    got = qfp.setups_at(candles, 13, 30, atr)
    days = [int(s["entry_t"]) // 86400 for s in got]
    assert len(days) == len(set(days)), "more than one setup on some day"
