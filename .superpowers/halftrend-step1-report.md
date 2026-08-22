# HalfTrend extraction, step 1: characterization suite

Branch `refactor/halftrend-lane`. No production code touched (`git diff
scripts/backtest.py` is clean at the end of this step). This step only adds
`service/tests/test_halftrend_characterization.py` and 21 new golden files
under `service/tests/data/golden_ht_*.json`.

## 1. The actual feature list found

Read `build_parser()` (all `add_argument` calls) and the full body of `run()`
in `scripts/backtest.py`. The optional-feature surface, beyond the plain
strict-window default already pinned by `test_backtest_golden.py`:

1. **Regime gate** (`--regime-gate off|range|range-strict|highvol`) — refuses
   new entries based on the service's live `classify_regime()` over the
   300-bar window the EA posts.
2. **ATR-spike gate** (`--atr-spike-gate RATIO`) — refuses entries when
   ATR(14) exceeds `RATIO x` the trailing-100-bar median.
3. **Chop filter** (`--chop-flips`, `--chop-bars`, `--chop-box-atr`,
   `--chop-mode skip|soft|off`) — flip-count + box/ATR tightness tag; `skip`
   refuses, `soft` halves risk and freezes adds.
4. **Bias / HTF-confirm filter** (`--bias-ema`, `--bias-mode
   tag|target|target_lock|size_target|skip`, `--bias-tf M5|M15`,
   `--bias-buffer-atr`) plus its own internal chop gate (`--chop-eff-max`,
   `--chop-eff-bars`) that decides whether the M15 side-test runs at all.
   **Live default since 2026-08-20** (`BIAS_EMA=55`, `BIAS_MODE=skip`,
   `BIAS_TF=M15`) — this is not an "off by default" feature like the others.
5. **Support/resistance proximity** (`--sr-lookback`, `--sr-min-headroom`,
   `--sr-report`).
6. **Profit-floor exit scheme** (`--exit-scheme
   target-exit|floor-a|floor-b|floor-a-adds`).
7. **Minimum-stop floor** (`--min-stop-atr`).
8. **Entry mode** (`--entry-mode adr|fixed`, `--fixed-lots`).
9. **Trading window** (`--window-start`, `--window-end`).
10. **Exposure budget** (`--expo`).
11. **Profit target override** (`--profit-target`, including `<= 0` = off,
    explicitly NOT the same code path as `--entry-mode fixed`).
12. **Trading timeframe** (`--tf M5|M15`).

Two more exist that weren't in the brief's starter list and are genuinely
separate experiments, not sub-knobs of the above:

13. **Confirmation price** (`--confirm-mode close|open`) — counts closes vs.
    next-bar opens against the EMA.
14. **Entry-window strictness** (`--loose-window`, and the `CONFIRM_CLOSES`
    waiting-bar count via `--confirm N`) — already exercised once each (loose
    vs. strict) by the existing goldens, but never combined with
    `--confirm-mode open`, and never at a `CONFIRM_CLOSES` value other than
    1 (loose pin) / 2 (strict pin/default).

Continuous rulebook overrides that exist (`--adx`, `--risk`, `--stop-buffer`,
`--ema-len`) were treated as calibration knobs on the always-on baseline
rulebook, not optional feature blocks — not pinned separately here, since the
task's "optional feature surface" framing is about code paths that are
off/degenerate by default, and these aren't.

## 2. Combinations chosen (21 pinned) and why each moves the outcome

All measured against the strict-window default on `bars_slice.json`
(43 trades, balance 3657.74, max_dd 385.71). Every entry below was verified
to differ from that baseline before being pinned:

