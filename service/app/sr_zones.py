"""Support/resistance ZONES, three timeframe tiers, drawn as boxes
(docs/superpowers/specs/2026-09-05-sr-zones-design.md).

Pure functions: candles in, zones out. This module is the SINGLE Python
implementation -- the mini-app, the dashboard, the screenshot renders and
(increment 2) the replay all call it. Its twin is
mt5/Include/XauAssistant/SrZones.mqh; the two must produce the same boxes
from the same bars, so the processing order below is a contract, not a
convenience. Change one, change both.
"""
from dataclasses import asdict, dataclass

TIER_SEC = {"M15": 900, "H1": 3600, "H4": 14400}
TIER_ORDER = ("H4", "H1", "M15")          # strongest first
LOOKBACK = {"M15": 400, "H1": 400, "H4": 300}
SRC_SEC = 300                             # the M5 source feed
# Enough M5 rows to seed the H4 tier (+2 buckets of slack for the partial
# trailing bucket resample() drops and an off-by-one at the head).
M5_ROWS_FOR_ZONES = (LOOKBACK["H4"] + 2) * (TIER_SEC["H4"] // SRC_SEC)

PIVOT_WING = 3
MIN_HEIGHT_ATR = 0.15
MAX_HEIGHT_ATR = 0.60
MERGE_ATR = 0.50
BREAK_ATR = 0.50
KEEP_PER_ROLE = 3
ATR_PERIOD = 14


@dataclass
class Zone:
    tier: str
    role: str          # "support" | "resistance"
    lo: float
    hi: float
    touches: int
    born: int          # bar time of the first pivot
    last_touch: int    # bar time of the latest merged pivot


def sma_atr(candles: list, period: int = ATR_PERIOD) -> list:
    """MT5's built-in iATR: a SIMPLE moving average of True Range (the
    terminal's ATR.mq5 seeds with mean(TR[1..period]) and then slides the
    window -- it is NOT Wilder smoothing). TR[0] = 0 like the terminal.
    None before index `period`. Exact reproducibility is the point: the EA
    reads iATR at a shift, this reads the same window over the same bars."""
    n = len(candles)
    out = [None] * n
    if period <= 0 or n <= period:
        return out
    tr = [0.0] * n
    for i in range(1, n):
        c, p = candles[i], candles[i - 1]
        tr[i] = max(c["h"], p["c"]) - min(c["l"], p["c"])
    s = sum(tr[1:period + 1])
    out[period] = s / period
    for i in range(period + 1, n):
        s += tr[i] - tr[i - period]
        out[i] = s / period
    return out


def resample(candles: list, sec: int, src_sec: int = SRC_SEC) -> list:
    """The replay engine's bucket rule (scripts/backtest.py::resample),
    re-implemented here so app code never imports the engine module:
    buckets are t // sec; open = first, h/l = extremes, close = last,
    volume summed; ONLY a trailing incomplete bucket is dropped. Pinned
    equal to the engine by test_resample_matches_the_replay_engine_rule."""
    if sec == src_sec:
        return list(candles)
    out, cur_b, off = [], None, None
    for x in candles:
        b = x["t"] // sec
        off = x["t"] % sec
        if b != cur_b:
            cur_b = b
            out.append({"t": b * sec, "o": x["o"], "h": x["h"], "l": x["l"],
                        "c": x["c"], "v": x["v"]})
        else:
            cur = out[-1]
            cur["h"] = max(cur["h"], x["h"])
            cur["l"] = min(cur["l"], x["l"])
            cur["c"] = x["c"]
            cur["v"] += x["v"]
    if out and off != sec - src_sec:
        out.pop()
    return out


def _distance(z: Zone, price: float) -> float:
    if z.lo <= price <= z.hi:
        return 0.0
    return min(abs(price - z.lo), abs(price - z.hi))


def _clamp(role: str, lo: float, hi: float, pivot: float, atr: float,
           min_height_atr: float, max_height_atr: float) -> tuple:
    min_h, max_h = min_height_atr * atr, max_height_atr * atr
    if hi - lo < min_h:
        lo, hi = pivot - min_h / 2, pivot + min_h / 2
    if hi - lo > max_h:
        if role == "resistance":
            lo = hi - max_h
        else:
            hi = lo + max_h
    return lo, hi


def _birth_or_merge(zones: list, tier: str, role: str, lo: float, hi: float,
                    pivot: float, t: int, atr: float, merge_atr: float,
                    min_height_atr: float, max_height_atr: float) -> None:
    lo, hi = _clamp(role, lo, hi, pivot, atr, min_height_atr, max_height_atr)
    merge_px = merge_atr * atr
    best, best_d = None, None
    for z in zones:                       # zones are sorted by born: ties -> oldest
        if z.role != role:
            continue
        d = _distance(z, pivot)
        if d <= merge_px and (best is None or d < best_d):
            best, best_d = z, d
    if best is None:
        zones.append(Zone(tier, role, lo, hi, 1, t, t))
        return
    nlo, nhi = min(best.lo, lo), max(best.hi, hi)
    max_h = max_height_atr * atr
    if nhi - nlo > max_h:
        if role == "resistance":
            nlo = nhi - max_h
        else:
            nhi = nlo + max_h
    best.lo, best.hi = nlo, nhi
    best.touches += 1
    best.last_touch = t


def build_zones(candles: list, tier: str, *, wing: int = PIVOT_WING,
                min_height_atr: float = MIN_HEIGHT_ATR,
                max_height_atr: float = MAX_HEIGHT_ATR,
                merge_atr: float = MERGE_ATR, break_atr: float = BREAK_ATR,
                keep_per_role: int = KEEP_PER_ROLE,
                atr_period: int = ATR_PERIOD) -> list:
    """Zones alive after the LAST candle in `candles` (all treated as
    closed bars -- callers drop a still-forming bar first). Per closed bar
    i, in this order: (1) death by close-through, (2) the pivot confirmed
    at i - wing births or merges, (3) keep the nearest `keep_per_role`
    zones per role, (4) sort by (born, lo). Same order in SrZones.mqh."""
    n = len(candles)
    zones: list = []
    if wing < 1 or n < 2 * wing + 1:
        return zones
    atr = sma_atr(candles, atr_period)
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    for i in range(2 * wing, n):
        a = atr[i]
        if a is None or a <= 0:
            continue
        close = candles[i]["c"]
        zones = [z for z in zones if not (
            (z.role == "resistance" and close > z.hi + break_atr * a)
            or (z.role == "support" and close < z.lo - break_atr * a))]
        j = i - wing
        bar = candles[j]
        wing_idx = [k for k in range(j - wing, j + wing + 1) if k != j]
        if all(highs[j] > highs[k] for k in wing_idx):
            _birth_or_merge(zones, tier, "resistance", max(bar["o"], bar["c"]),
                            bar["h"], bar["h"], bar["t"], a, merge_atr,
                            min_height_atr, max_height_atr)
        if all(lows[j] < lows[k] for k in wing_idx):
            _birth_or_merge(zones, tier, "support", bar["l"],
                            min(bar["o"], bar["c"]), bar["l"], bar["t"], a,
                            merge_atr, min_height_atr, max_height_atr)
        kept = []
        for role in ("resistance", "support"):
            same = [z for z in zones if z.role == role]
            same.sort(key=lambda z: (_distance(z, close), -z.touches, z.born))
            kept.extend(same[:keep_per_role])
        zones = sorted(kept, key=lambda z: (z.born, z.lo))
    return zones


def zone_dicts(zones: list) -> list:
    return [asdict(z) for z in zones]


def sr_verdict(zones: list, direction: str, entry: float, room: float,
               tiers=TIER_ORDER) -> tuple:
    """Report-only verdict behind an entry (spec 'Strategy hook'):
    1 = clear, 0 = a wall -- the entry sits INSIDE an opposing zone, or the
    nearest opposing zone's near edge is within `room` (absolute price)
    ahead of it. BUY looks at resistance above, SELL at support below.
    The tier returned is the STRONGEST wall (H4 > H1 > M15), '' when clear."""
    walls = set()
    for zn in zones:
        if zn.tier not in tiers:
            continue
        if direction == "BUY" and zn.role == "resistance":
            if zn.lo <= entry <= zn.hi or (zn.lo > entry and zn.lo - entry <= room):
                walls.add(zn.tier)
        elif direction == "SELL" and zn.role == "support":
            if zn.lo <= entry <= zn.hi or (zn.hi < entry and entry - zn.hi <= room):
                walls.add(zn.tier)
    if not walls:
        return 1, ""
    for t in TIER_ORDER:
        if t in walls:
            return 0, t
    return 0, sorted(walls)[0]


def zones_from_m5(m5_rows: list) -> list:
    """All three tiers from one M5 history (the candles table / the replay
    feed). Each tier sees at most its LOOKBACK bars of resampled history."""
    zones: list = []
    for tier in TIER_ORDER:
        rows = resample(m5_rows, TIER_SEC[tier])[-LOOKBACK[tier]:]
        zones.extend(build_zones(rows, tier))
    return zones


def zones_from_rings(candles_by_tf: dict) -> list:
    """All three tiers from the mini-app's per-TF ring buffers. The LAST
    row of each ring is the still-forming bar (the bridge re-sends it on
    every tick), so it is dropped before building."""
    zones: list = []
    for tier in TIER_ORDER:
        rows = list(candles_by_tf.get(tier) or [])[:-1][-LOOKBACK[tier]:]
        zones.extend(build_zones(rows, tier))
    return zones


def sr_zones_from_db(db, cache: dict) -> list:
    """Zones (as dicts) from the persistent M5 candles table, for surfaces
    whose own candle window is too short for an H4 lookback (the dashboard
    accumulator holds 2000 M5 bars ~ 7 days; H4 needs ~50). Memoised in
    `cache` ({"t": newest bar time, "zones": [...]}) so the 30 s dashboard
    poll re-reads one cheap row set and rebuilds only after a new M5 bar.
    FAIL-OPEN: [] on any error, or when the stored series is not M5."""
    try:
        series = db.latest_candle_series()
        if not series or series[1] != "M5":
            return []
        rows = db.get_candles(series[0], "M5", limit=M5_ROWS_FOR_ZONES)
        if not rows:
            return []
        newest = rows[-1]["t"]
        if cache.get("t") == newest and "zones" in cache:
            return cache["zones"]
        zones = zone_dicts(zones_from_m5(rows))
        cache["t"], cache["zones"] = newest, zones
        return zones
    except Exception:
        return []
