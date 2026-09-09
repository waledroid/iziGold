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

