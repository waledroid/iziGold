# S/R Zones — Increment 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three-tier (H4/H1/M15) support/resistance zone engine, draw the zones as light-blue/light-yellow boxes on the MT5 chart, the mini-app, the dashboard and the trade screenshots, and log a report-only "S/R wall" verdict on every entry — with NO change to any trading decision.

**Architecture:** One pure zone algorithm implemented twice — `service/app/sr_zones.py` (serves the mini-app, dashboard, screenshot renders, and later the replay) and `mt5/Include/XauAssistant/SrZones.mqh` (serves the EA). Chart drawing is separate from the engine on both sides (`SrBoxes.mqh`; a pooled baseline-series drawer in the two HTML pages; rectangles in `render.py`). The verdict flows through the existing `rsi_agree`-style trade-event pipe as `sr_agree` + `sr_tier`.

**Tech Stack:** Python 3.11+ / FastAPI / pytest / matplotlib (already deps); Lightweight Charts v4.2.3 (vendored, unchanged); MQL5 compiled in MetaEditor on Windows (cannot be compiled here — verify by reading, then the owner compiles).

**Spec:** `docs/superpowers/specs/2026-09-05-sr-zones-design.md`

## Global Constraints

- All Python commands run from `service/` with the venv active: `cd service && source .venv/bin/activate`.
- Run the fast suite with `pytest` (slow tests excluded by `pyproject.toml`). The suite must stay green after every task.
- **No trading behaviour changes** in this increment. The verdict is report-only; nothing new may return `SIGNAL_NONE`, block an entry, or alter a stop.
- **Fail-open everywhere**: any failure in zone building or drawing degrades to "no zones / no box / verdict -1", never an exception out of an endpoint, a render, or the EA's `OnInit`/`OnTick`.
- `scripts/backtest.py` is **not touched** in this increment (its golden pins must stay byte-identical). The shared `resample` rule is re-implemented in `app/sr_zones.py` and pinned equal by a parity test.
- Colours (owner): support = light blue base `173,216,230`; resistance = light yellow base `255,245,157`; alpha by tier H4 `0.35`, H1 `0.22`, M15 `0.12`. MT5 pre-blended tints: support H4 `C'120,170,220'`, H1 `C'90,130,175'`, M15 `C'60,95,130'`; resistance H4 `C'235,225,140'`, H1 `C'185,175,105'`, M15 `C'130,122,75'`.
- Engine constants (spec): `PIVOT_WING=3`, min height `0.15` ATR, max height `0.60` ATR, merge `0.50` ATR, break `0.50` ATR, keep `3` per role per tier, lookbacks H4 `300`, H1 `400`, M15 `400`, ATR period `14`. ATR is **MT5's built-in iATR definition: a simple moving average of True Range** (see Task 1) — not Wilder.
- Every commit that touches `mt5/` or `service/app/` must update `.claude/agents/izi.md` in the same commit or the immediate follow-up (Task 10 does the consolidated write-up; earlier tasks may commit with a one-line izi placeholder note under a new heading `## S/R zones — three timeframe tiers as boxes (owner, 2026-09-05)` that Task 10 expands).
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01EjFKpE8N3V38qkUPcraDD3
  ```
- MQL5 files must be verified by careful reading; the owner compiles in MetaEditor (expect 0 errors, 0 warnings) and re-copies `mt5/` into the MT5 data folder. State this in the final report.

## File Structure

| File | Responsibility |
|---|---|
| `service/app/sr_zones.py` (new) | Pure engine: `sma_atr`, `resample`, `Zone`, `build_zones`, `sr_verdict`, `zones_from_m5`, `zones_from_rings`, `zone_dicts`, `sr_zones_from_db` |
| `service/tests/test_sr_zones.py` (new) | Engine unit tests + resample parity with the replay |
| `service/app/miniapp.py` | `/api/history` gains `sr_zones` from the H4/H1/M15 ring buffers |
| `service/app/static/miniapp.html` | Pool of 18 baseline series draws the boxes; history table gains the "S/R" column |
| `service/app/main.py` | `/api/overlays` gains `sr_zones` (DB M5 → resample); `/screenshot` caption query carries `sr_agree`/`sr_tier`; `/chart` snapshot passes zones |
| `service/app/static/dashboard.html` | Same pooled drawer for the dashboard chart |
| `service/app/render.py` | Rectangles for zones in both render functions |
| `service/app/trade_report.py` | Zones for the trade render; caption line `S/R:` |
| `service/app/models.py`, `db.py`, `reports.py` | `sr_agree` (int) + `sr_tier` (str) plumbing |
| `mt5/Include/XauAssistant/SrZones.mqh` (new) | `CSrZoneTier` + `CSrZoneSet` engine |
| `mt5/Include/XauAssistant/SrBoxes.mqh` (new) | `CSrBoxes` chart rectangles + labels |
| `mt5/Include/XauAssistant/Strategy.mqh` | `LastSrAgree()` / `LastSrTier()` virtuals |
| `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh` | Report-only verdict at the confirm |
| `mt5/Include/XauAssistant/UiSink.mqh`, `UiApi.mqh` | Post `sr_agree` + `sr_tier` |
| `mt5/Experts/XauAssistant.mq5` | Inputs, globals, wiring |
| `.claude/agents/izi.md`, the spec | Knowledge base + two spec clarifications |

---

### Task 1: Python zone engine — ATR, resample, pivots, boxes

**Files:**
- Create: `service/app/sr_zones.py`
- Create: `service/tests/test_sr_zones.py`

**Interfaces:**
- Consumes: nothing new. Candles are plain dicts `{"t","o","h","l","c","v"}` (the `db.get_candles` / mini-app ring shape).
- Produces:
  - `sma_atr(candles: list[dict], period: int = 14) -> list[float | None]`
  - `resample(candles: list[dict], sec: int, src_sec: int = 300) -> list[dict]`
  - `@dataclass Zone(tier: str, role: str, lo: float, hi: float, touches: int, born: int, last_touch: int)` with `role in ("support", "resistance")`
  - `build_zones(candles: list[dict], tier: str, *, wing=3, min_height_atr=0.15, max_height_atr=0.60, merge_atr=0.50, break_atr=0.50, keep_per_role=3, atr_period=14) -> list[Zone]` — zones sorted by `(born, lo)`.
  - Constants `TIER_SEC`, `TIER_ORDER`, `LOOKBACK`, `SRC_SEC`, `M5_ROWS_FOR_ZONES`.

**Algorithm (this is the parity contract with the MQL engine in Task 7 — do not deviate):**

Process closed bars in ascending order, `i` from `2*wing` to `n-1`. At bar `i` with `atr = sma_atr[i]` (skip the bar entirely if `atr` is `None` or `<= 0`):
1. **Death**: drop every resistance zone with `close[i] > hi + break_atr*atr` and every support zone with `close[i] < lo - break_atr*atr`.
2. **Pivot** at `j = i - wing`: swing high if `high[j]` is strictly greater than every `high[k]` for `k in [j-wing, j+wing], k != j`; swing low mirrored with lows. A swing high births/merges a resistance box `[max(o,c), h]` with pivot price `h`; a swing low a support box `[l, min(o,c)]` with pivot price `l`. Birth clamp: if height `< min_h` expand symmetrically about the pivot price; then if height `> max_h` trim toward the pivot (resistance: `lo = hi - max_h`; support: `hi = lo + max_h`). Merge: the nearest same-role zone whose edge distance to the pivot price is `<= merge_atr*atr` (0 if the pivot is inside; ties → the older `born`) absorbs it: `lo=min, hi=max`, re-trim to `max_h` toward the pivot side, `touches += 1`, `last_touch = t[j]`. Otherwise a new zone `touches=1, born=last_touch=t[j]`.
3. **Bound**: per role keep the `keep_per_role` zones with the smallest distance to `close[i]` (0 if it contains the close), tie-break by larger `touches`, then older `born`.
4. Sort zones by `(born, lo)` so the next bar's merge tie-break is deterministic.

**ATR definition** (MT5 `iATR`, file `Indicators/Examples/ATR.mq5`): `TR[0]=0`, `TR[i]=max(h[i],c[i-1])-min(l[i],c[i-1])`; `ATR[period]=mean(TR[1..period])`; `ATR[i]=ATR[i-1]+(TR[i]-TR[i-period])/period` — i.e. a plain SMA of TR over the trailing `period` bars, `None` before index `period`. This is what makes EA/Python parity exact.

- [ ] **Step 1: Write the failing tests**

```python
# service/tests/test_sr_zones.py
import pytest

from app.sr_zones import (LOOKBACK, TIER_SEC, Zone, build_zones, resample,
                          sma_atr)
from tests.test_backtest_golden import _load_bt


def bar(t, o, h, l, c, v=1.0):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def flat(n, t0=0, sec=900, px=100.0):
    """n quiet bars: body px..px, wick +-0.5 -> TR = 1.0 every bar."""
    return [bar(t0 + i * sec, px, px + 0.5, px - 0.5, px) for i in range(n)]


def with_spike(rows, idx, high, close=None):
    """Turn rows[idx] into a swing-high candidate: wick to `high`, body
    100 -> close (default 101)."""
    r = dict(rows[idx])
    r["h"] = high
    r["c"] = 101.0 if close is None else close
    rows[idx] = r
    return rows


# ---------------------------------------------------------------- ATR

def test_sma_atr_is_the_mt5_sma_of_true_range():
    rows = flat(20)
    rows[5] = bar(rows[5]["t"], 100, 103.0, 99.5, 100)     # TR = 3.5
    out = sma_atr(rows, 14)
    assert out[:14] == [None] * 14
    # first value = mean(TR[1..14]); TR is 1.0 except bar 5 (3.5) and
    # bar 6 (max(100.5, 100) - min(99.5, 100) = 1.0)
    assert out[14] == pytest.approx((13 * 1.0 + 3.5) / 14)
    # sliding window: bar 5 has left the window at index 19
    assert out[19] == pytest.approx(1.0)


def test_sma_atr_short_input_is_all_none():
    assert sma_atr(flat(5), 14) == [None] * 5
    assert sma_atr([], 14) == []


# ----------------------------------------------------------- resample

def test_resample_matches_the_replay_engine_rule():
    bt = _load_bt()
    assert bt.SRC_SEC == 300
    rows = [bar(300 * i + 600, 100 + i, 101 + i, 99 + i, 100.5 + i, 2.0)
            for i in range(50)]
    assert resample(rows, 900) == bt.resample(rows, 900)
    assert resample(rows, 3600) == bt.resample(rows, 3600)
    assert resample(rows, 300) == rows


def test_resample_drops_only_a_trailing_partial_bucket():
    rows = [bar(300 * i, 1, 2, 0, 1) for i in range(7)]   # 7 bars = 2 full M15 + 1 partial
    out = resample(rows, 900)
    assert [b["t"] for b in out] == [0, 900]
    assert out[0]["v"] == 3.0


# ------------------------------------------------------------- pivots

def test_flat_series_has_no_zones():
    assert build_zones(flat(60), "H1") == []


def test_swing_high_births_a_resistance_box_anchored_on_the_wick():
    rows = with_spike(flat(60), 30, 104.0)
    zones = build_zones(rows, "H1")
    assert len(zones) == 1
    z = zones[0]
    assert z.role == "resistance" and z.tier == "H1"
    assert z.hi == 104.0                       # wick extreme is the top edge
    assert z.born == rows[30]["t"] and z.touches == 1
    atr = sma_atr(rows, 14)[33]               # confirmed at bar 30 + wing
    assert z.hi - z.lo == pytest.approx(0.60 * atr)   # 3.0 raw -> trimmed to max


def test_swing_low_births_a_support_box_anchored_on_the_wick():
    rows = flat(60)
    r = dict(rows[30]); r["l"] = 96.0; r["c"] = 99.0; rows[30] = r
    zones = build_zones(rows, "H4")
    assert len(zones) == 1
    assert zones[0].role == "support" and zones[0].lo == 96.0


