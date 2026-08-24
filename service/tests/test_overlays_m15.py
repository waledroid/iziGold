from app.main import _resample_m15, _OVERLAY_BUILDERS
from app.models import Candle


def m5(t, c):
    return Candle(t=t, o=c - 1, h=c + 1, l=c - 2, c=c, v=1)


def test_resample_m15_buckets_and_ohlc():
    # 09:00, 09:05, 09:10 -> one M15 bucket; 09:15 starts the next
    candles = [m5(900, 10.0), m5(1200, 12.0), m5(1500, 11.0), m5(1800, 13.0)]
    out = _resample_m15(candles)
    assert [b["t"] for b in out] == [900, 1800]
    b0 = out[0]
    assert b0["o"] == candles[0].o
    assert b0["c"] == 11.0                       # last M5 close in the bucket
    assert b0["h"] == max(c.h for c in candles[:3])
    assert b0["l"] == min(c.l for c in candles[:3])


def test_m15_overlays_align_with_m5_list():
    candles = [m5(900 + 300 * i, 100.0 + i)
               for i in range(600)]              # 600 M5 bars = 200 M15 bars
    closes = [c.c for c in candles]
    out = _OVERLAY_BUILDERS["halftrend_m15_v1"](candles, closes)
    assert set(out) == {"halftrend", "ema55", "ema200"}
    for arr in out.values():
        assert len(arr) == len(candles)          # 1:1 with /ui/candles
    # three consecutive M5 bars share their M15 bucket's value
    i = 450
    j = i - (i % 3)
    assert out["ema55"][j] == out["ema55"][j + 1] == out["ema55"][j + 2]


def test_bb_alias_registered():
    assert _OVERLAY_BUILDERS["boll_stochrsi"] is _OVERLAY_BUILDERS["boll_stochrsi_v1"]
