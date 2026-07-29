import math

from app.models import Candle


def _mk(i, o, c, hl_pad, v=100.0):
    hi, lo = max(o, c) + hl_pad, min(o, c) - hl_pad
    return Candle(t=1700000000 + i * 900, o=o, h=hi, l=lo, c=c, v=v)


def trend_candles(n=200):
    out, price = [], 3000.0
    for i in range(n):
        out.append(_mk(i, price, price + 2.0, 0.5))
        price += 2.0
    return out


def range_candles(n=200):
    out = []
    for i in range(n):
        o = 3000.0 + 3.0 * math.sin(i / 2.0)
        c = 3000.0 + 3.0 * math.sin((i + 1) / 2.0)
        out.append(_mk(i, o, c, 0.5))
    return out


def spike_candles(n=200):
    out = list(range_candles(n - 10))
    price = out[-1].c
    for i in range(n - 10, n):
        out.append(_mk(i, price, price + (15.0 if i % 2 else -15.0), 8.0))
        price = out[-1].c
    return out
