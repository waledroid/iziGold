# Backtest CLI + Visual Report — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the existing backtest engine an honest, inspectable output — a self-contained HTML report showing 12 months of M5 XAUUSD with MT5-style HalfTrend/EMA overlays and every replayed trade drawn with its SL/TP and the stop as it actually moved.

**Architecture:** `scripts/backtest.py` stays the engine and is extended, never rewritten — its behaviour is proven and every published study depends on it. A characterization ("golden") test is captured FIRST so no rule can change silently while the engine is edited. The engine gains the fields a chart needs (leg entry times, stop history, TP price, running balance), then emits one `--json` artifact. A separate module turns that artifact into a self-contained HTML page. The page draws trade boxes on an overlay `<canvas>` synced to the chart through Lightweight Charts' public coordinate API — deliberately not the v4 plugin/primitive internals.

**Tech Stack:** Python 3.12 (stdlib only for the engine; no new deps), pytest, TradingView Lightweight Charts v4.2.3 (already vendored at `service/app/static/vendor/lightweight-charts.standalone.production.js`).

**Spec:** `docs/superpowers/specs/2026-08-20-backtest-report-design.md`

**Scope:** This plan covers spec §1, §2, §3 and §5 — the CLI and the standalone report, which together are useful on their own. Spec §4 (Mini App tab) and §6 (day/month report views) are explicitly later phases and get their own plan once this one lands.

## Global Constraints

- **Never change replay behaviour except where a task says so.** Task 1's golden test is the gate; if it fails in Tasks 2, 4, 5, 6 or 7, the change is wrong, not the test.
- **The daily-loss brake and kill switch are NOT modelled.** Do not add them. (Owner decision, 2026-08-20.)
- **The news blackout is NOT modelled.** Do not add it.
- **No new Python dependencies.** The engine and report writer use the standard library only. `matplotlib` stays an optional import inside `plot()` exactly as it is today.
- **Indicators come from `app.indicators`** (`ema`, `halftrend` with `amplitude=4`) — the same functions the EA port and Mini App use. The report never computes an indicator or a rule itself.
- **The report page must be self-contained**: no `http://` or `https://` asset references. Everything is inlined.
- Run tests from `service/`: `cd service && .venv/bin/python -m pytest -q`.
- The repo root is `/mnt/c/Users/aatanda/Desktop/xau`. `bars_max.json` (99,999 M5 bars, 2025-03-19 → 2026-08-17) sits at the repo root and is NOT committed — never add it to git.
- Commit after every task. Follow the repo's commit style (`feat(backtest):`, `test(backtest):`, `fix(backtest):`).
- Per `CLAUDE.md`, `.claude/agents/izi.md` must be updated in the same commit as any behaviour change. Task 8 does this once for the whole feature; individual tasks need not.

---

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

### Task 3: The strict 3-bar entry rule becomes the default

The live EA enforces this always; the replay has it behind `--strict-window`. After this task the default run is what the EA would have done.

**Files:**
- Modify: `scripts/backtest.py` — the `STRICT_WINDOW` constant (~line 380 region, search `STRICT_WINDOW`), and `main()`'s argparse (~line 1087) and its assignment (~line 1157)
- Create: `service/tests/test_backtest_strict_window.py`

**Interfaces:**
- Consumes: `_load_bt()` from Task 1.
- Produces: module global `bt.STRICT_WINDOW: bool` now defaults to `True`. CLI gains `--loose-window` (sets it False). `--strict-window` still parses and is a no-op.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_strict_window.py`:

```python
"""The EA's law: after a HalfTrend flip, wait one closed bar, and enter on the
next bar only if it opens beyond EMA-55. Otherwise the signal is dead until
the next flip. The replay must default to that law."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def test_strict_window_is_the_default():
    bt = _load_bt()
    assert bt.STRICT_WINDOW is True


def test_loose_window_flag_turns_it_off():
    bt = _load_bt()
    args = bt.build_parser().parse_args(["--loose-window"])
    assert args.loose_window is True


def test_strict_window_flag_still_parses_as_a_noop():
    bt = _load_bt()
    args = bt.build_parser().parse_args(["--strict-window"])
    assert args.loose_window is False


def test_strict_takes_fewer_entries_than_loose():
    """Strict can only ever refuse entries loose would take, never add any."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.STRICT_WINDOW = True
    strict = bt.run(candles, 4000.0, False)[0]
    bt.STRICT_WINDOW = False
    loose = bt.run(candles, 4000.0, False)[0]
    assert len(strict) < len(loose), "strict must filter something in this slice"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py -q
```

Expected: FAIL — `STRICT_WINDOW is False`, and `build_parser` does not exist yet.

- [ ] **Step 3: Flip the default**

Find `STRICT_WINDOW = False` in the constants block and change it, with the reason recorded:

```python
STRICT_WINDOW = True      # EA law since 2026-08-16: flip -> wait one closed
                          # bar -> enter only if the next bar OPENS beyond the
                          # EMA, else the signal is dead until the next flip.
                          # --loose-window restores the pre-2026-08-16 replay.
```

- [ ] **Step 4: Extract the parser so tests can reach it**

`main()` currently builds its parser inline. Split the construction out (this is also what Task 5 needs):

```python
def build_parser():
    ap = argparse.ArgumentParser(...)   # the existing construction, unchanged
    ...
    return ap


