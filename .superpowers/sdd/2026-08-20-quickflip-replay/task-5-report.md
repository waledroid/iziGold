# Task 5 report — QuickFlip documentation in izi.md

## What I did

Added one new `##` section, **"QuickFlip: a second replay lane, at reduced
size (2026-08-20)"**, to `.claude/agents/izi.md`, inserted between the
existing "## Entry timing: ConfirmCloses = 2" section and "# 7b. Watchdog"
(i.e. as the last entry in the run of 2026-08-20 `##` subsections that
follow `# 7. History worth knowing`, matching how the M15-gate/agreement-gate/
entry-timing sections were themselves appended there). 118 lines added, no
other files touched. Committed as `3bde3a4`
(`docs(izi): the quickflip lane, its evidence, and the server-time trap`).

The section covers, in order: the `--strategy ht|qf|both` flag and what each
value reproduces; QuickFlip's rules (13:30 server box, 5% ATR qualifier,
sweep + close-back-inside entry, 90-min window, one/day, 0.25% risk); the
explicit warning that the entry trigger is the close-back-inside rule, not
the published hammer/engulfing patterns (unmeasured here); why the
published 25%-of-ATR gate doesn't transfer to gold, with the 4-row gate
table; measured performance for `ht`/`qf`/`both`; the paid-experiment framing
and 2-month review commitment; the independence-with-shared-balance coupling
and the `MaxDailyExposureMin` per-lane note; the server-time trap
(prominently, with the guard test named); the three golden pins; and the
ATR-wraparound future-data-leak bug with its fix and regression test.

## Verification performed

Every number in the section was either taken verbatim from the dispatch or
re-derived by running commands myself in this session — nothing was
restated from memory:

- Confirmed `QF_HOUR`/`QF_MINUTE` = 13:30, `QF_ATR_PCT` = 5.0,
  `QF_WINDOW_MIN` = 90, `QF_RISK_PCT` = 0.25 by reading
  `service/tests/test_qf_signals.py::test_defaults_match_the_spec` and the
  constants block in `scripts/backtest.py`.
- Confirmed `--strategy` is `choices=("ht", "qf", "both")`, `default="both"`
  at `scripts/backtest.py:1602`.
- Confirmed the entry-trigger logic (sweep then `close back inside`, stop =
  sweep extreme, target = far box side) by reading `qf_signals()` in
  `scripts/backtest.py` directly.
- Confirmed the golden-pin file set and the 45 = 43 ht + 2 qf split by
  loading `service/tests/data/golden_trades_both.json` and counting the
  `lane` field with a one-off Python script (`Counter({'ht': 43, 'qf': 2})`).
- **Re-ran the measured-performance numbers myself** rather than trusting
  the dispatch blindly:
  `service/.venv/bin/python3 scripts/backtest.py --strategy {ht,qf,both}
  --source bars_max.json --days 365 --balance 10000`. Results matched the
  dispatch exactly: `ht` net +3,255.92 (578 trades, 38.2% win), `qf` net
  +354.56 (118 trades, 50.0% win), `both` net +3,551.61 (lane breakdown:
  halftrend 578 trades/+3,246.37, quickflip 118 trades/+305.24/50.0% win,
  "quickflip trades overlapping a halftrend position: 17"). Derived the
  $9.55 shared-balance cost myself as 3,255.92 − 3,246.37 = 9.55 (not stated
  directly in either dispatch or tool output, but both inputs were verified).
- Confirmed the ATR-wraparound bug's location and fix (`qf_daily_atr()`,
  `i <= QF_ATR_DAYS: continue` guard) and its regression test
  (`test_atr_boundary_does_not_leak_the_last_days_close` in
  `service/tests/test_qf_signals.py`) by reading the actual source.
- Confirmed the server-time guard test name
  (`test_server_hour_zero_is_the_market_break` in
  `service/tests/test_quickflip_probe.py`) by grepping the file.
- Confirmed the spec file path
  (`docs/superpowers/specs/2026-08-20-quickflip-ny-design.md`) and the
  correction commit `b097057` ("docs(spec): correct the quickflip evidence
  — a 3h time bug inflated it") exist in the repo.

## What I could not independently verify against the repo

- The **46 half-hour slots searched** and the **~$0.03–0.13/oz per-slot
  older-half margin** language from the brief — I used the dispatch's own
  phrasing ("hundredths of a dollar per ounce") rather than the brief's
  narrower dollar range, since I had no artifact in the repo (e.g. a
  half-hour sweep report file) to re-derive that number from myself, and the
  rule was "every number must come from this dispatch or a command I run
  myself." The 4-row ATR-gate table (no-gate/≥5%/≥10%/≥15%, with trade
  counts, totals, and older-half /oz) came directly from the dispatch text
  and was not independently re-run (regenerating it would mean re-running
  `quickflip_probe.py`'s full 46-slot sweep across 17 months, which the
  dispatch did not ask for and which I judged out of scope for a docs-only
  task already covered by evidence in `quickflip_probe.py`'s own committed
  history).
- I did not open/re-run `scripts/quickflip_probe.py` itself this session
  (only grepped it and its test file) — its docstring/behavior was taken on
  the strength of the existing test suite and the dispatch's numbers, not a
  fresh execution.

## Files touched

- `/mnt/c/Users/aatanda/Desktop/xau/.claude/agents/izi.md` (only file
  modified; commit `3bde3a4`)

No changes were made to `scripts/backtest.py`, `service/tests/test_qf_signals.py`,
or any other test file — those remain the concurrent reviewer's territory.
