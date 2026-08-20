# Backtest CLI + visual report — design

Date: 2026-08-20
Status: approved (brainstorm, this session)

## Problem

`scripts/backtest.py` already replays the rulebook faithfully and produced
every study this week, but two things are missing:

1. **You cannot see a backtest.** `--chart` writes a matplotlib PNG with no
   HalfTrend line, no EMAs, no SL/TP, and *fabricated* trade spans — it draws
   a box "12 bars back from the exit" because the engine never records when a
   trade opened. A result you cannot inspect is a result you cannot trust.
2. **The default run is not the live EA.** The strict 3-bar entry rule that
   the EA now enforces always is opt-in here (`--strict-window`), so the
   out-of-the-box replay takes entries the live system refuses.

`--help` is also a flat dump of ~35 flags, which makes the tool hard to pick
up after a week away.

## What we are building

An extension of the existing engine — not a rewrite. Three deliverables:

- engine records what a chart needs, and defaults to the live EA's entry rule;
- `--json` writes one artifact describing a run;
- `--web` writes a self-contained HTML report; later the same page becomes a
  Mini App tab.

## Non-goals

- No rewrite of the replay loop. Its behaviour is proven and every published
  study depends on it.
- **The daily-loss brake and kill switch are NOT modelled** (owner decision
  2026-08-20). A simulated day that loses 3% keeps trading, where the live EA
  would stop. Reports must say so.
- The **news blackout is NOT modelled** — no offline calendar of historical
  high-impact USD events exists. Reports must say so.
- No live/paper trading from the backtester. It reads history and writes
  files; it never touches MT5 or the trading service.

## 1. Engine changes (`scripts/backtest.py`)

### 1.1 Record the trade's shape

The trade dict gains four fields. Nothing existing is removed — the study
reports read the current fields and must keep working.

| Field | Meaning |
|---|---|
| `legs[].t` | bar time (epoch seconds) at which that leg filled |
| `stop_history` | `[{t, stop}]`, appended on **every** stop change: initial placement, laddered move after an add, profit-lock arm, floor ratchet |
| `tp` | the take-profit price level in force (None in `fixed` entry mode) |
| `bal_after` | account balance after the basket closed |

`stop_history` is the honest record of a moving stop. A trade whose stop never
moved has a single entry.

### 1.2 Defaults become the live EA's entry rule

`STRICT_WINDOW` defaults to **on**: after a HalfTrend flip, wait one closed
bar, and enter on the next bar only if it opens beyond EMA-55; otherwise the
signal is dead until the next flip. `--loose-window` restores the old
permissive behaviour for comparison against earlier studies.

`--strict-window` is kept as an accepted no-op alias so existing scripted runs
do not break.

### 1.3 `--help` grouped