def test_pivot_needs_the_full_right_wing():
    rows = with_spike(flat(60), 58, 104.0)    # only 1 bar to the right
    assert build_zones(rows, "H1") == []


def test_tiny_wick_is_expanded_to_the_minimum_height():
    rows = flat(60)
    # still a strict swing high (100.6 > the 100.5 neighbours) but the wick
    # above the body top is only 0.05 -> below the 0.15 ATR floor
    r = dict(rows[30]); r["h"] = 100.6; r["c"] = 100.55; rows[30] = r
    zones = build_zones(rows, "M15")
    assert len(zones) == 1
    atr = sma_atr(rows, 14)[33]
    assert zones[0].hi - zones[0].lo == pytest.approx(0.15 * atr)
    assert (zones[0].hi + zones[0].lo) / 2 == pytest.approx(100.6)


# ---------------------------------------------------------- merge/bound

def test_second_touch_at_the_same_level_merges_and_counts():
    rows = with_spike(with_spike(flat(80), 30, 104.0), 50, 104.2)
    zones = build_zones(rows, "H1")
    assert len(zones) == 1
    assert zones[0].touches == 2
    assert zones[0].hi == 104.2
    assert zones[0].born == rows[30]["t"] and zones[0].last_touch == rows[50]["t"]


def test_far_pivot_births_a_separate_zone():
    rows = with_spike(with_spike(flat(80), 30, 104.0), 50, 112.0)
    zones = build_zones(rows, "H1")
    assert sorted(z.hi for z in zones) == [104.0, 112.0]


def test_close_through_kills_a_zone_but_a_wick_through_does_not():
    killed = with_spike(flat(80), 30, 104.0)
    for k in range(40, 80):                    # price closes well above the box
        killed[k] = bar(killed[k]["t"], 106, 106.5, 105.5, 106)
    assert build_zones(killed, "H1") == []

    wicked = with_spike(flat(80), 30, 104.0)
    wicked[45] = bar(wicked[45]["t"], 100, 104.1, 99.5, 100)   # wick into the box, close below
    zones = build_zones(wicked, "H1")
    assert len(zones) == 1 and zones[0].role == "resistance"


def test_bound_keeps_the_three_nearest_per_role():
    rows = flat(200)
    for k, hi in ((20, 104.0), (50, 112.0), (80, 120.0), (110, 128.0), (140, 136.0)):
        rows = with_spike(rows, k, hi)
    zones = build_zones(rows, "H1")
    res = [z for z in zones if z.role == "resistance"]
    assert len(res) == 3
    assert sorted(z.hi for z in res) == [104.0, 112.0, 120.0]   # nearest to close 100


def test_zones_are_sorted_by_born_then_lo():
    rows = with_spike(with_spike(flat(80), 30, 112.0), 50, 104.0)
    zones = build_zones(rows, "H1")
    assert [z.born for z in zones] == sorted(z.born for z in zones)


def test_constants_match_the_spec():
    assert TIER_SEC == {"M15": 900, "H1": 3600, "H4": 14400}
    assert LOOKBACK == {"M15": 400, "H1": 400, "H4": 300}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd service && pytest tests/test_sr_zones.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app.sr_zones'`.

- [ ] **Step 3: Write the engine**

```python
# service/app/sr_zones.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd service && pytest tests/test_sr_zones.py -q`
Expected: all pass. If `test_swing_high_births_a_resistance_box_anchored_on_the_wick` fails on the `0.60 * atr` assertion, check that the ATR used is the one at the CONFIRMATION bar (`i = j + wing`), not the pivot bar.

- [ ] **Step 5: Run the whole fast suite**

Run: `cd service && pytest -q`
Expected: green (the new file adds tests, nothing else changed).

- [ ] **Step 6: Commit**

```bash
git add service/app/sr_zones.py service/tests/test_sr_zones.py
git commit -m "feat(sr): S/R zone engine — MT5-SMA ATR, replay-parity resample, pivot boxes (Python twin)"
```
(Tests-only + a new pure module with no caller yet: behaviour-neutral, izi note comes with Task 3.)

---

### Task 2: Verdict + data-source helpers

**Files:**
- Modify: `service/app/sr_zones.py` (append)
- Modify: `service/tests/test_sr_zones.py` (append)

**Interfaces:**
- Consumes: `Zone`, `build_zones`, `resample`, `LOOKBACK`, `TIER_SEC` from Task 1.
- Produces:
  - `sr_verdict(zones, direction: str, entry: float, room: float, tiers=TIER_ORDER) -> tuple[int, str]` — `(1, "")` clear, `(0, "<tier>")` wall with the strongest tier; `room` is an absolute price distance (the caller multiplies its ATR).
  - `zones_from_m5(m5_rows: list[dict]) -> list[Zone]` — resamples to the three tiers.
  - `zones_from_rings(candles_by_tf: dict[str, Iterable[dict]]) -> list[Zone]` — mini-app ring buffers; drops the last (forming) bar of each tier.
  - `sr_zones_from_db(db, cache: dict) -> list[dict]` — reads `db.latest_candle_series()` + `db.get_candles(sym, "M5", limit=M5_ROWS_FOR_ZONES)`, returns `zone_dicts`, memoised in `cache` on the newest M5 bar time; `[]` on any failure or when the stored series is not M5.

- [ ] **Step 1: Append the failing tests**

```python
# append to service/tests/test_sr_zones.py
from app.sr_zones import (M5_ROWS_FOR_ZONES, sr_verdict, sr_zones_from_db,
                          zone_dicts, zones_from_m5, zones_from_rings)


def z(tier, role, lo, hi, touches=1, born=0):
    return Zone(tier, role, lo, hi, touches, born, born)


def test_verdict_buy_inside_or_just_below_resistance_is_a_wall():
    zones = [z("M15", "resistance", 101.0, 101.5), z("H4", "resistance", 101.2, 101.8)]
    assert sr_verdict(zones, "BUY", 101.1, room=0.5) == (0, "H4")   # inside M15, 0.1 below H4 -> strongest
    assert sr_verdict(zones, "BUY", 100.6, room=0.5) == (0, "M15")  # 0.4 below M15 lo; H4 is 0.6 away
    assert sr_verdict(zones, "BUY", 100.4, room=0.5) == (1, "")     # 0.6 / 0.8 below: both beyond room


def test_verdict_clear_when_room_is_enough():
    zones = [z("H1", "resistance", 102.0, 102.5)]
    assert sr_verdict(zones, "BUY", 100.0, room=1.0) == (1, "")
    assert sr_verdict(zones, "BUY", 101.0, room=1.0) == (0, "H1")   # exactly room away counts


def test_verdict_ignores_disabled_tiers_and_wrong_roles():
    zones = [z("H4", "resistance", 100.5, 101.0), z("H4", "support", 99.0, 99.5)]
    assert sr_verdict(zones, "BUY", 100.4, room=0.5, tiers=("H1", "M15")) == (1, "")
    assert sr_verdict(zones, "BUY", 100.4, room=0.5) == (0, "H4")
    assert sr_verdict(zones, "SELL", 100.4, room=0.5) == (1, "")   # support 0.9 below


def test_verdict_sell_mirror():
    zones = [z("H1", "support", 98.0, 98.5), z("M15", "support", 99.0, 99.4)]
    assert sr_verdict(zones, "SELL", 99.2, room=0.3) == (0, "M15")
    assert sr_verdict(zones, "SELL", 99.8, room=0.3) == (1, "")
    assert sr_verdict(zones, "SELL", 98.7, room=0.3) == (0, "H1")


def test_verdict_no_zones_is_clear():
    assert sr_verdict([], "BUY", 100.0, room=1.0) == (1, "")


def _m5_rows(n, t0=1_700_000_000 - (1_700_000_000 % 14400)):
    rows = []
    for i in range(n):
        px = 2000.0 + (i % 200) * 0.05
        rows.append(bar(t0 + 300 * i, px, px + 0.3, px - 0.3, px + 0.1, 1.0))
    return rows


def test_zones_from_m5_builds_every_tier():
    rows = _m5_rows(M5_ROWS_FOR_ZONES)
    # one big single-bar spike: every tier's bucket containing it is a
    # strict swing high (a 48-bar plateau would give EQUAL highs -> no pivot)
    r = dict(rows[8000]); r["h"] = 2100.0; rows[8000] = r
    zones = zones_from_m5(rows)
    tiers = {zn.tier for zn in zones}
    assert tiers == {"H4", "H1", "M15"}
    assert all(isinstance(d["born"], int) for d in zone_dicts(zones))


def test_zones_from_rings_drops_the_forming_bar():
    rows = with_spike(flat(60), 58, 104.0)          # spike 1 bar from the end
    rows.append(bar(rows[-1]["t"] + 900, 100, 100.5, 99.5, 100))   # forming bar
    rings = {"M15": rows, "H1": [], "H4": flat(10)}
    assert zones_from_rings(rings) == []            # spike's right wing never completes
    assert zones_from_rings({"M15": with_spike(flat(80), 30, 104.0) + [rows[-1]]})[0].tier == "M15"


class _FakeDb:
    def __init__(self, rows, series=("XAUUSD", "M5")):
        self.rows, self.series, self.calls = rows, series, 0

    def latest_candle_series(self):
        return self.series

    def get_candles(self, symbol, timeframe, start_ts=None, end_ts=None, limit=None):
        self.calls += 1
        assert timeframe == "M5" and limit == M5_ROWS_FOR_ZONES
        return self.rows[-limit:]


def test_sr_zones_from_db_memoises_on_the_newest_bar():
    db = _FakeDb(_m5_rows(M5_ROWS_FOR_ZONES))
    cache = {}
    a = sr_zones_from_db(db, cache)
    b = sr_zones_from_db(db, cache)
    assert a == b and db.calls == 2 and isinstance(a, list)   # one cheap re-read, one build
    assert cache["t"] == db.rows[-1]["t"]


def test_sr_zones_from_db_fails_open():
    assert sr_zones_from_db(_FakeDb([], series=None), {}) == []
    assert sr_zones_from_db(_FakeDb(_m5_rows(100), series=("XAUUSD", "M15")), {}) == []

    class Boom:
        def latest_candle_series(self):
            raise RuntimeError("db gone")
    assert sr_zones_from_db(Boom(), {}) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd service && pytest tests/test_sr_zones.py -q`
Expected: `ImportError: cannot import name 'sr_verdict'`.

- [ ] **Step 3: Append the implementation**

```python
# append to service/app/sr_zones.py

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
```

- [ ] **Step 4: Run the tests**

Run: `cd service && pytest tests/test_sr_zones.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add service/app/sr_zones.py service/tests/test_sr_zones.py
git commit -m "feat(sr): verdict + M5/ring/db zone sources — report-only wall test, fail-open"
```

---

### Task 3: Mini-app — `sr_zones` in `/api/history` and pooled box drawing

**Files:**
- Modify: `service/app/miniapp.py:203-208` (`history`) and `:217-247` (`_indicator_series` stays; add a sibling)
- Modify: `service/app/static/miniapp.html` (series pool near line 1560; `loadHistory` near line 827)
- Modify: `service/tests/test_miniapp.py` (append)

