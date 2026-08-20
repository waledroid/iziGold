# QuickFlip — Dual-Lane Replay (Phase 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the replay engine a second, independent strategy lane so a single run shows HalfTrend and QuickFlip trading the same account at the same time, without either closing the other's positions.

**Architecture:** QuickFlip's setups are pure price geometry — they depend on candles and daily ATR, never on the account — so they are precomputed by a pure function and then executed inside the existing `run()` loop as a second lane sharing one balance. HalfTrend's code path is untouched; the lane is additive and gated by `--strategy`.

**Tech Stack:** Python 3.12, stdlib only, pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-quickflip-ny-design.md`

## Global Constraints

- **Never change HalfTrend's behaviour.** `service/tests/test_backtest_golden.py` pins it and is the gate; `--strategy ht` must reproduce today's numbers exactly.
- **Candle `t` is SERVER wall-clock.** `hhmm()` reads it directly with `dt.datetime.fromtimestamp(t, dt.UTC)`. Do NOT add a timezone offset — a +3h shift is the exact bug that invalidated the first two spikes. Server hour 00 has zero bars (the daily break); use that as your sanity check.
- QuickFlip default session is **13:30 server**, threshold **10%** of daily ATR(14), window **90** minutes, risk **0.25%**, **one trade per server day**.
- The published 25%-of-ATR rule is NOT used — gold's median opening range is ~7% of daily ATR.
- No new Python dependencies.
- The daily-loss brake, kill switch and news blackout remain UNMODELLED (standing caveats).
- `bars_max.json` (repo root, ~8.7 MB) is untracked and must never be committed.
- Tests run from `service/`: `cd service && .venv/bin/python -m pytest -q`. `service/tests/` is a package; cross-test imports use `from tests.<module> import ...`.
- Commit style: `feat(backtest):` / `test(backtest):` / `docs(izi):`.

---

### Task 1: Promote the probe to a reproducible tool

The numbers in the spec came from a scratchpad script that no longer exists. Evidence nobody can re-run is not evidence.

**Files:**
- Create: `scripts/quickflip_probe.py`
- Create: `service/tests/test_quickflip_probe.py`

**Interfaces:**
- Produces: `quickflip_probe.daily_atr(candles) -> dict[int, float]` (server-day index → ATR(14) of the prior 14 days); `quickflip_probe.setups_at(candles, hour, minute, atr, window_min=90, spread=0.20) -> list[dict]` where each dict has `ratio`, `pl`, `green`, `entry_t`, `stop`, `tp`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_quickflip_probe.py`:

```python
"""The probe is the only evidence behind the spec's numbers, so it is pinned.

Deliberately includes a guard on the TIME CONVENTION: candle `t` is server
wall-clock and must be read with no offset. A +3h shift is what invalidated
the first two spikes and inflated the result that was nearly built live.
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BARS = pathlib.Path(__file__).parent / "data" / "bars_slice.json"


def _probe():
    spec = importlib.util.spec_from_file_location(
        "qfp", ROOT / "scripts" / "quickflip_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_server_hour_zero_is_the_market_break():
    """The convention guard: read candle t with NO offset and server hour 00
    is empty, because that is the daily break. If this fails, someone has
    reintroduced a timezone shift."""
    import datetime as dt
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    hours = {dt.datetime.fromtimestamp(int(c["t"]), dt.UTC).hour for c in candles}
    assert 0 not in hours, "server hour 00 must be empty (the daily break)"


def test_daily_atr_warms_up_and_is_positive():
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    assert atr, "no daily ATR computed"
    assert all(v > 0 for v in atr.values())


def test_setups_have_a_stop_on_the_losing_side_of_entry():
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    got = qfp.setups_at(candles, 13, 30, atr)
    for s in got:
        if s["green"]:      # sold the sweep: stop above, target below
            assert s["stop"] > s["tp"]
        else:
            assert s["stop"] < s["tp"]


def test_at_most_one_setup_per_day():
    import datetime as dt
    qfp = _probe()
    candles = json.loads(BARS.read_text())
    atr = qfp.daily_atr(candles)
    got = qfp.setups_at(candles, 13, 30, atr)
    days = [int(s["entry_t"]) // 86400 for s in got]
    assert len(days) == len(set(days)), "more than one setup on some day"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd service && .venv/bin/python -m pytest tests/test_quickflip_probe.py -q
```

Expected: FAIL — `scripts/quickflip_probe.py` does not exist.

- [ ] **Step 3: Write the probe**

Create `scripts/quickflip_probe.py`:

