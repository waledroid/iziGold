# Task 5 report — grouped `--help` with the caveat block

## What changed

`scripts/backtest.py`:
- Added `CAVEATS = [...]` (verbatim from the brief) right below the module
  docstring, before the imports.
- Rewrote `build_parser()` to build an `argparse.ArgumentParser` with a
  `description`, an `epilog` built from `CAVEATS` plus the starting-balance
  guidance, and `formatter_class=argparse.RawDescriptionHelpFormatter`, then
  created four `add_argument_group` objects (`data`, `rules`, `exp`, `out`)
  and re-pointed every existing `ap.add_argument(...)` call at its group.
  **No flag name, type, default, choices list, nargs, or help string was
  changed** — only the receiver object changed, per call.

`service/tests/test_backtest_help.py` (new, verbatim from the brief, Step 1):
four tests — caveats name the unmodelled rails, help text contains "not
modelled" and the balance guidance, the four group titles exist, and every
non-help argument belongs to one of the four named groups.

## The four groups and their membership

**Data** (6): `--balance --source --days --start --end --tf`

**Rules** (12): `--adx --expo --risk --confirm --stop-buffer --exit-scheme
--entry-mode --fixed-lots --ema-len --loose-window --strict-window
--profit-target`

**Experiments** (15): `--regime-gate --atr-spike-gate --confirm-mode
--chop-flips --chop-bars --chop-box-atr --chop-mode --min-stop-atr
--bias-ema --bias-mode --bias-tf --window-start --window-end --sr-lookback
--sr-min-headroom`

**Output** (4): `--verbose --chart --hour-table --sr-report`

6 + 12 + 15 + 4 = 37, matching the parser's full argument count (37
`add_argument` calls + `-h`/`--help`, which argparse keeps in its own
`options` group and is exempt by the test's own design).

## Flags not named in the brief's Step-4 list

None. Cross-checking the brief's four bullet lists against every
`add_argument` call in the original `build_parser()` accounts for all 37
flags exactly as enumerated (Data: source/start/end/days/tf/balance = 6;
Rules: risk/confirm/stop-buffer/adx/expo/entry-mode/fixed-lots/
profit-target/ema-len/loose-window/strict-window/exit-scheme = 12;
Experiments: regime-gate/atr-spike-gate/confirm-mode/chop-flips/chop-bars/
chop-box-atr/chop-mode/min-stop-atr/bias-ema/bias-mode/bias-tf/sr-lookback/
sr-min-headroom/window-start/window-end = 15; Output:
verbose/chart/hour-table/sr-report = 4). No leftover flag needed a
judgment call.

## Proof nothing else changed — parser-inventory diff

Command run before editing `build_parser()`:

```bash
cd service && .venv/bin/python -c "
import sys
sys.path.insert(0, '..'); sys.path.insert(0, '.')
from scripts import backtest as bt
ap = bt.build_parser()
rows = [(a.dest, tuple(a.option_strings), str(a.type), repr(a.default),
         a.choices, a.nargs, a.required, a.help) for a in ap._actions]
rows.sort()
for r in rows: print(r)
" > /tmp/.../parser_before.txt
```

Same command run again after the edit, into `parser_after.txt`, then:

```bash
diff /tmp/.../parser_before.txt /tmp/.../parser_after.txt
echo "diff exit code: $?"
```

Result:

```
diff exit code: 0
```

Empty diff — every `dest`, `option_strings`, `type`, `default`, `choices`,
`nargs`, `required`, and `help` string is byte-identical before and after
the move, across all 37 arguments plus `-h`. (38 rows captured each side.)

Also spot-checked that the suppressed `--strict-window` no-op still parses:

```python
ap.parse_args(['--strict-window']).strict_window  # -> True
```

## Rendered `--help` output