**Interfaces:**
- Consumes: `zones_from_rings`, `zone_dicts` (Task 2); `FeedState.candles` dict of deques keyed by TF.
- Produces: `/api/history` JSON gains `"sr_zones": [{"tier","role","lo","hi","touches","born","last_touch"}]` on EVERY tf tab; page globals `initSrPool()` / `setSrZones(zones, times)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to service/tests/test_miniapp.py

def _spiky_rows(n, sec, t0=1_700_000_000 - (1_700_000_000 % 14400), spike_at=30):
    rows = []
    for i in range(n):
        px = 4000.0
        h = px + 0.5 if i != spike_at else px + 30.0
        c = px if i != spike_at else px + 1.0
        rows.append({"t": t0 + sec * i, "o": px, "h": h, "l": px - 0.5, "c": c, "v": 1.0})
    return rows


def test_history_includes_sr_zones_from_the_tier_rings(client):
    _push(client, {"candles": {"H4": _spiky_rows(80, 14400),
                               "H1": _spiky_rows(80, 3600),
                               "M15": _spiky_rows(80, 900),
                               "M5": _spiky_rows(60, 300)}})
    body = client.get("/api/history", params={"tf": "M5"}).json()
    zones = body["sr_zones"]
    assert {z["tier"] for z in zones} == {"H4", "H1", "M15"}
    for z in zones:
        assert set(z) == {"tier", "role", "lo", "hi", "touches", "born", "last_touch"}
        assert z["role"] == "resistance" and z["hi"] == 4030.0
    # every tab carries the same zone list -- it is not a per-TF indicator
    assert client.get("/api/history", params={"tf": "H1"}).json()["sr_zones"] == zones


def test_history_sr_zones_empty_without_tier_data(client):
    _push(client, {"candles": {"M5": [_candle(1000, 4000.0), _candle(1300, 4001.0)]}})
    body = client.get("/api/history", params={"tf": "M5"}).json()
    assert body["sr_zones"] == []


def test_page_has_the_sr_pool(client):
    html = client.get("/").text
    assert "initSrPool" in html and "setSrZones" in html
    assert "173,216,230" in html and "255,245,157" in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd service && pytest tests/test_miniapp.py -k sr -q`
Expected: `KeyError: 'sr_zones'` ×2 and the assertion on `initSrPool`.

- [ ] **Step 3: Serve `sr_zones` from the rings**

In `service/app/miniapp.py`, add the import and a helper, and extend `history`:

```python
from app.sr_zones import zone_dicts, zones_from_rings
```

```python
def _sr_zone_payload(state_candles: dict) -> list:
    """S/R boxes for every tab (2026-09-05): built from the H4/H1/M15 ring
    buffers the bridge already pushes (500 bars each >= the tier
    lookbacks), never from the tab's own TF. Fail-open: [] on any error."""
    try:
        return zone_dicts(zones_from_rings(state_candles))
    except Exception:
        return []
```

```python
@app.get("/api/history")
def history(tf: str, _=Depends(require_viewer)):
    if tf not in TFS:
        raise HTTPException(status_code=400, detail=f"unknown tf {tf}")
    rows = list(state.candles[tf])
    return {"tf": tf, "candles": rows, **_indicator_series(rows, tf),
            "sr_zones": _sr_zone_payload(state.candles)}
```

- [ ] **Step 4: Draw the boxes in `miniapp.html`**

Add these declarations next to the other series globals (after `var lastBarT = null;`, around line 448):

```js
  // S/R zone boxes (owner 2026-09-05, spec 2026-09-05-sr-zones-design.md):
  // Lightweight Charts v4 has no rectangle primitive, so each box is a
  // BASELINE series filled between baseValue (= box lo) and value (= box
  // hi). A fixed POOL of 18 series is created in boot() BEFORE the candle
  // series so candles paint on top; slots are grouped weakest tier first
  // (M15, H1, H4) so a stronger tier's fill draws over a weaker one where
  // they overlap. Unused slots hold empty data. autoscaleInfoProvider
  // returns null so a far-away box can never stretch the price axis.
  var SR_TIERS = ["M15", "H1", "H4"];
  var SR_PER_TIER = 6;                       // 3 per role x 2 roles
  var SR_ALPHA = { H4: 0.35, H1: 0.22, M15: 0.12 };
  var SR_RGB = { support: "173,216,230", resistance: "255,245,157" };
  var SR_CLEAR = "rgba(0,0,0,0)";
  var srPool = {};                           // tier -> [baseline series]

  function srColor(zone) {
    return "rgba(" + SR_RGB[zone.role] + "," + SR_ALPHA[zone.tier] + ")";
  }

  function initSrPool() {
    SR_TIERS.forEach(function (tier) {
      srPool[tier] = [];
      for (var k = 0; k < SR_PER_TIER; k++) {
        srPool[tier].push(chart.addBaselineSeries({
          baseValue: { type: "price", price: 0 },
          topLineColor: SR_CLEAR, bottomLineColor: SR_CLEAR,
          topFillColor1: SR_CLEAR, topFillColor2: SR_CLEAR,
          bottomFillColor1: SR_CLEAR, bottomFillColor2: SR_CLEAR,
          lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: function () { return null; }
        }));
      }
    });
  }

  // zones: the /api/history sr_zones list; times: the loaded bars' times
  // (unix seconds, ascending -- toLwCandle passes c.t through). A box
  // spans from its `born` bar (or the first loaded bar when older than
  // the window) to the newest loaded bar.
  function setSrZones(zones, times) {
    var byTier = {};
    SR_TIERS.forEach(function (t) { byTier[t] = []; });
    (Array.isArray(zones) ? zones : []).forEach(function (z) {
      if (z && byTier[z.tier] && isNumeric(z.lo) && isNumeric(z.hi) && z.hi > z.lo) {
        byTier[z.tier].push(z);
      }
    });
    SR_TIERS.forEach(function (tier) {
      var pool = srPool[tier] || [];
      for (var k = 0; k < pool.length; k++) {
        var z = byTier[tier][k];
        if (!z) { try { pool[k].setData([]); } catch (e) {} continue; }
        var pts = [];
        for (var i = 0; i < times.length; i++) {
          if (times[i] >= z.born) pts.push({ time: times[i], value: z.hi });
        }
        if (!pts.length && times.length) {
          pts.push({ time: times[times.length - 1], value: z.hi });
        }
        var col = srColor(z);
        try {
          pool[k].applyOptions({ baseValue: { type: "price", price: z.lo },
                                 topFillColor1: col, topFillColor2: col });
          pool[k].setData(pts);
        } catch (e) {}
      }
    });
  }
```

In `boot()`, immediately BEFORE `series = chart.addCandlestickSeries({` (line ~1573), add:

```js
    initSrPool();   // 18 box slots, under the candles
```

In `loadHistory`, after `setIndicatorSeriesData(body, idxMap, times);` (line ~828), add:

```js
        setSrZones(body.sr_zones, times);
```

Note for the implementer: `times[i]` must be unix seconds matching `body.candles[idxMap[i]].t`. Confirm by reading `toLwCandle` (line ~620): it returns `{ time: c.t, ... }`. If it converts, compare against `body.candles[idxMap[i]].t` instead.

- [ ] **Step 5: Run the tests**

Run: `cd service && pytest tests/test_miniapp.py tests/test_sr_zones.py -q`
Expected: all pass.

- [ ] **Step 6: Eyeball it (if the service is running)**

Run: `cd service && source .venv/bin/activate && uvicorn app.miniapp:app --host 127.0.0.1 --port 9001` is NOT how the mini-app runs here — it is mounted by `app.main`. Skip the manual check if the service is not already up; the owner verifies in Telegram's Live Chart after deploy. Record that in the task report.

- [ ] **Step 7: izi placeholder + commit**

Append to `.claude/agents/izi.md` (before the first `## ` history heading, i.e. just above `## "Aggressive M15" request → confirm-clearance filter instead (2026-09-01)`):

```markdown
## S/R zones — three timeframe tiers as boxes (owner, 2026-09-05)

(Placeholder — expanded in the increment-1 wrap-up commit. Engine:
`service/app/sr_zones.py` + `mt5/Include/XauAssistant/SrZones.mqh`;
spec `docs/superpowers/specs/2026-09-05-sr-zones-design.md`. Mini-app
`/api/history` now carries `sr_zones`, drawn by a pool of 18 baseline
series in miniapp.html.)
```

```bash
git add service/app/miniapp.py service/app/static/miniapp.html service/tests/test_miniapp.py .claude/agents/izi.md
git commit -m "feat(miniapp): S/R zone boxes — light-blue support / light-yellow resistance, three tiers"
```

---

### Task 4: Dashboard — `sr_zones` in `/api/overlays` and the same drawer

**Files:**
- Modify: `service/app/main.py:1132-1160` (`ui_overlays`), lifespan state near `:404`
- Modify: `service/app/static/dashboard.html:1205-1215` (series) and `:1300-1306` (apply)
- Modify: `service/tests/test_overlays_m15.py` (append) or a new `tests/test_sr_dashboard.py`

**Interfaces:**
- Consumes: `sr_zones_from_db(db, cache)` (Task 2).
- Produces: `/api/overlays?strategy=...` JSON gains `"sr_zones": [...]` whenever it returns a non-empty payload; `app.state.sr_cache = {}`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_sr_dashboard.py
import importlib

from fastapi.testclient import TestClient

from app.sr_zones import M5_ROWS_FOR_ZONES


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "sr.db"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "shots"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app), main


def test_overlays_carry_sr_zones_from_the_candles_table(tmp_path, monkeypatch):
    client, main = _client(tmp_path, monkeypatch)
    with client:
        t0 = 1_700_000_000 - (1_700_000_000 % 14400)
        rows = []
        for i in range(M5_ROWS_FOR_ZONES):
            px = 4000.0
            spike = (i == 8000)          # single bar: distinct highs after resampling
            rows.append({"t": t0 + 300 * i, "o": px, "h": (px + 40.0) if spike else px + 0.5,
                         "l": px - 0.5, "c": (px + 1.0) if spike else px, "v": 1.0})
        main.app.state.db.upsert_candles("XAUUSD", "M5", rows)
        # the overlays endpoint needs an accumulator to key its cache on
        from app.models import Candle
        main.app.state.recent_candles = {"symbol": "XAUUSD", "timeframe": "M5",
                                         "candles": [Candle(**r) for r in rows[-300:]]}
        body = client.get("/api/overlays", params={"strategy": "halftrend_ema_v1"}).json()
        assert "sr_zones" in body
        assert {z["tier"] for z in body["sr_zones"]} == {"H4", "H1", "M15"}
        assert all(z["hi"] == 4040.0 for z in body["sr_zones"])


def test_overlays_sr_zones_fail_open_when_no_history(tmp_path, monkeypatch):
    client, main = _client(tmp_path, monkeypatch)
    with client:
        from app.models import Candle
        rows = [{"t": 900 + 300 * i, "o": 1, "h": 2, "l": 0, "c": 1, "v": 1} for i in range(10)]
        main.app.state.recent_candles = {"symbol": "XAUUSD", "timeframe": "M5",
                                         "candles": [Candle(**r) for r in rows]}
        body = client.get("/api/overlays", params={"strategy": "halftrend_ema_v1"}).json()
        assert body["sr_zones"] == []
```

Check `upsert_candles`'s expected row shape first (`app/db.py:492`): if it takes `Candle` objects or `bar_time` keys rather than `t`, adapt the test's rows to that shape (do not change `upsert_candles`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd service && pytest tests/test_sr_dashboard.py -q`
Expected: `KeyError: 'sr_zones'`.

- [ ] **Step 3: Add `sr_zones` to the overlays payload**

In `service/app/main.py`, import:

```python
from app.sr_zones import sr_zones_from_db
```

In the lifespan block where `app.state.overlays_cache = {}` is set (line ~404), add:

```python
    app.state.sr_cache = {}                   # {"t": newest M5 bar, "zones": [...]}
```

In `ui_overlays`, find the point where the builder's payload is returned (both the cache-hit and the freshly-built paths return the same dict object). Change so that the payload always carries zones, computed on the miss and stored with the payload:

