"""Chart-overlay indicators for render.py.

Pure functions over candle lists -- no MT5/MQL5 dependency, no I/O. Used to
paint the same HalfTrend + EMA context lines on the rendered trade PNGs that
the EA paints live on the MT5 chart (see
mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh). Display-only: nothing
here feeds back into signal logic.
"""

from __future__ import annotations


def ema(closes: list[float], period: int) -> list[float | None]:
    """Standard EMA, seeded with the SMA of the first `period` closes.

    Returns a list the same length as `closes`. The first `period - 1`
    positions are None (not enough data to seed yet); index `period - 1`
    onward holds the EMA value. Handles short/degenerate input by returning
    all-None rather than raising.
    """
    n = len(closes)
    if period <= 0 or n < period:
        return [None] * n

    k = 2.0 / (period + 1.0)
    out: list[float | None] = [None] * (period - 1)
    sma = sum(closes[:period]) / period
    out.append(sma)
    prev = sma
    for i in range(period, n):
        prev = closes[i] * k + prev * (1.0 - k)
        out.append(prev)
    return out


def halftrend(candles, amplitude: int = 4) -> list[tuple[float, int] | None]:
    """Python port of CHalfTrendEmaStrategy::ProcessClosedBar (see
    mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh lines 89-151).

    `candles` must be oldest -> newest (matches the /analyze payload and
    tests/fixtures.py). Returns a list the same length as `candles`; each
    entry is either None (warm-up, not enough bars yet) or
    (line_value, trend) where trend is 0 = up (line = maxLowPrice, the
    running max of amplitude-window lows since the last flip) or 1 = down
    (line = minHighPrice, the running min of amplitude-window highs since
    the last flip).

    The MQL5 version walks MT5 "shift" bars (shift 1 = most recent closed
    bar, increasing shift = further into the past) from oldest to newest,
    using CopyHigh/CopyLow(shift, amplitude) -- i.e. a window of `amplitude`
    bars ending at (and including) the bar being processed. In forward
    chronological index `p`, that window is candles[p - amplitude + 1 : p + 1],
    and MQL5's "previous bar" (shift + 1) is candles[p - 1].
    """
    n = len(candles)
    out: list[tuple[float, int] | None] = [None] * n
    if amplitude <= 0:
        return out

    p_start = amplitude  # first p with a full window AND a valid p-1
    if n <= p_start:
        return out

    trend = -1  # -1 = not yet seeded, mirrors m_trend
    next_trend = 0
    max_low_price = 0.0
    min_high_price = 0.0

    for p in range(p_start, n):
        window = candles[p - amplitude + 1: p + 1]
        highs = [c.h for c in window]
        lows = [c.l for c in window]
        high_price = max(highs)
        low_price = min(lows)
        highma = sum(highs) / amplitude
        lowma = sum(lows) / amplitude

        close = candles[p].c
        prev_low = candles[p - 1].l
        prev_high = candles[p - 1].h

        if trend < 0:  # seed on the very first processed bar
            trend = 0
            next_trend = 0
            max_low_price = prev_low
            min_high_price = prev_high

        if next_trend == 1:
            max_low_price = max(low_price, max_low_price)
            if highma < max_low_price and close < prev_low:
                trend = 1
                next_trend = 0
                min_high_price = high_price
        else:
            min_high_price = min(high_price, min_high_price)
            if lowma > min_high_price and close > prev_high:
                trend = 0
                next_trend = 1
                max_low_price = low_price

        line_value = max_low_price if trend == 0 else min_high_price
        out[p] = (line_value, trend)

    return out
