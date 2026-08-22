# Second HalfTrend lane on M15 (halftrend_m15_v1)

Branch `refactor/halftrend-lane`. Owner wants the same strategy on M15 as a
second modular strategy, switchable from Telegram, compared one at a time
against the live M5 lane — not run simultaneously.

## What changed

### 1. `Id()` became a constructor argument

`mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`: `CHalfTrendEmaStrategy`
now takes `string id` as its first constructor parameter, stores it in
`m_id`, and `Id()` returns `m_id` instead of the hardcoded literal. Every
`Print`/`PrintFormat` inside the class that used to hardcode
`"halftrend_ema_v1: "` now prefixes with `m_id` so the two live instances
are distinguishable in the Experts log. The M5 registration still passes
`"halftrend_ema_v1"` literally — **that id and every M5 default is
unchanged**.

### 2. Second registration, `mt5/Experts/XauAssistant.mq5` `OnInit`

```
g_registry.Register(new CHalfTrendEmaStrategy("halftrend_m15_v1", PERIOD_M15, M15Amplitude, M15EmaLength,
                    M15ConfirmCloses, M15StopBufferATR,
                    M15CatchupEnabled, M15CatchupMaxAgeBars, M15CatchupMaxChaseATR,
                    M15HtfConfirm, M15HtfConfirmTf, M15HtfConfirmEma, M15HtfConfirmBufferATR,
                    M15HtfChopOnly, M15HtfChopBars, M15HtfChopEffMax));
```

Hardcoded `PERIOD_M15` (not `TradeTimeframe`) — it trades M15 regardless of
what the M5 lane's own trade timeframe input is set to. Registered as a
shadow, evaluated every bar like every other strategy; `ActiveStrategy`
still decides who trades/alerts.

### 3. New M15 inputs (grouped in the Inputs dialog)

Three `input group` dividers were added: `"HalfTrend M5 (halftrend_ema_v1)"`,
`"HalfTrend M15 (halftrend_m15_v1) — second lane, owner runs ONE at a time
via ActiveStrategy"`, `"BollStochRsi (boll_stochrsi)"`. `input group` is a
display-only pragma; the regex the config test uses to parse EA defaults
does not match it.

| Input | Default | Same as M5? |
|---|---|---|
| `M15Amplitude` | 4 | yes |
| `M15EmaLength` | 55 | yes |
| `M15ConfirmCloses` | **3** | **no — deliberate** |
| `M15StopBufferATR` | 0.75 | yes |
| `M15CatchupEnabled` | true | yes |
| `M15CatchupMaxAgeBars` | 12 | yes |
| `M15CatchupMaxChaseATR` | 1.0 | yes |
| `M15HtfConfirm` | true | yes |
| `M15HtfConfirmTf` | **PERIOD_H1** | **no — deliberate** |
| `M15HtfConfirmEma` | 55 | yes |
| `M15HtfChopOnly` | true | yes |
| `M15HtfChopBars` | 48 | yes |
| `M15HtfChopEffMax` | 0.08 | yes |
| `M15HtfConfirmBufferATR` | 2.0 | yes |

`M15ConfirmCloses = 3`: on M15, 3 waiting bars was the only setting measured
positive in BOTH halves of the 17-month history (+1,477.65 full period) —
see the "Not taken, and worth remembering" note in `.claude/agents/izi.md`.

`M15HtfConfirmTf = PERIOD_H1`: the M5 lane confirms against M15 (one step
up); a strategy trading M15 can't confirm against M15, so H1 is the
equivalent one-step-up timeframe.

### 4. `config/strategy.json` restructured

Split into `shared` (10 TradeManager/RiskManager parameters that apply no
matter which strategy is active — risk %, profit target, trail,
add-trigger, max positions, ADX threshold, daily exposure minutes, trading
window; there is only ONE set of these EA inputs, never duplicated per
strategy) and `strategies` (`halftrend_ema_v1` / `halftrend_m15_v1`, each
with its own `confirm_closes`/`ema_length`/`ht_amplitude`/`stop_buffer_atr`/
`htf_confirm_ema`/`htf_confirm_buffer_atr`/`htf_chop_eff_max`/
`htf_chop_bars`). The M5 block's values are byte-identical in meaning to the
old flat file.