```python
    hit = cache.get((key, strategy))
    if hit is not None:
        return hit
    closes = [c.c for c in rc["candles"]]
    payload = builder(rc["candles"], closes)
    # S/R boxes (2026-09-05): from the persistent M5 table, not the 2000-bar
    # accumulator (H4 needs ~50 days). Memoised on the newest M5 bar.
    payload["sr_zones"] = sr_zones_from_db(app.state.db, app.state.sr_cache)
    cache.clear() if len(cache) > 8 else None
    cache[(key, strategy)] = payload
    return payload
```

Read the existing body of `ui_overlays` (lines 1141-1160) first and keep its cache-bounding logic exactly as it is — only add the `payload["sr_zones"] = ...` line before the payload is cached/returned. Do not copy the sketch above verbatim if the real function differs; the contract is "the returned dict has `sr_zones`".

- [ ] **Step 4: Draw the boxes in `dashboard.html`**

After `S.bb_lower=chart.addLineSeries(...)` (line ~1213) and BEFORE `candleSeries=chart.addCandlestickSeries({`, add:

```js
  // S/R zone boxes (2026-09-05) -- same pooled-baseline trick as miniapp.html
  // (see initSrPool there): 18 slots under the candles, weakest tier first.
  S.sr={};
  const SR_TIERS=['M15','H1','H4'], SR_CLEAR='rgba(0,0,0,0)';
  SR_TIERS.forEach(t=>{ S.sr[t]=[]; for(let k=0;k<6;k++){ S.sr[t].push(chart.addBaselineSeries({
    baseValue:{type:'price',price:0}, topLineColor:SR_CLEAR, bottomLineColor:SR_CLEAR,
    topFillColor1:SR_CLEAR, topFillColor2:SR_CLEAR, bottomFillColor1:SR_CLEAR, bottomFillColor2:SR_CLEAR,
    lineWidth:1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false,
    autoscaleInfoProvider:()=>null })); } });
```

Add a top-level function next to `setLine` (line ~1225):

```js
function setSrZones(zones,times){
  const ALPHA={H4:0.35,H1:0.22,M15:0.12}, RGB={support:'173,216,230',resistance:'255,245,157'};
  const byTier={M15:[],H1:[],H4:[]};
  (Array.isArray(zones)?zones:[]).forEach(z=>{ if(z&&byTier[z.tier]&&Number.isFinite(z.lo)&&Number.isFinite(z.hi)&&z.hi>z.lo) byTier[z.tier].push(z); });
  Object.keys(byTier).forEach(tier=>{
    const pool=(S.sr&&S.sr[tier])||[];
    pool.forEach((s,k)=>{
      const z=byTier[tier][k];
      if(!z){ try{s.setData([]);}catch(e){} return; }
      let pts=times.filter(t=>t>=z.born).map(t=>({time:t,value:z.hi}));
      if(!pts.length&&times.length) pts=[{time:times[times.length-1],value:z.hi}];
      const col='rgba('+RGB[z.role]+','+ALPHA[z.tier]+')';
      try{ s.applyOptions({baseValue:{type:'price',price:z.lo},topFillColor1:col,topFillColor2:col}); s.setData(pts); }catch(e){}
    });
  });
}
```

In the refresh block, after `setHt(ov.halftrend);` (line ~1304), add:

```js
      setSrZones(ov.sr_zones, candles.map(c=>c.t));
```

- [ ] **Step 5: Run the tests**

Run: `cd service && pytest tests/test_sr_dashboard.py tests/test_overlays_m15.py tests/test_ui_endpoints.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add service/app/main.py service/app/static/dashboard.html service/tests/test_sr_dashboard.py
git commit -m "feat(dashboard): S/R zone boxes from the M5 candles table via /api/overlays"
```

---

### Task 5: Screenshot renders — zone rectangles

**Files:**
- Modify: `service/app/render.py` (both render functions + one helper)
- Modify: `service/app/trade_report.py:84-124` (`_report_trade_event`)
- Modify: `service/app/main.py:280-306` (`/chart` snapshot) and `:1283-1289` (task creation)
- Modify: `service/tests/test_render.py` (append)

**Interfaces:**
- Consumes: `sr_zones_from_db(db, cache)`; zone dicts.
- Produces: `render_trade_chart(candles, trade, out_path, trade_ema_len=55, sr_zones=None)`, `render_snapshot_chart(candles, out_path, positions=None, sr_zones=None)`; `_report_trade_event(..., sr_cache: dict | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to service/tests/test_render.py

def _zones():
    """Two boxes inside the rendered 100-bar window, one far outside it
    (must be skipped so it cannot stretch the y axis)."""
    w = trend_candles(200)[-100:]
    lo, hi = min(c.l for c in w), max(c.h for c in w)
    span = hi - lo
    return [{"tier": "H4", "role": "resistance", "lo": lo + 0.70 * span, "hi": lo + 0.74 * span,
             "touches": 3, "born": 0, "last_touch": 0},
            {"tier": "M15", "role": "support", "lo": lo + 0.20 * span, "hi": lo + 0.23 * span,
             "touches": 1, "born": 0, "last_touch": 0},
            {"tier": "H1", "role": "resistance", "lo": hi + 50 * span, "hi": hi + 51 * span,
             "touches": 1, "born": 0, "last_touch": 0}]


def test_render_trade_chart_accepts_zones_and_keeps_the_axis_tight(tmp_path):
    from app import render as r
    seen = {}
    real = r._plot_sr_zones

    def spy(ax, window, zones):
        real(ax, window, zones)
        seen["ylim"] = ax.get_ylim()
        seen["patches"] = len(ax.patches)
    r._plot_sr_zones = spy
    try:
        ok = render_trade_chart(trend_candles(200), _trade(), str(tmp_path / "z.png"),
                                sr_zones=_zones())
    finally:
        r._plot_sr_zones = real
    assert ok is True
    assert seen["patches"] == 2            # the far zone is off-window and skipped
    w = trend_candles(200)[-100:]
    assert seen["ylim"][1] < max(c.h for c in w) * 1.5


def test_render_snapshot_chart_accepts_zones(tmp_path):
    from app.render import render_snapshot_chart
    assert render_snapshot_chart(trend_candles(200), str(tmp_path / "s.png"),
                                 sr_zones=_zones()) is True


def test_render_without_zones_is_unchanged(tmp_path):
    assert render_trade_chart(trend_candles(200), _trade(), str(tmp_path / "n.png")) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd service && pytest tests/test_render.py -k zones -q`
Expected: `TypeError: ... unexpected keyword argument 'sr_zones'` / `AttributeError: _plot_sr_zones`.

- [ ] **Step 3: Implement the rectangle drawer and thread it through**

In `service/app/render.py`, add near `_plot_ema`:

```python
SR_RGB = {"support": (173 / 255, 216 / 255, 230 / 255),
          "resistance": (255 / 255, 245 / 255, 157 / 255)}
SR_ALPHA = {"H4": 0.35, "H1": 0.22, "M15": 0.12}


def _plot_sr_zones(ax, window, zones):
    """Light-blue support / light-yellow resistance boxes (2026-09-05),
    behind everything else. Only zones that overlap the window's own price
    range are drawn -- an off-screen box must never widen the y axis of a
    100-bar snapshot. x spans from the zone's `born` bar (or the window's
    first bar) to the right edge; label at the right edge."""
    if not zones or not window:
        return
    lo_px = min(c.l for c in window)
    hi_px = max(c.h for c in window)
    pad = (hi_px - lo_px) * 0.05
    n = len(window)
    for z in zones:
        try:
            lo, hi = float(z["lo"]), float(z["hi"])
            if hi < lo_px - pad or lo > hi_px + pad:
                continue
            rgb = SR_RGB.get(z.get("role"))
            alpha = SR_ALPHA.get(z.get("tier"))
            if rgb is None or alpha is None:
                continue
            x0 = next((i for i, c in enumerate(window) if c.t >= int(z.get("born", 0))), 0)
            ax.add_patch(Rectangle((x0 - 0.5, lo), n - x0, hi - lo,
                                   facecolor=rgb, alpha=alpha, edgecolor="none",
                                   zorder=0.3))
            tag = ("R " if z.get("role") == "resistance" else "S ") + \
                  f"{z.get('tier')} x{int(z.get('touches', 1))}"
            ax.text(n - 1, hi if z.get("role") == "resistance" else lo, tag,
                    fontsize=6, ha="right", va="bottom", color=rgb, alpha=0.9,
                    zorder=6)
        except Exception:
            continue
```

`Rectangle` is already imported in render.py (used for the risk box) — confirm, else `from matplotlib.patches import Rectangle`.

Change the signatures and call the helper right after the axes exist and BEFORE the HalfTrend/EMA overlays (so the boxes sit underneath):

```python
def render_trade_chart(candles, trade: dict, out_path: str,
                       trade_ema_len: int = 55, sr_zones=None) -> bool:
    ...
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)
        _plot_sr_zones(ax, window, sr_zones)
        _plot_halftrend(...)
```

```python
def render_snapshot_chart(candles, out_path: str, positions=None, sr_zones=None) -> bool:
    ...
        ax = fig.add_subplot(111)
        _plot_sr_zones(ax, window, sr_zones)
        _plot_halftrend(...)
```

Also the trade render's docstring: add one line "sr_zones: zone dicts from app.sr_zones; None/[] draws nothing."

In `service/app/trade_report.py`:

```python
from app.sr_zones import sr_zones_from_db
```

Add the keyword `sr_cache=None` to `_report_trade_event` and, inside the `should_render and last_candles` branch just before `ok = await asyncio.to_thread(render_trade_chart, ...)`:

```python
            zones = []
            if sr_cache is not None:
                try:
                    zones = await asyncio.to_thread(sr_zones_from_db, db, sr_cache)
                except Exception:
                    zones = []
            ok = await asyncio.to_thread(
                render_trade_chart, render_candles, trade_dict,
                str(render_path), ema_len, zones)
```

In `service/app/main.py` at the task creation (line ~1283) add `sr_cache=app.state.sr_cache,` to the `_report_trade_event(` call. In the `/chart` snapshot path (line ~304):

```python
    zones = await asyncio.to_thread(sr_zones_from_db, app.state.db, app.state.sr_cache)
    ok = await asyncio.to_thread(render_snapshot_chart, candles, out, positions, zones)
```

- [ ] **Step 4: Run the tests**

Run: `cd service && pytest tests/test_render.py tests/test_trades.py tests/test_chart_cmd.py -q`
Expected: all pass. If `test_chart_cmd.py` mocks `render_snapshot_chart` with a 3-argument signature, update that mock to accept `*args`.

- [ ] **Step 5: Commit**

```bash
git add service/app/render.py service/app/trade_report.py service/app/main.py service/tests/test_render.py
git commit -m "feat(render): S/R zone boxes on trade renders and /chart snapshots"
```

---

### Task 6: `sr_agree` / `sr_tier` plumbing — models, DB, caption, report, mini-app column

**Files:**
- Modify: `service/app/models.py:139-140`, `service/app/db.py:297-303, 583-607`, `service/app/main.py:1308-1316`, `service/app/trade_report.py:169-197`, `service/app/reports.py:58-63, 165-186, 296-310`
- Modify: `service/app/static/miniapp.html:1265, 1296-1300`
- Modify: `service/tests/test_trades.py`, `tests/test_basket_twins.py:134,150`, `tests/test_miniapp.py:421-423`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure plumbing).
- Produces: `TradeEventRequest.sr_agree: int = -1`, `TradeEventRequest.sr_tier: str = ""`; DB columns `sr_agree INTEGER DEFAULT -1`, `sr_tier TEXT DEFAULT ''`; `_trade_caption(..., sr_agree=-1, sr_tier="")`; basket flags `"sr": bool|None`, `"sr_tier": str`.

- [ ] **Step 1: Write the failing tests**

