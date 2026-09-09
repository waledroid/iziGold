### Task 1: Golden-run harness — capture today's behaviour before touching anything

This is the safety net for every later task. It replays a fixed slice of real data and pins the exact trade list the CURRENT code produces.

**Files:**
- Create: `service/tests/data/bars_slice.json`
- Create: `service/tests/data/golden_trades.json`
- Create: `service/tests/test_backtest_golden.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `service/tests/test_backtest_golden.py::_load_bt()` — loads `scripts/backtest.py` as a module named `bt`; later tasks reuse this helper for their own tests. `bt.run(candles, start_balance, verbose)` returns `(trades, bal, max_dd, max_valley)`.

- [ ] **Step 1: Cut a fixture slice from the real data**

Run from the repo root:

```bash
mkdir -p service/tests/data
service/.venv/bin/python - <<'PY'
import json
bars = json.load(open('bars_max.json'))
c = bars['candles'] if isinstance(bars, dict) and 'candles' in bars else bars
# 6000 M5 bars ~= 21 calendar days: enough for ~100 trades, ~350 KB on disk.
# Taken from the middle of the history so it contains both trends and chop.
slice_ = c[40000:46000]
json.dump(slice_, open('service/tests/data/bars_slice.json', 'w'), separators=(',', ':'))
print('bars', len(slice_), 'first', slice_[0]['t'], 'last', slice_[-1]['t'])
PY
ls -la service/tests/data/bars_slice.json
```

Expected: 6000 bars, file well under 1 MB.

- [ ] **Step 2: Write the golden test (it will fail — no golden file yet)**

Create `service/tests/test_backtest_golden.py`:

```python
"""Characterization test for the backtest engine.

Pins the exact trade list `scripts/backtest.py` produces over a fixed slice
of real M5 data. This is NOT a correctness test -- it is a change detector.
If it fails, the replay's behaviour moved. Unless the task you are doing
explicitly changes replay behaviour, the change is a bug.

Regenerate deliberately (and only deliberately) with:
    cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py --regen-golden
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(__file__).parent / "data"
BARS = DATA / "bars_slice.json"
GOLDEN = DATA / "golden_trades.json"


def _load_bt():
    """Import scripts/backtest.py as a module. It is a script, not a package
    member, so it is loaded by path; importing it is side-effect free (the
    argparse/main call is guarded by __name__ == '__main__')."""
    spec = importlib.util.spec_from_file_location(
        "bt", ROOT / "scripts" / "backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(trades):
    """Compact, human-diffable summary of a trade list."""
    return [{"dir": t["dir"],
             "entry": round(t["legs"][0]["px"], 2),
             "legs": len(t["legs"]),
             "exit": round(t["exit"], 2),
             "why": t["why"],
             "pl": round(t["pl"], 2)} for t in trades]


def _replay():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)


def test_replay_matches_golden():
    digest, bal, max_dd = _replay()
    golden = json.loads(GOLDEN.read_text())
    assert len(digest) == len(golden["trades"]), (
        f"trade COUNT moved: {len(golden['trades'])} -> {len(digest)}")
    for i, (got, want) in enumerate(zip(digest, golden["trades"])):
        assert got == want, f"trade {i} changed:\n  was {want}\n  now {got}"
    assert bal == golden["final_balance"]
    assert max_dd == golden["max_dd"]
```

- [ ] **Step 3: Run it to confirm it fails for the right reason**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: FAIL — `FileNotFoundError: .../golden_trades.json`. If it fails with an import error instead, fix the loader before continuing.

- [ ] **Step 4: Generate the golden file from the current (unmodified) engine**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau/service && .venv/bin/python - <<'PY'
import json, sys, pathlib
from tests.test_backtest_golden import _replay, GOLDEN
digest, bal, dd = _replay()
GOLDEN.write_text(json.dumps(
    {"trades": digest, "final_balance": bal, "max_dd": dd}, indent=1))
print("golden trades:", len(digest), "final bal", bal, "max dd", dd)
PY
```

Expected: a non-zero trade count printed (roughly 100 for a 21-day slice). **If it prints 0 trades, stop** — the slice landed outside the trading window or the data is malformed; pick a different slice in Step 1 and regenerate.

- [ ] **Step 5: Run the test — it must now pass**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: 1 passed.

- [ ] **Step 6: Prove it actually detects change**

Temporarily break a rule and confirm the test screams:

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
sed -i 's/^RISK_PCT = 1.0/RISK_PCT = 1.5/' scripts/backtest.py
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q 2>&1 | tail -3
cd .. && sed -i 's/^RISK_PCT = 1.5/RISK_PCT = 1.0/' scripts/backtest.py
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: FAIL while modified, PASS after reverting. A golden test that cannot fail is worthless — do not skip this step.

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
git add service/tests/data/bars_slice.json service/tests/data/golden_trades.json service/tests/test_backtest_golden.py
git commit -m "test(backtest): golden-run characterization test over a fixed M5 slice"
```

---