`service/tests/test_strategy_config.py` was extended: `SHARED_MAPPING` (10
keys) + `STRATEGY_EA_NAMES` (per-strategy EA input names, 8 keys x 2
strategies) replace the old flat `MAPPING`. `test_strategy_config_matches_the_ea`
now checks the shared block AND both strategy blocks against their own EA
inputs; `test_config_has_no_unmapped_or_missing_keys` checks the shared
block, the set of strategy ids, and each block's own keys for
completeness. `scripts/backtest.py` flattens `shared` + the
`halftrend_ema_v1` block into the same `_CFG` shape it always read — it
does **not** gain an M15 lane. `--tf M15` (pre-existing) is still how M15
is replayed.

## Mutation evidence (proves the extended test still bites)

Mutated `config/strategy.json`'s `strategies.halftrend_m15_v1.ema_length`
from `55` to `99`:

```
$ .venv/bin/python -m pytest tests/test_strategy_config.py::test_strategy_config_matches_the_ea -q
...
E       AssertionError: strategy config drifted from the live EA:
E         strategies.halftrend_m15_v1.ema_length: config/strategy.json=99 != EA input M15EmaLength=55
E       assert not ['strategies.halftrend_m15_v1.ema_length: config/strategy.json=99 != EA input M15EmaLength=55']
1 failed in 1.40s
```

Restored to `55`:

```
$ .venv/bin/python -m pytest tests/test_strategy_config.py -q
...............................                                          [100%]
31 passed in 1.12s
```

## Compile

```
Result: 0 errors, 0 warnings, 3545 ms elapsed, cpu='X64 Regular'
```

Copied `mt5/Experts/XauAssistant.mq5` and
`mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh` into the live
terminal's MQL5 tree, compiled with metaeditor64.exe, log
`m15lane-compile.log` (UTF-16LE, converted with iconv).

## Live EA confirmation (post hot-reload)

Newest `heartbeats` row in `service/xau_assistant.db`:

```
{'id': 23882, 'ts': 1787393398, ..., 'active_strategy': 'halftrend_ema_v1'}
```

`ts` corresponds to ~14 seconds before the check (HeartbeatSec=5 cadence) —
the EA is alive and heart-beating after the compile/hot-reload, and
`active_strategy` is unchanged: `halftrend_ema_v1`. Registering the second
instance did not activate it.

## Test results

- `service/tests/test_strategy_config.py`: 31 passed (was 23; +8 from the
  per-strategy parametrized coverage, all new tests).
- Goldens (`test_backtest_golden.py`): loose, strict, both-lane — all pass,
  unchanged.
- Characterization (`test_halftrend_characterization.py`): all 21 combos
  pass, unchanged.
- `scripts/backtest.py --tf M15 --strategy ht`: still runs correctly against
  the frozen fixture slice, confirming the M5-lane-only `_CFG` flattening
  didn't break the M15 replay path.
- Full suite: `cd service && .venv/bin/python -m pytest -q` → **569 passed,
  1 deselected** (baseline was 561; the +8 delta is exactly the new
  `test_strategy_config.py` parametrized tests, nothing removed or
  weakened).

## Untouched by design

`TradeManager.mqh`, `RiskManager.mqh`, `TradeBoxes.mqh`, magic numbers — not
touched. No M5 default changed. `halftrend_ema_v1`'s id, behavior, and all
three goldens + 21 characterization pins are unchanged. `bars_max.json`
remains gitignored and was not committed.

## izi.md

Updated in the same commit (`.claude/agents/izi.md`, new section "Second
HalfTrend lane on M15 (owner request, 2026-08-22)") per the project's
non-negotiable rule that any `mt5/` behavior change updates the living
knowledge base alongside it.