def main():
    args = build_parser().parse_args()
    ...
```

Move every existing `ap.add_argument(...)` call into `build_parser()` verbatim. Do not reword or reorder them in this task — Task 5 does that.

- [ ] **Step 5: Add `--loose-window`, keep `--strict-window` as a no-op**

Replace the existing `--strict-window` argument with both:

```python
    ap.add_argument("--loose-window", action="store_true",
                    help="disable the EA's strict 3-bar entry window (flip -> "
                         "one waiting bar -> entry only if that bar opens "
                         "beyond the EMA). Use to reproduce studies run before "
                         "2026-08-20, when loose was the default.")
    ap.add_argument("--strict-window", action="store_true",
                    help=argparse.SUPPRESS)   # now the default; kept so older
                                              # scripted runs keep working
```

And where `STRICT_WINDOW = args.strict_window` was assigned:

```python
    STRICT_WINDOW = not args.loose_window
```

- [ ] **Step 6: Run the new tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_strict_window.py -q
```

Expected: 4 passed.

- [ ] **Step 7: Keep the golden test honest**

The golden file pinned LOOSE behaviour (it was the default when captured). The golden test must keep testing loose, so the pin stays comparable. Add the explicit setting to `_replay()` in `service/tests/test_backtest_golden.py`:

```python
def _replay():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    # The golden file pins LOOSE-window behaviour, captured 2026-08-20 before
    # strict became the default. Keep it loose so the pin stays a like-for-like
    # change detector across that default flip.
    bt.STRICT_WINDOW = False
    trades, bal, max_dd, _valley = bt.run(candles, 4000.0, False)
    return _digest(trades), round(bal, 2), round(max_dd, 2)
```

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py -q
```

Expected: PASS (unchanged numbers).

- [ ] **Step 8: Measure what the fix is worth over 12 months**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
echo "STRICT (new default):"; service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 --balance 4000 2>&1 | tail -3
echo "LOOSE (old behaviour):"; service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 --balance 4000 --loose-window 2>&1 | tail -3
```

Record both numbers in the commit message. This is the first real answer the tool produces: loose was −$1,998 (−50%) over the last 365 days at $4,000.

- [ ] **Step 9: Commit**

```bash
git add scripts/backtest.py service/tests/test_backtest_strict_window.py service/tests/test_backtest_golden.py
git commit -m "feat(backtest): strict 3-bar entry window is the default, --loose-window restores the old replay"
```

---

### Task 4: Balance validation and the sizing-clamp measurement

> **Superseded 2026-08-20 (post-review).** Every clamp figure quoted in this
> task was measured under the **LOOSE** entry window, before strict became the
> default. Re-measured on the shipped default (strict),
> `--source bars_max.json --days 365`: **94.7%** at $500, 68.5% at $800,
> **47.0%** at $1,200, 32.3% at $2,000, **16.7%** at $4,000, **1.3%** at
> $10,000, 0.0% at $25,000 (20.5% at $4,000 / 3.7% at $10,000 over the full
> 516-day source). The guidance moved with it: **$10,000+ is the floor for a
> clean test; $4,000 still clamps roughly one entry in six**, which trips the
> tool's own >10% "results distorted" flag. The >10% threshold itself is
> unchanged. Live text lives in `scripts/backtest.py` (constants comment,
> `validate_balance()`, the `--help` epilog), the report page, and spec §5.

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

### Task 5: Grouped `--help` with the caveat block

35 flags currently dump flat, and nothing tells the reader what the model does not simulate.

**Files:**
- Modify: `scripts/backtest.py` — `build_parser()`
- Create: `service/tests/test_backtest_help.py`

**Interfaces:**
- Consumes: `build_parser()` from Task 3.
- Produces: `bt.CAVEATS` — a `list[str]`, each one line, reused verbatim by Task 6's JSON artifact and Task 7's page header.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_help.py`:

```python
"""--help must state what the replay does NOT model. A result whose limits are
invisible gets trusted further than it deserves."""
from tests.test_backtest_golden import _load_bt


def test_caveats_name_the_unmodelled_rails():
    bt = _load_bt()
    blob = " ".join(bt.CAVEATS).lower()
    assert "brake" in blob
    assert "kill switch" in blob
    assert "news" in blob


def test_help_text_contains_the_caveats_and_the_balance_guidance():
    bt = _load_bt()
    text = bt.build_parser().format_help()
    assert "not modelled" in text.lower()
    assert "$4,000" in text or "4000" in text


def test_arguments_are_grouped():
    bt = _load_bt()
    titles = [g.title for g in bt.build_parser()._action_groups]
    for expected in ("Data", "Rules", "Experiments", "Output"):
        assert expected in titles, f"missing '{expected}' group in --help"


def test_every_argument_belongs_to_one_of_the_four_groups():
    bt = _load_bt()
    ap = bt.build_parser()
    named = {"Data", "Rules", "Experiments", "Output"}
    grouped = {a.dest for g in ap._action_groups if g.title in named
               for a in g._group_actions}
    ungrouped = {a.dest for a in ap._actions} - grouped - {"help"}
    assert not ungrouped, f"arguments outside the four groups: {sorted(ungrouped)}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_help.py -q
