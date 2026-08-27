"""M15 trades and charts use EMA 45 (2026-08-27 sweep): the dashboard M15
overlay and the mini-app M15 tab must compute the trading EMA at 45, while
every M5 surface keeps 55. The wire key stays "ema55" (it is the series
slot, not the length); visible labels update in the frontends."""
from app.indicators import ema
from app.main import _OVERLAY_BUILDERS, _resample_m15
from app.miniapp import _indicator_series
from app.models import Candle


def m5(t, c):
    return Candle(t=t, o=c - 1, h=c + 1, l=c - 2, c=c, v=1)


def _wavy(n=600):
    return [m5(900 + 300 * i, 100.0 + (i % 7) - 3 + i * 0.01) for i in range(n)]


def test_dashboard_m15_overlay_trading_ema_is_45():
    candles = _wavy()
    closes = [c.c for c in candles]
    out = _OVERLAY_BUILDERS["halftrend_m15_v1"](candles, closes)
    m15 = _resample_m15(candles)
    closes15 = [b["c"] for b in m15]
    want45 = ema(closes15, 45)
    want55 = ema(closes15, 55)
    i = 450
    bucket = i - (i % 3)
    k = next(j for j, b in enumerate(m15) if b["t"] == candles[bucket].t)
    assert out["ema55"][i] == want45[k]
    assert out["ema55"][i] != want55[k]


def test_miniapp_m15_tab_trading_ema_is_45():
    rows = [{"t": 900 + 900 * i, "o": 1.0, "h": 2.0, "l": 0.5,
             "c": 100.0 + (i % 5) + i * 0.01, "v": 1} for i in range(200)]
    closes = [r["c"] for r in rows]
    out15 = _indicator_series(rows, "M15")
    out5 = _indicator_series(rows, "M5")
    assert out15["ema55"] == ema(closes, 45)
    assert out5["ema55"] == ema(closes, 55)
