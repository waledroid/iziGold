# Stage 3c — backtest.py lane plug-in

Scope: `scripts/backtest.py` and its tests only. Branch `feat/multi-magic-rails`.

## The smell, and what replaced it

`STRATEGY` was a module-level global: `main()` mutated it (`global STRATEGY;
STRATEGY = args.strategy`), and `run()` read it live across three different
sites (the qf-signal precompute, the qf-only short-circuit, and nowhere
else — the actual QuickFlip trading logic never even checked it, it was
just always active once `qf_by_i` was non-empty). That's action-at-a-
distance: two calls to `run()` in the same process could see different
lane sets depending on what some *other* code path had last set the
global to, which is exactly the shape of bug a plug-in system would make
impossible.

`run()` now takes `active_lanes` (a `set` of lane ids) as an explicit
4th argument, defaulting to `None` → `set(LANES)` (all registered lanes)
when omitted. `main()` computes `active_lanes = lanes_for(args.strategy)`
locally and passes it straight into `run()` — no global write. `STRATEGY`
the module constant still exists, but it is now *only* the single source
of truth for the `--strategy` argparse default (test_cli_defaults_
match_the_module_defaults pins `args.strategy == bt.STRATEGY`); nothing
reads it as runtime state any more.

## The lane contract

```python
class Account:
    """bal (read-only property) + realize(trade) (append to trades, fold
    trade["pl"] into bal/peak_bal/max_dd). The only way a lane may touch
    shared money state."""

class Lane:
    def step(self, i, candles, account): ...       # per-bar: open/manage/close
    def floating_pl(self, px): ...                  # per-bar: unrealized P/L
```