```

Expected: FAIL — `CAVEATS` does not exist.

- [ ] **Step 3: Define the caveats**

Near the top of `scripts/backtest.py`, below the module docstring:

```python
# Stated in --help, in the --json artifact and on the report page. A model's
# limits must travel with its output.
CAVEATS = [
    "daily-loss brake NOT modelled -- a real losing day would have been cut short",
    "kill switch NOT modelled -- a real 10% drawdown would have stopped trading",
    "news blackout NOT modelled -- no offline calendar of high-impact USD events",
    "acts on bar CLOSES only; fills at close +/- half-spread, $0.20/oz round trip",
    "no margin modelling and no stop-out: a small account can go negative here",
]
```

- [ ] **Step 4: Group the arguments**

In `build_parser()`, create the four groups before the `add_argument` calls and move each existing call onto its group. Change only which object the argument is added to — never the flag names, types, defaults or help strings:

```python
def build_parser():
    ap = argparse.ArgumentParser(
        description="Replay halftrend_ema_v1 with the current money rulebook "
                    "over historical candles and report P/L.",
        epilog="NOT MODELLED:\n  " + "\n  ".join(CAVEATS) +
               "\n\nSTARTING BALANCE: $4,000 minimum for meaningful results; "
               "$10,000+ for a clean\ntest of the risk rules. Below $2,000 most "
               "entries clamp to the 0.01 minimum\nlot and take more than the "
               "intended 1% risk; below $500 the run is refused.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    data = ap.add_argument_group("Data")
    rules = ap.add_argument_group(
        "Rules", "the EA's real knobs -- defaults are what the live EA does")
    exp = ap.add_argument_group(
        "Experiments", "study filters; all default to OFF (live EA has none)")
    out = ap.add_argument_group("Output")
```

Assignment (every existing argument, nothing dropped):

- **Data:** `--source --start --end --days --tf --balance`
- **Rules:** `--risk --confirm --stop-buffer --adx --expo --entry-mode --fixed-lots --profit-target --ema-len --loose-window --strict-window --exit-scheme`
- **Experiments:** `--regime-gate --atr-spike-gate --confirm-mode --chop-flips --chop-bars --chop-box-atr --chop-mode --min-stop-atr --bias-ema --bias-mode --bias-tf --sr-lookback --sr-min-headroom --window-start --window-end`
- **Output:** `--verbose --chart --hour-table --sr-report`

(Tasks 6 and 7 add `--json` and `--web` to **Output**.)

- [ ] **Step 5: Run the tests and read the help yourself**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_help.py -q
cd .. && service/.venv/bin/python scripts/backtest.py --help | head -40
```

Expected: 4 passed, and a help screen with four labelled sections and the caveat block at the end.

- [ ] **Step 6: Confirm nothing broke**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py tests/test_backtest_records.py tests/test_backtest_strict_window.py tests/test_backtest_balance.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/backtest.py service/tests/test_backtest_help.py
git commit -m "feat(backtest): group --help into Data/Rules/Experiments/Output and state what is not modelled"
```

---

### Task 6: `--json PATH` — the run artifact

One artifact, read by the standalone page now and the Mini App tab later. The page must never re-implement a rule.

**Files:**
- Modify: `scripts/backtest.py` — add `build_run_json()` and the `--json` flag
- Create: `service/tests/test_backtest_json.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: `bt.build_run_json(candles, trades, args, stats) -> dict` with keys `meta`, `stats`, `candles`, `ind`, `trades`, exactly as spec §2 describes. `stats` carries `clamp_pct`, `risk_median`, `risk_p90` from `run.sizing`.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_json.py`:

```python
"""The JSON artifact is the ONLY interface between engine and page."""
import json

from tests.test_backtest_golden import BARS, _load_bt


def _artifact(tmp_path, balance=10000.0):
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, balance, False)
    args = bt.build_parser().parse_args(["--balance", str(balance)])
    return bt, bt.build_run_json(candles, trades, args,
                                 {"bal": bal, "max_dd": dd, "valley": valley})


def test_candles_are_parallel_arrays_of_equal_length(tmp_path):
    _bt, art = _artifact(tmp_path)
    c = art["candles"]
    n = len(c["t"])
    assert n > 0
    for k in ("o", "h", "l", "c"):
        assert len(c[k]) == n


def test_indicator_series_align_with_the_candles(tmp_path):
    _bt, art = _artifact(tmp_path)
    n = len(art["candles"]["t"])
    for k in ("ema9", "ema21", "ema55", "ema200"):
        assert len(art["ind"][k]) == n
    assert len(art["ind"]["ht"]["v"]) == n
    assert len(art["ind"]["ht"]["trend"]) == n


def test_trades_carry_entry_times_stop_history_and_tp(tmp_path):
    _bt, art = _artifact(tmp_path)
    assert art["trades"]
    for t in art["trades"]:
        assert t["legs"] and all("t" in leg for leg in t["legs"])
        assert t["stop_history"]
        assert "tp" in t and "exit_t" in t and "bal_after" in t


def test_stats_carry_the_clamp_measurement(tmp_path):
    _bt, art = _artifact(tmp_path)
    for k in ("clamp_pct", "risk_median", "risk_p90", "net", "win_rate",
              "start_balance", "end_balance", "max_dd"):
        assert k in art["stats"], f"stats missing {k}"


def test_meta_carries_the_caveats(tmp_path):
    bt, art = _artifact(tmp_path)
    assert art["meta"]["caveats"] == bt.CAVEATS


