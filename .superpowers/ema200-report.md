# EMA-200 own-timeframe confirmation — report

Branch `refactor/halftrend-lane`. Owner's rule: on the strategy's OWN
trading timeframe, BUY agrees when price is above EMA-200, SELL when price
is below. Evaluated on every entry, always; reported everywhere; enforced
by nothing, by default (service-controlled toggle, default OFF for both
strategies).

## Commits

| SHA | Subject |
|---|---|
| `3fa5ecb` | feat(mt5): EMA-200 own-timeframe confirmation for HalfTrend, HTF dropped from M15 |
| `4bb2901` | feat(config): drop the M15 lane's now-removed HTF-confirm keys |
| `d29d03a` | feat(service): thread trades.ema200_agree through db/models/reports/telegram |
| `993cd4b` | feat(backtest): model the EMA-200 own-timeframe confirmation |
| `841f19f` | feat(backfill): scripts/backfill_ema200_agree.py, and run it |
| `e92e201` | docs(izi): record the EMA-200 confirmation and the M15 HTF removal |

## What was added

**mt5/** (`mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`,
`mt5/Include/XauAssistant/Strategy.mqh`, `mt5/Include/XauAssistant/UiApi.mqh`,
`mt5/Include/XauAssistant/UiSink.mqh`, `mt5/Experts/XauAssistant.mq5`):

- `Ema200Agrees(dir)` (verdict, always evaluated) / `Ema200Enforced()`
  (enforcement gate) / `SetEma200Override(v)` (`/agree`-style runtime
  override) / `LastEma200Agree()` (trade-log accessor) on
  `CHalfTrendEmaStrategy` — the same four-way split as
  `HtfAgrees`/`HtfEnforced`/`SetHtfOverride`/`LastHtfAgree`, reusing the
  existing `m_ema200Handle` (no second handle).
- Reads `m_confirmEma200`/`m_confirmClose`, both captured at the SAME
  bar/shift the strict-window confirmation decided on — not a fresh "now"
  read — so a catch-up entry firing bars later still judges the bar that
  actually confirmed, and the M15 and EMA200 verdicts describe the same
  instant.
- New EA inputs `Ema200Confirm` (M5) / `M15Ema200Confirm` (M15), both
  default `false`.
- **The M15 lane's HTF module is removed entirely**: `halftrend_m15_v1`
  registers with `htfConfirm=false`; the `M15Htf*` EA inputs are gone from
  the input table rather than left unused. `config/strategy.json`'s
  `halftrend_m15_v1` block and `test_strategy_config.py`'s
  `STRATEGY_EA_NAMES["halftrend_m15_v1"]` lost their `htf_confirm_*` keys
  to match. **The M5 lane's own HTF behaviour is unchanged.**
- `ema200_agree` threads through `PostTradeEvent`/`OnTradeEvent` alongside
  `htf_agree`; `ema200_enforce` threads through the heartbeat request/
  response alongside `htf_enforce`.

**service/app/**: `trades.ema200_agree` column (same migration style as
`htf_agree`), `TradeEventRequest.ema200_agree`,
`HeartbeatResponse.ema200_enforce`, `SignalDb.EMA200_CHOICES`/
`ema200_enforce()`/`set_ema200_enforce()`. `reports.py`'s `_htf_flag`
generalized into `_flag(entries, key)` with an `_ema200_flag` twin;
`_group_baskets`/`_fetch_closed_baskets` carry `ema200_agree` on every
basket entry (this is exactly the field the M15-column-dash bug once
dropped — `test_basket_twins.py` now seeds a basket where the two verdicts
deliberately DISAGREE so one twin can't hide the other silently dropping).
Day report rows gain an `"e200"` field; `miniapp.html`'s Trades tab gets an
**E200** column next to **M15** (same Yes/No/– rendering). `_trade_caption`
gains an `E200: agrees ✅` / `E200: DISAGREES ⚠️` line beside `M15:`.
`/agree` is now the "what confirms a trade" menu — existing HTF buttons
unchanged, new `e200:off`/`e200:on` buttons toggle `ema200_enforce`.

**scripts/backtest.py**: `--ema200-confirm off|on` (default off, byte-
identical) — same plain side test, on the trading timeframe (`--tf`-aware),
tagging every basket with `ema200_agree`.

**scripts/backfill_ema200_agree.py**: sibling to `backfill_htf_agree.py`
(the rule is same-timeframe, not a fixed higher one, and the trading
timeframe differs per `strategy_id`, so it resamples M5→M15 for
`halftrend_m15_v1` the same way `backtest.py --tf M15` does).

## Found, not fixed

`UiApi.mqh`'s `PostHeartbeat` has always declared an `htfEnforce_out`
reference parameter but never populated it from the heartbeat response body
(no `ExtractString(body, "htf_enforce")` call ever existed). The M5 lane's
`/agree` HTF enforcement toggle has therefore never actually reached the EA
at runtime — `SetHtfOverride` is never called from `OnTimer`, and the M5
lane has been running on its `HtfChopOnly`-gated EA-input default
regardless of what `/agree` shows in Telegram. Left as-is: fixing it would
change the M5 lane's live enforcement behaviour, out of this task's scope
("the M5 strategy's own behaviour must not change at all"). The new
`ema200_enforce_out` IS correctly parsed — the EMA200 toggle actually works
at runtime; the pre-existing HTF one still doesn't. Worth a deliberate
follow-up fix plus a live-behaviour review, on its own.

## Four-case evidence

Rule exercised with `Ema200Agrees(dir)` semantics (BUY agrees when
price > EMA200, SELL agrees when price < EMA200), against real EMA-200
values computed from `bars_max.json` via `app.indicators.ema`:

```
[1] BUY  / AGREE     bar 2026-08-21 11:45  price=4581.09  ema200=4532.92  -> agrees=True
[2] SELL / DISAGREE  bar 2026-08-21 11:45  price=4581.09  ema200=4532.92  -> agrees=False
[3] SELL / AGREE     bar 2026-08-20 16:25  price=4471.83  ema200=4480.25  -> agrees=True
[4] BUY  / DISAGREE  bar 2026-08-20 16:25  price=4471.83  ema200=4480.25  -> agrees=False
```

Same instant, both directions tested each way — BUY/SELL against the same
price/EMA200 pair flip the verdict exactly as the rule requires.

Also confirmed at the trade-caption level (`test_entry_caption_reports_the_ema200_verdict`
in `service/tests/test_trades.py`): `agree`/`refuse`/`unknown`/`closed`
cases render `E200: agrees ✅`, `E200: DISAGREES ⚠️`, no E200 line, and no
E200 line respectively — and a combined case proves the M15 and EMA200
lines are independent (`M15: agrees` + `E200: DISAGREES` in the same
caption).

## Rendered day-report row (M15 + E200 together)

`app.reports._report_day` against the live `service/xau_assistant.db` for
2026-08-21 (after the backfill), showing both columns on real trades:

```json
{
  "time": "05:12", "direction": "BUY", "entries": 1,
  "entry": 4526.26, "exit": 4518.92, "reason": "stop-loss", "pl": -44.52,
  "regime": "high_volatility", "m15": true, "e200": true,
  "session": "Asia", "strategy_id": "halftrend_ema_v1"
},
{
  "time": "09:50", "direction": "BUY", "entries": 3,
  "entry": 4553.69, "exit": 4567.2, "reason": "profit target", "pl": 95.95,
  "regime": "trend", "m15": true, "e200": true,
  "session": "Asia", "strategy_id": "halftrend_ema_v1"
},
{
  "time": "20:25", "direction": "BUY", "entries": 3,
  "entry": 4610.02, "exit": 4618.11, "reason": "profit lock", "pl": 32.46,
  "regime": "high_volatility", "m15": null, "e200": true,
  "session": "LDN+NY", "strategy_id": "halftrend_ema_v1"
}
```

Row 3 shows `m15: null` (not covered by the HTF backfill's reach) next to
`e200: true` (covered) — proof the two columns are independently populated
and both survive `_group_baskets`, not just structurally identical stubs.
In the mini app's day table this renders as M15 "–" / E200 "Yes" on that
row, and M15 "Yes" / E200 "Yes" on the other two.

## Backfill counts

`scripts/backfill_ema200_agree.py --db service/xau_assistant.db --bars bars_max.json --apply`:

```
51 open events: 27 agree, 24 disagree, 0 not covered by the candle history
wrote 51 rows
```

All 51 historical `halftrend_ema_v1` open events covered (no `halftrend_m15_v1`
trades exist yet — that lane was only just registered). Verified in the DB
afterward: 0 rows remain at the `-1` (unknown) default.

## Backtest verification

Against `service/tests/data/bars_slice.json` (6,000 M5 bars):

```
--ema200-confirm off (default): 60 trades
--ema200-confirm on:            52 trades
```

`on` removes entries that opened against their own EMA200 (e.g. the
`10-15 15:55 SELL` and `10-21 08:05 SELL` in the verbose trade list) while
every other trade is untouched — proof the gate only refuses what it's
supposed to, not a blanket reduction.

## MQL5 compile

Copied `mt5/Experts/XauAssistant.mq5` and `mt5/Include/XauAssistant/` into
`.../MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075/MQL5/`, compiled
via MetaEditor CLI:

```
Result: 0 errors, 0 warnings, 3483 ms elapsed, cpu='X64 Regular'
```

Post-compile (hot-reload) verification: the newest `heartbeats` row landed
within seconds of the compile finishing (id `23954`, `active_strategy =
halftrend_ema_v1`, `kill_switch = 0`), and stayed stable at its normal
~60-65s cadence afterward — the EA reloaded cleanly and is alive on
`halftrend_ema_v1`.

## Pins confirmed unmoved

With both new EA inputs and the new backtest flag defaulting OFF, nothing
was supposed to move, and nothing did:

- `test_backtest_golden.py` — both the loose and strict pins pass unmoved.
- `test_halftrend_characterization.py` — all 21 characterization combos
  pass unmoved.
- `test_strategy_config.py` — the M5 lane's EA-input/config/backtest parity
  still holds exactly; the M15 lane's trimmed block matches its trimmed EA
  inputs.
- Full Python suite: **530 passed, 1 deselected** (the slow-marker Chronos
  test, excluded by default), 0 failed. (Baseline was 529; the net +1 in
  the base test count reflects the new EMA200 coverage added alongside
  small existing-test extensions, not fewer or weaker tests.)

## Constraints honored

- M5 strategy's own behaviour: unchanged (same registration shape, same
  inputs, only a new trailing constructor argument that defaults to what
  the constructor already did before).
- `bars_max.json` not committed; `bars_slice.json` untouched.
- No new dependencies.
- MQL5 gate: 0 errors, 0 warnings; EA confirmed alive on `halftrend_ema_v1`
  post-hot-reload.
