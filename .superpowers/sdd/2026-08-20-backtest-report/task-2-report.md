# Task 2 report — record what the chart needs

## Summary

Added five recorded fields to `scripts/backtest.py`'s `run()` so later tasks
(JSON artifact, HTML report) have leg fill times, the stop's full history,
the profit-target price, and the running account balance to draw from.
Task 2 only records; it changes no trading decision — confirmed by the
Task 1 golden test still passing unchanged.

## Fields added and where

All edits in `/mnt/c/Users/aatanda/Desktop/xau/scripts/backtest.py`, inside `run()`.

1. **`legs[i]["t"]`** — `int` epoch seconds of the bar that filled that leg.
   - Entry leg: set at basket creation (~line 933), `{"px": px, "oz": oz, "t": int(x["t"])}`.
   - Add leg: set at the pyramid-add site (~line 839), `basket["legs"].append({"px": px, "oz": oz, "t": int(x["t"])})`.

2. **`stop_history`** — `list[dict]` of `{"t": int, "stop": float}`, appended
   chronologically on every stop change including the initial placement.
   - Seeded at basket creation (~line 935): `"stop_history": [{"t": int(x["t"]), "stop": stop}]`.
   - A new `note_stop(bk, t, stop)` helper (added immediately above the main
     `for i in range(...)` loop, ~line 708) appends only when the stop value
     actually changed (`hist[-1]["stop"] != stop`), matching the brief's
     dedup rule.
   - Called at the three stop-assignment sites:
     - floor-arm site (~line 814, after `basket["stop"] = fpx`)
     - ladder-under-floor site (~line 850, after the tightening `basket["stop"] = ladder`)
     - ladder-unarmed site (~line 853, after the unconditional `basket["stop"] = ladder`)

3. **`tp`** — `float | None`, the price at which the profit target is
   realized. Computed once per basket, immediately after `target = ...`
   (~line 797):
   ```python
   if basket.get("tp") is None and ENTRY_MODE != "fixed":
       basket["tp"] = floor_price(basket["legs"], s, target)
   ```
   Guarded by `basket.get("tp") is None` so it is set once, on the first
   pass through the non-hit branch for that basket, and never recomputed as
   the basket grows (adds change `cycle_bal`-derived target math, but the
   recorded `tp` intentionally stays pinned to the first computation, per
   the brief's resolved ambiguity).

4. **`bal_after`** — `float`, account balance after the basket closed.
   Added to the dict appended in `close_basket()` (~line 676) as
   `"bal_after": bal` — `bal` is already updated by `bal += pl` at the top
   of `close_basket()`, so this is correct at the append site.

5. **`opened_t`** — `int` epoch seconds of the first leg, a convenience
   mirror of `legs[0]["t"]`. Set on the basket dict at creation
   (~line 936, `"opened_t": int(x["t"])`) and copied onto the trade dict in
   `close_basket()` as `"opened_t": basket.get("opened_t")`.

As a bonus consistent with the brief's `close_basket()` snippet, `exit_t`
(`int(when.timestamp())`) was also added — it wasn't separately called out
in the field list but is part of the literal Step 6 snippet and is needed
to pair with `opened_t` for duration on a chart.

No other keys in the basket-creation dict, the leg-append call, or the
`trades.append({...})` dict in `close_basket()` were touched.

## Tests

Created `/mnt/c/Users/aatanda/Desktop/xau/service/tests/test_backtest_records.py`
verbatim from the brief (6 tests). Ran from `service/`:

Before the implementation (Step 2, expected failures):
```
tests/test_backtest_records.py:41: KeyError
...
6 failed in 3.91s
```
(`KeyError: 't'`, `'opened_t'`, `'stop_history'`, `'tp'`, `'bal_after'` — as predicted.)

After implementation:
```
$ .venv/bin/python -m pytest tests/test_backtest_records.py -q
......                                                                   [100%]
6 passed in 2.04s
```

### Golden test (the gate) — unchanged behaviour confirmed

```
$ .venv/bin/python -m pytest tests/test_backtest_golden.py -q
.                                                                        [100%]
1 passed in 1.21s
```

Still pins 114 trades, final balance 3906.30ish, max drawdown 468.06 — no
decision moved.

### Full suite

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 48%]
........................................................................ [ 64%]
........................................................................ [ 81%]
........................................................................ [ 97%]
............                                                             [100%]
444 passed, 1 deselected, 3 warnings in 74.15s (0:01:14)
```

(1 deselected = the `-m slow` real-Chronos test, excluded by default per
`pyproject.toml` addopts — unaffected by this change.)

## Notes for later tasks

- **`stop_history` length tracks leg count exactly in this fixture**: for
  all 114 trades, `len(stop_history) == len(legs)` (leg-count distribution
  `{1: 45, 2: 47, 3: 22}` maps to the identical stop_history-length
  distribution). Every add in this slice happened to move the stop (the
  ladder was always tighter than the current stop when the add fired), so
  don't assume `stop_history` is always strictly longer than `legs` — the
  dedup in `note_stop` means a basket *could* in principle have more legs
  than stop moves (e.g. an add whose ladder doesn't tighten the stop under
  `floor` conditions), just not in this fixture.

- **`tp` can be `None` even outside `fixed` entry mode.** Of 114 trades, 3
  have `tp is None` despite `ENTRY_MODE != "fixed"`. All three are
  `why == "stop"` exits that happened on the very next bar after entry
  (exactly 300s / one M5 bar later) — the basket's stop was hit on the
  first pass through the open-basket management block, so execution never
  reached the `else` (non-hit) branch where `tp` gets computed. This is a
  real, expected case (not a bug): a chart/report consumer must treat `tp`
  as `Optional[float]` for reasons beyond "fixed mode" — genuinely
  immediate stop-outs also leave it unset. The brief's own test
  (`test_tp_is_a_price_on_the_profitable_side_of_entry`) already skips
  `None` regardless of cause, so this is compatible with Task 2's contract
  as written; just flagging it so Task 3+ doesn't assume "`tp is None`"
  implies "`fixed` mode".

- 111/114 trades have a non-None `tp`; 3/114 are the immediate-stop-out
  case above; the fixture's `ENTRY_MODE` is not `fixed` for any trade (0
  trades with `tp is None` due to fixed mode in this particular slice) —
  so fixed-mode `tp is None` behaviour is exercised only by the brief's
  logic/guard, not by a concrete example in this fixture. Worth a synthetic
  unit check in a later task if fixed-mode charting is exercised directly.

- Full new key set on a trade dict, for reference:
  `atr_ratio, bal_after, bars_open, bias, bias_ema, chop, chop_box,
  chop_flips, cycle_bal, dir, dist_atr, entry_offset, entry_stop, exit,
  exit_t, flip_t, floor, floored, headroom, legs, opened, opened_t,
  orig_dist, orig_hit_bar, orig_oz, orig_stop, pl, regime, soft,
  stop_history, tp, why`.

## Commit

```
git add scripts/backtest.py service/tests/test_backtest_records.py
git commit -m "feat(backtest): record leg times, stop history, TP price and running balance"
```
