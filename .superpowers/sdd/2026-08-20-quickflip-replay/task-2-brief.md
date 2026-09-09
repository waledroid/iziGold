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

