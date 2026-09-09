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