```python
# append to service/tests/test_trades.py

def test_sr_agree_and_tier_stored_and_exposed(client):
    client.post("/trade-event", json=_trade(ticket=12350, sr_agree=0, sr_tier="H4"))
    trades = client.get("/api/trades").json()["trades"]
    assert trades[0]["sr_agree"] == 0 and trades[0]["sr_tier"] == "H4"
    client.post("/trade-event", json=_trade(ticket=12351))
    trades = client.get("/api/trades").json()["trades"]
    assert trades[0]["sr_agree"] == -1 and trades[0]["sr_tier"] == ""


def test_caption_reports_the_sr_wall():
    from app.trade_report import _trade_caption
    clear = _trade_caption("open", "BUY", 0.1, 4500.0, "signal BUY", 0.0,
                           -1, -1, -1, -1, 1, "")
    wall = _trade_caption("open", "SELL", 0.1, 4500.0, "signal SELL", 0.0,
                          -1, -1, -1, -1, 0, "H1")
    none = _trade_caption("open", "BUY", 0.1, 4500.0, "signal BUY", 0.0)
    assert "S/R: clear ✅" in clear
    assert "S/R: WALL H1 ⚠️" in wall
    assert "S/R" not in none and "S/R" not in _trade_caption(
        "close", "BUY", 0.1, 4500.0, "stop-loss", -3.0, -1, -1, -1, -1, 0, "H4")
```

Then update the pinned key sets:
- `tests/test_basket_twins.py:134` → `entry_keys = {"ts", "price", "lots", "htf_agree", "ema200_agree", "news_blackout", "rsi_agree", "sr_agree", "sr_tier"}` and line 150's expected difference set gains `"sr_agree", "sr_tier"`.
- `tests/test_miniapp.py:421-423` → both expected entry dicts gain `"sr_agree": None, "sr_tier": None`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd service && pytest tests/test_trades.py -k "sr_" tests/test_basket_twins.py tests/test_miniapp.py -q`
Expected: failures on the new keys/fields.

- [ ] **Step 3: Implement**

`models.py`, after `rsi_agree`:

```python
    # S/R zone verdict at the confirm bar (owner 2026-09-05, report-only):
    # 1 = clear room ahead, 0 = the entry sat inside or within SrRoomAtr of
    # an opposing zone ("wall"), -1 = unknown. sr_tier names the strongest
    # tier that judged 0 ("H4"/"H1"/"M15"), "" otherwise.
    sr_agree: int = -1
    sr_tier: str = ""
```

`db.py` migrations, after the `rsi_agree` block:

```python
        if "sr_agree" not in trade_cols:
            # S/R zone verdict (2026-09-05, report-only): 1 clear / 0 wall /
            # -1 unknown; sr_tier = strongest tier behind a 0, '' otherwise.
            for ddl in ("ALTER TABLE trades ADD COLUMN sr_agree INTEGER DEFAULT -1",
                        "ALTER TABLE trades ADD COLUMN sr_tier TEXT DEFAULT ''"):
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
```

`insert_trade`: add `sr_agree, sr_tier` to the column list, two more `?`, and `int(ev.get("sr_agree", -1)), str(ev.get("sr_tier", "") or "")` to the tuple. `recent_trades`: append `"sr_agree", "sr_tier"` to `cols`.

`main.py` `/screenshot` query: append `", COALESCE(sr_agree, -1), COALESCE(sr_tier, '')"` to the SELECT list (before ` FROM trades`).

`trade_report.py` `_trade_caption` signature gains `sr_agree: int = -1, sr_tier: str = ""`; after the RSI block:

```python
        if sr_agree in (0, 1):
            # Report-only S/R zone verdict (2026-09-05): "WALL <tier>" =
            # the entry sat inside / within SrRoomAtr of an opposing box.
            caption += ("\nS/R: clear ✅" if sr_agree == 1
                        else f"\nS/R: WALL {sr_tier or '?'} ⚠️")
```

`reports.py`: in `_group_baskets` the entry dict gains `"sr_agree": r.get("sr_agree"), "sr_tier": r.get("sr_tier")`. In `_report_day`'s SQL builder add `has_sr = "sr_agree" in cols` and two more selected columns (`sr_agree` / `-1`, `sr_tier` / `''`), extend the row dict with `"sr_agree": r[15], "sr_tier": r[16]`. In the per-basket output dict after `"rsi": _flag(entries, "rsi_agree"),` add:

```python
            # S/R zone verdict at entry (2026-09-05, report-only): True =
            # clear, False = wall, None unknown; sr_tier = the wall's tier.
            "sr": _flag(entries, "sr_agree"),
            "sr_tier": _first_str(entries, "sr_tier"),
```

and define next to `_flag`:

```python
def _first_str(entries, key):
    for e in sorted(entries, key=lambda x: x.get("ts") or 0):
        v = e.get(key)
        if v:
            return str(v)
    return ""
```

`miniapp.html` `renderDay`: insert `"S/R"` after `"RSI"` in the header list (line 1265) and, right after `tr.appendChild(rsiCell);`, add:

```js
      // S/R zone verdict at entry (report-only, 2026-09-05): Yes = clear
      // room ahead; "No H4" = the entry sat inside / within one ATR of an
      // opposing H4 box; – = not recorded.
      var srCell = el("td", r.sr === true ? "dir-buy" : (r.sr === false ? "dir-sell" : "muted"),
                      r.sr === true ? "Yes" : (r.sr === false ? ("No " + (r.sr_tier || "")).trim() : "–"));
      srCell.title = r.sr === true ? "no support/resistance wall within reach of this entry"
                   : (r.sr === false ? "entered into a " + (r.sr_tier || "") + " support/resistance zone (report only — nothing blocked)"
                                     : "not recorded for this trade");
      tr.appendChild(srCell);
```

- [ ] **Step 4: Run the whole suite**

Run: `cd service && pytest -q`
Expected: green. Watch `test_models.py` and `test_ui_endpoints.py` for pinned column lists and extend them if they enumerate trade columns.

- [ ] **Step 5: Commit**

```bash
git add service/app/models.py service/app/db.py service/app/main.py service/app/trade_report.py service/app/reports.py service/app/static/miniapp.html service/tests/test_trades.py service/tests/test_basket_twins.py service/tests/test_miniapp.py
git commit -m "feat(trades): sr_agree + sr_tier — report-only S/R verdict on every entry, history column, caption"
```

---

### Task 7: MQL5 zone engine — `SrZones.mqh`

**Files:**
- Create: `mt5/Include/XauAssistant/SrZones.mqh`

**Interfaces:**
- Consumes: MQL5 series functions on the tier timeframe; `iATR(_Symbol, tf, 14)`.
- Produces:
  - `struct SrZone { int role; double lo; double hi; int touches; datetime born; datetime lastTouch; }` with `SR_ROLE_RES = 0`, `SR_ROLE_SUP = 1`.
  - `struct SrParams { int wing; double minHeightAtr; double maxHeightAtr; double mergeAtr; double breakAtr; int keepPerRole; }`.
  - `class CSrZoneTier { void Init(string name, ENUM_TIMEFRAMES tf, int lookback, const SrParams &p); bool Update(); int Count() const; void Get(int i, SrZone &out) const; string Name() const; bool Seeded() const; }`.
  - `class CSrZoneSet { void Init(const SrParams &p, int lookbackH4, int lookbackH1, int lookbackM15); bool Update(); int Version() const; bool Seeded() const; int TierCount() const; CSrZoneTier *Tier(int i); int Verdict(int dir, double entry, double room, string tiers, string &tierOut); }`.
  - Tier iteration order from `Tier(i)`: `i=0` M15, `1` H1, `2` H4 (weakest first — the drawing order).

The algorithm and processing order are the Task 1 contract. MQL reads bars by SHIFT (0 = forming), so "bar `i` confirms pivot `i - wing`" becomes "closed bar at shift `s` confirms the pivot at shift `s + wing`, whose left wing reaches shift `s + 2*wing`".

- [ ] **Step 1: Write the file**

```cpp
// SrZones.mqh — support/resistance ZONE engine, three timeframe tiers
// (docs/superpowers/specs/2026-09-05-sr-zones-design.md). Pure state
// machine over closed bars of ONE timeframe per tier: candles in, zones
// out, no chart objects, no trade state. Twin of service/app/sr_zones.py —
// the processing order per closed bar is a CONTRACT shared with it:
//   1. death by close-through (close beyond the box by breakAtr x ATR)
//   2. the pivot confirmed by this bar (wing bars to its right) births a
//      box or merges into the nearest same-role box within mergeAtr x ATR
//   3. keep the keepPerRole nearest boxes per role (distance to close,
//      then more touches, then older)
//   4. sort by (born, lo) so merge tie-breaks are deterministic
// ATR is the terminal's own iATR(14) (an SMA of True Range), read at the
// confirming bar's shift, which the Python twin reproduces exactly.
// FAIL-OPEN: a tier that cannot read its bars/ATR simply stays empty.
#ifndef XAU_SRZONES_MQH
#define XAU_SRZONES_MQH

#define SR_ROLE_RES 0
#define SR_ROLE_SUP 1
#define SR_MAX_ZONES 64

struct SrZone
  {
   int      role;
   double   lo;
   double   hi;
   int      touches;
   datetime born;
   datetime lastTouch;
  };

struct SrParams
  {
   int    wing;
   double minHeightAtr;
   double maxHeightAtr;
   double mergeAtr;
   double breakAtr;
   int    keepPerRole;
  };