```python
#!/usr/bin/env python3
"""QuickFlip evidence probe — the tool behind the numbers in
docs/superpowers/specs/2026-08-20-quickflip-ny-design.md.

Box the first M15 candle of a chosen half-hour, qualify it against daily
ATR(14), then trade the SWEEP-AND-REVERSE: price leaves the box in the
opening candle's direction, comes back inside, and we bet the far side of the
box gets hit before the sweep extreme does.

TIME CONVENTION -- read this before editing: candle `t` is SERVER wall-clock
already. Read it with dt.datetime.fromtimestamp(t, dt.UTC) and NO offset.
Adding one shifts every session by that many hours; a +3h shift is what made
an earlier version of this analysis report the wrong sessions entirely.
Sanity check: server hour 00 contains zero bars (the daily market break).

Usage:
    python3 scripts/quickflip_probe.py [--source bars_max.json]
                                       [--hour 13] [--minute 30]
                                       [--sweep]     # every half-hour
"""
import argparse
import datetime as dt
import json

SPREAD_USD = 0.20     # per oz, round trip -- same charge the replay uses
WINDOW_MIN = 90
ATR_DAYS = 14


def _server(t):
    return dt.datetime.fromtimestamp(int(t), dt.UTC)


def _day(t):
    return int(t) // 86400


def load(path):
    raw = json.loads(open(path).read())
    c = raw["candles"] if isinstance(raw, dict) and "candles" in raw else raw
    for x in c:
        x["t"] = int(x["t"])
    return c


def daily_atr(candles):
    """server-day index -> ATR(ATR_DAYS) computed from the PRIOR days only."""
    days = {}
    for x in candles:
        d = days.setdefault(_day(x["t"]), {"h": x["h"], "l": x["l"], "c": x["c"]})
        d["h"] = max(d["h"], x["h"])
        d["l"] = min(d["l"], x["l"])
        d["c"] = x["c"]
    keys = sorted(days)
    out = {}
    for i, k in enumerate(keys):
        if i < ATR_DAYS:
            continue
        s = 0.0
        for j in range(i - ATR_DAYS, i):
            dj, pc = days[keys[j]], days[keys[j - 1]]["c"]
            s += max(dj["h"] - dj["l"], abs(dj["h"] - pc), abs(dj["l"] - pc))
        out[k] = s / ATR_DAYS
    return out


def setups_at(candles, hour, minute, atr, window_min=WINDOW_MIN,
              spread=SPREAD_USD):
    """Every completed sweep-and-reverse trade at this half-hour."""
    by_day = {}
    for x in candles:
        by_day.setdefault(_day(x["t"]), []).append(x)
    out = []
    for k in sorted(by_day):
        if k not in atr:
            continue
        rows = by_day[k]
        if len(rows) < 100:          # half-day / holiday
            continue
        box = [x for x in rows if _server(x["t"]).hour == hour
               and minute <= _server(x["t"]).minute < minute + 15]
        if len(box) != 3:            # need the whole 15-minute candle
            continue
        hi = max(x["h"] for x in box)
        lo = min(x["l"] for x in box)
        green = box[-1]["c"] >= box[0]["o"]
        ratio = (hi - lo) / atr[k]
        t_end = box[-1]["t"] + 300
        path = [x for x in rows if t_end <= x["t"] < t_end + window_min * 60]
        swept = False
        ext = entry = stop = tp = entry_t = pl = None
        for x in path:
            if not swept:
                if green and x["h"] > hi:
                    swept, ext = True, x["h"]
                elif not green and x["l"] < lo:
                    swept, ext = True, x["l"]
                continue
            if entry is None:
                ext = max(ext, x["h"]) if green else min(ext, x["l"])
                if green and x["c"] < hi:
                    entry, stop, tp, entry_t = x["c"], ext, lo, x["t"]
                elif not green and x["c"] > lo:
                    entry, stop, tp, entry_t = x["c"], ext, hi, x["t"]
                continue
            if green:                                    # short
                if x["h"] >= stop:
                    pl = -(stop - entry) - spread
                    break
                if x["l"] <= tp:
                    pl = (entry - tp) - spread
                    break
            else:                                        # long
                if x["l"] <= stop:
                    pl = -(entry - stop) - spread
                    break
                if x["h"] >= tp:
                    pl = (tp - entry) - spread
                    break
        if pl is not None:
            out.append({"ratio": ratio, "pl": pl, "green": green,
                        "entry_t": entry_t, "stop": stop, "tp": tp,
                        "box_hi": hi, "box_lo": lo})
    return out


def report(rows, label):
    if not rows:
        print(f"{label:>7}  (no completed trades)")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["pl"] > 0)
    tot = sum(r["pl"] for r in rows)
    half = n // 2
    h1 = sum(r["pl"] for r in rows[:half]) / max(half, 1)
    h2 = sum(r["pl"] for r in rows[half:]) / max(n - half, 1)
    both = "  <== positive in BOTH halves" if h1 > 0 and h2 > 0 else ""
    print(f"{label:>7} {n:>5} {100*wins/n:>6.1f}% {tot/n:>8.2f} {tot:>9.2f} "
          f"{h1:>8.2f} {h2:>8.2f}{both}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="bars_max.json")
    ap.add_argument("--hour", type=int, default=13)
    ap.add_argument("--minute", type=int, default=30)
    ap.add_argument("--sweep", action="store_true",
                    help="scan every half-hour instead of one session")
    args = ap.parse_args()
    candles = load(args.source)
    atr = daily_atr(candles)
    print(f"{'server':>7} {'n':>5} {'win%':>6} {'exp$/oz':>8} {'total':>9} "
          f"{'H1':>8} {'H2':>8}")
    if args.sweep:
        for h in range(1, 24):
            for m in (0, 30):
                rows = setups_at(candles, h, m, atr)
                if len(rows) >= 80:
                    report(rows, f"{h:02d}:{m:02d}")
    else:
        report(setups_at(candles, args.hour, args.minute, atr),
               f"{args.hour:02d}:{args.minute:02d}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_quickflip_probe.py -q
```

