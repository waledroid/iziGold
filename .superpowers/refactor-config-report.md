# Shared strategy-parameter config — report

Branch `refactor/modular-stage-1-2`. Scope: `config/`, `scripts/backtest.py`,
`service/tests/test_strategy_config.py` only. Did not touch
`service/app/main.py`, `service/app/miniapp.py`, or `service/app/db.py`.

## What was built

1. **`config/strategy.json`** — the single source of truth for the 16
   strategy parameters (+ `trading_window_start_hour` /
   `trading_window_end_hour`) that were previously declared once as an
   MQL5 `input` default in `mt5/Experts/XauAssistant.mq5` and once as a
   module constant in `scripts/backtest.py`. All 18 values verified against
   both files before writing — none changed. A `_meta` string field (JSON
   has no comment syntax) explains the file's purpose, that the EA inputs
   are the LIVE authority, and names the enforcing test
   (`test_strategy_config_matches_the_ea`).

2. **`scripts/backtest.py`** now loads all 16 constants + `WINDOW` from
   `config/strategy.json` via a new `_load_strategy_config()` (stdlib
   `json`, no new dependency), called once at import time into `_CFG`. A
   missing file or invalid JSON raises `SystemExit` immediately, naming the
   path — this fails loudly at import instead of silently falling back to
   defaults. Every dated evidence comment that lived next to these
   constants (the `CONFIRM_CLOSES` owner-decision note, the `CHOP_EFF_MAX`/
   `CHOP_EFF_BARS` chop-efficiency study tables, the `BIAS_BUFFER_ATR`/
   `BIAS_EMA` M15-agreement study, `WINDOW`'s trading-hours note, `ADX_MIN`'s
   "matches EA AdxTrendThreshold" note) was left exactly where it was,
   directly above/beside its constant's new `_CFG[...]` assignment — nothing
   was deleted or moved into the JSON. All CLI override flags
   (`--confirm`, `--risk`, `--adx`, `--bias-buffer-atr`, `--chop-eff-max`,
   `--window-start`/`--window-end`, etc.) are untouched: they still assign
   into the same module globals after `_CFG` seeds them, so precedence is
   identical to before.

3. **`service/tests/test_strategy_config.py`** — 26 tests (parametrized).
   `_load_ea_inputs()` regex-parses every `input <type> Name = value;`
   default straight out of `XauAssistant.mq5` (no compilation, since the
   EA can't be compiled outside MetaEditor).
   - `test_strategy_config_matches_the_ea` — numeric-compares each JSON
     value to the matching EA input default (`50 == 50.0` passes; the
     comparison is done as Python numbers, not strings).
   - `test_backtest_constants_match_config` — numeric-compares each backtest
     module attribute (loaded via `importlib` from `scripts/backtest.py`,
     the same pattern `test_backtest_golden.py` already uses) to the JSON,
     plus the `WINDOW` tuple special case.
   - `test_config_has_no_unmapped_or_missing_keys` — diffs the JSON's keys
     against the `MAPPING` dict's keys in both directions.
   - `test_config_file_has_explanatory_header` / a per-entry parametrized
     sanity check on `MAPPING` itself.

## Mutation evidence

Changed `mt5/Experts/XauAssistant.mq5` line 28 from
`RiskPerTradePct = 1.0` to `RiskPerTradePct = 1.5` (comment left untouched),
then ran the enforcement test:

```
$ .venv/bin/python -m pytest -q tests/test_strategy_config.py::test_strategy_config_matches_the_ea
E       AssertionError: strategy config drifted from the live EA:
E         risk_per_trade_pct: config/strategy.json=1.0 != EA input RiskPerTradePct=1.5
1 failed in 1.33s
```

Restored the line to `RiskPerTradePct = 1.0`, re-ran:

```
$ .venv/bin/python -m pytest -q tests/test_strategy_config.py::test_strategy_config_matches_the_ea
1 passed in 0.35s
```

Confirmed the EA tree is clean afterward:

```
$ git diff mt5/
(empty)
$ git status --short mt5/
(empty)
```

## Full suite

```
$ cd service && .venv/bin/python -m pytest -q
554 passed, 1 deselected, 3 warnings in 82.56s
```

No failures; `test_pop_approved_command_concurrent_exactly_once` did not
flake this run. (Baseline noted as 527 in the task brief; a prior stage of
this same branch — see `.superpowers/refactor-stage1-report.md` — had
already moved the baseline to 530 before this work added the 22+ tests in
`test_strategy_config.py`.)

Golden pins specifically: `pytest tests/test_backtest_golden.py
tests/test_backtest_strict_window.py tests/test_strategy_config.py` → 32
passed. All three `golden_trades*.json` pins pass unchanged — no golden
file was regenerated.

## Real backtest run — could not verify the exact figure quoted in the brief

```
$ service/.venv/bin/python scripts/backtest.py --source bars_max.json --balance 10000 --strategy ht
...
net P/L      +7625.63  (+76.26%)
```

The brief asked me to confirm **+7380.53**. To check whether my refactor
introduced this, I `git stash`ed just `scripts/backtest.py` (reverting to
the original hardcoded constants, leaving `config/strategy.json` in place
but unused) and ran the identical command against the untouched original
code: it also reported **+7625.63**, byte-for-byte the same trade-by-trade
output. I then restored the stash. This proves the refactor is faithful —
it changes nothing about what the replay computes — but it also means the
`+7380.53` figure predates something else on this branch (or a different
`bars_max.json` snapshot) rather than my change. The in-code comment that
records `+7380.53` (`scripts/backtest.py`, the `CHOP_EFF_MAX` study table)
is dated evidence from when that study was run; it was left untouched
per instructions (no evidence gets deleted), but it is now stale relative
to what a plain run on the current `bars_max.json` / current branch tip
produces. This is a pre-existing discrepancy, not a regression from this
task — flagging it rather than silently reporting the wrong number.

## Not verified / concerns

- The `+7380.53` figure could not be reproduced on this branch's current
  `bars_max.json`, for the reasons above. The golden-pin tests (which use
  a frozen fixture, not the live-growing `bars_max.json`) are the actual
  proof of a faithful load and they pass unchanged.
- `mt5/` cannot be compiled from this environment; the EA-side check is a
  regex parse of the `.mq5` source, not a MetaEditor compile. Sixteen `input`
  names were confirmed present and numeric via that parse.