class CSrZoneTier
  {
private:
   string          m_name;
   ENUM_TIMEFRAMES m_tf;
   int             m_lookback;
   SrParams        m_p;
   int             m_atrHandle;
   datetime        m_lastBar;    // newest CLOSED tier bar already processed
   bool            m_seeded;
   SrZone          m_zones[SR_MAX_ZONES];
   int             m_count;

   double Distance(const SrZone &z, double price) const
     {
      if(price >= z.lo && price <= z.hi) return 0.0;
      return MathMin(MathAbs(price - z.lo), MathAbs(price - z.hi));
     }

   void Clamp(int role, double &lo, double &hi, double pivot, double atr) const
     {
      double minH = m_p.minHeightAtr * atr, maxH = m_p.maxHeightAtr * atr;
      if(hi - lo < minH) { lo = pivot - minH / 2.0; hi = pivot + minH / 2.0; }
      if(hi - lo > maxH)
        {
         if(role == SR_ROLE_RES) lo = hi - maxH;
         else                    hi = lo + maxH;
        }
     }

   void Remove(int idx)
     {
      for(int k = idx; k < m_count - 1; k++) m_zones[k] = m_zones[k + 1];
      m_count--;
     }

   void BirthOrMerge(int role, double lo, double hi, double pivot, datetime t, double atr)
     {
      Clamp(role, lo, hi, pivot, atr);
      double mergePx = m_p.mergeAtr * atr;
      int best = -1; double bestD = 0.0;
      for(int k = 0; k < m_count; k++)          // sorted by born: ties -> oldest
        {
         if(m_zones[k].role != role) continue;
         double d = Distance(m_zones[k], pivot);
         if(d <= mergePx && (best < 0 || d < bestD)) { best = k; bestD = d; }
        }
      if(best < 0)
        {
         if(m_count >= SR_MAX_ZONES) return;     // cannot happen after Bound(), guard anyway
         m_zones[m_count].role = role; m_zones[m_count].lo = lo; m_zones[m_count].hi = hi;
         m_zones[m_count].touches = 1; m_zones[m_count].born = t; m_zones[m_count].lastTouch = t;
         m_count++;
         return;
        }
      double nlo = MathMin(m_zones[best].lo, lo), nhi = MathMax(m_zones[best].hi, hi);
      double maxH = m_p.maxHeightAtr * atr;
      if(nhi - nlo > maxH)
        {
         if(role == SR_ROLE_RES) nlo = nhi - maxH;
         else                    nhi = nlo + maxH;
        }
      m_zones[best].lo = nlo; m_zones[best].hi = nhi;
      m_zones[best].touches++; m_zones[best].lastTouch = t;
     }

   // Strict fractal at shift p: higher than every high within wing bars
   // on BOTH sides (the right side p-1 .. p-wing must all be closed, i.e.
   // p - wing >= 1 -- the caller guarantees it).
   bool IsSwingHigh(int p) const
     {
      double h = iHigh(_Symbol, m_tf, p);
      for(int k = p - m_p.wing; k <= p + m_p.wing; k++)
        {
         if(k == p) continue;
         if(!(h > iHigh(_Symbol, m_tf, k))) return false;
        }
      return true;
     }

   bool IsSwingLow(int p) const
     {
      double l = iLow(_Symbol, m_tf, p);
      for(int k = p - m_p.wing; k <= p + m_p.wing; k++)
        {
         if(k == p) continue;
         if(!(l < iLow(_Symbol, m_tf, k))) return false;
        }
      return true;
     }

   // Keep the keepPerRole nearest per role: distance to close asc, touches
   // desc, born asc. Selection by repeated extraction keeps it simple.
   void Bound(double close)
     {
      SrZone kept[SR_MAX_ZONES]; int nk = 0;
      for(int role = SR_ROLE_RES; role <= SR_ROLE_SUP; role++)
        {
         bool used[SR_MAX_ZONES]; ArrayInitialize(used, false);
         for(int pick = 0; pick < m_p.keepPerRole; pick++)
           {
            int best = -1;
            for(int k = 0; k < m_count; k++)
              {
               if(used[k] || m_zones[k].role != role) continue;
               if(best < 0) { best = k; continue; }
               double dk = Distance(m_zones[k], close), db = Distance(m_zones[best], close);
               if(dk < db || (dk == db && (m_zones[k].touches > m_zones[best].touches
                  || (m_zones[k].touches == m_zones[best].touches && m_zones[k].born < m_zones[best].born))))
                  best = k;
              }
            if(best < 0) break;
            used[best] = true;
            kept[nk++] = m_zones[best];
           }
        }
      for(int k = 0; k < nk; k++) m_zones[k] = kept[k];
      m_count = nk;
      SortByBorn();
     }

   void SortByBorn()
     {
      for(int a = 1; a < m_count; a++)          // insertion sort, n <= 6
        {
         SrZone v = m_zones[a]; int b = a - 1;
         while(b >= 0 && (m_zones[b].born > v.born
               || (m_zones[b].born == v.born && m_zones[b].lo > v.lo)))
           { m_zones[b + 1] = m_zones[b]; b--; }
         m_zones[b + 1] = v;
        }
     }

   // One CLOSED bar at shift s (the confirming bar). Pivot candidate at
   // s + wing; its left wing reaches s + 2*wing.
   void ProcessBar(int s)
     {
      double ab[];
      if(CopyBuffer(m_atrHandle, 0, s, 1, ab) != 1) return;
      double atr = ab[0];
      if(atr <= 0 || atr == EMPTY_VALUE) return;
      double close = iClose(_Symbol, m_tf, s);
      for(int k = m_count - 1; k >= 0; k--)
        {
         bool dead = (m_zones[k].role == SR_ROLE_RES && close > m_zones[k].hi + m_p.breakAtr * atr)
                  || (m_zones[k].role == SR_ROLE_SUP && close < m_zones[k].lo - m_p.breakAtr * atr);
         if(dead) Remove(k);
        }
      int p = s + m_p.wing;
      datetime t = iTime(_Symbol, m_tf, p);
      double o = iOpen(_Symbol, m_tf, p), c = iClose(_Symbol, m_tf, p);
      double h = iHigh(_Symbol, m_tf, p), l = iLow(_Symbol, m_tf, p);
      if(IsSwingHigh(p)) BirthOrMerge(SR_ROLE_RES, MathMax(o, c), h, h, t, atr);
      if(IsSwingLow(p))  BirthOrMerge(SR_ROLE_SUP, l, MathMin(o, c), l, t, atr);
      Bound(close);
     }

public:
   CSrZoneTier() : m_name(""), m_tf(PERIOD_CURRENT), m_lookback(0),
                   m_atrHandle(INVALID_HANDLE), m_lastBar(0), m_seeded(false), m_count(0) {}

   void Init(string name, ENUM_TIMEFRAMES tf, int lookback, const SrParams &p)
     {
      m_name = name; m_tf = tf; m_lookback = lookback; m_p = p;
      m_atrHandle = iATR(_Symbol, m_tf, 14);
      m_lastBar = 0; m_seeded = false; m_count = 0;
     }

   // Processes every tier bar closed since the last call (all of the
   // lookback on the first call). True when the zone list may have changed.
   bool Update()
     {
      if(m_atrHandle == INVALID_HANDLE || m_p.wing < 1) return false;
      datetime closed = iTime(_Symbol, m_tf, 1);
      if(closed == 0 || closed == m_lastBar) return false;
      bool changed = false;
      if(!m_seeded)
        {
         int avail = Bars(_Symbol, m_tf) - 2 * m_p.wing - 2;
         int from = MathMin(m_lookback, MathMax(avail, 1));
         if(avail < m_lookback)
            PrintFormat("SrZones %s: seeding from %d bars (wanted %d)", m_name, avail, m_lookback);
         for(int s = from; s >= 1; s--) ProcessBar(s);
         m_seeded = true; changed = true;
        }
      else
        {
         int last = iBarShift(_Symbol, m_tf, m_lastBar, true);
         if(last < 1) last = 2;                 // history reloaded under us: process the newest bar only
         for(int s = last - 1; s >= 1; s--) { ProcessBar(s); changed = true; }
        }
      m_lastBar = closed;
      return changed;
     }

   string Name() const   { return m_name; }
   bool   Seeded() const { return m_seeded; }
   int    Count() const  { return m_count; }
   void   Get(int i, SrZone &out) const { if(i >= 0 && i < m_count) out = m_zones[i]; }
  };

class CSrZoneSet
  {
private:
   CSrZoneTier m_tiers[3];      // 0 = M15, 1 = H1, 2 = H4 (weakest first)
   int         m_version;

public:
   CSrZoneSet() : m_version(0) {}

   void Init(const SrParams &p, int lookbackH4, int lookbackH1, int lookbackM15)
     {
      m_tiers[0].Init("M15", PERIOD_M15, lookbackM15, p);
      m_tiers[1].Init("H1",  PERIOD_H1,  lookbackH1,  p);
      m_tiers[2].Init("H4",  PERIOD_H4,  lookbackH4,  p);
      m_version = 0;
     }

   bool Update()
     {
      bool changed = false;
      for(int i = 0; i < 3; i++) if(m_tiers[i].Update()) changed = true;
      if(changed) m_version++;
      return changed;
     }

   int  Version() const   { return m_version; }
   int  TierCount() const { return 3; }
   CSrZoneTier *Tier(int i) { return (i >= 0 && i < 3) ? GetPointer(m_tiers[i]) : NULL; }
   bool Seeded() const
     {
      for(int i = 0; i < 3; i++) if(m_tiers[i].Seeded()) return true;
      return false;
     }

   // 1 clear / 0 wall / -1 not evaluated (no tier seeded yet). BUY looks
   // at resistance above `entry`, SELL at support below; a wall is a box
   // containing the entry or whose near edge is within `room` (price
   // units) ahead. tiers = "H4,H1,M15"-style list of tiers to honour;
   // tierOut = the STRONGEST wall's name (H4 > H1 > M15) or "".
   int Verdict(int dir, double entry, double room, string tiers, string &tierOut)
     {
      tierOut = "";
      if(!Seeded()) return -1;
      for(int i = 2; i >= 0; i--)               // strongest first
        {
         if(StringFind(tiers, m_tiers[i].Name()) < 0) continue;
         for(int k = 0; k < m_tiers[i].Count(); k++)
           {
            SrZone z; m_tiers[i].Get(k, z);
            bool wall = false;
            if(dir == SIGNAL_BUY && z.role == SR_ROLE_RES)
               wall = (entry >= z.lo && entry <= z.hi) || (z.lo > entry && z.lo - entry <= room);
            else if(dir == SIGNAL_SELL && z.role == SR_ROLE_SUP)
               wall = (entry >= z.lo && entry <= z.hi) || (z.hi < entry && entry - z.hi <= room);
            if(wall) { tierOut = m_tiers[i].Name(); return 0; }
           }
        }
      return 1;
     }
  };
#endif
```

`SIGNAL_BUY`/`SIGNAL_SELL` come from `Strategy.mqh`; add `#include <XauAssistant/Strategy.mqh>` at the top of this file (it is include-guarded).

- [ ] **Step 2: Verify by reading against the Task 1 contract**

Walk one synthetic case by hand (the `test_second_touch_at_the_same_level_merges_and_counts` shape): flat bars, spike at bar 30 (h=104, c=101), spike at bar 50 (h=104.2). In MQL terms with 80 closed bars: the bar-30 pivot is confirmed at shift `80-33 = 47`... rather than shift math, confirm each step maps 1:1: death → pivot(s+wing) → BirthOrMerge → Bound → SortByBorn. Check especially:
- `IsSwingHigh(p)` reads `p+wing` (older, always exists during seeding because `from <= Bars - 2*wing - 2`) and `p-wing = s` (the confirming closed bar). ✓
- `Clamp` receives the pivot price (`h` for resistance, `l` for support). ✓ (Python `_clamp(role, lo, hi, pivot, ...)`)
- Merge tie → oldest because the array is sorted by born and the strict `<` keeps the first candidate. ✓
- `Bound` ordering: distance asc, touches desc, born asc — Python `(_distance, -touches, born)`. ✓
- `EMPTY_VALUE` ATR guard, `CopyBuffer` failure → the bar is skipped, same as Python's `None` skip. ✓

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/SrZones.mqh
git commit -m "feat(ea): SrZones.mqh — three-tier S/R zone engine, twin of app/sr_zones.py (uncompiled: MetaEditor check pending)"
```

---

### Task 8: MQL5 chart boxes — `SrBoxes.mqh` + EA wiring

**Files:**
- Create: `mt5/Include/XauAssistant/SrBoxes.mqh`
- Modify: `mt5/Experts/XauAssistant.mq5` (includes `:5-20`, inputs after `:113`, globals near `:123`, `OnInit` `:250` and `:317`, `ProcessBar` `:631`, `OnDeinit` `:569`)

**Interfaces:**
- Consumes: `CSrZoneSet`, `SrZone`, `SrParams`, `SR_ROLE_*` (Task 7).
- Produces: `class CSrBoxes { void Init(CSrZoneSet *zones, bool show); void Redraw(); void Clear(); }`; EA globals `g_srZones` (`CSrZoneSet`) and `g_srBoxes` (`CSrBoxes`); EA inputs listed below.

Design note (a deliberate clarification of the spec, recorded in Task 10): the zones are lane-independent (same H4/H1/M15 boxes whichever lane trades), so the boxes are NOT gated on the active lane's `EnablePaint`; they draw whenever `ShowSrBoxes` is true.

- [ ] **Step 1: Write `SrBoxes.mqh`**

```cpp
// SrBoxes.mqh — S/R zone rectangles on the MT5 chart. Pure charting, like
// TradeBoxes.mqh: reads CSrZoneSet, draws, never touches trading state,
// every path best-effort. Support = LIGHT BLUE, resistance = LIGHT YELLOW
// (owner 2026-09-05); tier = shade (H4 strongest). MT5 objects have no
// alpha, so the tints are pre-blended for the dark theme and drawn BACK
// (behind candles). Tiers are drawn weakest-first (M15, H1, H4) so a
// stronger tier's box covers a weaker one where they overlap. One prefix,
// "xau_sr_", so ObjectsDeleteAll clears everything; the whole set is
// rebuilt only when the engine's version changes or a new chart bar opens
// (right edge tracks the forming bar). Dead zones are DELETED, not kept as
// history — a stale S/R box is misinformation.
#ifndef XAU_SRBOXES_MQH
#define XAU_SRBOXES_MQH
#include <XauAssistant/SrZones.mqh>