def test_artifact_round_trips_through_json(tmp_path):
    _bt, art = _artifact(tmp_path)
    p = tmp_path / "run.json"
    p.write_text(json.dumps(art))
    assert json.loads(p.read_text())["stats"]["net"] == art["stats"]["net"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_json.py -q
```

Expected: FAIL — `build_run_json` does not exist.

- [ ] **Step 3: Implement `build_run_json()`**

Add to `scripts/backtest.py`, above `plot()`:

```python
def build_run_json(candles, trades, args, res):
    """The run artifact (spec 2026-08-20 section 2). Parallel arrays, not
    per-bar objects: 12 months of M5 is ~74k bars, and the array form roughly
    halves the payload with no loss of detail."""
    closes = [x["c"] for x in candles]
    ht = halftrend([type("C", (), x)() for x in candles], amplitude=AMPLITUDE)
    r2 = lambda v: None if v is None else round(v, 2)   # noqa: E731
    sizing = getattr(run, "sizing", {}) or {}
    n = len(trades)
    wins = sum(1 for t in trades if t["pl"] > 0)
    net = res["bal"] - args.balance
    return {
        "meta": {
            "generated_at": int(dt.datetime.now(dt.UTC).timestamp()),
            "source": args.source, "tf": TF, "bars": len(candles),
            "start": int(candles[0]["t"]), "end": int(candles[-1]["t"]),
            "strict_window": STRICT_WINDOW,
            "entry_mode": ENTRY_MODE,
            "args": {k: v for k, v in vars(args).items() if v is not None},
            "caveats": CAVEATS,
        },
        "stats": {
            "trades": n, "wins": wins, "losses": n - wins,
            "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
            "net": round(net, 2),
            "start_balance": round(args.balance, 2),
            "end_balance": round(res["bal"], 2),
            "max_dd": round(res["max_dd"], 2),
            "max_valley": round(res["valley"], 2),
            "best": round(max((t["pl"] for t in trades), default=0.0), 2),
            "worst": round(min((t["pl"] for t in trades), default=0.0), 2),
            "clamp_pct": sizing.get("clamp_pct", 0.0),
            "risk_median": sizing.get("risk_median", 0.0),
            "risk_p90": sizing.get("risk_p90", 0.0),
        },
        "candles": {
            "t": [int(x["t"]) for x in candles],
            "o": [r2(x["o"]) for x in candles],
            "h": [r2(x["h"]) for x in candles],
            "l": [r2(x["l"]) for x in candles],
            "c": [r2(x["c"]) for x in candles],
        },
        "ind": {
            "ema9": [r2(v) for v in ema(closes, 9)],
            "ema21": [r2(v) for v in ema(closes, 21)],
            "ema55": [r2(v) for v in ema(closes, 55)],
            "ema200": [r2(v) for v in ema(closes, 200)],
            "ht": {"v": [r2(p[0]) if p else None for p in ht],
                   "trend": [p[1] if p else None for p in ht]},
        },
        "trades": [{
            "dir": t["dir"],
            "legs": [{"t": leg["t"], "px": r2(leg["px"]), "oz": leg["oz"]}
                     for leg in t["legs"]],
            "tp": r2(t.get("tp")),
            "stop_history": [{"t": h["t"], "stop": r2(h["stop"])}
                             for h in t["stop_history"]],
            "exit": r2(t["exit"]), "exit_t": t["exit_t"], "why": t["why"],
            "pl": round(t["pl"], 2), "bal_after": round(t["bal_after"], 2),
            "regime": t.get("regime"),
        } for t in trades],
    }
```

- [ ] **Step 4: Add the flag and the write**

In `build_parser()`'s **Output** group:

```python
    out.add_argument("--json", default=None, metavar="PATH",
                     help="write the full run (candles, indicators, trades, "
                          "stats) to this JSON file")
```

In `main()`, in the report tail next to the `--chart` handling:

```python
    if args.json:
        art = build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        Path(args.json).write_text(json.dumps(art, separators=(",", ":")))
        print(f"json       {args.json} "
              f"({Path(args.json).stat().st_size / 1e6:.1f} MB)")
```

Use whatever local names `main()` already holds for the run results — check them before writing this, rather than assuming.

- [ ] **Step 5: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_json.py -q
```

Expected: 6 passed.

- [ ] **Step 6: Produce a real 12-month artifact and check its size**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 4000 --json /tmp/run12m.json 2>&1 | tail -4
service/.venv/bin/python -c "
import json; a=json.load(open('/tmp/run12m.json'))
print('bars', len(a['candles']['t']), 'trades', len(a['trades']))
print('stats', a['stats'])"
```

Expected: ~74,000 bars, a few hundred trades, and a file in the 6–7 MB range. Measured (`--days 365` M5 run, `--balance 4000`): 70,707 bars, 1,210 trades, 6.3 MB `--json` / 6.4 MB `--web` — recorded in izi.md. Those sizes are for `--days 365` only: a plain run over the whole source (516 days, 99,999 bars, 1,729 trades) writes ~8.9 MB JSON / ~9.0 MB HTML.

- [ ] **Step 7: Commit**

```bash
git add scripts/backtest.py service/tests/test_backtest_json.py
git commit -m "feat(backtest): --json writes the full run artifact (candles, indicators, trades, stats)"
```

---

### Task 7: `--web PATH` — the self-contained report page

**Files:**
- Create: `service/app/static/backtest_report.html` (the template; also the source the Mini App tab will reuse in a later phase)
- Create: `scripts/backtest_report.py` (the writer — keeps `backtest.py` from growing further; it is already ~1,450 lines)
- Modify: `scripts/backtest.py` — add the `--web` flag and call the writer
- Create: `service/tests/test_backtest_web.py`

**Interfaces:**
- Consumes: `build_run_json()` from Task 6.
- Produces: `backtest_report.write_report(artifact: dict, out_path: str) -> None` — inlines the vendored chart library, the template and the artifact into one HTML file.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_backtest_web.py`:

```python
"""The report must be one self-contained file: no network, no server."""
import importlib.util
import json
import pathlib
import re

from tests.test_backtest_golden import BARS, _load_bt

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _writer():
    spec = importlib.util.spec_from_file_location(
        "btr", ROOT / "scripts" / "backtest_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, 10000.0, False)
    args = bt.build_parser().parse_args(["--balance", "10000"])
    return bt.build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": dd, "valley": valley})


def test_report_is_written_and_is_not_empty(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    assert out.stat().st_size > 100_000


def test_report_references_no_external_assets(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for m in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', html):
        assert not m.startswith(("http://", "https://", "//")), \
            f"external asset would break offline use: {m}"


def test_report_embeds_the_chart_library_and_the_run(tmp_path):
    out = tmp_path / "r.html"
    art = _artifact()
    _writer().write_report(art, str(out))
    html = out.read_text(encoding="utf-8")
    assert "createChart" in html, "chart library not inlined"
    assert '"trades"' in html, "run artifact not inlined"
    assert str(art["stats"]["trades"]) in html


def test_no_placeholder_survives_into_the_output(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for token in ("__LIB__", "__DATA__", "__TITLE__"):
        assert token not in html, f"unsubstituted placeholder {token}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_web.py -q
```

Expected: FAIL — `scripts/backtest_report.py` does not exist.

- [ ] **Step 3: Write the page template**

Create `service/app/static/backtest_report.html`. Three placeholders are substituted by the writer: `__LIB__`, `__DATA__`, `__TITLE__`.

The chart matches the Mini App exactly (see `service/app/static/miniapp.html` lines ~1415-1435 for the series setup it mirrors): HalfTrend is ONE line whose colour changes per point — `HT_UP_COLOR = "#1e90ff"`, `HT_DOWN_COLOR = "#ff4500"` — plus EMA 9/21/55/200.

Trade boxes are drawn on an overlay `<canvas>` sitting above the chart, using only the public coordinate API (`timeScale().timeToCoordinate()`, `series.priceToCoordinate()`). This deliberately avoids the v4 plugin/primitive internals.

```html
<!doctype html>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0e1116; color:#d7dce3;
         font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:10px 14px; border-bottom:1px solid #232833; }
  h1 { margin:0 0 6px; font-size:15px; font-weight:600; }
  .stats { display:flex; flex-wrap:wrap; gap:14px; font-variant-numeric:tabular-nums; }
  .stat b { display:block; font-size:16px; }
  .stat span { color:#8b95a5; font-size:11px; text-transform:uppercase; }
  .pos { color:#26a69a; } .neg { color:#ef5350; }
  .warn { margin:8px 14px 0; padding:8px 10px; border-radius:6px;
          background:#3a2a12; border:1px solid #6b4a18; color:#f0c674; }
  .caveats { margin:6px 14px 10px; color:#8b95a5; font-size:11px; }
  #wrap { position:relative; height:62vh; margin:0 14px; }
  #chart, #overlay { position:absolute; inset:0; }
  #overlay { pointer-events:none; }
  #legend { position:absolute; top:6px; left:8px; z-index:3; font-size:11px;
            background:rgba(14,17,22,.72); padding:4px 7px; border-radius:4px;
            pointer-events:none; }
  #legend i { font-style:normal; margin-right:8px; }
  table { width:calc(100% - 28px); margin:12px 14px 40px; border-collapse:collapse;
          font-variant-numeric:tabular-nums; }
  th,td { padding:5px 8px; border-bottom:1px solid #1c212b; text-align:right; }
  th { color:#8b95a5; font-weight:500; text-align:right; position:sticky; top:0;
       background:#0e1116; }
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:#161b24; }
</style>

<header>
  <h1 id="title">XAUUSD backtest</h1>
  <div class="stats" id="stats"></div>
</header>
<div id="warnbox"></div>
<div class="caveats" id="caveats"></div>

<div id="wrap">
  <div id="chart"></div>
  <canvas id="overlay"></canvas>
  <div id="legend">
    <i style="color:#1e90ff">HalfTrend</i><i style="color:#e0e0e0">EMA9</i>
    <i style="color:#ffb74d">EMA21</i><i style="color:#42a5f5">EMA55</i>
    <i style="color:#ab47bc">EMA200</i>
  </div>
</div>

<table>
  <thead><tr><th>#</th><th>Opened</th><th>Dir</th><th>Legs</th><th>Entry</th>
  <th>Exit</th><th>Why</th><th>P/L</th><th>Balance</th></tr></thead>
  <tbody id="rows"></tbody>
</table>

<script>__LIB__</script>
<script>
var RUN = __DATA__;

var HT_UP = "#1e90ff", HT_DOWN = "#ff4500";
var GREEN = "rgba(38,166,154,0.18)", RED = "rgba(239,83,80,0.18)";
var GREEN_L = "#26a69a", RED_L = "#ef5350";

function money(v) { return (v >= 0 ? "+$" : "-$") + Math.abs(v).toFixed(2); }
function stamp(t) { return new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "); }

// ---- header -------------------------------------------------------------
(function header() {
  var s = RUN.stats, m = RUN.meta;
  document.getElementById("title").textContent =
    "XAUUSD " + m.tf + " backtest — " + stamp(m.start) + " to " + stamp(m.end) +
    "  (" + m.bars.toLocaleString() + " bars, " +
    (m.strict_window ? "strict" : "loose") + " entry window)";
  var cells = [
    ["Net P/L", money(s.net), s.net >= 0 ? "pos" : "neg"],
    ["Trades", String(s.trades), ""],
    ["Win rate", s.win_rate.toFixed(1) + "%", ""],
    ["Max drawdown", "$" + s.max_dd.toFixed(2), "neg"],
    ["Balance", "$" + s.start_balance.toFixed(0) + " → $" + s.end_balance.toFixed(2), ""],
    ["Risk taken", s.risk_median.toFixed(2) + "% / p90 " + s.risk_p90.toFixed(2) + "%", ""]
  ];
  document.getElementById("stats").innerHTML = cells.map(function (c) {
    return '<div class="stat"><b class="' + c[2] + '">' + c[1] + "</b><span>" + c[0] + "</span></div>";
  }).join("");
  if (s.clamp_pct > 10) {
    document.getElementById("warnbox").innerHTML =
      '<div class="warn"><b>' + s.clamp_pct.toFixed(1) +
      "% of entries were forced to the 0.01 minimum lot.</b> At this starting " +
      "balance the 1% risk rule is often not obeyed — this run measures " +
      "minimum-lot behaviour as much as the rulebook. $4,000 minimum for " +
      "meaningful results; $10,000+ for a clean test.</div>";
  }
  document.getElementById("caveats").textContent = "Not modelled: " + m.caveats.join(" · ");
})();

// ---- chart --------------------------------------------------------------
var chart = LightweightCharts.createChart(document.getElementById("chart"), {
  layout: { background: { color: "#0e1116" }, textColor: "#8b95a5" },
  grid: { vertLines: { color: "#171b24" }, horzLines: { color: "#171b24" } },
  rightPriceScale: { borderColor: "#232833" },
  timeScale: { borderColor: "#232833", timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 }
});

var noLabel = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
function line(color, width) {
  return chart.addLineSeries(Object.assign({ color: color, lineWidth: width }, noLabel));
}
var ema9 = line("#e0e0e0", 1), ema21 = line("#ffb74d", 1),
    ema55 = line("#42a5f5", 2), ema200 = line("#ab47bc", 1);
var htSeries = chart.addLineSeries(Object.assign({ color: HT_UP, lineWidth: 2 }, noLabel));
var stopSeries = chart.addLineSeries(Object.assign({
  color: "#f0c674", lineWidth: 1, lineStyle: 2,
  lineType: LightweightCharts.LineType.WithSteps }, noLabel));
var candles = chart.addCandlestickSeries({
  upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
  wickUpColor: "#26a69a", wickDownColor: "#ef5350" });

(function feed() {
  var C = RUN.candles, I = RUN.ind, n = C.t.length;
  var bars = [], e9 = [], e21 = [], e55 = [], e200 = [], ht = [];
  for (var i = 0; i < n; i++) {
    var t = C.t[i];
    bars.push({ time: t, open: C.o[i], high: C.h[i], low: C.l[i], close: C.c[i] });
    e9.push(I.ema9[i] == null ? { time: t } : { time: t, value: I.ema9[i] });
    e21.push(I.ema21[i] == null ? { time: t } : { time: t, value: I.ema21[i] });
    e55.push(I.ema55[i] == null ? { time: t } : { time: t, value: I.ema55[i] });
    e200.push(I.ema200[i] == null ? { time: t } : { time: t, value: I.ema200[i] });
    ht.push(I.ht.v[i] == null ? { time: t }
      : { time: t, value: I.ht.v[i], color: I.ht.trend[i] === 0 ? HT_UP : HT_DOWN });
  }
  candles.setData(bars);
  ema9.setData(e9); ema21.setData(e21); ema55.setData(e55); ema200.setData(e200);
  htSeries.setData(ht);

  // stepped stop line: one segment per trade, whitespace between them so
  // separate trades never join into one line
  var stops = [], seen = {};
  RUN.trades.forEach(function (tr) {
    tr.stop_history.forEach(function (h) { if (!seen[h.t]) { seen[h.t] = 1; stops.push({ time: h.t, value: h.stop }); } });
    if (!seen[tr.exit_t]) { seen[tr.exit_t] = 1; stops.push({ time: tr.exit_t, value: tr.stop_history[tr.stop_history.length - 1].stop }); }
    var gap = tr.exit_t + 300;
    if (!seen[gap]) { seen[gap] = 1; stops.push({ time: gap }); }
  });
  stops.sort(function (a, b) { return a.time - b.time; });
  stopSeries.setData(stops);

  // markers: entry, each pyramid add, exit
  var marks = [];
  RUN.trades.forEach(function (tr, i) {
    var up = tr.dir === "BUY";
    marks.push({ time: tr.legs[0].t, position: up ? "belowBar" : "aboveBar",
                 color: up ? GREEN_L : RED_L, shape: up ? "arrowUp" : "arrowDown",
                 text: "#" + (i + 1) });
    tr.legs.slice(1).forEach(function (leg) {
      marks.push({ time: leg.t, position: up ? "belowBar" : "aboveBar",
                   color: "#f0c674", shape: "circle", text: "+" });
    });
    marks.push({ time: tr.exit_t, position: up ? "aboveBar" : "belowBar",
                 color: tr.pl >= 0 ? GREEN_L : RED_L, shape: "square",
                 text: money(tr.pl) });
  });
  marks.sort(function (a, b) { return a.time - b.time; });
  candles.setMarkers(marks);
})();

// ---- trade boxes on the overlay canvas ----------------------------------
var cv = document.getElementById("overlay"), ctx = cv.getContext("2d");

function sizeCanvas() {
  var r = document.getElementById("wrap").getBoundingClientRect();
  var dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr);
  cv.style.width = r.width + "px"; cv.style.height = r.height + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawBoxes() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  var ts = chart.timeScale(), vr = ts.getVisibleRange();
  if (!vr) return;
  RUN.trades.forEach(function (tr) {
    var t0 = tr.legs[0].t, t1 = tr.exit_t;
    if (t1 < vr.from || t0 > vr.to) return;              // off screen
    var x0 = ts.timeToCoordinate(Math.max(t0, vr.from));
    var x1 = ts.timeToCoordinate(Math.min(t1, vr.to));
    if (x0 == null || x1 == null) return;
    var entry = candles.priceToCoordinate(tr.legs[0].px);
    var stop0 = candles.priceToCoordinate(tr.stop_history[0].stop);
    if (entry == null || stop0 == null) return;
    var w = Math.max(1, x1 - x0);
    // risk zone: entry -> initial stop
    ctx.fillStyle = RED;
    ctx.fillRect(x0, Math.min(entry, stop0), w, Math.abs(stop0 - entry));
    // reward zone: entry -> target (fixed mode has no target: nothing drawn)
    if (tr.tp != null) {
      var tp = candles.priceToCoordinate(tr.tp);
      if (tp != null) {
        ctx.fillStyle = GREEN;
        ctx.fillRect(x0, Math.min(entry, tp), w, Math.abs(tp - entry));
      }
    }
    ctx.strokeStyle = tr.pl >= 0 ? GREEN_L : RED_L;
    ctx.lineWidth = 1;
    ctx.strokeRect(x0 + 0.5, Math.min(entry, stop0) + 0.5, w, Math.abs(stop0 - entry));
  });
}

function redraw() { sizeCanvas(); drawBoxes(); }
chart.timeScale().subscribeVisibleTimeRangeChange(drawBoxes);
new ResizeObserver(redraw).observe(document.getElementById("wrap"));
redraw();

// ---- trade table --------------------------------------------------------
document.getElementById("rows").innerHTML = RUN.trades.map(function (t, i) {
  return '<tr data-i="' + i + '"><td>' + (i + 1) + "</td><td>" + stamp(t.legs[0].t) +
    "</td><td>" + t.dir + "</td><td>" + t.legs.length + "</td><td>" +
    t.legs[0].px.toFixed(2) + "</td><td>" + t.exit.toFixed(2) + "</td><td>" +
    t.why + '</td><td class="' + (t.pl >= 0 ? "pos" : "neg") + '">' + money(t.pl) +
    "</td><td>" + t.bal_after.toFixed(2) + "</td></tr>";
}).join("");

document.getElementById("rows").addEventListener("click", function (ev) {
  var tr = ev.target.closest("tr"); if (!tr) return;
  var t = RUN.trades[+tr.dataset.i], pad = 60 * 300;   // 60 M5 bars of context
  chart.timeScale().setVisibleRange({ from: t.legs[0].t - pad, to: t.exit_t + pad });
});
</script>
```

- [ ] **Step 4: Write the writer**

Create `scripts/backtest_report.py`:

```python
#!/usr/bin/env python3
"""Turn a backtest run artifact into ONE self-contained HTML file.

Self-contained on purpose: the report is an artifact you keep, mail, and
compare against next month's. It must open from disk with no server, no
network and no build step, so the chart library, the page and the run data are
all inlined.

The template lives in service/app/static/ so the Mini App tab (spec section 4)
can reuse the same drawing code later instead of growing a second one.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "service" / "app" / "static" / "backtest_report.html"
LIB = ROOT / "service" / "app" / "static" / "vendor" / \
    "lightweight-charts.standalone.production.js"


def write_report(artifact, out_path):
    """artifact: the dict from backtest.build_run_json(). Writes out_path."""
    html = TEMPLATE.read_text(encoding="utf-8")
    meta = artifact.get("meta", {})
    title = f"XAUUSD backtest — {meta.get('bars', 0)} bars, " \
            f"{len(artifact.get('trades', []))} trades"
    # Substitute data LAST: the artifact is arbitrary JSON and must never be
    # re-scanned for placeholder tokens.
    html = html.replace("__TITLE__", title)
    html = html.replace("__LIB__", LIB.read_text(encoding="utf-8"))
    html = html.replace("__DATA__", json.dumps(artifact, separators=(",", ":")))
    Path(out_path).write_text(html, encoding="utf-8")
```

- [ ] **Step 5: Wire `--web` into the CLI**

In `build_parser()`'s **Output** group:

```python
    out.add_argument("--web", default=None, metavar="PATH",
                     help="write a self-contained HTML report (chart with "
                          "HalfTrend/EMA overlays and every trade drawn with "
                          "its SL/TP and stop path) to this file")
```

In `main()`, beside the `--json` handling:

```python
    if args.web:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from backtest_report import write_report
        art = build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": max_dd, "valley": max_valley})
        write_report(art, args.web)
        print(f"report     {args.web} "
              f"({Path(args.web).stat().st_size / 1e6:.1f} MB)")
```

Reuse the artifact if `--json` already built one in the same run rather than building it twice.

- [ ] **Step 6: Run the tests**

```bash
cd service && .venv/bin/python -m pytest tests/test_backtest_web.py -q
```

Expected: 4 passed.

- [ ] **Step 7: Generate the real 12-month report and LOOK at it**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau
service/.venv/bin/python scripts/backtest.py --source bars_max.json --days 365 \
    --balance 4000 --web /tmp/backtest12m.html 2>&1 | tail -4
ls -la /tmp/backtest12m.html
```

Then open it (`explorer.exe` from WSL, or copy to the Desktop) and verify by eye:

1. Candles render across the whole 12 months and the page is usable (zoom/pan responsive).
2. HalfTrend is ONE line that changes colour blue↔orange-red — not two lines.
3. All four EMAs are present and the legend matches.
4. Trade boxes appear at trades, with the red risk zone below/above entry and the green target zone on the profit side.
5. The dashed stop line steps up (or down) within a pyramided trade.
6. Clicking a table row zooms the chart to that trade.
7. The header shows net P/L, win rate, drawdown, risk taken, and the caveat line.

Fix what does not match before committing. **Do not skip this step** — the unit tests prove the file is well-formed, not that the chart is right.

- [ ] **Step 8: Full suite**

```bash
cd service && .venv/bin/python -m pytest -q
```

Expected: all pass (437 existing + the new ones), 1 deselected.

- [ ] **Step 9: Commit**

```bash
git add scripts/backtest.py scripts/backtest_report.py \
        service/app/static/backtest_report.html service/tests/test_backtest_web.py
git commit -m "feat(backtest): --web writes a self-contained report with MT5-style overlays and trade boxes"
```

---

### Task 8: Documentation — izi.md and the README line

`CLAUDE.md` makes this law: behaviour changes update `.claude/agents/izi.md`.

**Files:**
- Modify: `.claude/agents/izi.md`
- Modify: `docs/superpowers/plans/2026-08-20-backtest-report.md` (tick the boxes)

- [x] **Step 1: Find the backtest section**

```bash
grep -n -i 'backtest' .claude/agents/izi.md | head -20
```

- [x] **Step 2: Document the new behaviour**

Update (or add) the backtest section to state, in izi.md's voice:

- `scripts/backtest.py` now defaults to the **strict 3-bar entry window** — the live EA's law since 2026-08-16. `--loose-window` reproduces studies run before 2026-08-20; every pre-2026-08-20 study in `.superpowers/` was a LOOSE run.
- `--balance` refuses below $500 and warns below $2,000. The binding constraint is the 0.01 minimum lot, not spread: measured over 12 months of M5, entries clamp 88.7% at $500, 50.8% at $1,200, 10.2% at $4,000, 0.4% at $10,000. Every run reports its clamp rate and the risk actually taken.
- `--json PATH` writes the run artifact; `--web PATH` writes a self-contained HTML report (chart + trade boxes + stepped stop). The template is `service/app/static/backtest_report.html`, shared with the Mini App tab planned in spec §4.
- The replay does NOT model the daily-loss brake, the kill switch, or the news blackout — and now says so in `--help` and on every report.
- `service/tests/test_backtest_golden.py` pins replay behaviour over a fixed slice; if it fails and you did not intend to change a rule, you broke one.

- [x] **Step 3: Record the headline result**

Add the strict-vs-loose 12-month comparison measured in Task 3 Step 8 to izi.md's history section — this is the first evidence of what the strict-entry fix is worth over a year.

- [x] **Step 4: Commit**

```bash
git add .claude/agents/izi.md docs/superpowers/plans/2026-08-20-backtest-report.md
git commit -m "docs(izi): backtest defaults, balance floors, --json/--web report"
```

---

## Later phases (not this plan)

- **Spec §4 — Mini App tab.** Serve the artifact at `/api/backtest` behind `viewer_ok()`, add a 📊 Backtest tab reusing `backtest_report.html`'s drawing code, plus the balance input with $1k/$4k/$10k/$25k presets and a `[Run backtest]` button (a 12-month replay takes ~3.6 s). Enable `GZipMiddleware` there — the artifact is 6.3–6.4 MB over the wire for a `--days 365` run (70,707 bars, 1,210 trades) and ~8.9 MB for the full 516-day source (99,999 bars, 1,729 trades).
- **Spec §6 — day/month report views.** Refactor `_report_month()` / `_report_day()` in `service/app/miniapp.py` to take a list of baskets instead of a sqlite connection, capture a golden test of today's live output first, then feed them backtest trades so the live and backtest reports share one shaper and one renderer.