`step(i, candles, account)` matches the brief exactly: `account.bal` is a
read-only view (implemented as a property that proxies to `run()`'s own
`bal` closure variable via a getter lambda, so there is exactly one `bal`
in memory — the Account doesn't duplicate it), and `account.realize(trade)`
is the one path that appends to `trades` and folds P/L into
`bal`/`peak_bal`/`max_dd`. `floating_pl(px)` is the second contract method,
used only by `mark_equity()` (see the invariant section below).

`QuickFlipLane(Lane)` is the QuickFlip block moved behind that contract
verbatim — same `qf_signals()`/`qf_resolve()` calls, same sizing math, same
trade-dict shape, same verbose-print text. Its `__init__(candles, verbose)`
precomputes `_by_i` from `qf_signals()` exactly like the old `qf_by_i`
precompute did. Its `sizing` dict (entries/clamped/min_lot/risk_pct) is
what used to be the bare `qf_sizing` local in `run()`.

Lanes are registered in a dict:

```python
LANES = {
    "ht": None,           # not a plug-in — orchestrated inline in run()
    "qf": QuickFlipLane,
}

def lanes_for(strategy):
    return {"ht"} if strategy == "ht" else \
           {"qf"} if strategy == "qf" else set(LANES)
```

`run()` builds its lane instances with `[factory(candles, verbose) for
lane_id, factory in LANES.items() if factory is not None and lane_id in
active_lanes]`. A third lane is `LANES["new_id"] = NewLane` plus a class —
nothing in `run()`'s body changes, and nothing in `main()` changes either
(`lanes_for()` and the `active_lanes in`/`not in` checks are lane-count-
agnostic already). The only place that stays a hand-written conditional is
`--strategy`'s three CLI choices, which is a CLI surface decision, not a
lane-wiring one.

## The mark-every-bar invariant

`mark_equity(px)` used to do `bal + (basket_pl(px) if basket else 0.0) +
qf_pl(px)` — a QuickFlip-shaped expression that happened to be called from
two places (the `--strategy qf` short-circuit and the very end of the
HalfTrend path), and the bug in the spec (a qf-only run reporting a
fabricated `0.00` valley) was exactly "someone moved the call site inside
the HalfTrend block below the short-circuit."

It now reads:

```python
eq = bal + (basket_pl(px) if basket else 0.0) \
    + sum(ln.floating_pl(px) for ln in lanes)
```

`lanes` is the list of *active* plug-in instances built once at the top of
`run()`. Summing over it, rather than naming `qf_pl()`, means a third
lane is folded into the valley automatically the moment it's added to
`LANES` and turned on — there is no second place to remember to touch.
The two call sites (`--strategy qf`/`ht`-not-active short-circuit, and the
end of the HalfTrend per-bar body) are unchanged in number and position
from before the refactor, so the existing coverage
(`test_the_open_equity_valley_is_marked_in_a_qf_only_run`,
`test_the_valley_is_never_shallower_than_the_closed_drawdown` in
`service/tests/test_qf_lane.py`) still exercises exactly the code path the
bug lived in — it just now asserts against a generic sum instead of a
qf-specific function.

HalfTrend's own realize path (`close_basket()`) was left untouched — it
still mutates `bal`/`trades`/`peak_bal`/`max_dd` directly with `nonlocal`,
per the "leave HalfTrend inline" instruction. `Account.realize()` is a
second path to the *same* three variables (via `nonlocal` inside `run()`'s
own `_realize` closure), not a competing ledger — there is still exactly
one `bal`.

## What a HalfTrend extraction would take

Left inline, as instructed. For the record, what it would take to give
HalfTrend the same `Lane` shape:

1. **State**: `basket` (dir/legs/stop/peak/cycle_bal/target_mult/lock_mult/
   floor/floor_px/…), `fired_flip`, `last_flip`, `extreme`,
   `consec_above`/`consec_below`, plus five diagnostic accumulators
   (`expo`, `skipped`, `open_diff_bars`, `dead_signals`, and the sizing
   dict) — all would move onto the Lane instance as `self.` attributes.
2. **Indicators**: `ema55`, `ht`, `atr`, `adx`, `sr_ctx`, `bias_ema` are
   currently computed once in `run()` and closed over by the per-bar body;
   an extracted lane would need them passed in at construction (cheap,
   `QuickFlipLane` already takes `candles` the same way) or recomputed
   internally.
3. **~15 module-level globals it reads every bar** (`EMA_LEN`, `ADX_MIN`,
   `RISK_PCT`, `STOP_BUFFER_ATR`, `CONFIRM_CLOSES`, `MAX_POSITIONS`,
   `ADD_TRIGGER_ATR`, `PROFIT_TARGET_PCT`, `TRAIL_LOCK_PCT`,
   `TRAIL_ACTIVATE_R`, `WINDOW`, `EXPO_MIN`, `FLATTEN_HM`, `STRICT_WINDOW`,
   `ENTRY_MODE`, `EXIT_SCHEME`, plus every experiment flag: regime gate,
   ATR-spike gate, chop filter, bias/HTF-confirm, S/R headroom, min-stop
   floor) — none of these are read by QuickFlip, so `QuickFlipLane` never
   had to decide whether a plug-in reads module globals directly or takes
   them as constructor args. HalfTrend would force that decision for real:
   probably a config object threaded through `__init__`, since there are
   too many knobs to pass positionally and CLI wiring in `main()` already
   assembles them as globals.
4. **~9 experiment feature blocks in the entry path alone** (regime gate,
   ATR-spike gate, chop skip/soft, HTF-confirm bias with the M15
   resample + efficiency check, S/R headroom, min-stop floor, entry-mode
   fixed vs adr, strict vs loose window) each with their own tagging
   fields on the trade dict and their own summary block in `main()` — the
   summary-printing code alone is ~150 lines keyed off `ht_trades` and
   would need to either stay in `main()` reading `trades` generically (it
   mostly already does, via `t.get("lane","ht")=="ht"`) or move behind a
   second Lane method like `summary()`.
5. **Estimate**: this is a materially bigger job than QuickFlip's
   extraction — QuickFlip was ~120 lines of self-contained geometry with
   one sizing rule and three exit reasons; HalfTrend's entry/manage/exit
   path alone is ~350 lines with a dozen interacting feature flags. Doing
   it safely would want its own stage (a dedicated config dataclass, one
   flag extracted and byte-diffed against the golden pins at a time), not
   a single sitting.

## Verification

- Full suite: **557 passed, 1 deselected** — matches the stated baseline
  exactly. (`test_pop_approved_command_concurrent_exactly_once` didn't
  flake this run.)
- All three golden pins
  (`golden_trades.json`, `golden_trades_strict.json`,
  `golden_trades_both.json`) pass unchanged — not regenerated.
- `test_probe_and_engine_pin_the_same_defaults`
  (`scripts/quickflip_probe.py` vs `qf_signals()`) passes — the probe was
  not touched.
- Real-data runs, `bars_max.json` (untracked, not committed), both the
  pre-change code (`git show HEAD:scripts/backtest.py`, run from a
  same-directory temp copy so its own relative imports resolved, deleted
  afterward — never committed) and the post-change code, all at
  `--balance 10000`:

  **Dataset fingerprint: `d58363eca94b`** (identical on all six runs —
  101,084 bars, 2025-03-19 12:00 → 2026-08-21 11:45 server time).

  | `--strategy` | net P/L | ht lane (trades / win% / net / max dd) | qf lane (trades / win% / net / max dd) | final bal | max dd | max valley |
  |---|---|---|---|---|---|---|
  | `ht`   | +7625.63 | 923 / 39.5 / 7625.63 / 3427.38 | — | 17625.63 | 3427.38 | 3522.60 |
  | `qf`   | +150.82  | — | 179 / 48.0 / 150.82 / 386.69 | 10150.82 | 386.69 | 395.09 |
  | `both` | +7743.88 | 922 / 39.6 / 7732.44 / 3448.46 | 179 / 48.0 / 11.44 / 408.53 | 17743.88 | 3648.26 | 3653.46 |

  `diff` of the full stdout (every line — header, per-trade table, every
  breakdown block, sizing lines, the NOT MODELLED footer) between the
  pre-change and post-change run was **empty for all three
  `--strategy` values** — byte-for-byte identical, not just the summary
  numbers shown above.

## Files touched

- `scripts/backtest.py` — the refactor described above.
- `service/tests/test_backtest_golden.py` — `_replay()`/`_replay_strict()`/
  `_replay_both()` now pass `bt.lanes_for(...)` into `bt.run()` instead of
  setting `bt.STRATEGY` (which `run()` no longer reads).
- `service/tests/test_qf_lane.py` — same change to the `_run()` helper and
  the two standalone valley tests; one docstring updated (`qf_pl()` no
  longer exists, replaced by "summing lanes' `floating_pl()` in
  `mark_equity()`").
- `service/tests/test_qf_report.py`, `service/tests/test_backtest_web.py` —
  same `bt.STRATEGY = X` → `bt.lanes_for(X)` mechanical update at their
  `bt.run()` call sites.

## Concerns

None outstanding. The one thing worth flagging for whoever adds lane #3:
`LANES`/`lanes_for()` make lane *construction* generic, but `--strategy`
itself is still a fixed three-way CLI choice (`ht|qf|both`) — a third lane
needs its own argparse wiring (or a different CLI shape, e.g.
`--lanes ht,qf,new` as a comma list) before it's reachable from the
command line, even though `run()` itself would already accept
`{"ht", "qf", "new"}` as `active_lanes` with no further change.
