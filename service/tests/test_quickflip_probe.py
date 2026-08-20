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
    reintroduced a timezone shift."""
    import datetime as dt
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    hours = {dt.datetime.fromtimestamp(int(c["t"]), dt.UTC).hour for c in candles}
    assert 0 not in hours, "server hour 00 must be empty (the daily break)"


def test_daily_atr_warms_up_and_is_positive():
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    assert atr, "no daily ATR computed"
    assert all(v > 0 for v in atr.values())


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
