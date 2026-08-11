"""Forming-bar merge for the /chart command.

The closed-candle accumulator only advances when a bar closes (the
/analyze cadence); the heartbeat carries the still-forming bar 0 so a
/chart render is real-time to the last heartbeat (~5 s)."""
from app.models import Candle


def merge_forming_bar(candles: list, hb) -> list:
    """Return `candles` with the heartbeat's forming bar appended, or
    replacing the last candle when it is the same bar re-observed. The
    input list is never mutated. No-op (same list back) when there is no
    usable forming bar: bar_t absent/0, prices 0 (CopyRates failure), an
    empty accumulator, or a forming bar older than the last closed candle
    (stale heartbeat from before the last close)."""
    bar_t = int(getattr(hb, "bar_t", 0) or 0)
    if not candles or bar_t <= 0:
        return candles
    o = getattr(hb, "bar_o", 0.0)
    h = getattr(hb, "bar_h", 0.0)
    l = getattr(hb, "bar_l", 0.0)
    c = getattr(hb, "bar_c", 0.0)
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return candles
    forming = Candle(t=bar_t, o=o, h=h, l=l, c=c, v=0.0)
    last_t = candles[-1].t
    if bar_t == last_t:
        return candles[:-1] + [forming]
    if bar_t < last_t:
        return candles
    return candles + [forming]
