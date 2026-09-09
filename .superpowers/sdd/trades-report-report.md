# Trades report tab — build report (2026-08-18)

**Status:** done. Branch `feat/trades-report`, commit `c4999f9`.

## What was built
- `service/app/miniapp.py`
  - `_group_baskets(rows, cap=BASKETS_MAX)` extended (not duplicated): `cap=None` for the report; baskets now carry `pl` (sum of ALL close-leg profits), `entry_mode` (blank→"adr"), `reason` (final close), `strategy_id`. `/api/trades` output unchanged in shape (extra keys only).
  - `GET /api/report?view=month&month=YYYY-MM` / `?view=day&date=YYYY-MM-DD` — `require_viewer`, read-only db via `_open_trades_db_ro`, fail-open (db errors → 200 empty), malformed params → 400. Defaults to current broker month / today.
  - `SERVER_UTC_OFFSET_H = 3` (broker UTC+3) — day/month boundaries and the signals `bar_time` join both use it. Must track broker DST (flagged in izi §8).
  - Balance-after: first heartbeat ≤10 min after the close, else last heartbeat before + basket pl, else null (`balance_src` tells which).
  - Regime/AI: nearest active BUY/SELL signal same direction, bar open ≤ first entry + 60 s within 4 h; `ai` = agree/disagree/null.
- `service/app/static/miniapp.html` — tab bar (📈 Chart | 📋 Trades), Trades panel: Month view (day rows, tap → Day view, ‹ › month nav, footer incl. regime win-rate, inline-SVG equity sparkline), Day view (← Back; Time/Dir/Mode/Entry→Exit/Reason/P/L/Balance/AI; no regime column), ⬇ CSV (Blob download) + Copy CSV (clipboard w/ execCommand fallback) on both. All DOM built via textContent (no innerHTML of data). Uses the same `X-Telegram-Init-Data` header plumbing.
- `service/tests/test_miniapp.py` — 8 new tests (auth 403, bad params 400, month aggregation across a UTC-midnight/server-day boundary, day rows incl. heartbeat pick + regime + AI, empty month/day, missing db fail-open, page contains tab markup).
- `.claude/agents/izi.md` §8 — "Trades report tab" paragraph (endpoints, offset caveat, P/L semantics, CSV/copy behavior).

## Gates
- `pytest tests/test_miniapp.py -q` → 36 passed.
- Full suite → 423 passed, 1 deselected (no flake hit).
- `node --check` on the extracted inline script → OK.
- Deploy: watchdog restarted the miniapp; `curl :9001/` contains `id="tabTrades"` + `id="repCsv"`; `/api/report` → 403 unauthenticated (auth enforced, route live).

## Deviations from the DATA guidance (deliberate, documented in izi)
1. Basket P/L = SUM of every close row's profit in the basket, not only the final close row — the EA posts one close row per deal, so multi-leg exits (real db has them: e.g. -25.04 non-final + -40.86 final at the same ts) would be understated otherwise.
2. Balance-after prefers the first heartbeat AFTER the close (≤10 min) — a heartbeat before the close does not yet reflect the deal; falls back to last-before + pl, then null (no running-sum reconstruction when there are zero heartbeats — rendered "–").

## Concerns
- `SERVER_UTC_OFFSET_H` is hard-coded 3; when the broker moves to winter time (UTC+2) the buckets and the signal join shift by an hour until it's edited.
- Rows fetched per request span [window − 45 days, window end] so a basket opened before the month still groups; fine at current volumes (hundreds of rows), not indexed.
- The running miniapp now serves the feature-branch working tree (watchdog deploys on file mtime); merging to main keeps it identical.
- Frontend verified by syntax check + code reading only (no browser available in WSL); the Blob download inside Telegram's webview is best-effort by design — Copy CSV is the reliable path.

## Review follow-up (2 items landed)
1. Balance-after fallback now walks baskets in close order with a running carry: inside one heartbeat gap the n-th basket shows `hb_before + pl1 + … + pln`; carry resets on a real post-close heartbeat. New test `test_report_balance_fallback_carries_cumulative_pl_across_a_heartbeat_gap` (asserts 1040 / 1025 / 1035 and `balance_src` = hb_before+pl, hb_before+pl, hb_after).
2. `csvCell()` formula-injection guard: string cells starting with `= + - @ \t \r` get a leading `'`; numeric cells untouched (negative numbers stay numbers).
Gates: tests/test_miniapp.py 37 passed; full suite 424 passed, 1 deselected; node --check OK. izi §8 updated.
