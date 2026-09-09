### Task 4: Balance validation and the sizing-clamp measurement

Small balances do not fail loudly — `oz = max(MIN_OZ, int(risk / dist))` takes the minimum lot and over-risks. Measure it and say so.

**Files:**
- Modify: `scripts/backtest.py` — the sizing site in `run()` (~line 925), `run()`'s preamble, `main()`'s balance handling and its report tail
- Create: `service/tests/test_backtest_balance.py`

**Interfaces:**
- Consumes: `build_parser()` from Task 3, `_load_bt()` from Task 1.
- Produces:
  - `bt.MIN_BALANCE = 500.0` and `bt.WARN_BALANCE = 2000.0`
  - `run.sizing` — a dict set as a function attribute after every `run()` call (same pattern the file already uses for `run.bias_flips`): `{"entries": int, "clamped": int, "clamp_pct": float, "risk_median": float, "risk_p90": float}`. `clamp_pct` and the risk percentiles are `0.0` when no entry was sized.
  - `bt.validate_balance(value: float) -> str | None` — returns `None` when the balance is fine, a warning string when it is between `MIN_BALANCE` and `WARN_BALANCE`, and raises `SystemExit` below `MIN_BALANCE`.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_balance.py`:

```python
"""Below ~$4,000 the 1% risk rule stops being obeyed: sizing clamps to the
0.01 minimum lot and over-risks rather than skipping the trade. Measured over
12 months of M5: 51% of entries clamp at $1,200, 89% at $500."""
import json

import pytest

from tests.test_backtest_golden import BARS, _load_bt


def test_below_minimum_balance_refuses_to_run():
    bt = _load_bt()
    with pytest.raises(SystemExit):
        bt.validate_balance(300.0)


def test_small_balance_warns_but_runs():
    bt = _load_bt()
    msg = bt.validate_balance(1200.0)
    assert msg is not None and "0.01" in msg


def test_healthy_balance_is_silent():
    bt = _load_bt()
    assert bt.validate_balance(10000.0) is None


def test_sizing_stats_are_recorded_after_a_run():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.run(candles, 10000.0, False)
    s = bt.run.sizing
    assert s["entries"] > 0
    assert 0.0 <= s["clamp_pct"] <= 100.0
    assert s["risk_median"] > 0.0


def test_a_small_account_clamps_far_more_than_a_large_one():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.run(candles, 800.0, False)
    small = bt.run.sizing["clamp_pct"]
    bt.run(candles, 25000.0, False)
    large = bt.run.sizing["clamp_pct"]
    assert small > large + 20.0, (
        f"expected a large clamp gap, got {small:.1f}% vs {large:.1f}%")
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_balance.py -q
```

Expected: FAIL — `validate_balance` does not exist.

- [ ] **Step 3: Add the thresholds and the validator**

Near the other constants in `scripts/backtest.py`:

```python
# Starting-balance floors. The binding constraint is the 0.01 minimum lot,
# not spread: when 1% of balance cannot cover one ounce at the stop distance,
# sizing clamps to the minimum and OVER-risks instead of skipping the trade.
# Measured over 12 months of M5 (2025-08 -> 2026-08): entries clamped 88.7% at
# $500, 50.8% at $1,200, 10.2% at $4,000, 0.4% at $10,000.
MIN_BALANCE = 500.0     # below this the result is fiction (no margin stop-out
                        # is modelled either -- a $300 account goes negative)
WARN_BALANCE = 2000.0   # below this, warn loudly and name the clamp rate


def validate_balance(value):
    """None = fine, str = warn and run, SystemExit = refuse."""
    if value < MIN_BALANCE:
        raise SystemExit(
            f"--balance {value:.0f} is below the ${MIN_BALANCE:.0f} floor.\n"
            "At that size nearly every entry clamps to the 0.01 minimum lot, "
            "so the replay measures minimum-lot behaviour, not the rulebook -- "
            "and margin stop-out is not modelled, so the account can go "
            "negative. Use $4,000+ for meaningful results, $10,000+ for a "
            "clean test of the risk rules.")
    if value < WARN_BALANCE:
        return (f"WARNING: at ${value:.0f}, 1% risk often cannot cover one "
                f"ounce at the stop distance, so sizing falls back to the "
                f"0.01 minimum lot and takes MORE than 1% risk. The clamp "
                f"rate for this run is reported below -- read it before "
                f"trusting the P/L.")
    return None
```

- [ ] **Step 4: Measure the clamp at the sizing site**

In `run()`, initialise the counters in the preamble (next to `trades = []`):

```python
    sizing = {"entries": 0, "clamped": 0, "risk_pct": []}
```

At the sizing site, replace:

```python
                        risk = bal * rp / 100
                        oz = max(MIN_OZ, int(risk / dist))
```

with:

```python
                        risk = bal * rp / 100
                        want = int(risk / dist)
                        oz = max(MIN_OZ, want)
                        sizing["entries"] += 1
                        if want < MIN_OZ:
                            sizing["clamped"] += 1
                        sizing["risk_pct"].append(100.0 * oz * dist / bal)
```

At the end of `run()`, just before `return trades, bal, max_dd, max_valley`, publish the summary the same way the file already publishes `run.bias_flips`:

```python
    r = sorted(sizing["risk_pct"])
    n = sizing["entries"]
    run.sizing = {
        "entries": n,
        "clamped": sizing["clamped"],
        "clamp_pct": round(100.0 * sizing["clamped"] / n, 1) if n else 0.0,
        "risk_median": round(r[len(r) // 2], 2) if r else 0.0,
        "risk_p90": round(r[int(0.9 * len(r))], 2) if r else 0.0,
    }
```

Note `fixed` entry mode does not risk-size, so it records no entries and reports `0.0` — correct, not a bug.

- [ ] **Step 5: Wire validation and reporting into `main()`**

Right after `args = build_parser().parse_args()`:

```python
    warning = validate_balance(args.balance)
    if warning:
        print(warning)
```

And in the report tail, next to the `net P/L` line:

```python
    s = getattr(run, "sizing", None)
    if s and s["entries"]:
        flag = "  <-- results distorted" if s["clamp_pct"] > 10 else ""
        print(f"sizing     {s['clamp_pct']:.1f}% of entries clamped to the "
              f"0.01 minimum lot{flag}")
        print(f"           risk actually taken: median {s['risk_median']:.2f}% "
              f"p90 {s['risk_p90']:.2f}%  (target {RISK_PCT:.2f}%)")
```

- [ ] **Step 6: Run the new tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_balance.py -q
```

Expected: 5 passed.

- [ ] **Step 7: Verify against the real 12 months and confirm no behaviour drift**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 --balance 1200 2>&1 | tail -5
service/.venv/bin/python scripts/backtest.py --source bars_max.json --balance 300 ; echo "exit=$?"
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: the $1,200 run prints the warning and a clamp rate near 51%; the $300 run refuses with exit 1; the golden test passes (counting is not deciding).

- [ ] **Step 8: Commit**

```bash
git add scripts/backtest.py service/tests/test_backtest_balance.py
git commit -m "feat(backtest): --balance floor at \$500, warn under \$2000, report min-lot clamp rate and realized risk"
```

---

