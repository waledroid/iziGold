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


def bollinger(
    closes: list[float], period: int = 20, dev: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger Bands: SMA basis with +/- `dev` standard deviations.

    Returns `(upper, mid, lower)`, each the same length as `closes`. The
    first `period - 1` positions are None (not enough data to seed yet).
    Uses the population standard deviation (ddof=0) over each rolling
    window, matching the common charting-platform convention. Handles
    short/degenerate input by returning all-None rather than raising.
    """
    n = len(closes)
    if period <= 0 or n < period:
        return [None] * n, [None] * n, [None] * n

    upper: list[float | None] = [None] * (period - 1)
    mid: list[float | None] = [None] * (period - 1)
    lower: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        m = sum(window) / period
        variance = sum((x - m) ** 2 for x in window) / period
        sd = variance ** 0.5
        mid.append(m)
        upper.append(m + dev * sd)
        lower.append(m - dev * sd)
    return upper, mid, lower


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


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI (matches MT5's iRSI): seed with the simple average of
    the first `period` gains/losses, then Wilder-smooth. Same length as
    `closes`; the first `period` positions are None. Flat tape (no gains
    AND no losses) reads 50; all-gains reads 100, all-losses 0. Short or
    degenerate input degrades to all-None. Added 2026-09-02 for the
    replay filter study — reporting/replay only, never in the EA."""
    n = len(closes)
    if period <= 0 or n <= period:
        return [None] * n
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains[i] = d
        else:
            losses[i] = -d
    out: list[float | None] = [None] * period
    ag = sum(gains[1:period + 1]) / period
    al = sum(losses[1:period + 1]) / period

    def _rsi_val(g, l):
        if l == 0.0:
            return 50.0 if g == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out.append(_rsi_val(ag, al))
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out.append(_rsi_val(ag, al))
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26,
         signal_period: int = 9) -> tuple:
    """Classic MACD: line = EMA(fast) − EMA(slow), signal = EMA(signal_period)
    of the line, histogram = line − signal. Three lists, each the same
    length as `closes`, None during warm-up. NOTE MT5's built-in MACD
    draws an SMA signal line instead of an EMA one — this is the classic
    (TradingView/textbook) form, used only by the replay/reporting side."""
    n = len(closes)
    e_fast, e_slow = ema(closes, fast), ema(closes, slow)
    line: list[float | None] = [
        (e_fast[i] - e_slow[i]) if e_slow[i] is not None else None
        for i in range(n)]
    defined = [v for v in line if v is not None]
    sig_defined = ema(defined, signal_period)
    signal: list[float | None] = [None] * n
    j = 0
    for i in range(n):
        if line[i] is not None:
            signal[i] = sig_defined[j]
            j += 1
    hist: list[float | None] = [
        (line[i] - signal[i]) if signal[i] is not None else None
        for i in range(n)]
    return line, signal, hist
