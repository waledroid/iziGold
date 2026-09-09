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