Expected: 4 passed.

- [ ] **Step 5: Reproduce the spec's headline row**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/quickflip_probe.py --hour 13 --minute 30
```

Expected: roughly `n=246, win 52.4%, exp +0.36, total +89`, both halves positive. Small differences from the spec are acceptable (the spec's table came from an earlier ATR warm-up), but a **sign flip or a 2x difference means something is wrong** — investigate before continuing, and report what you found.

- [ ] **Step 6: Commit**

```bash
git add scripts/quickflip_probe.py service/tests/test_quickflip_probe.py
git commit -m "feat(backtest): promote the quickflip probe to a reproducible tool"
```

---

### Task 2: `qf_signals()` — QuickFlip setups as pure price geometry

**Files:**
- Modify: `scripts/backtest.py` (add the function and its constants near the other strategy constants)
- Create: `service/tests/test_qf_signals.py`

**Interfaces:**
- Consumes: `_load_bt()` and `BARS` from `service/tests/test_backtest_golden.py`.
- Produces: `bt.qf_signals(candles) -> list[dict]`, each `{"i": int, "entry_t": int, "dir": "BUY"|"SELL", "entry": float, "stop": float, "tp": float, "expire_t": int, "ratio": float, "box_hi": float, "box_lo": float}` where `i` is the index of the candle whose CLOSE triggers entry. Also module constants `QF_HOUR=13`, `QF_MINUTE=30`, `QF_ATR_PCT=10.0`, `QF_WINDOW_MIN=90`, `QF_RISK_PCT=0.25`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_qf_signals.py`:

```python
"""QuickFlip setups are pure geometry: they depend on candles only, never on
the account. That is what lets the lane be precomputed and then executed
inside the existing balance-aware loop."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _sigs():
    bt = _load_bt()
    return bt, bt.qf_signals(json.loads(BARS.read_text()))


def test_defaults_match_the_spec():
    bt = _load_bt()
    assert (bt.QF_HOUR, bt.QF_MINUTE) == (13, 30)
    assert bt.QF_ATR_PCT == 10.0
    assert bt.QF_WINDOW_MIN == 90
    assert bt.QF_RISK_PCT == 0.25


def test_signals_are_pure_of_the_account():
    """Called twice, the same candles must give identical setups."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    assert bt.qf_signals(candles) == bt.qf_signals(candles)


def test_stop_sits_beyond_entry_and_target_on_the_other_side():
    _bt, sigs = _sigs()
    for s in sigs:
        if s["dir"] == "SELL":
            assert s["stop"] > s["entry"] and s["tp"] < s["entry"]
        else:
            assert s["stop"] < s["entry"] and s["tp"] > s["entry"]


def test_entry_index_points_at_the_entry_bar():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    for s in bt.qf_signals(candles):
        assert candles[s["i"]]["t"] == s["entry_t"]


def test_expiry_is_within_the_window_of_the_box():
    _bt, sigs = _sigs()
    for s in sigs:
        assert s["expire_t"] > s["entry_t"]


def test_one_setup_per_server_day():
    _bt, sigs = _sigs()
    days = [s["entry_t"] // 86400 for s in sigs]
    assert len(days) == len(set(days))


def test_threshold_filters_setups_out():
    """A 90% ATR threshold must leave far fewer setups than a 0% one --
    proof the qualifier is actually applied."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.QF_ATR_PCT = 0.0
    loose = len(bt.qf_signals(candles))
    bt.QF_ATR_PCT = 90.0
    strict = len(bt.qf_signals(candles))
    assert strict < loose
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_signals.py -q
```