#define XAU_SR_PREFIX "xau_sr_"

class CSrBoxes
  {
private:
   CSrZoneSet *m_zones;
   bool        m_show;
   int         m_drawnVersion;
   datetime    m_rightEdge;

   color Fill(string tier, int role) const
     {
      if(role == SR_ROLE_SUP)
        {
         if(tier == "H4") return C'120,170,220';
         if(tier == "H1") return C'90,130,175';
         return C'60,95,130';
        }
      if(tier == "H4") return C'235,225,140';
      if(tier == "H1") return C'185,175,105';
      return C'130,122,75';
     }

   string Name(string tier, int role, datetime born) const
     {
      return XAU_SR_PREFIX + tier + "_" + (role == SR_ROLE_RES ? "R" : "S") + "_" + (string)(long)born;
     }

   void DrawOne(string tier, const SrZone &z, datetime now)
     {
      string name = Name(tier, z.role, z.born);
      if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, z.born, z.hi, now, z.lo)) return;
      color clr = Fill(tier, z.role);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      string lbl = name + "_lbl";
      double y = (z.role == SR_ROLE_RES) ? z.hi : z.lo;
      if(ObjectCreate(0, lbl, OBJ_TEXT, 0, now, y))
        {
         ObjectSetString(0, lbl, OBJPROP_TEXT,
                         (z.role == SR_ROLE_RES ? "R " : "S ") + tier + " x" + (string)z.touches);
         ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 7);
         ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, z.role == SR_ROLE_RES ? ANCHOR_RIGHT_LOWER : ANCHOR_RIGHT_UPPER);
         ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
        }
     }

public:
   CSrBoxes() : m_zones(NULL), m_show(false), m_drawnVersion(-1), m_rightEdge(0) {}

   void Init(CSrZoneSet *zones, bool show)
     {
      m_zones = zones; m_show = show; m_drawnVersion = -1; m_rightEdge = 0;
      if(!m_show) Clear();
     }

   // Call once per closed TRADE-TF bar (after CSrZoneSet.Update()).
   void Redraw()
     {
      if(!m_show || m_zones == NULL || !m_zones.Seeded()) return;
      datetime now = iTime(_Symbol, PERIOD_CURRENT, 0);
      if(now == 0) return;
      if(m_zones.Version() == m_drawnVersion && now == m_rightEdge) return;
      ObjectsDeleteAll(0, XAU_SR_PREFIX);
      for(int i = 0; i < m_zones.TierCount(); i++)         // weakest first
        {
         CSrZoneTier *t = m_zones.Tier(i);
         if(t == NULL) continue;
         for(int k = 0; k < t.Count(); k++)
           {
            SrZone z; t.Get(k, z);
            DrawOne(t.Name(), z, now);
           }
        }
      m_drawnVersion = m_zones.Version();
      m_rightEdge = now;
      ChartRedraw();
     }

   void Clear()
     {
      ObjectsDeleteAll(0, XAU_SR_PREFIX);
      m_drawnVersion = -1; m_rightEdge = 0;
     }
  };
#endif
```

- [ ] **Step 2: Wire the EA**

Includes (after `#include <XauAssistant/TradeBoxes.mqh>`):

```cpp
#include <XauAssistant/SrZones.mqh>
#include <XauAssistant/SrBoxes.mqh>
```

Inputs (after the `Chart panels` group, line ~113):

```cpp
input group "S/R zones — three timeframe tiers drawn as boxes (spec 2026-09-05); REPORT-ONLY, never blocks"
input bool   ShowSrBoxes     = true;          // draw the zone boxes on the chart (light blue support / light yellow resistance)
input string SrTiers         = "H4,H1,M15";   // tiers the verdict honours (drawing always shows all three)
input double SrRoomAtr       = 1.0;           // "wall" = opposing box within this x ATR(14, lane TF) of the entry, or containing it
input int    SrPivotWing     = 3;             // fractal wing: bars each side a swing must dominate
input double SrMinHeightAtr  = 0.15;          // box height floor, x tier ATR(14)
input double SrMaxHeightAtr  = 0.60;          // box height cap, x tier ATR(14)
input double SrMergeAtr      = 0.50;          // a pivot within this of a box merges into it (adds a touch)
input double SrBreakAtr      = 0.50;          // a CLOSE beyond a box by this kills it (a wick does not)
input int    SrKeepPerRole   = 3;             // nearest boxes kept per role per tier (3 -> at most 18 boxes)
input int    SrLookbackH4    = 300;           // seeding depth per tier, tier bars
input int    SrLookbackH1    = 400;
input int    SrLookbackM15   = 400;
```

Globals (next to `CTradeBoxes g_tradeBoxes;`, line ~123):

```cpp
CSrZoneSet     g_srZones;   // shared by every lane: zones are not lane-specific
CSrBoxes       g_srBoxes;
```

`OnInit`, BEFORE the first `g_registry.Register(...)` (line ~250):

```cpp
   // S/R zone engine (spec 2026-09-05): built before the lanes so both
   // HalfTrend instances can be handed the shared pointer. Fail-open by
   // construction — an unreadable tier stays empty and the verdict is -1.
   SrParams srp;
   srp.wing = SrPivotWing; srp.minHeightAtr = SrMinHeightAtr; srp.maxHeightAtr = SrMaxHeightAtr;
   srp.mergeAtr = SrMergeAtr; srp.breakAtr = SrBreakAtr; srp.keepPerRole = SrKeepPerRole;
   g_srZones.Init(srp, SrLookbackH4, SrLookbackH1, SrLookbackM15);
   g_srBoxes.Init(&g_srZones, ShowSrBoxes);
```

`ProcessBar`, as the FIRST statements (before the pending-switch block, line ~633):

```cpp
   // Advance the S/R zone tiers that closed a bar with this one, then
   // repaint the boxes (cheap: nothing is redrawn unless a tier changed or
   // the chart opened a new bar). Runs BEFORE the strategies evaluate so a
   // confirm on this bar is judged against zones that include it.
   g_srZones.Update();
   g_srBoxes.Redraw();
```

`OnDeinit`, after `g_chartPanels.Deinit();`:

```cpp
   g_srBoxes.Clear();
```

The strategy hand-off (`SetSrZones`) is Task 9; do NOT add it here — the EA must still compile after this task on its own (no new strategy methods are referenced yet).

- [ ] **Step 3: Verify by reading**

Check: every new identifier resolves (`SrParams` fields, `CSrZoneSet::Init` arity, `SIGNAL_BUY` visible in SrZones.mqh via the Strategy.mqh include). `ObjectsDeleteAll(0, XAU_SR_PREFIX)` uses the `(chart_id, prefix)` overload — the same form `ClearPaint` uses. `ANCHOR_RIGHT_LOWER`/`ANCHOR_RIGHT_UPPER` exist in `ENUM_ANCHOR_POINT`. `(string)(long)born` matches the naming style in `TradeBoxes.mqh`.

- [ ] **Step 4: Commit**

```bash
git add mt5/Include/XauAssistant/SrBoxes.mqh mt5/Experts/XauAssistant.mq5
git commit -m "feat(ea): S/R zone boxes on the MT5 chart — light-blue support, light-yellow resistance, three tiers (uncompiled)"
```

---

### Task 9: Strategy hook + trade-event plumbing (EA side)

**Files:**
- Modify: `mt5/Include/XauAssistant/Strategy.mqh:47-52`
- Modify: `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh` (members `:96-99`, constructor `:356-386`, accessors `:523-525`, `Evaluate` `:583-594`)
- Modify: `mt5/Include/XauAssistant/UiSink.mqh:92-105`, `mt5/Include/XauAssistant/UiApi.mqh:276-297`
- Modify: `mt5/Experts/XauAssistant.mq5:251-265` (registration)

**Interfaces:**
- Consumes: `CSrZoneSet::Verdict(int dir, double entry, double room, string tiers, string &tierOut)` (Task 7); EA globals `g_srZones`, inputs `SrTiers`, `SrRoomAtr` (Task 8).
- Produces: `CStrategy::LastSrAgree() const -> int` (default -1), `CStrategy::LastSrTier() const -> string` (default ""); `CHalfTrendEmaStrategy::SetSrZones(CSrZoneSet *zones, string tiers, double roomAtr)`; `CUiApi::PostTradeEvent(..., int rsiAgree = -1, int srAgree = -1, string srTier = "")` emitting `"sr_agree"` and `"sr_tier"` JSON fields.

- [ ] **Step 1: `Strategy.mqh` virtuals**

After `virtual int LastRsiAgree() const { return -1; }`:

```cpp
   // S/R zone verdict behind the current signal (owner 2026-09-05,
   // spec 2026-09-05-sr-zones-design.md): 1 = clear room ahead, 0 = the
   // entry sits inside or within SrRoomAtr of an opposing zone ("wall"),
   // -1 = not evaluated / no zone engine. REPORT-ONLY in increment 1 —
   // never blocks. LastSrTier() names the strongest wall ("H4"/"H1"/"M15")
   // behind a 0, "" otherwise.
   virtual int    LastSrAgree() const { return -1; }
   virtual string LastSrTier()  const { return ""; }
```

- [ ] **Step 2: `HalfTrendEma.mqh`**

Include at the top (after the Strategy include): `#include <XauAssistant/SrZones.mqh>`.

Members, after `int m_lastRsiAgree;`:

```cpp
   // S/R zone verdict (owner 2026-09-05) — report-only. The engine is the
   // EA-owned CSrZoneSet shared by every lane (zones are not lane-specific);
   // this lane only holds the pointer, the tiers it honours and its room
   // distance. Judged at Evaluate time (the confirm bar's close for a live
   // confirm; for a catch-up entry, the zones/ATR as they stand now).
   CSrZoneSet *m_srZones;
   string      m_srTiers;
   double      m_srRoomAtr;
   int         m_lastSrAgree;   // 1 clear / 0 wall / -1 not evaluated
   string      m_lastSrTier;    // strongest wall's tier behind a 0
```

Constructor initialiser list, after `m_rsiHandle(INVALID_HANDLE), m_confirmRsi(-1), m_lastRsiAgree(-1),`:

```cpp
        m_srZones(NULL), m_srTiers(""), m_srRoomAtr(0.0), m_lastSrAgree(-1), m_lastSrTier(""),
```

Public methods next to `LastRsiAgree`:

```cpp
   virtual int    LastSrAgree() const { return m_lastSrAgree; }
   virtual string LastSrTier()  const { return m_lastSrTier; }
   // Hand this lane the EA's shared zone engine. Called once from OnInit.
   void SetSrZones(CSrZoneSet *zones, string tiers, double roomAtr)
     { m_srZones = zones; m_srTiers = tiers; m_srRoomAtr = roomAtr; }
```

In `Evaluate`, directly after the RSI `if(m_lastRsiAgree == 0) Print(...)` statement and BEFORE the `if(!htfOk && !HtfEnforced())` line:

```cpp
         // S/R zone verdict (owner 2026-09-05) — report-only, never blocks.
         // Room is SrRoomAtr x ATR(14) of THIS lane's timeframe (shift 1 =
         // the bar that just closed, i.e. the confirm bar on a live confirm).
         m_lastSrAgree = -1; m_lastSrTier = "";
         if(m_srZones != NULL)
           {
            double srAb[]; double room = 0.0;
            if(CopyBuffer(m_atrHandle, 0, 1, 1, srAb) == 1 && srAb[0] > 0)
               room = m_srRoomAtr * srAb[0];
            m_lastSrAgree = m_srZones.Verdict(wanted, m_confirmClose, room, m_srTiers, m_lastSrTier);
            if(m_lastSrAgree == 0)
               Print(m_id + ": ", (wanted == SIGNAL_BUY ? "BUY" : "SELL"),
                     " — S/R WALL: ", m_lastSrTier,
                     (wanted == SIGNAL_BUY ? " resistance" : " support"),
                     " inside/within ", DoubleToString(m_srRoomAtr, 2), " ATR of ",
                     DoubleToString(m_confirmClose, 2),
                     " but the check is report-only; entering anyway");
           }
```

