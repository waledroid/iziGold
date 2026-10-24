"""Pre-entry preview (app.preview): an approximate, display-only estimate of
the trade a lane would take if the current HalfTrend setup holds. Never fed
into trading; the frontends draw it only when the owner enables the toggle.

These tests pin the contract, not the exact prices (the strategy is the
authority). They use synthetic candle series that produce a known HalfTrend
trend so the preview's BUY/SELL/None decision is deterministic.
"""
from app import preview
from app.models import Candle


def _uptrend(n=120):
    return [Candle(t=900 + 300 * i, o=100 + i, h=101.5 + i, l=99 + i,
                   c=101 + i, v=1) for i in range(n)]


def _downtrend_reversal(n=120):
    bars = []
    for i in range(n):
        if i < n // 2:
            p = 100 + i
            o, h, l, c = p, p + 1.5, p - 1, p + 1
        else:
            p = (100 + n // 2) - (i - n // 2) * 2
            o, h, l, c = p + 2, p + 3, p - 0.5, p - 0.2
        bars.append(Candle(t=900 + 300 * i, o=o, h=h, l=l, c=c, v=1))
    return bars


def test_preview_buy_in_uptrend():
    pv = preview.build_preview(_uptrend(), "izi5")
    assert pv is not None and pv["active"] is True
    assert pv["direction"] == "BUY"
    # BUY: SL below entry, TP above entry
    assert pv["sl"] < pv["entry"] < pv["tp"]
    assert "HalfTrend up" in pv["reason"]


def test_preview_sell_in_downtrend():
    pv = preview.build_preview(_downtrend_reversal(), "izi5")
    assert pv is not None and pv["direction"] == "SELL"
    # SELL: TP below entry, SL above entry
    assert pv["tp"] < pv["entry"] < pv["sl"]
    assert "HalfTrend down" in pv["reason"]


def test_preview_none_on_short_data():
    assert preview.build_preview(_uptrend(10), "izi5") is None
    assert preview.build_preview([], "izi5") is None


def test_preview_uses_the_lanes_params():
    """izi15 (EMA50 + 0.3 ATR clearance + 1.75 stop buffer) must differ from
    izi5 (EMA55 + 0.75 buffer) -- the lane params really drive the band."""
    candles = _uptrend()
    p5 = preview.build_preview(candles, "izi5")
    p15 = preview.build_preview(candles, "izi15")
    assert p5["sl"] != p15["sl"]     # different stop buffer -> different SL
    # wider M15 stop buffer -> wider (lower, for a BUY) stop
    assert p15["sl"] < p5["sl"]


def test_preview_profit_target_override_changes_tp():
    candles = _uptrend()
    base = preview.build_preview(candles, "izi5")
    bigger = preview.build_preview(candles, "izi5", profit_target_pct=4.0)
    assert base["tp"] < bigger["tp"]
    # exact relationship: TP = entry * (1 + pct/100) for a BUY
    assert abs(bigger["tp"] - round(bigger["entry"] * 1.04, 2)) < 1e-6


def test_preview_fail_open_on_bad_input():
    class Bad:
        pass
    assert preview.build_preview([Bad()], "izi5") is None


def test_lane_params_aliases_and_defaults():
    assert preview.lane_params("halftrend_m15_v1") == preview.lane_params("izi15")
    assert preview.lane_params("does_not_exist")["ema_len"] == 55