Expected: FAIL — `qf_signals` does not exist.

- [ ] **Step 3: Implement**

Add to `scripts/backtest.py`, above `def run(`:

```python
# --- quickflip_ny_v1: the second lane (spec 2026-08-20-quickflip-ny-design) --
# Box the first M15 candle of QF_HOUR:QF_MINUTE, qualify it against daily
# ATR(14), then trade the sweep-and-reverse back to the far side of the box.
# Candle t is SERVER wall-clock -- never add an offset (a +3h shift is what
# invalidated the original analysis).
QF_HOUR = 13           # server; 13:30 had the best win% + both halves positive
QF_MINUTE = 30
QF_ATR_PCT = 10.0      # box range as % of daily ATR(14); NOT the published 25
QF_WINDOW_MIN = 90
QF_RISK_PCT = 0.25     # reduced size: a paid experiment, not a proven edge
QF_ATR_DAYS = 14


def qf_daily_atr(candles):
    """server-day -> ATR(QF_ATR_DAYS) over the PRIOR days only."""
    days = {}
    for x in candles:
        d = days.setdefault(x["t"] // 86400,
                            {"h": x["h"], "l": x["l"], "c": x["c"]})
        d["h"] = max(d["h"], x["h"])
        d["l"] = min(d["l"], x["l"])
        d["c"] = x["c"]
    keys = sorted(days)
    out = {}
    for i, k in enumerate(keys):
        if i < QF_ATR_DAYS:
            continue
        s = 0.0
        for j in range(i - QF_ATR_DAYS, i):
            dj, pc = days[keys[j]], days[keys[j - 1]]["c"]
            s += max(dj["h"] - dj["l"], abs(dj["h"] - pc), abs(dj["l"] - pc))
        out[k] = s / QF_ATR_DAYS
    return out


def qf_signals(candles):
    """Every QuickFlip setup, as pure price geometry (no account state)."""
    atr = qf_daily_atr(candles)
    idx_of = {}
    by_day = {}
    for i, x in enumerate(candles):
        idx_of[x["t"]] = i
        by_day.setdefault(x["t"] // 86400, []).append(x)
    out = []
    for k in sorted(by_day):
        if k not in atr:
            continue
        rows = by_day[k]
        if len(rows) < 100:
            continue
        box = [x for x in rows
               if hhmm(x["t"])[1] == QF_HOUR
               and QF_MINUTE <= hhmm(x["t"])[2] < QF_MINUTE + 15]
        if len(box) != 3:
            continue
        hi = max(x["h"] for x in box)
        lo = min(x["l"] for x in box)
        if (hi - lo) < QF_ATR_PCT / 100.0 * atr[k]:
            continue
        green = box[-1]["c"] >= box[0]["o"]
        t_end = box[-1]["t"] + 300
        expire = t_end + QF_WINDOW_MIN * 60
        swept, ext = False, None
        for x in [r for r in rows if t_end <= r["t"] < expire]:
            if not swept:
                if green and x["h"] > hi:
                    swept, ext = True, x["h"]
                elif not green and x["l"] < lo:
                    swept, ext = True, x["l"]
                continue
            ext = max(ext, x["h"]) if green else min(ext, x["l"])
            if green and x["c"] < hi:
                out.append({"i": idx_of[x["t"]], "entry_t": x["t"],
                            "dir": "SELL", "entry": x["c"], "stop": ext,
                            "tp": lo, "expire_t": expire,
                            "ratio": (hi - lo) / atr[k],
                            "box_hi": hi, "box_lo": lo})
                break
            if not green and x["c"] > lo:
                out.append({"i": idx_of[x["t"]], "entry_t": x["t"],
                            "dir": "BUY", "entry": x["c"], "stop": ext,
                            "tp": hi, "expire_t": expire,
                            "ratio": (hi - lo) / atr[k],
                            "box_hi": hi, "box_lo": lo})
                break
    return out
```

- [ ] **Step 4: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_signals.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Confirm HalfTrend is untouched**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: 3 passed. This task only ADDS a function; if a golden moved, you edited something you should not have.

- [ ] **Step 6: Commit**

```bash
git add scripts/backtest.py service/tests/test_qf_signals.py
git commit -m "feat(backtest): qf_signals() computes QuickFlip setups as pure price geometry"
```

---

### Task 3: Run both lanes against one balance

