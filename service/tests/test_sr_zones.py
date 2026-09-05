import pytest

from app.sr_zones import (LOOKBACK, TIER_SEC, Zone, build_zones, resample,
                          sma_atr, M5_ROWS_FOR_ZONES, sr_verdict, sr_zones_from_db,
                          zone_dicts, zones_from_m5, zones_from_rings)
from tests.test_backtest_golden import _load_bt


def bar(t, o, h, l, c, v=1.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def flat(n, t0=0, sec=900, px=100.0):
    """n quiet bars: body px..px, wick +-0.5 -> TR = 1.0 every bar."""
    return [bar(t0 + i * sec, px, px + 0.5, px - 0.5, px) for i in range(n)]


def with_spike(rows, idx, high, close=None):
    """Turn rows[idx] into a swing-high candidate: wick to `high`, body
    100 -> close (default 101)."""
    r = dict(rows[idx])
    r["h"] = high
    r["c"] = 101.0 if close is None else close
    rows[idx] = r
    return rows


# ---------------------------------------------------------------- ATR

def test_sma_atr_is_the_mt5_sma_of_true_range():
    rows = flat(20)
    rows[5] = bar(rows[5]["t"], 100, 103.0, 99.5, 100)     # TR = 3.5
    out = sma_atr(rows, 14)
    assert out[:14] == [None] * 14
    # first value = mean(TR[1..14]); TR is 1.0 except bar 5 (3.5) and
    # bar 6 (max(100.5, 100) - min(99.5, 100) = 1.0)
    assert out[14] == pytest.approx((13 * 1.0 + 3.5) / 14)
    # sliding window: bar 5 has left the window at index 19
    assert out[19] == pytest.approx(1.0)


def test_sma_atr_short_input_is_all_none():
    assert sma_atr(flat(5), 14) == [None] * 5
    assert sma_atr([], 14) == []


# ----------------------------------------------------------- resample

def test_resample_matches_the_replay_engine_rule():
    bt = _load_bt()
    assert bt.SRC_SEC == 300
    rows = [bar(300 * i + 600, 100 + i, 101 + i, 99 + i, 100.5 + i, 2.0)
            for i in range(50)]
    assert resample(rows, 900) == bt.resample(rows, 900)
    assert resample(rows, 3600) == bt.resample(rows, 3600)
    assert resample(rows, 300) == rows


def test_resample_drops_only_a_trailing_partial_bucket():
    rows = [bar(300 * i, 1, 2, 0, 1) for i in range(7)]   # 7 bars = 2 full M15 + 1 partial
    out = resample(rows, 900)
    assert [b["t"] for b in out] == [0, 900]
    assert out[0]["v"] == 3.0


# ------------------------------------------------------------- pivots

def test_flat_series_has_no_zones():
    assert build_zones(flat(60), "H1") == []


def test_swing_high_births_a_resistance_box_anchored_on_the_wick():
    rows = with_spike(flat(60), 30, 104.0)
    zones = build_zones(rows, "H1")
    assert len(zones) == 1
    z = zones[0]
    assert z.role == "resistance" and z.tier == "H1"
    assert z.hi == 104.0                       # wick extreme is the top edge
    assert z.born == rows[30]["t"] and z.touches == 1
    atr = sma_atr(rows, 14)[33]               # confirmed at bar 30 + wing
    assert z.hi - z.lo == pytest.approx(0.60 * atr)   # 3.0 raw -> trimmed to max


def test_swing_low_births_a_support_box_anchored_on_the_wick():
    rows = flat(60)
    r = dict(rows[30]); r["l"] = 96.0; r["c"] = 99.0; rows[30] = r
    zones = build_zones(rows, "H4")
    assert len(zones) == 1
    assert zones[0].role == "support" and zones[0].lo == 96.0


def test_pivot_needs_the_full_right_wing():
    rows = with_spike(flat(60), 58, 104.0)    # only 1 bar to the right
    assert build_zones(rows, "H1") == []


def test_tiny_wick_is_expanded_to_the_minimum_height():
    rows = flat(60)
    # still a strict swing high (100.6 > the 100.5 neighbours) but the wick
    # above the body top is only 0.05 -> below the 0.15 ATR floor
    r = dict(rows[30]); r["h"] = 100.6; r["c"] = 100.55; rows[30] = r
    zones = build_zones(rows, "M15")
    assert len(zones) == 1
    atr = sma_atr(rows, 14)[33]
    assert zones[0].hi - zones[0].lo == pytest.approx(0.15 * atr)
    assert (zones[0].hi + zones[0].lo) / 2 == pytest.approx(100.6)


# ---------------------------------------------------------- merge/bound

def test_second_touch_at_the_same_level_merges_and_counts():
    rows = with_spike(with_spike(flat(80), 30, 104.0), 50, 104.2)
    zones = build_zones(rows, "H1")
    assert len(zones) == 1
    assert zones[0].touches == 2
    assert zones[0].hi == 104.2
    assert zones[0].born == rows[30]["t"] and zones[0].last_touch == rows[50]["t"]


def test_far_pivot_births_a_separate_zone():
    rows = with_spike(with_spike(flat(80), 30, 104.0), 50, 112.0)
    zones = build_zones(rows, "H1")
    assert sorted(z.hi for z in zones) == [104.0, 112.0]


def test_close_through_kills_a_zone_but_a_wick_through_does_not():
    killed = with_spike(flat(80), 30, 104.0)
    for k in range(40, 80):                    # price closes well above the box
        killed[k] = bar(killed[k]["t"], 106, 106.5, 105.5, 106)
    assert build_zones(killed, "H1") == []

    wicked = with_spike(flat(80), 30, 104.0)
    wicked[45] = bar(wicked[45]["t"], 100, 104.1, 99.5, 100)   # wick into the box, close below
    zones = build_zones(wicked, "H1")
    assert len(zones) == 1 and zones[0].role == "resistance"


def test_bound_keeps_the_three_nearest_per_role():
    rows = flat(200)
    for k, hi in ((20, 104.0), (50, 112.0), (80, 120.0), (110, 128.0), (140, 136.0)):
        rows = with_spike(rows, k, hi)
    zones = build_zones(rows, "H1")
    res = [z for z in zones if z.role == "resistance"]
    assert len(res) == 3
    assert sorted(z.hi for z in res) == [104.0, 112.0, 120.0]   # nearest to close 100


def test_zones_are_sorted_by_born_then_lo():
    rows = with_spike(with_spike(flat(80), 30, 112.0), 50, 104.0)
    zones = build_zones(rows, "H1")
    assert [z.born for z in zones] == sorted(z.born for z in zones)


def test_constants_match_the_spec():
    assert TIER_SEC == {"M15": 900, "H1": 3600, "H4": 14400}
    assert LOOKBACK == {"M15": 400, "H1": 400, "H4": 300}


# ---------------------------------------------------------- verdict + sources

def z(tier, role, lo, hi, touches=1, born=0):
    return Zone(tier, role, lo, hi, touches, born, born)


def test_verdict_buy_inside_or_just_below_resistance_is_a_wall():
    zones = [z("M15", "resistance", 101.0, 101.5), z("H4", "resistance", 101.2, 101.8)]
    assert sr_verdict(zones, "BUY", 101.1, room=0.5) == (0, "H4")   # inside M15, 0.1 below H4 -> strongest
    assert sr_verdict(zones, "BUY", 100.6, room=0.5) == (0, "M15")  # 0.4 below M15 lo; H4 is 0.6 away
    assert sr_verdict(zones, "BUY", 100.4, room=0.5) == (1, "")     # 0.6 / 0.8 below: both beyond room


def test_verdict_clear_when_room_is_enough():
    zones = [z("H1", "resistance", 102.0, 102.5)]
    assert sr_verdict(zones, "BUY", 100.0, room=1.0) == (1, "")
    assert sr_verdict(zones, "BUY", 101.0, room=1.0) == (0, "H1")   # exactly room away counts


def test_verdict_ignores_disabled_tiers_and_wrong_roles():
    zones = [z("H4", "resistance", 100.5, 101.0), z("H4", "support", 99.0, 99.5)]
    assert sr_verdict(zones, "BUY", 100.4, room=0.5, tiers=("H1", "M15")) == (1, "")
    assert sr_verdict(zones, "BUY", 100.4, room=0.5) == (0, "H4")
    assert sr_verdict(zones, "SELL", 100.4, room=0.5) == (1, "")   # support 0.9 below


def test_verdict_sell_mirror():
    zones = [z("H1", "support", 98.0, 98.5), z("M15", "support", 99.0, 99.4)]
    assert sr_verdict(zones, "SELL", 99.2, room=0.3) == (0, "M15")
    assert sr_verdict(zones, "SELL", 99.8, room=0.3) == (1, "")
    assert sr_verdict(zones, "SELL", 98.7, room=0.3) == (0, "H1")


def test_verdict_no_zones_is_clear():
    assert sr_verdict([], "BUY", 100.0, room=1.0) == (1, "")


def _m5_rows(n, t0=1_700_000_000 - (1_700_000_000 % 14400)):
    rows = []
    for i in range(n):
        px = 2000.0 + (i % 200) * 0.05
        rows.append(bar(t0 + 300 * i, px, px + 0.3, px - 0.3, px + 0.1, 1.0))
    return rows


def test_zones_from_m5_builds_every_tier():
    rows = _m5_rows(M5_ROWS_FOR_ZONES)
    # one big single-bar spike: every tier's bucket containing it is a
    # strict swing high (a 48-bar plateau would give EQUAL highs -> no pivot)
    r = dict(rows[8000]); r["h"] = 2100.0; rows[8000] = r
    zones = zones_from_m5(rows)
    tiers = {zn.tier for zn in zones}
    assert tiers == {"H4", "H1", "M15"}
    assert all(isinstance(d["born"], int) for d in zone_dicts(zones))


def test_zones_from_rings_drops_the_forming_bar():
    rows = with_spike(flat(60), 58, 104.0)          # spike 1 bar from the end
    rows.append(bar(rows[-1]["t"] + 900, 100, 100.5, 99.5, 100))   # forming bar
    rings = {"M15": rows, "H1": [], "H4": flat(10)}
    assert zones_from_rings(rings) == []            # spike's right wing never completes
    assert zones_from_rings({"M15": with_spike(flat(80), 30, 104.0) + [rows[-1]]})[0].tier == "M15"


class _FakeDb:
    def __init__(self, rows, series=("XAUUSD", "M5")):
        self.rows, self.series, self.calls = rows, series, 0

    def latest_candle_series(self):
        return self.series

    def get_candles(self, symbol, timeframe, start_ts=None, end_ts=None, limit=None):
        self.calls += 1
        assert timeframe == "M5" and limit == M5_ROWS_FOR_ZONES
        return self.rows[-limit:]


def test_sr_zones_from_db_memoises_on_the_newest_bar():
    db = _FakeDb(_m5_rows(M5_ROWS_FOR_ZONES))
    cache = {}
    a = sr_zones_from_db(db, cache)
    b = sr_zones_from_db(db, cache)
    assert a == b and db.calls == 2 and isinstance(a, list)   # one cheap re-read, one build
    assert cache["t"] == db.rows[-1]["t"]


def test_sr_zones_from_db_fails_open():
    assert sr_zones_from_db(_FakeDb([], series=None), {}) == []
    assert sr_zones_from_db(_FakeDb(_m5_rows(100), series=("XAUUSD", "M15")), {}) == []

    class Boom:
        def latest_candle_series(self):
            raise RuntimeError("db gone")
    assert sr_zones_from_db(Boom(), {}) == []
