"""Shared, spec-driven chart-overlay builders.

The dashboard (`/api/overlays`) and the mini-app (`/api/history`) each used to
compute their own EMA/HalfTrend/RSI/MACD/Bollinger series with their own
hardcoded parameters (e.g. the M15 trading-EMA length lived in three places).
This module is the single builder both call; indicator parameters (trading-EMA
length per timeframe) come from config/chart_spec.json via app.chart_spec, and
the underlying maths stays in app.indicators.

Two wire formats are intentionally preserved, one per caller:

  - build_izi5 / build_izi15 / build_boll -> the dashboard wire format
    (halftrend as [value, trend] lists, keyed "ema55" as the trading-EMA slot).
  - indicator_series(rows, tf) -> the mini-app wire format
    (halftrend as {"v", "trend"} objects, plus rsi / macd sub-panel series).
"""

from __future__ import annotations

from app import chart_spec
from app.indicators import bollinger, ema, halftrend, rsi
from app.models import Candle


def _closes(candles: list) -> list[float]:
    return [c.c for c in candles]


def build_izi5(candles: list, closes: list) -> dict:
    """izi5 (M5 lane): EMA-9/21/trade/200 + HalfTrend(4) on the M5 bars."""
    ht = halftrend(candles, amplitude=4)
    ema_len = chart_spec.trade_ema_len("M5")
    return {
        "halftrend": [list(v) if v is not None else None for v in ht],
        "ema55": ema(closes, ema_len),
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema200": ema(closes, 200),
    }


def build_boll(candles: list, closes: list) -> dict:
    upper, mid, lower = bollinger(closes, period=20, dev=2.0)
    return {"bb_upper": upper, "bb_mid": mid, "bb_lower": lower}


def _resample_m15(candles: list) -> list:
    """3xM5 -> 1xM15 dict bars aligned to 900 s boundaries. The trailing
    (possibly incomplete) bucket is kept -- on a live chart the forming M15
    bar is real information, and the EA's own M15 lane sees the same thing."""
    out = []
    for c in candles:
        bucket = c.t - (c.t % 900)
        if out and out[-1]["t"] == bucket:
            b = out[-1]
            b["h"] = max(b["h"], c.h)
            b["l"] = min(b["l"], c.l)
            b["c"] = c.c
        else:
            out.append({"t": bucket, "o": c.o, "h": c.h, "l": c.l, "c": c.c})
    return out


def build_izi15(candles: list, closes: list) -> dict:
    """izi15's indicators computed on M15 bars, expanded back to
    the M5 candle list (each M5 bar takes its M15 bucket's value) so the
    arrays stay 1:1 with /api/candles like every other overlay."""
    m15 = _resample_m15(candles)
    if len(m15) < 3:
        return {}
    m15_objs = [type("C", (), b)() for b in m15]
    closes15 = [b["c"] for b in m15]
    ht15 = halftrend(m15_objs, amplitude=4)
    # M15 trades EMA 50 (owner 2026-08-31, sweep-checked). "ema55" stays as
    # the wire SLOT name every chart frontend already binds to — the value is
    # the lane's trading EMA.
    ema_len = chart_spec.trade_ema_len("M15")
    e55, e200 = ema(closes15, ema_len), ema(closes15, 200)
    idx = {b["t"]: i for i, b in enumerate(m15)}

    # Bucket sizes, so the interpolation below knows where each M5 bar sits
    # inside its M15 bucket (3 normally; fewer at session edges/gaps).
    counts: dict = {}
    for c in candles:
        b = c.t - (c.t % 900)
        counts[b] = counts.get(b, 0) + 1

    # Smooth EMA expansion (owner 2026-09-02: the flat-per-bucket step
    # rendering read as "ziggy zaggy"): a bucket's EMA value anchors on that
    # bucket's LAST M5 bar — the moment the value actually became known —
    # and the M5 bars inside the bucket interpolate linearly from the
    # previous bucket's anchor. The forming bucket interpolates toward its
    # live (partial-close) EMA, so the line stays continuous to the newest
    # bar. HalfTrend stays bucket-stepped on purpose: it IS a step line.
    def smooth(series, i, b, kth):
        cur = series[i]
        if cur is None:
            return None
        prev = series[i - 1] if i > 0 else None
        if prev is None:
            return cur if kth == counts[b] else None
        return prev + (cur - prev) * (kth / counts[b])

    seen: dict = {}
    out_ht, out55, out200 = [], [], []
    for c in candles:
        b = c.t - (c.t % 900)
        i = idx[b]
        seen[b] = seen.get(b, 0) + 1
        v = ht15[i]
        out_ht.append(list(v) if v is not None else None)
        out55.append(smooth(e55, i, b, seen[b]))
        out200.append(smooth(e200, i, b, seen[b]))
    return {"halftrend": out_ht, "ema55": out55, "ema200": out200}


def indicator_series(rows: list[dict], tf: str = "M5") -> dict:
    """Mini-app indicator payload: EMA-9/21/trade/200 + HalfTrend(4) + RSI,
    computed fresh from the ring buffer on every request (<=500 candles:
    cheap, no cache needed). Uses the exact same app.indicators math the
    trade-chart PNGs use, so the mini-app overlays match the EA's live MT5
    chart. The trading-EMA length follows config/chart_spec.json (M15 -> 50).
    MACD was removed from charts 2026-09-24 (owner request); app.indicators.
    macd() still feeds the agent context and the backtest study, it is just
    no longer a chart series.
    """
    closes = [r["c"] for r in rows]
    candle_objs = [Candle(**r) for r in rows]
    ht = halftrend(candle_objs, amplitude=4)
    ema_len = chart_spec.trade_ema_len(tf)
    return {
        "ema9": ema(closes, 9),
        "ema21": ema(closes, 21),
        "ema55": ema(closes, ema_len),
        "ema200": ema(closes, 200),
        "halftrend": [({"v": e[0], "trend": e[1]} if e else None) for e in ht],
        "rsi": rsi(closes, 14),
    }