| combo | setting | trades | note |
|---|---|---:|---|
| `regime_gate_range` | `REGIME_GATE=range` | 17 | |
| `regime_gate_range_strict` | `REGIME_GATE=range-strict` | 4 | small n but a real, distinct (superset) gate |
| `regime_gate_highvol` | `REGIME_GATE=highvol` | 32 | |
| `atr_spike_gate` | `ATR_SPIKE_RATIO=1.3` | 38 | |
| `chop_skip` | `CHOP_FLIPS=1,N=24,X=5.0,mode=skip` | 22 | |
| `chop_soft` | `CHOP_FLIPS=1,N=12,X=3.0,mode=soft` | 43 | same COUNT as default, but content differs — the point of `soft` (size/adds change, not the entry list) |
| `bias_off` | `BIAS_EMA=0` | 84 | restores the pre-2026-08-20 replay |
| `bias_chop_eff_wide` | `CHOP_EFF_MAX=0.12` | 31 | see landmine below |
| `bias_tf_m5` | `BIAS_TF=M5` | 41 | |
| `sr_proximity` | `SR_LOOKBACK=50,SR_MIN_HEADROOM=0.5` | 23 | |
| `exit_floor_a` | `EXIT_SCHEME=floor-a` | 43 | same count, different exits/P&L |
| `exit_floor_b` | `EXIT_SCHEME=floor-b` | 43 | " |
| `exit_floor_a_adds` | `EXIT_SCHEME=floor-a-adds` | 43 | " |
| `min_stop_floor` | `MIN_STOP_ATR=1.5` | 43 | same count, sizing/stop differ |
| `entry_mode_fixed` | `ENTRY_MODE=fixed` | 38 | |
| `trading_window` | `WINDOW=(8,16)` | 20 | |
| `exposure_budget` | `EXPO_MIN=60` | 28 | |
| `profit_target_off` | `PROFIT_TARGET_PCT=0` | 43 | same count, no target exits |
| `tf_m15` | `TF=M15` (resampled) | 16 | |
| `confirm_mode_open_loose` | `CONFIRM_MODE=open, STRICT_WINDOW=False` | 58 | see rejected combo below |
| `confirm_closes_3` | `CONFIRM_CLOSES=3` | 46 | |

All digests kept small (4–84 trades; most in the tens), well inside "tens,
not thousands."

## 3. Combinations tried and REJECTED as inert

