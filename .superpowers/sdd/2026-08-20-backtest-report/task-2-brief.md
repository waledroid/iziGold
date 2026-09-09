### Task 2: Record what the chart needs — leg times, stop history, TP price, running balance

**Files:**
- Modify: `scripts/backtest.py` — `run()` (leg creation at the entry site ~line 933 and the add site ~line 836; the stop-assignment sites ~lines 813, 849, 851; `close_basket()` ~line 672)
- Create: `service/tests/test_backtest_records.py`

**Interfaces:**
- Consumes: `_load_bt()` from Task 1.
- Produces: each trade dict gains
  - `legs[i]["t"]` — `int` epoch seconds of the bar that filled that leg
  - `stop_history` — `list[dict]`, each `{"t": int, "stop": float}`, appended on every stop change including the initial placement, in chronological order
  - `tp` — `float | None`, the price at which the profit target would be realized (`None` in `fixed` entry mode, which has no target)
  - `bal_after` — `float`, account balance after the basket closed
  - `opened_t` — `int` epoch seconds of the first leg (convenience mirror of `legs[0]["t"]`)

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_records.py`:

```python
"""The chart cannot draw what the engine does not record: entry times, the
stop as it actually moved, and the target price."""
import json
import pathlib

from tests.test_backtest_golden import BARS, _load_bt


def _trades():
    bt = _load_bt()
    return bt.run(json.loads(BARS.read_text()), 4000.0, False)[0]


def test_every_leg_carries_its_bar_time():
    for t in _trades():
        for leg in t["legs"]:
            assert isinstance(leg["t"], int) and leg["t"] > 0
        # legs fill in chronological order
        times = [leg["t"] for leg in t["legs"]]
        assert times == sorted(times)


def test_opened_t_mirrors_first_leg():
    for t in _trades():
        assert t["opened_t"] == t["legs"][0]["t"]


def test_stop_history_starts_at_entry_and_never_goes_backwards_in_time():
    for t in _trades():
        hist = t["stop_history"]
        assert hist, "every basket places a stop at entry"
        assert hist[0]["t"] == t["legs"][0]["t"]
        assert [h["t"] for h in hist] == sorted(h["t"] for h in hist)


def test_a_trade_with_adds_records_more_stop_moves_than_a_single_leg_trade():
    trades = _trades()
    multi = [t for t in trades if len(t["legs"]) > 1]
    assert multi, "the slice must contain at least one pyramided basket"
    # every add ladders the shared stop, so history grows past the initial one
    assert any(len(t["stop_history"]) > 1 for t in multi)


def test_tp_is_a_price_on_the_profitable_side_of_entry():
    for t in _trades():
        if t["tp"] is None:
            continue
        entry = t["legs"][0]["px"]
        if t["dir"] == "BUY":
            assert t["tp"] > entry
        else:
            assert t["tp"] < entry


def test_bal_after_chains_to_the_final_balance():
    bt = _load_bt()
    trades, bal, _dd, _v = bt.run(json.loads(BARS.read_text()), 4000.0, False)
    assert trades
    assert round(trades[-1]["bal_after"], 2) == round(bal, 2)
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_records.py -q
```

Expected: FAIL with `KeyError: 't'` / `KeyError: 'stop_history'`.

- [ ] **Step 3: Record leg times and the initial stop**

In `run()`, at the entry site where the basket is created (search for `basket = {"dir": signal, "legs": [{"px": px, "oz": oz}],`), change the leg to carry the bar time and seed the stop history:

```python
                    basket = {"dir": signal, "legs": [{"px": px, "oz": oz, "t": int(x["t"])}],
                              "stop": stop, "peak": 0.0, "cycle_bal": bal,
                              "stop_history": [{"t": int(x["t"]), "stop": stop}],
                              "opened_t": int(x["t"]),
                              "dist_atr": (orig_dist / a) if a else None,
                              "floored": floored, "orig_stop": orig_stop,
```

Keep every other key in that dict exactly as it is — only the three additions above and the `"t"` on the leg are new.

- [ ] **Step 4: Record the add's leg time**

At the pyramid-add site (search for `basket["legs"].append({"px": px, "oz": oz})`):

```python
                        basket["legs"].append({"px": px, "oz": oz, "t": int(x["t"])})
```

- [ ] **Step 5: Record every stop change**

The stop is assigned in three places. Add a helper immediately above the `for i in range(...)` main loop inside `run()`:

```python
    def note_stop(bk, t, stop):
        """Append to the basket's stop history when the stop actually moved."""
        hist = bk["stop_history"]
        if not hist or hist[-1]["stop"] != stop:
            hist.append({"t": int(t), "stop": stop})
```

Then, after EACH of the three `basket["stop"] = ...` assignments (the floor-arm site, and both ladder sites in the add block), add the matching call:

```python
                        if fpx * s > basket["stop"] * s:
                            basket["stop"] = fpx
                            note_stop(basket, x["t"], fpx)
```

```python
                        if basket.get("floor") is not None:
                            if ladder * s > basket["stop"] * s:
                                basket["stop"] = ladder
                                note_stop(basket, x["t"], ladder)
                        else:
                            basket["stop"] = ladder
                            note_stop(basket, x["t"], ladder)
```

- [ ] **Step 6: Record the TP price and the running balance**

The profit target is computed as a dollar amount (`target = basket["cycle_bal"] * PROFIT_TARGET_PCT / 100 ...`). Immediately after that line, store it as a PRICE using the existing `floor_price()` helper, which converts a target amount into the price that realizes it:

```python
                    if basket.get("tp") is None and ENTRY_MODE != "fixed":
                        basket["tp"] = floor_price(basket["legs"], s, target)
```

Then in `close_basket()`, extend the appended dict (keep every existing key):

```python
        trades.append({"dir": basket["dir"], "legs": list(basket["legs"]),
                       "exit": px, "when": when, "why": why, "pl": pl,
                       "opened_t": basket.get("opened_t"),
                       "exit_t": int(when.timestamp()),
                       "stop_history": list(basket.get("stop_history", [])),
                       "tp": basket.get("tp"),
                       "bal_after": bal,
                       "opened": basket.get("opened"),
```

Note `bal` is already updated at the top of `close_basket()` (`bal += pl`), so `bal_after` is correct at this point.

- [ ] **Step 7: Run the new tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_records.py -q
```

Expected: 6 passed. If `test_a_trade_with_adds_...` fails for lack of a pyramided basket, widen the fixture slice in Task 1 Step 1 rather than weakening the test.

- [ ] **Step 8: Run the golden test — behaviour must NOT have moved**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: PASS. This task only records; it must change no decision. If it fails, you have altered a rule — revert and redo.

- [ ] **Step 9: Commit**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
git add scripts/backtest.py service/tests/test_backtest_records.py
git commit -m "feat(backtest): record leg times, stop history, TP price and running balance"
```

---