```
usage: backtest.py [-h] [--balance BALANCE] [--source SOURCE] [--verbose]
                   [--adx ADX] [--chart CHART] [--days DAYS] [--expo EXPO]
                   [--risk RISK] [--confirm CONFIRM]
                   [--stop-buffer STOP_BUFFER]
                   [--exit-scheme {target-exit,floor-a,floor-b,floor-a-adds}]
                   [--entry-mode {adr,fixed}] [--fixed-lots FIXED_LOTS]
                   [--regime-gate {off,range,range-strict,highvol}]
                   [--atr-spike-gate ATR_SPIKE_GATE] [--start START]
                   [--end END] [--ema-len EMA_LEN]
                   [--confirm-mode {close,open}] [--chop-flips CHOP_FLIPS]
                   [--chop-bars CHOP_BARS] [--chop-box-atr CHOP_BOX_ATR]
                   [--chop-mode {skip,soft,off}] [--loose-window]
                   [--min-stop-atr MIN_STOP_ATR] [--bias-ema BIAS_EMA]
                   [--bias-mode {tag,target,target_lock,size_target,skip}]
                   [--bias-tf {M5,M15}] [--window-start WINDOW_START]
                   [--window-end WINDOW_END] [--hour-table]
                   [--sr-lookback SR_LOOKBACK]
                   [--sr-min-headroom SR_MIN_HEADROOM] [--sr-report]
                   [--tf {M5,M15}] [--profit-target PROFIT_TARGET]

Replay halftrend_ema_v1 with the current money rulebook over historical candles and report P/L.

options:
  -h, --help            show this help message and exit

Data:
  --balance BALANCE
  --source SOURCE
  --days DAYS           backtest only the last N days of the source data
  --start START         drop candles before this server time (YYYY-MM-
                        DD[THH:MM]); indicators warm up from here
  --end END             drop candles after this server time (YYYY-MM-
                        DD[THH:MM])
  --tf {M5,M15}         trading timeframe (EA TradeTimeframe): M5 (default,
                        byte-identical) or M15 (the M5 source aggregated to
                        15-minute bars before anything else runs). Bar-based
                        parameters are then read in M15 bars; the exposure
                        budget and the trading-window hours keep their wall-
                        clock meaning

Rules:
  the EA's real knobs -- defaults are what the live EA does

  --adx ADX             override ADX gate
  --expo EXPO           override daily exposure minutes (0 = unlimited)
  --risk RISK           override risk percent per trade
  --confirm CONFIRM     override ConfirmCloses (consecutive closes beyond the
                        EMA required after a flip; default 1)
  --stop-buffer STOP_BUFFER
                        override the stop pad in ATR(14) multiples (default
                        0.75)
  --exit-scheme {target-exit,floor-a,floor-b,floor-a-adds}
                        profit-floor experiment scheme (default: current
                        behavior, close at profit target)
  --entry-mode {adr,fixed}
                        adr = live behavior; fixed = fixed lots, no
                        adds/target/lock, exit on confirmed reversal or stop
  --fixed-lots FIXED_LOTS
  --ema-len EMA_LEN     override the trading EMA length (default 55)
  --loose-window        disable the EA's strict 3-bar entry window (flip ->
                        one waiting bar -> entry only if that bar opens beyond
                        the EMA). Use to reproduce studies run before
                        2026-08-20, when loose was the default.
  --profit-target PROFIT_TARGET
                        override ProfitTargetPct, the basket's bank-at percent
                        of cycle balance (default 2.0; <= 0 turns the target
                        off exactly like the EA input, leaving the lock / stop
                        / reversal to close the basket)

Experiments:
  study filters; all default to OFF (live EA has none)

  --regime-gate {off,range,range-strict,highvol}
                        refuse new entries by the service's live regime
                        classifier: range = skip 'range' bars, range-strict =
                        only enter on 'trend' bars, highvol = skip
                        'high_volatility' bars
  --atr-spike-gate ATR_SPIKE_GATE
                        refuse new entries when ATR(14) > RATIO x its median
                        over the last 100 bars (0 = off)
  --confirm-mode {close,open}
                        close = EA behavior (closes beyond the EMA); open =
                        next bar's open beyond the EMA (same decision bar)
  --chop-flips CHOP_FLIPS
                        chop filter: HalfTrend flips within --chop-bars that
                        mark a bar as chop (0 = off)
  --chop-bars CHOP_BARS
                        chop filter lookback in closed bars (default 24)
  --chop-box-atr CHOP_BOX_ATR
                        chop filter: box over the lookback must be < X x
                        ATR(14) (default 2.0; 0 = flip count alone)
  --chop-mode {skip,soft,off}
                        skip = refuse chop entries (H1); soft = enter at half
                        risk with no adds (H2); off = tag/report only
  --min-stop-atr MIN_STOP_ATR
                        minimum stop distance floor in ATR(14) multiples: an
                        ENTRY stop closer than K x ATR is pushed out to
                        exactly K x ATR and lots are sized over the wider
                        distance (0 = off, byte-identical)
  --bias-ema BIAS_EMA   EMA-N market bias at the entry bar (close vs EMA-N; 0
                        = off, byte-identical). Tags every trade with/counter
                        and prints the split
  --bias-mode {tag,target,target_lock,size_target,skip}
                        tag = report only; target = counter-trend target x0.5
                        (lock untouched, EA-literal); target_lock = target
                        x0.5 and lock arm x0.5; size_target = target x0.5 and
                        risk x0.5; skip = refuse counter-trend
  --bias-tf {M5,M15}    timeframe of the bias EMA: M5 (default) or M15
                        (resampled, last completed M15 bar)
  --window-start WINDOW_START
                        first server hour that may OPEN a trade (EA
                        TradingWindowStartHour, default 4)
  --window-end WINDOW_END
                        first server hour that may NOT open a trade (EA
                        TradingWindowEndHour, default 23); exits and the 23:50
                        flatten are never gated by the window
  --sr-lookback SR_LOOKBACK
                        support/resistance: swing-pivot lookback in bars (0 =
                        off). Tags every entry with its headroom.
  --sr-min-headroom SR_MIN_HEADROOM
                        skip entries whose headroom to the nearest opposing
                        level is below this many ATR(14) (0 = tag only)

Output:
  --verbose
  --chart CHART         write a PNG chart to this path
  --hour-table          print the entry-hour breakdown (trades / win% / net /
                        avg P/L per server hour 0-23)
  --sr-report           S/R diagnostic: tag and report headroom but never
                        refuse an entry (overrides --sr-min-headroom)

NOT MODELLED:
  daily-loss brake NOT modelled -- a real losing day would have been cut short
  kill switch NOT modelled -- a real 10% drawdown would have stopped trading
  news blackout NOT modelled -- no offline calendar of high-impact USD events
  acts on bar CLOSES only; fills at close +/- half-spread, $0.20/oz round trip
  no margin modelling and no stop-out: a small account can go negative here

STARTING BALANCE: $4,000 minimum for meaningful results; $10,000+ for a clean
test of the risk rules. Below $2,000 most entries clamp to the 0.01 minimum
lot and take more than the intended 1% risk; below $500 the run is refused.
```