- **`--sr-report`** (tag-only diagnostic): confirmed byte-identical trade
  digest to the baseline off-run. By design (per the docstring, "never
  refuses anything") — nothing to pin beyond what the digest schema already
  captures (headroom isn't in the trade digest, and shouldn't be, matching
  the existing goldens' shape).
- **`--chop-mode` variants with the default box/flip thresholds**
  (`CHOP_FLIPS=2,N=24,X=2.0`; `CHOP_FLIPS=3,N=12,X=1.5`; several others near
  the shipped defaults): all byte-identical to baseline — this window of
  `bars_slice.json` simply never crosses those particular flip-count/box
  thresholds. Had to search flip=1 with a wide box (`X=5.0`/`X=3.0`) to find
  settings that actually flag entries as chop in this fixture.
- **`--confirm-mode open` under the (default) strict window**: byte-identical
  to `close` mode. This is BY DESIGN per the module docstring — the strict
  window's one-shot decision already fires on the same bar as the close
  rule, and the docstring explicitly says this mode "exists to demonstrate
  that ... is not an earlier signal" for an EA on closed bars. Pinned instead
  under `--loose-window`, where it genuinely diverges (58 vs. 57 trades).
- **`--bias-mode target` / `target_lock` / `size_target`**: NOT rejected for
  being uninteresting — rejected because **no parameter setting makes them
  differ from `skip`/baseline at all**. See the landmine below; this is the
  most important finding of this pass.

## 4. The landmine: `trending` is shadowed inside the bias block

`run()` computes an ADX-based entry gate once per bar:

```python
trending = adx[i] is not None and adx[i] >= ADX_MIN     # line ~1399
if in_window and trending and expo_ok:
    ...
```

Inside that same block, when `BIAS_EMA > 0` (the shipped default), the bias
code **reassigns the same local name** to a completely different concept —
"is the tape trending by the chop-efficiency test" — while deciding whether
the M15 side-test should even run:

```python
trending = False
if CHOP_EFF_MAX > 0:
    ...
    trending = eff > CHOP_EFF_MAX
if trending:
    bias = "with"
elif signal == "BUY":
    bias = "with" if px > bval + buf else "counter"
else:
    bias = "with" if px < bval - buf else "counter"
```

The FINAL gate that actually authorizes an entry re-reads the same name:

```python
if in_window and trending and expo_ok and signal:   # line ~1468
```

By the time this line runs, `trending` is no longer the ADX flag — it is
whatever the chop-efficiency test left behind. Consequence, proven on
`bars_slice.json`:

- Whenever `bias == "counter"` is assigned, it is *structurally* assigned
  inside the `elif` branches that only run when the chop-efficiency
  `trending` is already `False` (the `if trending: bias = "with"` branch
  short-circuits it otherwise). So **every counter-trend classification
  coincides with the final gate being closed** — the entry never happens,
  regardless of `BIAS_MODE`. Verified directly: running the full fixture
  with `BIAS_MODE=tag` (which never blocks on `counter`) still produces
  **zero** realized trades tagged `counter` (38 `with`, 5 `None`, 0
  `counter`). `--bias-mode target/target_lock/size_target` — the reduced
  target/lock sizing for counter-trend baskets, the entire point of the
  owner's 2026-08-18 idea — is **dead code** under the current wiring, not
  merely untested.
- `--chop-eff-max 0` ("run the check all day", the documented
  pre-2026-08-21 alternative) is far more destructive than its docstring
  implies: because the reassignment always executes and is never set back to
  `True` by the (skipped) efficiency check, `trending` stays `False` for
  every bar where a bias check applies, collapsing the HalfTrend lane from
  43 trades to **5** on this fixture — not a parameter tweak, a near-total
  kill switch, and clearly not the intended effect of "run the M15 check all
  day."

This was NOT fixed (constraints forbid touching `scripts/backtest.py`
outside the two temporary mutations in §5, both reverted). It is pinned
as-is: `bias_chop_eff_wide` (`CHOP_EFF_MAX=0.12`, 31 trades) exercises the
same shadowing at a less extreme threshold, and the `bias_*` pins together
lock in the current (buggy) behavior so that:
- a future refactor that accidentally "fixes" the shadowing (e.g. by giving
  the ADX gate and the chop-efficiency flag their own names, which any
  sane extraction would naturally do) will be caught immediately by these
  pins turning up MORE counter-trend entries than before, and
- the extraction plan should treat this as a named, explicit decision point
  — "preserve the shadow bug" vs. "fix it and re-measure" — not something to
  discover by accident mid-refactor.

**This is the single most important finding of this step.**

## 5. Mutation evidence

Two different rules inside the inline HalfTrend path were mutated in turn,
full suite run, then reverted.

### Mutation A — invert stop-before-target precedence

Restructured the open-basket management block (`scripts/backtest.py`
~line 1304) so the shared stop is checked LAST (after target/lock/reversal)
instead of first, inverting the documented convention "stop beats
target/lock/reversal in a bar."

```
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py tests/test_halftrend_characterization.py -q
```

Result: **4 failed, 4 passed** — all 3 existing golden tests failed
(`test_replay_matches_golden`, `test_strict_replay_matches_golden`,
`test_both_lane_replay_matches_golden`), plus
`test_every_combo_matches_its_golden`. Per-combo breakdown (21 combos):
14 FAIL / 7 PASS — failures: `regime_gate_highvol`, `atr_spike_gate`,
`chop_soft`, `bias_off`, `bias_tf_m5`, `sr_proximity`, `exit_floor_a`,
`exit_floor_b`, `exit_floor_a_adds`, `min_stop_floor`, `profit_target_off`,
`tf_m15`, `confirm_mode_open_loose`, `confirm_closes_3`. Passes (correctly
unaffected, since they have no target/lock in play or reach it identically):
`regime_gate_range`, `regime_gate_range_strict`, `chop_skip`,
`bias_chop_eff_wide`, `entry_mode_fixed`, `trading_window`,
`exposure_budget`. Example failure message (exactly the "name the
combination and the first differing trade" format required):

```
AssertionError: [regime_gate_highvol] trade 13 changed (first difference):
  was {'lane': 'ht', 'dir': 'BUY', 'entry': 4129.34, 'legs': 3, 'exit': 4134.7, 'why': 'stop', 'pl': -7.87}
  now {'lane': 'ht', 'dir': 'BUY', 'entry': 4129.34, 'legs': 3, 'exit': 4139.98, 'why': 'profit lock', 'pl': 23.78}
```

Reverted with `git checkout -- scripts/backtest.py`; full suite green again
(8/8 on the two files, 561/561 overall).

### Mutation B — flip a comparison in the chop gate

`chop_at()` (`scripts/backtest.py` line 644): changed
`ratio is not None and ratio < box_atr` to `ratio is not None and ratio >
box_atr` — chop now means a WIDE box relative to ATR instead of a TIGHT one
(a materially different definition of "choppy").

```
cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py tests/test_halftrend_characterization.py -q
```

Result: **1 failed, 7 passed**. All 4 existing `test_backtest_golden.py`
tests PASSED — completely blind to this mutation, because `CHOP_FLIPS=0` by
default means `chop_at()` never runs in any of their 3 configurations. Only
`test_every_combo_matches_its_golden` failed, specifically on `chop_skip`
(trade COUNT moved: 22 -> 24) and, confirmed by direct per-combo check,
`chop_soft` also failed — the only two combos that turn chop filtering on at
all. Every other one of the 21 new combos passed unaffected, as expected
(chop filtering is orthogonal to them).

This is the demonstration the task asked for: **a real trading-rule bug that
the existing golden suite (557/561 tests, including the 4 HalfTrend goldens)
does not catch, that this new characterization suite catches immediately**,
naming exactly which combos and which trade.

Reverted with `git checkout -- scripts/backtest.py`; confirmed clean
(`git diff --stat scripts/backtest.py` empty) and full suite green
(561 passed, 1 deselected).

## 6. What could NOT be pinned, and why

- **`--bias-mode target` / `target_lock` / `size_target`**: could not find
  any setting that makes these differ from `skip`/`tag`, because (§4) no
  basket is ever opened with `bias == "counter"` under the current code —
  the feature is unreachable, not merely rare. Pinning "identical to
  baseline" would violate the instruction to reject inert combos; instead
  it's documented here as a hard blocker for the eventual extraction: the
  target/lock-multiplier code for counter-trend baskets needs either a
  decision (rewrite the shadowed `trending` naming so it can actually fire,
  or delete it as dead) before or during the extraction, not silently
  preserved as unreachable code in the new module.
- **`--sr-report`**: genuinely inert to the trade digest by design (tag-only,
  documented as never refusing). Nothing to pin beyond confirming it stays
  inert, which isn't a useful regression target for a trade-digest pin.
- Combined/stacked feature interactions (e.g. regime-gate + chop-filter +
  bias together) were not pinned — the task asked to exercise each feature,
  not the full cross-product, and `run()`'s gates are evaluated in sequence
  independently of each other except for the `trending`-shadowing landmine
  already covered by the `bias_*` combos. If a future step wants interaction
  coverage, it should build on top of these single-feature pins rather than
  duplicate them.

## Files

- `service/tests/test_halftrend_characterization.py` — the suite (21 combos,
  5 tests: per-combo match, "differs from strict default" guard, unique
  names, golden files exist, plus the module-level `regenerate_all()` used
  only for deliberate regeneration).
- `service/tests/data/golden_ht_*.json` — 21 new pin files, same shape as
  the existing `golden_trades*.json` (`{"trades": [...], "final_balance":
  ..., "max_dd": ...}`).
- No existing golden file, `bars_slice.json`, or `scripts/backtest.py` was
  modified.