`Verdict` takes `int dir`; `wanted` is already an `int` holding `SIGNAL_BUY`/`SIGNAL_SELL`. ✓

- [ ] **Step 3: `UiApi.mqh` + `UiSink.mqh`**

`PostTradeEvent` signature: append `, int srAgree = -1, string srTier = ""` after `int rsiAgree = -1`. JSON: replace the last line

```cpp
                    ",\"rsi_agree\":" + (string)rsiAgree + "}";
```
with
```cpp
                    ",\"rsi_agree\":" + (string)rsiAgree +
                    ",\"sr_agree\":" + (string)srAgree +
                    ",\"sr_tier\":\"" + srTier + "\"}";
```
(`srTier` is one of `""`, `"H4"`, `"H1"`, `"M15"` — no escaping needed.) Update the comment above it: "Field names/order match service/app/models.py TradeEventRequest exactly."

`UiSink.mqh`: change `int htfAgree = -1, ema200Agree = -1, newsBlackout = -1, rsiAgree = -1;` to also declare `int srAgree = -1; string srTier = "";`, set them inside the `event != "close"` block:

```cpp
         srAgree = active.LastSrAgree();
         srTier  = active.LastSrTier();
```

and pass `newsBlackout, rsiAgree, srAgree, srTier);` to `m_ui.PostTradeEvent(...)`.

- [ ] **Step 4: EA registration hand-off**

Replace the two `g_registry.Register(new CHalfTrendEmaStrategy(...))` statements with named pointers so the shared engine can be handed over:

```cpp
   CHalfTrendEmaStrategy *laneM5 = new CHalfTrendEmaStrategy("halftrend_ema_v1", TradeTimeframe, HtAmplitude, EmaLength,
                       ConfirmCloses, StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR,
                       HtfConfirm, HtfConfirmTf, HtfConfirmEma, HtfConfirmBufferATR,
                       HtfChopOnly, HtfChopBars, HtfChopEffMax, Ema200Confirm);
   laneM5.SetSrZones(&g_srZones, SrTiers, SrRoomAtr);
   g_registry.Register(laneM5);
```

and the same for `laneM15` (`"halftrend_m15_v1"`, keeping every existing argument exactly as it is), followed by `laneM15.SetSrZones(&g_srZones, SrTiers, SrRoomAtr); g_registry.Register(laneM15);`. Keep the existing explanatory comments in place.

- [ ] **Step 5: Verify by reading**

- Both lanes call `Verdict` only inside the `!m_fired && m_confirmShift != 0` block, so shadow lanes also produce a verdict — harmless, and `UiSink` only reads the ACTIVE lane's.
- Nothing new returns `SIGNAL_NONE`. Grep the diff for `return SIGNAL_NONE` — there must be no new occurrence.
- `Reconciler.mqh` calls `PostTradeEvent` with defaults — compiles unchanged.
- `CStrategy` base gained two `const` virtuals; `CBollStochRsiStrategy` / `CSessionStructureStrategy` inherit the defaults.

- [ ] **Step 6: Commit**

```bash
git add mt5/Include/XauAssistant/Strategy.mqh mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh mt5/Include/XauAssistant/UiSink.mqh mt5/Include/XauAssistant/UiApi.mqh mt5/Experts/XauAssistant.mq5
git commit -m "feat(ea): report-only S/R wall verdict on both HalfTrend lanes — sr_agree/sr_tier on every entry (uncompiled)"
```

---

### Task 10: Knowledge base, spec clarifications, compile checklist

**Files:**
- Modify: `.claude/agents/izi.md` (expand the placeholder heading from Task 3; add the module to the "Where the code lives" table area or a new module list)
- Modify: `docs/superpowers/specs/2026-09-05-sr-zones-design.md` (two clarifications)
- Modify: `service/tests/test_sr_zones.py` only if a spec edit changes a number (it should not)

- [ ] **Step 1: Spec clarifications**

In the spec's "Zone engine" step 4 (**Bound**), replace "keeps only the `3` nearest boxes above the last close and the `3` nearest below, by edge distance" with "keeps, per role, only the `3` boxes nearest the last close by edge distance (0 if the box contains it; ties → more touches, then older). Still at most 6 per tier / 18 in total." In the MT5 bullet of "Chart boxes", replace "Drawn only while painting is enabled (the ACTIVE lane, `EnablePaint`)" with "Drawn whenever `ShowSrBoxes` is true — the zones are lane-independent, so they are not tied to the active lane's paint state." Add one sentence under "Tier ATR": "ATR here is MT5's built-in `iATR`, an SMA of True Range (not Wilder) — exactly reproducible in Python, see `sma_atr`."

- [ ] **Step 2: Expand the izi entry**

Replace the placeholder body under `## S/R zones — three timeframe tiers as boxes (owner, 2026-09-05)` with:

```markdown
The owner asked both HalfTrend lanes to know where price was rejected
before, as BOXES (not lines), in three tiers: H4 major, H1 intermediate,
M15 minor. Spec: `docs/superpowers/specs/2026-09-05-sr-zones-design.md`.
Increment 1 (this entry) changes NO trading decision — report first,
sweep second, enforce last, the same law as M15/E200/RSI.

**Engine, twice, one contract.** `service/app/sr_zones.py` and
`mt5/Include/XauAssistant/SrZones.mqh` implement the identical per-bar
order: death by close-through (0.5 tier-ATR beyond the box), the fractal
pivot (3-bar wing) confirmed by this bar births a box (wick → body,
clamped 0.15–0.60 ATR) or merges into the nearest same-role box within
0.5 ATR (touch count +1), keep the 3 nearest per role, sort by (born, lo).
ATR is MT5's built-in `iATR` — an SMA of True Range, NOT Wilder — which is
why Python can reproduce the EA exactly (`sma_atr`). A wick through a box
is a test (the box stays); only a CLOSE through kills it. Lookbacks
H4 300 / H1 400 / M15 400 bars. Constants are EA inputs under the
"S/R zones" group and keyword args in Python; the replay sweep
(increment 2), not taste, moves them.

**Where the boxes come from on each surface.** The EA reads its own
tier bars. The mini-app builds from the H4/H1/M15 ring buffers the bridge
already pushes (`/api/history` → `sr_zones`, same list on every tab; the
forming bar is dropped). The dashboard (`/api/overlays` → `sr_zones`) and
the trade/`/chart` renders build from the persistent M5 `candles` table
resampled with the replay's bucket rule (`sr_zones_from_db`, memoised on
the newest M5 bar; `[]` if the stored series is not M5 or anything
fails). Lightweight Charts has no rectangle: a fixed pool of 18 baseline
series (weakest tier first, `autoscaleInfoProvider: null` so a far box
cannot stretch the axis) draws the fills. `render.py` draws only boxes
overlapping the 100-bar window for the same reason.

**Colours (owner):** support LIGHT BLUE `173,216,230`, resistance LIGHT
YELLOW `255,245,157`; alpha by tier 0.35/0.22/0.12. MT5 has no alpha, so
`SrBoxes.mqh` uses pre-blended tints; boxes are BACK objects with prefix
`xau_sr_`, labels `R H4 x3` / `S M15 x1`. Dead zones are deleted, not
kept — a stale S/R box is misinformation. Boxes are NOT tied to the
active lane's paint state (zones are lane-independent); `ShowSrBoxes`
input switches them.

**The verdict.** `CStrategy::LastSrAgree()` / `LastSrTier()` (default
-1/""). Both HalfTrend lanes judge at the confirm: BUY is a WALL (0) if
the entry sits inside a resistance box or the nearest one's bottom is
within `SrRoomAtr` (1.0) × ATR(14, lane TF) above it; SELL mirrored
against support. `sr_tier` names the strongest wall (H4 > H1 > M15).
Flows exactly like `rsi_agree`: UiSink → PostTradeEvent `sr_agree` +
`sr_tier` → `TradeEventRequest` → `trades` migration → `recent_trades` →
basket flags `sr` / `sr_tier` → mini-app history column **S/R**
(`Yes` / `No H4` / `–`) → screenshot caption `S/R: clear ✅` /
`S/R: WALL H4 ⚠️`. The EA logs "S/R WALL ... entering anyway".

**Not yet:** `--sr-filter` in the replay and any enforcement input are
increment 2/3 of the spec — do not enable a block without the sweep.
```

Also add the two new MQL files and `sr_zones.py` to whatever module list izi keeps for "where the code lives" (grep `SessionStructure.mqh` in izi.md to find the current module listing and add lines in the same style).

- [ ] **Step 3: Run the suite one last time**

Run: `cd service && pytest -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/izi.md docs/superpowers/specs/2026-09-05-sr-zones-design.md
git commit -m "docs(izi+spec): S/R zones increment 1 — engine contract, surfaces, colours, verdict plumbing"
```

- [ ] **Step 5: Owner hand-off checklist (put this in the final report verbatim)**

1. Compile `mt5/Experts/XauAssistant.mq5` in MetaEditor — expect **0 errors, 0 warnings**. New files: `Include/XauAssistant/SrZones.mqh`, `Include/XauAssistant/SrBoxes.mqh`.
2. Copy `mt5/` into the MT5 data folder, restart MT5, and verify the reload by the init fingerprint (izi: "verify by INIT FINGERPRINT", not the heartbeat).
3. On the chart: light-blue and light-yellow boxes present, three shades, labels like `R H4 x3`. Expert log shows `SrZones H4: seeding ...` only if history is short.
4. Restart the service (`scripts/setup.sh` is idempotent). Open the Telegram Live Chart: boxes on every tab. Dashboard: boxes on the price chart.
5. Next trade: the Telegram screenshot caption carries an `S/R:` line, and the history table shows the new **S/R** column.
6. Nothing about entries, stops, or targets changed. If a trade looks different from before, that is a bug — report it.

---

## Self-review

**Spec coverage.** Engine birth/merge/death/bound/lookback → Tasks 1, 7. Two implementations, one contract → Tasks 1 + 7 (contract text duplicated in both). Three chart surfaces + colours → Tasks 3 (mini-app), 4 (dashboard), 5 (renders), 8 (MT5). Strategy hook + plumbing → Tasks 6, 9. Error handling (fail-open) → every helper returns `[]`/-1 and the EA's `Update` bails on missing handles. Testing → unit tests per task; golden replay pins untouched because `scripts/backtest.py` is untouched. `SetSrOverride` is increment 3 and deliberately absent. Two spec sentences corrected in Task 10 (per-role bound; lane-independent boxes) and the ATR definition made explicit.

**Placeholder scan.** None remaining.

**Type consistency.** `sr_verdict(zones, direction, entry, room, tiers)` ↔ MQL `Verdict(dir, entry, room, tiers, tierOut)`; `Zone` fields ↔ `SrZone` fields (+ `tier` carried by the owning `CSrZoneTier`); `zone_dicts` keys ↔ JS `z.tier/role/lo/hi/born/touches` ↔ `render._plot_sr_zones` keys; `render_trade_chart(..., sr_zones=None)` positional 5th arg matches the `asyncio.to_thread(render_trade_chart, render_candles, trade_dict, str(render_path), ema_len, zones)` call; `render_snapshot_chart(candles, out, positions, zones)` matches its call; `PostTradeEvent(..., rsiAgree, srAgree, srTier)` matches the `UiSink` call and the JSON keys `sr_agree`/`sr_tier` match `TradeEventRequest`.
