# EA scope batch — news guard, daily loss brake, spread telemetry, maintenance script, multi-symbol keys

**Date:** 2026-08-09 (all four approved in-session)

## 1. News-event guard (`mt5/Include/XauAssistant/NewsGuard.mqh`)

- Inputs: `NewsBlackoutMin = 30` (minutes before AND after), `NewsGuardEnabled = true`.
- Uses the MQL5 economic-calendar API: high-importance (`CALENDAR_IMPORTANCE_HIGH`)
  events for currency `USD` within ±blackout of `TimeCurrent()`.
- `CanEnter` gains the check with refusal literal `"news blackout"` (flows to
  Telegram 🚫 automatically via the existing rejection path).
- **Fail-open**: calendar API unavailable/error/empty (common on some demo
  servers) → guard passes, one throttled Print. Never blocks on missing data.
- Poll the calendar at most once per minute (cache), not per tick.
- Blocks ENTRIES only (adds allowed? NO — adds are new exposure: the pyramid
  add path must also respect it; exits/flatten never blocked).

## 2. Daily loss brake (`RiskManager`)

- Input `MaxDailyLossPct = 3.0` (0 = off).
- Tracks TODAY's REALIZED P/L: sum of own closed deals (symbol+magic) since
  server midnight via `HistorySelect` (no global-var state — broker history is
  the source of truth, reload-safe).
- `CanEnter` refusal literal `"daily loss limit"` when realized ≤
  −MaxDailyLossPct% of the day's starting balance (approximate day-start
  balance = current balance − today's realized P/L).
- Blocks entries AND pyramid adds; never exits. Resets naturally at server
  midnight (HistorySelect window).

## 3. Spread telemetry

- EA: sample `SYMBOL_SPREAD` every timer tick (5 s); aggregate per closed bar
  min/avg/max; send in the `/analyze` payload as
  `spread_min`, `spread_avg`, `spread_max` (points, floats; 0 when unknown).
- Service: `AnalyzeRequest` gains the three optional fields (default 0);
  new table `spread_history(bar_time INTEGER PRIMARY KEY, spread_min REAL,
  spread_avg REAL, spread_max REAL)` upserted per analyze post (guarded
  CREATE; no migration needed — new table).
- No UI yet (data collection first). Contract tests.

## 4. Maintenance script (`mt5/Scripts/XauMaintenance.mq5`)

- A one-shot MQL5 Script (drag onto chart): prints every `XAU_*` global
  variable with value + interpretation (kill switch, cycle balance, peak,
  exposure, flatten day) to the Experts log AND a chart Alert summary.
- Script inputs (checkboxes): `ResetKillSwitch`, `ResetPeak`, `ResetCycle`,
  `ResetExposure` — each deletes/zeroes the matching key(s) when true, with
  an explicit log line per reset. All default false (pure inspection run).
- Compiled + copied to the data folder's `MQL5/Scripts/`.

## 5. Multi-symbol global keys

- Every EA global variable key gains the symbol: `XAU_<name>_<login>_<symbol>`
  (kill switch, HWM, exposure, cycle balance, peak — wherever `_<login>` keys
  are built in RiskManager/TradeManager/EA).
- One-time migration at OnInit: for each key, if the old login-only key exists
  and the new one doesn't, copy value → new key, delete old. Print once.
- Maintenance script (item 4) uses the new key shapes.

## Constraints

- All MQL5 compiles gated 0 errors / 0 warnings via the MetaEditor CLI.
- Service suite stays green; new tests for item 3.
- Fail-open discipline throughout; no new blocking of exits ever.
- izi.md updated in the same branch (risk gates list, ops runbook, key shapes).
