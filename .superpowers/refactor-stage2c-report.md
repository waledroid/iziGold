# Stage 2c report — extract trade-event reporting from main.py

## What moved

`service/app/main.py` (1114 -> 980 lines) -> new `service/app/trade_report.py` (158 lines):

- `_basket_legs`
- `_report_trade_event` (async; now takes `last_candles`, `screenshot_dir`, `db`,
  `telegram`, and a `mirror` callback as explicit keyword params instead of closing
  over the module-level `app` — avoids importing `app.main` from `trade_report.py`.
  The `asyncio.to_thread` dispatch and its "slow response -> EA re-delivers forever"
  comment survive unchanged.)
- `_pl_message`
- `_send_render_photo` (dead code — nothing calls it, in either location)
- `_trade_caption`
- `_prune_screenshots` (+ its `_SCREENSHOT_RETENTION = 500` constant)

`main.py` keeps only the `/trade-event` and `/screenshot` route handlers, importing
`_basket_legs`, `_report_trade_event`, `_trade_caption`, `_prune_screenshots` from
`app.trade_report`. The `/trade-event` handler's `asyncio.create_task(...)` call was
updated to pass `app.state.last_candles/screenshot_dir/db/telegram` plus
`mirror=lambda **kw: _mirror(app, **kw)` explicitly.

Also updated (import-path fallout of the move, not behaviour):
- `tests/test_basket_twins.py`, `tests/test_render.py`, `tests/test_trades.py`:
  `from app.main import _basket_legs/_trade_caption` -> `from app.trade_report import ...`
- `tests/test_render.py`'s two `render_trade_chart` monkeypatches now patch
  `app.trade_report.render_trade_chart` (where the call now lives) instead of
  `app.main.render_trade_chart`.

## Twin pointer fix

`main.py:904`'s `_basket_legs` docstring said its twin was `app/miniapp.py`'s
`_group_baskets` — stale; that function moved to `app/reports.py` earlier. Corrected
(now living in the moved function's docstring in `trade_report.py`). As a direct
consequence of the move itself, also corrected the reverse pointer in
`app/reports.py::_group_baskets`'s docstring (two places: "Mirrors `_basket_legs` in
app/main.py" and "TWIN WARNING: `app/main.py`'s `_basket_legs`") to say
`app/trade_report.py`, and `tests/test_basket_twins.py`'s module docstring
(`app/main.py::_basket_legs` -> `app/trade_report.py::_basket_legs`). Without these,
the move would have created a fresh set of stale pointers.

## Caption evidence

Exercised `_trade_caption` from `app.trade_report` directly, all four cases:

```
--- agree ---
open BUY 0.1@4500.0 — signal BUY
M15: agrees ✅

--- disagree ---
open SELL 0.1@4500.0 — signal SELL
M15: DISAGREES ⚠️

--- unknown ---
open BUY 0.1@4500.0 — signal BUY

--- close ---
close BUY 0.1@4500.0 — stop-loss; P/L -20.0
```

Matches `tests/test_trades.py::test_entry_caption_reports_the_m15_verdict` exactly:
agree carries "M15: agrees", disagree carries "M15: DISAGREES", unknown carries no
M15 line, close carries no M15 line (only the P/L suffix).

## Verification

- `cd service && .venv/bin/python -m pytest -q` -> **555 passed, 1 deselected** (the
  documented `test_pop_approved_command_concurrent_exactly_once` flake did not fire).
  Matches the stated baseline.
- `grep -n "^def _basket_legs\|^async def _report_trade_event\|^def _pl_message\|^def _send_render_photo\|^def _trade_caption\|^def _prune_screenshots" app/main.py` -> empty.
- `grep -rn "from app\.main import.*(_basket_legs|_report_trade_event|_pl_message|_send_render_photo|_trade_caption|_prune_screenshots)"` across the repo -> empty.

## Concerns found but deliberately left alone

- `_send_render_photo` (now in `trade_report.py`) has no callers anywhere in the
  codebase, in either the old or new location — looks like dead code left over from
  an earlier version of the render/photo flow. Moved as-is per the task's explicit
  move list; not removed since "no behaviour improvements" was in scope.
- `_report_trade_event`'s `last_candles`/`telegram` parameters are now evaluated
  eagerly at `asyncio.create_task(...)` call time in `main.py`, rather than read
  lazily from `app.state` inside the coroutine body at whatever moment the event
  loop actually runs it (the original closure-over-global behaviour). In the
  extremely narrow window between task creation and first execution, a concurrent
  `/analyze` post (which mutates `app.state.last_candles`) or an in-flight
  `/ui/profile` Telegram-client swap (`_apply_telegram`) could theoretically see a
  different value than before. This is a pre-existing race either way (the original
  code raced too, just at a slightly later point), not exercised by any test, and
  not fixable without either passing the whole `app` object (which the task
  explicitly steers away from) or adding lazy getter callables — flagging rather
  than "fixing" since it's outside the pure-move brief.

## Commit

`e4448e9` — `refactor(service): extract trade-event reporting into app/trade_report.py`
Branch: `refactor/modular-stage-1-2` (not pushed, not merged).