**Files:**
- Modify: `scripts/backtest.py` — `run()` (preamble, the main loop, and `close_basket`'s trade record), `build_parser()`, `main()`
- Create: `service/tests/test_qf_lane.py`

**Interfaces:**
- Consumes: `qf_signals()` from Task 2.
- Produces: every trade dict gains `"lane": "ht" | "qf"`. New module global `STRATEGY = "both"` (values `"ht"`, `"qf"`, `"both"`) and CLI flag `--strategy`. `run()`'s return arity is UNCHANGED.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_qf_lane.py`:

```python
"""Two lanes, one balance, neither able to touch the other's position."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _run(strategy, balance=10000.0):
    bt = _load_bt()
    bt.STRATEGY = strategy
    trades, bal, dd, valley = bt.run(json.loads(BARS.read_text()), balance, False)
    return bt, trades, bal


def test_ht_only_produces_no_qf_trades():
    _bt, trades, _bal = _run("ht")
    assert trades
    assert all(t["lane"] == "ht" for t in trades)


def test_qf_only_produces_no_ht_trades():
    _bt, trades, _bal = _run("qf")
    assert trades, "the fixture must contain at least one QuickFlip setup"
    assert all(t["lane"] == "qf" for t in trades)


def test_both_runs_both_lanes():
    _bt, trades, _bal = _run("both")
    lanes = {t["lane"] for t in trades}
    assert lanes == {"ht", "qf"}


def test_ht_lane_is_identical_whether_or_not_qf_runs():
    """The lanes must not interfere. QuickFlip changes the BALANCE path, so
    sizing may differ -- but the ENTRY and EXIT prices of HalfTrend's trades
    are decisions, and decisions must be untouched."""
    _b1, ht_only, _x = _run("ht")
    _b2, both, _y = _run("both")
    ht_in_both = [t for t in both if t["lane"] == "ht"]
    assert len(ht_only) == len(ht_in_both)
    for a, b in zip(ht_only, ht_in_both):
        assert a["dir"] == b["dir"]
        assert round(a["legs"][0]["px"], 2) == round(b["legs"][0]["px"], 2)
        assert round(a["exit"], 2) == round(b["exit"], 2)
        assert a["why"] == b["why"]


def test_qf_trades_carry_the_full_record_shape():
    """Downstream (--json, --web, the report page) reads these keys."""
    _bt, trades, _bal = _run("qf")
    for t in trades:
        assert t["legs"] and "t" in t["legs"][0] and "oz" in t["legs"][0]
        assert t["stop_history"] and t["tp"] is not None
        assert t["exit_t"] > t["legs"][0]["t"]
        assert t["why"] in ("qf target", "qf stop", "qf expired")


def test_balances_chain_across_both_lanes():
    _bt, trades, bal = _run("both")
    ordered = sorted(trades, key=lambda t: t["exit_t"])
    assert round(ordered[-1]["bal_after"], 2) == round(bal, 2)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_lane.py -q
```

Expected: FAIL — `KeyError: 'lane'` / `STRATEGY` missing.

- [ ] **Step 3: Tag existing trades with their lane**

In `close_basket()`'s `trades.append({...})`, add one key (keep every existing key):

```python
                       "lane": "ht",
```

- [ ] **Step 4: Add the lane state to `run()`'s preamble**

Next to `trades = []`:

```python
    # --- QuickFlip lane: independent positions, shared balance -------------
    # Precomputed because setups are pure geometry; executed here so sizing
    # sees the balance both lanes have produced so far.
    qf_by_i = {}
    if STRATEGY in ("qf", "both"):
        for s in qf_signals(candles):
            qf_by_i[s["i"]] = s
    qf_pos = None      # {dir, oz, entry, stop, tp, entry_t, expire_t}
```

**Exposure is deliberately NOT shared.** Do not add QuickFlip's open time to
HalfTrend's `expo[day]` budget. `MaxDailyExposureMin` paces ONE strategy's
time in market; sharing it would let QuickFlip consume HalfTrend's budget and
block its entries, which is exactly the interference this design forbids. The
account-level rails (drawdown, and the balance every lane sizes from) ARE
shared — the code above already updates `bal`, `peak_bal` and `max_dd`.

- [ ] **Step 5: Execute the lane inside the main loop**

At the very top of the `for i in range(...)` body — BEFORE HalfTrend's `continue` guards, so a warm-up skip can never swallow a QuickFlip bar — insert:

```python
        # ---- QuickFlip lane (independent of everything below) -------------
        if qf_pos is not None:
            qx = candles[i]
            hit = None
            if qf_pos["dir"] == "SELL":
                if qx["h"] >= qf_pos["stop"]:
                    hit = (qf_pos["stop"], "qf stop")
                elif qx["l"] <= qf_pos["tp"]:
                    hit = (qf_pos["tp"], "qf target")
            else:
                if qx["l"] <= qf_pos["stop"]:
                    hit = (qf_pos["stop"], "qf stop")
                elif qx["h"] >= qf_pos["tp"]:
                    hit = (qf_pos["tp"], "qf target")
            if hit is None and qx["t"] >= qf_pos["expire_t"]:
                hit = (qx["c"], "qf expired")
            if hit is not None:
                exit_px, why = hit
                sgn = 1.0 if qf_pos["dir"] == "BUY" else -1.0
                pl = (exit_px - qf_pos["entry"]) * sgn * qf_pos["oz"] \
                    - SPREAD_USD * qf_pos["oz"]
                bal += pl
                peak_bal = max(peak_bal, bal)
                max_dd = max(max_dd, peak_bal - bal)
                trades.append({
                    "lane": "qf", "dir": qf_pos["dir"],
                    "legs": [{"px": qf_pos["entry"], "oz": qf_pos["oz"],
                              "t": qf_pos["entry_t"]}],
                    "exit": exit_px, "when": hhmm(qx["t"])[0],
                    "exit_t": int(qx["t"]), "why": why, "pl": pl,
                    "opened_t": qf_pos["entry_t"],
                    "stop_history": [{"t": qf_pos["entry_t"],
                                      "stop": qf_pos["stop"]}],
                    "tp": qf_pos["tp"], "bal_after": bal,
                    "regime": None, "legs_count": 1})
                if verbose:
                    print(f"  qf    {hhmm(qx['t'])[0]:%m-%d %H:%M} "
                          f"{qf_pos['dir']} {qf_pos['oz']}oz "
                          f"@ {qf_pos['entry']:.2f} -> {exit_px:.2f} "
                          f"{why:>12}  P/L {pl:+8.2f}  bal {bal:9.2f}")
                qf_pos = None
        if qf_pos is None and i in qf_by_i:
            s = qf_by_i[i]
            dist = abs(s["entry"] - s["stop"])
            if dist > 0:
                oz = max(MIN_OZ, int(bal * QF_RISK_PCT / 100 / dist))
                qf_pos = {"dir": s["dir"], "oz": oz, "entry": s["entry"],
                          "stop": s["stop"], "tp": s["tp"],
                          "entry_t": s["entry_t"], "expire_t": s["expire_t"]}
```

- [ ] **Step 6: Gate the HalfTrend lane**

Immediately after the QuickFlip block, so `--strategy qf` runs QuickFlip alone:

```python
        if STRATEGY == "qf":
            continue
```

- [ ] **Step 7: Add the CLI flag**

In `build_parser()`'s **Rules** group:

```python
    rules.add_argument("--strategy", choices=("ht", "qf", "both"), default="both",
                       help="which lanes trade: ht = halftrend only (reproduces "
                            "every study before 2026-08-20), qf = quickflip "
                            "only, both = what runs live")
```

And in `main()`, beside the other global assignments:

```python
    global STRATEGY
    STRATEGY = args.strategy
```

Declare the module default next to the other strategy constants:

```python
STRATEGY = "both"      # ht | qf | both
```

- [ ] **Step 8: Run the new tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_lane.py -q
```

Expected: 6 passed. If `test_qf_only_produces_no_ht_trades` fails for lack of setups, print `len(bt.qf_signals(candles))` on the fixture and report it — do NOT change the fixture (its golden pin depends on it).

- [ ] **Step 9: Keep the HalfTrend goldens like-for-like**

The goldens must keep pinning HalfTrend alone. In BOTH `_replay()` and `_replay_strict()` in `service/tests/test_backtest_golden.py`, add:

```python
    bt.STRATEGY = "ht"
```

with a comment matching the ones already there, then:

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: 3 passed, unchanged numbers. **Do not regenerate either golden file.**

- [ ] **Step 10: Commit**

```bash
git add scripts/backtest.py service/tests/test_qf_lane.py service/tests/test_backtest_golden.py
git commit -m "feat(backtest): QuickFlip lane trades alongside HalfTrend on one balance"
```

---

### Task 4: Per-lane reporting, and a combined golden

**Files:**
- Modify: `scripts/backtest.py` — the report tail in `main()`, and `build_run_json()`
- Create: `service/tests/data/golden_trades_both.json`
- Modify: `service/tests/test_backtest_golden.py`
- Create: `service/tests/test_qf_report.py`

**Interfaces:**
- Consumes: `lane` on every trade (Task 3).
- Produces: stdout per-lane breakdown; `build_run_json()` emits `lane` per trade and a `lanes` block in `stats`; a third golden pinning `--strategy both`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_qf_report.py`:

```python
"""A combined run must report the lanes separately -- a blended number hides
which strategy actually made the money."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _artifact(balance=10000.0):
    bt = _load_bt()
    bt.STRATEGY = "both"
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, balance, False)
    args = bt.build_parser().parse_args(["--balance", str(balance)])
    return bt, bt.build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": dd, "valley": valley})


def test_every_trade_in_the_artifact_carries_its_lane():
    _bt, art = _artifact()
    assert art["trades"]
    assert all(t["lane"] in ("ht", "qf") for t in art["trades"])


def test_stats_break_down_by_lane():
    _bt, art = _artifact()
    lanes = art["stats"]["lanes"]
    for key in ("ht", "qf"):
        assert key in lanes
        for field in ("trades", "wins", "net"):
            assert field in lanes[key]


def test_lane_nets_sum_to_the_total():
    _bt, art = _artifact()
    lanes = art["stats"]["lanes"]
    total = round(lanes["ht"]["net"] + lanes["qf"]["net"], 2)
    assert abs(total - art["stats"]["net"]) < 0.02


def test_concurrency_is_reported():
    """How often both lanes held a position at once -- the thing a combined
    equity curve hides, and the reason exposure can exceed one lane's."""
    _bt, art = _artifact()
    assert "both_open_bars" in art["stats"]["lanes"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_report.py -q
```

Expected: FAIL — `lane` missing from the artifact / `lanes` missing from stats.

- [ ] **Step 3: Emit the lane and the per-lane stats**

In `build_run_json()`, add `"lane": t.get("lane", "ht"),` to the per-trade dict, and add to the `stats` dict:

```python
            "lanes": _lane_stats(trades),
```

Add above `build_run_json`:

```python
def _lane_stats(trades):
    """Per-lane breakdown plus how many bars both lanes were open together.
    A blended net hides which strategy earned it, and a combined equity curve
    hides that exposure can be doubled."""
    out = {}
    for lane in ("ht", "qf"):
        rows = [t for t in trades if t.get("lane", "ht") == lane]
        wins = sum(1 for t in rows if t["pl"] > 0)
        out[lane] = {
            "trades": len(rows),
            "wins": wins,
            "losses": sum(1 for t in rows if t["pl"] < 0),
            "win_rate": round(100.0 * wins / len(rows), 1) if rows else 0.0,
            "net": round(sum(t["pl"] for t in rows), 2),
            "best": round(max((t["pl"] for t in rows), default=0.0), 2),
            "worst": round(min((t["pl"] for t in rows), default=0.0), 2),
        }
    ht = [(t["legs"][0]["t"], t["exit_t"]) for t in trades
          if t.get("lane", "ht") == "ht"]
    qf = [(t["legs"][0]["t"], t["exit_t"]) for t in trades
          if t.get("lane") == "qf"]
    overlap = 0
    for a0, a1 in qf:
        for b0, b1 in ht:
            if a0 < b1 and b0 < a1:
                overlap += 1
                break
    out["both_open_bars"] = overlap
    return out
```

- [ ] **Step 4: Print the breakdown**

In `main()`'s report tail, before the `net P/L` line:

```python
    ls = _lane_stats(trades)
    if ls["ht"]["trades"] and ls["qf"]["trades"]:
        for lane, label in (("ht", "halftrend"), ("qf", "quickflip")):
            d = ls[lane]
            print(f"lane {label:<10} trades {d['trades']:>5}  win% "
                  f"{d['win_rate']:>5.1f}  net {d['net']:>10.2f}")
        print(f"           quickflip trades overlapping a halftrend position: "
              f"{ls['both_open_bars']}")
```

- [ ] **Step 5: Guard the HalfTrend-only report blocks**

Several report sections read keys only HalfTrend's baskets carry — the
min-stop savings block uses `t['orig_dist']` and `t['orig_oz']`, and the
regime, bias, chop, floor and hour-table breakdowns all assume HalfTrend
trades. A QuickFlip trade reaching them raises `KeyError` or silently
pollutes a per-regime table.

Filter at the source: at the top of the report tail in `main()`, immediately
after `trades` is available, derive

```python
    ht_trades = [t for t in trades if t.get("lane", "ht") == "ht"]
```

and use `ht_trades` in every HalfTrend-specific block (regime breakdown,
ATR-spike breakdown, bias/skip reporting, chop stats, floor stats, min-stop
savings, `--hour-table`, `--sr-report`). Leave the overall net/balance/
drawdown lines reading the full `trades` list — those describe the ACCOUNT,
which both lanes share.

Verify by running a combined run with the HalfTrend-only reports switched on:

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 10000 --strategy both --hour-table --min-stop-atr 0.5 2>&1 | tail -25
```

Expected: no traceback, and the per-regime/hour tables count HalfTrend trades
only.

- [ ] **Step 6: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_qf_report.py -q
```

Expected: 4 passed.

- [ ] **Step 7: Add the combined golden**

Append to `service/tests/test_backtest_golden.py`:

```python
GOLDEN_BOTH = DATA / "golden_trades_both.json"


def _replay_both():
    """Both lanes on one balance -- the configuration that ships."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    assert bt.STRATEGY == "both", "both-lane is supposed to be the default"
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def test_both_lane_replay_matches_golden():
    digest, bal, max_dd = _replay_both()
    _assert_matches(digest, bal, max_dd, GOLDEN_BOTH)
```

Extend `_digest()` to carry the lane, so a trade moving between lanes is caught:

```python
    return [{"lane": t.get("lane", "ht"), "dir": t["dir"], ...
```

(keep every existing field; add `lane` first). Then regenerate ONLY the two goldens that legitimately change shape — `golden_trades.json` and `golden_trades_strict.json` gain the `lane` key — and create the new one:

```bash
cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python - <<'PY'
import json, sys
sys.path.insert(0, '.')
from tests.test_backtest_golden import (_replay, _replay_strict, _replay_both,
                                        GOLDEN, GOLDEN_STRICT, GOLDEN_BOTH)
for fn, path, label in ((_replay, GOLDEN, "loose"),
                        (_replay_strict, GOLDEN_STRICT, "strict"),
                        (_replay_both, GOLDEN_BOTH, "both")):
    d, bal, dd = fn()
    path.write_text(json.dumps({"trades": d, "final_balance": bal, "max_dd": dd}, indent=1))
    print(f"{label:>7}: {len(d)} trades, bal {bal}, dd {dd}")
PY
```

**Before committing, verify the regeneration changed only the shape:** the loose and strict trade COUNTS, final balances and drawdowns must be identical to their previous values. If a number moved, a lane leaked into HalfTrend's path — stop and investigate.

- [ ] **Step 8: Full suite**

```bash
cd service && .venv/bin/python -m pytest -q
```

Expected: all pass.

- [ ] **Step 9: Run the real thing and read it**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
for s in ht qf both; do
  echo "--- $s ---"
  service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
      --balance 10000 --strategy $s 2>&1 | grep -E '^lane|^net P/L|overlapping'
done
```

Record all three in your report. The interesting number is whether `both` beats `ht` — that is the entire question this phase exists to answer.

- [ ] **Step 10: Commit**

```bash
git add scripts/backtest.py service/tests/
git commit -m "feat(backtest): per-lane reporting and a golden pin for the combined run"
```

---

### Task 5: Documentation

**Files:**
- Modify: `.claude/agents/izi.md`

- [ ] **Step 1: Document the lane, the numbers, and the bug**

Add a section covering:
- `--strategy ht|qf|both` (default `both`); `ht` reproduces every pre-2026-08-20 study.
- QuickFlip's rules and defaults (13:30 server, 10% of daily ATR, 90-min window, 0.25% risk, one trade/day, entry = close back inside the box after a sweep — NOT hammer/engulfing, which are unmeasured).
- **The time-convention trap, prominently**: candle `t` is server wall-clock; `hhmm()` reads it with no offset; server hour 00 is empty because it is the daily break. A +3h shift invalidated the first analysis and nearly put a mislabelled result into live trading.
- That QuickFlip is a **paid experiment at reduced size**, not a validated edge: 46 half-hours were searched, the slots passing a split test pass by ~$0.03–0.13/oz in the older half, and the pattern is the same recent-half-only shape seen across every study this week. Review after ~2 months of live logs.
- The three golden pins and what each guards.

- [ ] **Step 2: Commit**

```bash
git add .claude/agents/izi.md
git commit -m "docs(izi): the quickflip lane, its evidence, and the server-time trap"
```

---

## Phase 2 (separate plan, after this lands)

The EA side: `Strategies/QuickFlipNy.mqh`, a second `CTradeManager` on
`MagicNumber + QuickFlipMagicOffset`, attribution across the deal-history call
sites, and the hard requirement that `RiskManager` counts BOTH magics in the
daily-loss brake, drawdown and exposure accounting — otherwise QuickFlip's
losses are invisible to the 3% brake. That plan is written once this phase's
rules are proven in code.