Four argparse groups: **Data** (`--source --start --end --days --tf`),
**Rules** (the EA's real knobs: `--risk --confirm --stop-buffer --adx --expo
--entry-mode --fixed-lots --profit-target --ema-len --loose-window
--exit-scheme --strict-window`),
**Experiments** (study flags: regime/chop/bias/sr/min-stop/window),
**Output** (`--verbose --json --web --chart --hour-table --sr-report`).

The epilog carries the caveat block (brake, kill switch, news, close-only
fills, spread model) so `--help` states the model's limits; the same line is
printed at the end of every run, since stdout is where most results are read.

**Shipped deviations from this section.** `--exit-scheme` is listed above
under Experiments in the original draft but shipped under **Rules**, which is
correct: its default (`target-exit`) is live EA behaviour, and the Experiments
group is defined as flags that are off in the live EA. `--window-start` /
`--window-end` are the reverse case — they live in Experiments but default to
the EA's real 4-23 trading window, so the group blurb says so explicitly
instead of claiming everything in it defaults to OFF.

## 2. `--json PATH` — the run artifact

One JSON object, the only interface between engine and page:

```
{ "meta":   {generated_at, source, tf, start, end, args:{...}, caveats:[...]},
  "stats":  {trades, wins, losses, win_rate, net, start_balance, end_balance,
             max_dd, max_valley, best, worst,
             clamp_pct, risk_median, risk_p90},
  "candles": {"t":[...], "o":[...], "h":[...], "l":[...], "c":[...]},
  "ind":     {"ema9":[...], "ema21":[...], "ema55":[...], "ema200":[...],
              "ht":{"v":[...], "trend":[...]}},
  "trades": [{dir, legs:[{t,px,oz}], tp, stop_history:[{t,stop}],
              exit, exit_t, why, pl, bal_after, regime}] }
```

Candles and indicators are **parallel arrays**, not per-bar objects: 12 months
of M5 is ~74k bars, and the array form keeps the payload near 6–7 MB instead
of ~12 MB with no loss of detail. `null` marks an unwarmed indicator bar.
Measured: a 365-day M5 run (`--days 365`, 70,707 bars, 1,210 trades) writes a
6.3 MB `--json` artifact and a 6.4 MB `--web` report. Those figures are for
`--days 365` only — a plain run over the whole of `bars_max.json` (516 days,
99,999 bars, 1,729 trades) writes ~8.9 MB JSON / ~9.0 MB HTML.

Indicators come from `app.indicators.ema/halftrend` (amplitude 4) — the same
functions the Mini App and the EA port use. The page never computes a rule or
an indicator itself.

## 3. `--web PATH` — the report page

A single self-contained HTML file: no server, no network, opens anywhere,
keeps as an artifact for comparison. Data embedded as the §2 JSON.

Chart: TradingView Lightweight Charts (already vendored for the Mini App),
candles at the replay timeframe — **pure M5, all 12 months, nothing
aggregated**. Overlays match MT5 exactly, reusing the Mini App's approach:
HalfTrend as ONE line whose colour changes per point (blue up / orange-red
down), plus EMA 9/21/55/200.

Per trade:

- a box from entry bar to exit bar: green zone entry→TP, red zone entry→SL;
- a **stepped stop line** drawn from `stop_history`, so a ratcheting stop is
  visible as it actually moved;
- a marker at each pyramid add, a marker at the exit, and a P/L label.
  **Shipped deviation:** this section asked for a triangle at the add and an X
  at the exit; Lightweight Charts v4 offers neither shape (`arrowUp`,
  `arrowDown`, `circle`, `square` only), so the add ships as a yellow circle
  and the exit as a P/L-coloured square. Entry keeps `arrowUp`/`arrowDown`,
  which is the direction cue the next bullet asks for;
- BUY and SELL are distinguished by marker direction, not box colour;
- in `fixed` entry mode there is no TP, so the box shows only the red
  entry→SL zone and the stepped stop — never an invented target.

Header: net P/L, win rate, trade count, max drawdown, date range, and the
caveat line. Trade table below the chart; clicking a row scrolls the chart to
that trade.

## 4. Mini App tab (after the file works)

A **📊 Backtest** tab beside Chart and Trades. `--json` writes to a known path
under `service/`; miniapp serves it at `/api/backtest` behind the existing
`viewer_ok()` authorization, and the tab renders it with the same drawing code
as the standalone page. Read-only, last run only. If no run exists the tab
says so rather than erroring.

## 5. Starting balance and the minimum that means anything

`--balance` already exists (default 4000). What is missing is the honesty
around it.

**The binding constraint is the 0.01 minimum lot, not spread.** Sizing is
`oz = max(MIN_OZ, int(risk / dist))`: when 1% of balance cannot cover one
ounce at the stop distance, the replay does not skip the trade — it takes the
minimum lot and **over-risks**.

**Re-measured 2026-08-20 on the shipped default (STRICT entry window)**, with
`scripts/backtest.py --source bars_max.json --days 365 --balance N`:

| Starting balance | entries clamped to min lot | risk actually taken (median / p90) |
|---|---|---|
| $500   | 94.7% | 1.72% / 34.71% |
| $800   | 68.5% | 1.43% / 3.89% |
| $1,200 | 47.0% | 0.98% / 2.28% |
| $2,000 | 32.3% | 0.90% / 1.77% |
| $4,000 | 16.7% | 0.89% / 1.27% |
| $10,000 | 1.3% | 0.93% / 0.99% |
| $25,000 | 0.0% | 0.97% / 0.99% |

Over the full source (516 days, no `--days`) the same command clamps **20.5%**
at $4,000 and **3.7%** at $10,000.

> The first version of this table was measured under the **LOOSE** window,
> before strict became the default on 2026-08-20, and read materially lower
> (88.7% / 50.8% / 10.2% / 0.4% at $500 / $1,200 / $4,000 / $10,000). Strict
> refuses late-drift entries, which are disproportionately the wide-stop ones
> that size fine — so the surviving population clamps more often. Any clamp
> figure quoted anywhere must name the window it was measured under.

Below ~$10,000 the 1% rule stops being reliably obeyed. **$4,000 clamps
roughly one entry in six — enough to trip this tool's own ">10% ⇒ results
distorted" flag**, so it is no longer described as the minimum for meaningful
results. At $300 the account goes negative (-155%) because margin stop-out is
not modelled.

Spread is $0.20/oz round-trip and scales linearly with size — ~$950 over 12
months at $4,000, roughly half that year's net loss. Material to the RESULT,
irrelevant to the MINIMUM, because it costs the same fraction at every
balance.

### Interface

CLI validation on `--balance`:

- **< $500 — refuse.** Exit with the reason: below this the replay's result is
  fiction (near-total clamping, and an account that goes negative because no
  margin stop-out is modelled).
- **$500–$2,000 — run, warn loudly.** A banner in stdout and in the report
  naming the measured clamp rate for THAT run.
- **>= $2,000 — run clean**, still reporting clamp rate when it exceeds 5%.

**Every run reports its measured clamp rate and the risk actually taken**
(median and p90), in stdout, in `--json` `stats`, and in the report header.
This is a measurement, not a static threshold, because clamping depends on how
wide the stops were in the period tested — a volatile month clamps a balance
that a quiet month would have sized fine. A run whose clamp rate exceeds 10%
draws a visible warning banner on the page.

Guidance text shown in `--help`, in `validate_balance()` and on the page:
**"$10,000+ for a clean test of the risk rules; $4,000 still clamps roughly
one entry in six."** The **>10% "results distorted" threshold is unchanged** —
the guidance moved to match the measurement, not the other way round.

In the Mini App tab (§4), the balance is an input with presets
**$1k · $4k · $10k · $25k**, a $500 floor enforced client- and server-side,
and a **[Run backtest]** button — a 12-month replay takes ~3.6 s, so it is
genuinely interactive. That endpoint is the only compute-on-demand path in the
system: owner-only via the existing `viewer_ok()`, one run at a time, and
rate-limited.

## 6. Backtest day/month report (later phase)

The backtest gets the SAME tabled report the Mini App shows for live trades —
month view by default, a clickable day opening the full day table, back
navigation, and CSV export from both views.

"Exactly as today's Mini App reporting" is a structural guarantee, not a
resemblance: `_report_month()` and `_report_day()` in `miniapp.py` are pure
shaping functions sitting on top of one fetch (`_fetch_closed_baskets`). They
are refactored to take a **list of baskets** instead of a sqlite connection,
with the caller doing the fetch. Then:

- live view  = shape(baskets read from SQLite)
- backtest view = shape(baskets converted from the §2 `trades` array)

One renderer, one shaper, two sources. The two reports cannot drift apart,
and a change to either view lands in both.

The backtest→basket conversion maps a replayed basket onto the live row shape:
open/close time, direction, total lots, entry (size-weighted average of the
legs), exit, exit reason, P/L, running balance, regime. Fields the replay has
no source for (broker ticket, commission, swap) are omitted, not faked.

Differences forced by the data, and nothing else:

- the month picker spans the whole backtest range (a 12-month run has 12
  months to page through), where the live view centres on the current month;
- every report header carries the run's caveat block, so a backtest month can
  never be mistaken for a live month;
- day labels use the same server-time convention (`SERVER_UTC_OFFSET_H`) as
  the live report, so a backtest day and a live day mean the same hours.

Sequencing: this phase follows §4. It is specified here so §2's `trades`
array is designed to carry everything the report needs (open time, close
time, running balance, regime are all present), rather than being retrofitted.

## Testing

- **Golden-run regression.** A fixed slice of `bars_max.json` replayed with
  `--loose-window` must produce exactly the trade list the current code
  produces. This is the guard that no rule changed silently while the engine
  was edited; it must be captured BEFORE any engine change.
- **Strict window** unit tests: a flip whose third bar opens beyond EMA-55
  enters; one that does not is dead until the next flip.
- **`stop_history`** tests: initial stop recorded; a laddered move after an add
  appends; the profit lock appends; a never-moved stop has exactly one entry.
- **`--json`** shape test: parallel arrays same length as candles, trades carry
  entry times, round-trips through `json.load`.
- **`--web`** test: file written, self-contained (no external URLs), embeds the
  expected trade count.
- **Balance validation**: below $500 refuses; $500-$2,000 warns; clamp rate
  and realized-risk percentiles appear in stdout, `--json`, and the page.
- **Report parity** (phase 6): the refactored shaping functions, fed the
  same baskets, produce byte-identical output to today's live report —
  captured as a golden test before the refactor.
- Existing service suite (437 tests) stays green.

## Risks

- **Silent behaviour change while editing a 1,429-line engine.** Mitigated by
  the golden-run test captured first.
- **Page weight.** 74k bars is the owner's explicit choice; parallel arrays and
  a single indicator pass keep it near 6–7 MB for a 365-day run (measured:
  6.3 MB JSON / 6.4 MB HTML at 70,707 bars / 1,210 trades) and ~9 MB for the
  full 516-day source (99,999 bars, 1,729 trades). What actually shipped for
  a struggling browser is not a timeframe switcher: candles, indicators, the
  stepped stop and every trade's entry/exit markers still draw in full at any
  trade count, and the trade table is never thinned; above 300 trades the page
  drops only the pyramid-add ("+") chart markers, and discloses on-page how
  many trades' add markers it hid (the Legs column in the table still shows
  every add). A timeframe switcher was considered and rejected — it would
  change what the replay tests, not just how it renders.
- **Reports read as truth.** Every report and `--help` carries the caveat block
  naming the unmodelled brake and news blackout.
