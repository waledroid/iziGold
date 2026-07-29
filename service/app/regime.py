import numpy as np


def _hlc(candles):
    return (np.array([x.h for x in candles]),
            np.array([x.l for x in candles]),
            np.array([x.c for x in candles]))


def _true_range(h, l, c):
    prev = np.concatenate(([c[0]], c[:-1]))
    return np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))


def atr_series(h, l, c, period=14):
    tr = _true_range(h, l, c)
    out = np.full_like(tr, np.nan)
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def adx_series(h, l, c, period=14):
    up, down = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(h, l, c)[1:]

    def wilder_sum(x):
        s = np.full_like(x, np.nan)
        s[period - 1] = x[:period].sum()
        for i in range(period, len(x)):
            s[i] = s[i - 1] - s[i - 1] / period + x[i]
        return s

    trs, pdms, mdms = wilder_sum(tr), wilder_sum(pdm), wilder_sum(mdm)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, mdi = 100 * pdms / trs, 100 * mdms / trs
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    out = np.full_like(dx, np.nan)
    start = 2 * period - 1
    out[start] = np.nanmean(dx[period - 1:start + 1])
    for i in range(start + 1, len(dx)):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def last_atr(candles, period=14):
    h, l, c = _hlc(candles)
    return float(atr_series(h, l, c, period)[-1])


def classify_regime(candles, adx_threshold=25.0, vol_percentile=0.8):
    h, l, c = _hlc(candles)
    atr = atr_series(h, l, c)
    recent = atr[~np.isnan(atr)][-100:]
    rank = float((recent < recent[-1]).mean())
    if rank >= vol_percentile:
        return "high_volatility"
    if float(adx_series(h, l, c)[-1]) >= adx_threshold:
        return "trend"
    return "range"
