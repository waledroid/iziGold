# Stage 2a: split miniapp.py's trades-report engine into app/reports.py

## What moved

`service/app/miniapp.py` (699 lines) held two unrelated features. The
trades-report engine moved out to a new `service/app/reports.py` (370
lines) as pure functions taking a `sqlite3.Connection` — no FastAPI
coupling:

- `BASKETS_MAX`
- `_group_baskets` (full docstring incl. the TWIN WARNING, unchanged)
- the `SERVER_UTC_OFFSET_H` / `REPORT_LOOKBACK_S` / `SIGNAL_JOIN_WINDOW_S`
  / `HB_AFTER_WINDOW_S` constants and their comment block, plus the
  "# ---- Trades report" section header comment
- `_server_offset_s`, `_server_date`, `_server_hhmm`,
  `_server_day_bounds_utc`, `_server_month_bounds_utc`
- `_table_cols`, `_htf_flag`, `_fetch_closed_baskets`
- `_fmt_day_label`, `_report_month`, `_report_day`, `_empty_report`

`reports.py` picked up the imports those functions actually need:
`bisect`, `calendar`, `datetime as _dt`, `sqlite3`, and
`from app.telegram import market_session_short`.

`miniapp.py` (now 346 lines) keeps every route unchanged — `FeedState`,
`_Hub`, `/feed/push`, `/healthz`, `/`, `/api/history`, `/api/trades`,
`/api/report`, `/ws` — and now does
`from app.reports import (_empty_report, _group_baskets, _report_day, _report_month, _server_date)`.
`_open_trades_db_ro` stayed in `miniapp.py` since both `/api/trades` and
`/api/report` (both routes, both staying) use it, and it returns a
connection rather than taking one. Dropped now-unused imports from
`miniapp.py`: `bisect`, `calendar`, `from app.telegram import
market_session_short` (the last function that used it,
`_fetch_closed_baskets`, moved out).

Every docstring/comment moved verbatim, including the cumulative-carry
balance-after fallback comment, the signal-join window comment, and the
server-offset DST caveat.

### TWIN WARNING / test_basket_twins.py

The TWIN WARNING comment on `_group_baskets` itself needed no path
change — it names `app/main.py`'s `_basket_legs`, which hasn't moved.
`service/tests/test_basket_twins.py` imports `_group_baskets` directly,
so it needed updating: `from app.miniapp import _group_baskets` →
`from app.reports import _group_baskets`, plus two doc-comment path
references (`app/miniapp.py::_group_baskets` → `app/reports.py::...`,
and the `_rows_for_group_baskets` docstring's file pointer). Test still
passes.

Two more test files imported now-moved names directly from
`app.miniapp` and needed the same import-path fix (behavior-preserving,
no assertion changes):
- `service/tests/test_trades.py::test_report_rows_carry_m15_and_session`:
  `from app.miniapp import _htf_flag, market_session_short` →
  `from app.reports import _htf_flag` + `from app.telegram import
  market_session_short` (its real home; `miniapp.py` no longer
  re-exports it).
- `service/tests/test_trades.py::test_basket_grouping_preserves_the_m15_verdict`:
  `from app.miniapp import _group_baskets, _htf_flag` → `from
  app.reports import _group_baskets, _htf_flag`.

## Verification

**Full suite:** `FORECASTER=fake .venv/bin/python -m pytest -q` →
**555 passed, 1 deselected** (matches the documented baseline exactly).
`test_pop_approved_command_concurrent_exactly_once` flaked once on a
full run (`cannot start a transaction within a transaction`, its known
timing sensitivity) and passed clean on its own and on a subsequent
full re-run — not a regression from this change.

**API-level before/after diff**, run against the real
`service/xau_assistant.db` (read-only, no writes), by calling the
shaping functions directly — `git show HEAD:service/app/miniapp.py`
snapshotted as `app/_orig_miniapp_snapshot.py` (deleted after the
comparison) for the "before" side, `app.reports`/current `app.miniapp`
for "after":

- `_report_month(conn, 2026, 8)` — **byte-identical** JSON (`sort_keys`
  dump comparison), 15 days in the output.
- `_report_day(conn, date)` for three days with real trades
  (2026-08-10, 1 row; 2026-08-19, 3 rows; 2026-08-20, 5 rows) — **all
  byte-identical**.
- `_group_baskets(rows)` on the last 200 real `/api/trades` rows (the
  shape `/api/trades` builds) — **byte-identical**, 30 baskets both
  sides.

## Left alone (found but not fixed, per instructions)

`service/app/main.py`'s `_basket_legs` docstring (line ~904) still says
its TWIN WARNING pairs with `` `app/miniapp.py`'s `_group_baskets` ``.
That reference is now stale — `_group_baskets` lives in
`app/reports.py`. `main.py` was out of scope for this stage (another
agent may be working there), so it was left untouched; whoever next
touches `main.py`'s `_basket_legs` docstring (or a follow-up commit)
should update that one line to say `app/reports.py`.

## Commit

`refactor(service): split miniapp.py's trades-report engine into app/reports.py`
