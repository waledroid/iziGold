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