Note: `--strict-window` is present in the parser (proved by the inventory
diff and the direct `parse_args` check above) but does not appear in the
rendered help — that's `argparse.SUPPRESS` doing its job, unchanged from
before this task.

## Test results

```
cd service && .venv/bin/python -m pytest tests/test_backtest_help.py -q
....                                                                     [100%]
4 passed in 1.09s

cd service && .venv/bin/python -m pytest tests/test_backtest_golden.py \
  tests/test_backtest_records.py tests/test_backtest_strict_window.py \
  tests/test_backtest_balance.py -q
.................                                                        [100%]
17 passed in 3.58s
```

Full suite (`cd service && .venv/bin/python -m pytest -q`): 457 passed, 1
deselected (the `-m slow` Chronos test), 1 failed —
`test_pop_approved_command_concurrent_exactly_once`, the documented
timing-sensitive flake (CLAUDE.md: "a failure there alone -> re-run before
treating it as a regression"). Re-run in isolation: passed. Confirmed
unrelated to this change (it's in `tests/test_db_proposals.py`, nothing to
do with `scripts/backtest.py`).

## Commit

```
git add scripts/backtest.py service/tests/test_backtest_help.py
git commit -m "feat(backtest): group --help into Data/Rules/Experiments/Output and state what is not modelled"
```
