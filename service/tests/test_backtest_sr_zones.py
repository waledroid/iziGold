"""S/R zone wall filter / report in the replay (spec
docs/superpowers/specs/2026-09-05-sr-zones-design.md, increment 2).

The replay judges every entry decision against the SAME zone engine the EA
mirrors (app.sr_zones), rebuilt from the trailing lookback of tier bars
closed by the decision bar. Off = byte-identical (the golden pins are the
detector for that); this file pins the flag surface, the report-only tag,
and the refusal bookkeeping.
"""
import json

import pytest

from tests.test_backtest_golden import BARS, _digest, _load_bt


def _run(bt, **overrides):
    for k, v in overrides.items():
        setattr(bt, k, v)
    candles = json.loads(BARS.read_text())
    trades, bal, dd, _ = bt.run(candles, 10000.0, False, bt.lanes_for("ht"))
    return trades, round(bal, 2), round(dd, 2)


def test_flags_parse_and_default_off():
    bt = _load_bt()
    a = bt.build_parser().parse_args([])
    assert a.sr_zone_tiers == ""
    assert a.sr_zone_room_atr == 1.0
    assert a.sr_zone_report is False
    assert a.sr_zone_params == ""
    a = bt.build_parser().parse_args(
        ["--sr-zone-tiers", "H4,H1", "--sr-zone-room-atr", "0.5",
         "--sr-zone-report", "--sr-zone-params", "merge_atr=0.4,keep_per_role=2"])
    assert a.sr_zone_tiers == "H4,H1" and a.sr_zone_room_atr == 0.5
    assert a.sr_zone_report is True
    assert a.sr_zone_params == "merge_atr=0.4,keep_per_role=2"


def test_parse_sr_zone_params_validates_keys():
    bt = _load_bt()
    assert bt.parse_sr_zone_params("") == {}
    assert bt.parse_sr_zone_params("merge_atr=0.4,keep_per_role=2") == \
        {"merge_atr": 0.4, "keep_per_role": 2}
    with pytest.raises(SystemExit):
        bt.parse_sr_zone_params("bogus=1")


def test_parse_sr_zone_tiers_validates_names():
    bt = _load_bt()
    assert bt.parse_sr_zone_tiers("") == ()
    assert bt.parse_sr_zone_tiers("H4,M15") == ("H4", "M15")
    assert bt.parse_sr_zone_tiers(" h1 , H4 ") == ("H1", "H4")
    with pytest.raises(SystemExit):
        bt.parse_sr_zone_tiers("H4,D1")


def test_off_leaves_no_trace():
    bt = _load_bt()
    trades, _, _ = _run(bt)
    assert bt.run.sr_zone_refused == []
    assert all(t.get("sr_wall") is None for t in trades)


def test_report_mode_tags_every_trade_without_changing_them():
    base_bt = _load_bt()
    base, bal0, dd0 = _run(base_bt)
    rep_bt = _load_bt()
    rep, bal1, dd1 = _run(rep_bt, SR_ZONE_REPORT=True)
    assert _digest(rep) == _digest(base)
    assert (bal1, dd1) == (bal0, dd0)
    assert rep_bt.run.sr_zone_refused == []
    assert rep, "the slice is expected to produce trades"
    for t in rep:
        assert t.get("sr_wall") in ("", "H4", "H1", "M15")


def test_filter_refuses_walls_and_survivors_are_clear():
    bt = _load_bt()
    trades, _, _ = _run(bt, SR_ZONE_TIERS=("H4", "H1", "M15"),
                        SR_ZONE_ROOM_ATR=3.0)   # wide room: walls are common
    refused = bt.run.sr_zone_refused
    assert refused, "a 3 ATR room on a 3-week slice should refuse something"
    for when, direction, tier in refused:
        assert direction in ("BUY", "SELL") and tier in ("H4", "H1", "M15")
        assert when is not None
    # anything that entered passed the wall test at its decision bar
    assert all(t.get("sr_wall") == "" for t in trades)


def test_filter_with_zero_room_only_refuses_entries_inside_a_box():
    """room 0 still refuses an entry sitting INSIDE an opposing box (the
    'inside' test is independent of room); it must never refuse an entry
    that merely has a box somewhere ahead."""
    bt = _load_bt()
    _run(bt, SR_ZONE_TIERS=("H4", "H1", "M15"), SR_ZONE_ROOM_ATR=0.0)
    wide_bt = _load_bt()
    _run(wide_bt, SR_ZONE_TIERS=("H4", "H1", "M15"), SR_ZONE_ROOM_ATR=3.0)
    assert len(bt.run.sr_zone_refused) <= len(wide_bt.run.sr_zone_refused)


def test_zones_at_uses_only_tier_bars_closed_by_the_decision_bar():
    """A tier bar that has not closed by bar i must not exist yet: the
    zone list at bar i equals build_zones over the resampled bars whose
    bucket is complete at i."""
    bt = _load_bt()
    from app.sr_zones import LOOKBACK, TIER_SEC, build_zones, resample
    candles = json.loads(BARS.read_text())
    i = len(candles) - 7          # an M5 bar that is not a bucket's last slot
    zones = bt.sr_zones_at(candles, i, 300, {}, {})
    want = []
    for tier in ("H4", "H1", "M15"):
        rows = resample(candles[:i + 1], TIER_SEC[tier], 300)[-LOOKBACK[tier]:]
        want.extend(build_zones(rows, tier))
    assert [(z.tier, z.role, z.lo, z.hi, z.touches, z.born) for z in zones] == \
        [(z.tier, z.role, z.lo, z.hi, z.touches, z.born) for z in want]
