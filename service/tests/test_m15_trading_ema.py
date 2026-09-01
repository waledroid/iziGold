"""M15 trades and charts use EMA 50 (owner request 2026-08-31; sweep same
day: 50 nets +$10,069 vs 45's +$9,945 over 17 mo, better in BOTH halves,
same dd — 45 came from the 2026-08-27 sweep): the dashboard M15 overlay and
the mini-app M15 tab must compute the trading EMA at 50, while every M5
surface keeps 55. The wire key stays "ema55" (it is the series slot, not
the length); visible labels update in the frontends."""
from app.indicators import ema
from app.main import _OVERLAY_BUILDERS, _resample_m15
from app.miniapp import _indicator_series
from app.models import Candle

M15_TRADING_EMA = 50


def m5(t, c):
    return Candle(t=t, o=c - 1, h=c + 1, l=c - 2, c=c, v=1)


def _wavy(n=600):
    return [m5(900 + 300 * i, 100.0 + (i % 7) - 3 + i * 0.01) for i in range(n)]


def test_dashboard_m15_overlay_trading_ema():
    """The M15 trading EMA is drawn SMOOTH on the M5 chart (owner
    2026-09-02: the stair-stepped bucket expansion looked 'ziggy zaggy'):
    each completed M15 bucket's EMA value anchors on that bucket's LAST M5
    bar, and the M5 bars in between interpolate linearly between anchors."""
    candles = _wavy()
    closes = [c.c for c in candles]
    out = _OVERLAY_BUILDERS["halftrend_m15_v1"](candles, closes)
    m15 = _resample_m15(candles)
    closes15 = [b["c"] for b in m15]
    want = ema(closes15, M15_TRADING_EMA)
    want55 = ema(closes15, 55)
    # candles are 300 s apart starting on a 900 s boundary: bar i sits in
    # bucket i // 3, and i % 3 == 2 is the bucket's last M5 bar (anchor).
    i = 452                       # bucket-final bar of bucket 150
    k = i // 3
    assert out["ema55"][i] == want[k]           # anchor == bucket EMA
    assert out["ema55"][i] != want55[k]         # and it is the 50, not 55
    # mid-bucket bar: strictly between the two neighbouring anchors
    j = 451                       # second bar of the same bucket
    lo, hi = sorted((want[k - 1], want[k]))
    assert lo <= out["ema55"][j] <= hi
    assert out["ema55"][j] != out["ema55"][i] or want[k - 1] == want[k]
    # exact interpolation: 2/3 of the way from the previous anchor
    assert abs(out["ema55"][j] - (want[k - 1] + (want[k] - want[k - 1]) * 2 / 3)) < 1e-9


def test_miniapp_m15_tab_trading_ema():
    rows = [{"t": 900 + 900 * i, "o": 1.0, "h": 2.0, "l": 0.5,
             "c": 100.0 + (i % 5) + i * 0.01, "v": 1} for i in range(200)]
    closes = [r["c"] for r in rows]
    out15 = _indicator_series(rows, "M15")
    out5 = _indicator_series(rows, "M5")
    assert out15["ema55"] == ema(closes, M15_TRADING_EMA)
    assert out5["ema55"] == ema(closes, 55)
