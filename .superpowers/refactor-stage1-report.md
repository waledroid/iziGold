# Stage 1 modularity refactor — safety stage report

Branch `refactor/modular-stage-1-2`. Baseline: 527 passed (1 deselected slow
test). Final: 530 passed (1 deselected). No behaviour change anywhere in
this stage.

## Item 1 — pin the `_basket_legs` / `_group_baskets` twin

**What changed:** Added a `TWIN WARNING` cross-reference comment to both
`_basket_legs` (`service/app/main.py`) and `_group_baskets`
(`service/app/miniapp.py`), each naming the other, explaining why the
duplication exists (one walks backward in SQL from a single just-inserted
row id; the other groups a whole fetched window at once), and pointing at
the new pinning test. Added `service/tests/test_basket_twins.py` with
`test_basket_legs_and_group_baskets_agree_on_the_same_legs`, which builds
three synthetic baskets via `/trade-event` (a single-leg basket, a
multi-leg basket that survives a non-final partial-stop close, and a
trailing still-open basket with no close row yet) and asserts both twins
agree field-for-field on the legs they share (`price`, `lots`, in the same
order), plus explicit assertions on where they diverge.

**The shared contract:** a basket is the run of `'open'`/`'add'` trade rows
since the previous row with `event='close' AND final=1`, up to (and
including) the next such final close. A non-final close — a single leg
stopping out while the rest of the basket survives (`TradeEventRequest.
final=False`) — does not end the basket and is not itself a leg; its
`profit` still folds into the basket's running P/L. Both functions must
return the same leg set, in the same (ascending id / chronological) order,
agreeing on `price` and `lots` per leg.

**Where they legitimately diverge (documented in both comments and pinned
in the test):**
- `_basket_legs`' legs carry `event`, `sl`, `tp` — no `ts`, no `htf_agree`.
  `sl`/`tp` are needed downstream to backfill the chart render and the P/L
  Telegram message (`_report_trade_event`, `main.py:940-960`).
- `_group_baskets`' entries carry `ts`, `htf_agree` — no `sl`, no `tp`. The
  mini-app's own SQL (`/api/trades`, `_fetch_closed_baskets`) never even
  selects `sl`/`tp` for this path; live SL/TP lines in the mini-app UI come
  from a *different* endpoint (`renderPositions` in `miniapp.html`, backed
  by the current open-position feed, not trade history).
- `_group_baskets` also returns basket-level display fields `_basket_legs`
  has no reason to carry (`direction`, `entry_mode`, `strategy_id`,
  `reason`, `exit`, `pl`): it returns a whole basket record for a report
  table, where `_basket_legs` returns a flat leg list for a single
  render/Telegram call.

**Verdict: the existing divergence is benign, not a latent bug.** Every
extra field on either side traces to a real, distinct consumer need
(chart-render SL/TP backfill vs. mini-app report display), and the field
the two share (which rows are legs, price, lots, order) was — once
correctly exercised — in agreement. The one real fragility found while
writing the test is not a twin-divergence issue: `_basket_legs`' SQL has no
upper bound on `id`, only a lower one (`id > last_close`), so it is only
correct when called immediately after inserting the row it's asked about
(which is exactly how `main.py`'s `/trade-event` handler uses it, and is
already called out in that call site's own comment). That is a real,
if narrow, sharp edge worth knowing about but is outside this item's scope
to change in a no-behaviour-change stage.

## Item 2 — `ui_overlays` per-strategy branching -> registry

**What changed:** `ui_overlays` (`service/app/main.py`) hardcoded
`if strategy == "halftrend_ema_v1"` / `"boll_stochrsi_v1"`. Extracted each
branch into its own builder function (`_overlays_halftrend_ema_v1`,
`_overlays_boll_stochrsi_v1`, each `(candles, closes) -> dict`) and added a
`_OVERLAY_BUILDERS: dict[str, Callable]` registry. `ui_overlays` now looks
up `strategy` in the registry and calls the builder if found, otherwise
falls through to the original `{}` fallback (unknown strategy or no
candles yet). A new strategy now adds one registry entry instead of
editing a conditional, matching the tag-based pattern used everywhere else
(e.g. `db.stats()`).

No behaviour change: the existing `/ui/overlays` tests
(`tests/test_ui_endpoints.py`) pass unmodified, and the fallback path is
identical.

## Item 3 — `exec_mode`/`entry_mode`/`htf_enforce` -> generic toggle pair

**What changed:** Added `SignalDb.get_choice(name, choices, default)` and
`SignalDb.set_choice(name, value, choices)` (`service/app/db.py`), and
reimplemented `exec_mode()`/`set_exec_mode()`,
`entry_mode()`/`set_entry_mode()`, and `htf_enforce()`/`set_htf_enforce()`
on top of them. All three public method names, signatures, and setter
`ValueError` behaviour are unchanged; callers and existing tests needed no
changes. `HeartbeatResponse`'s three named fields (`models.py`) were left
untouched, as instructed — that stays the cross-process contract with the
MQL5 EA.

**The shared contract turned out to be:** "a kv-stored string value
restricted to a fixed choice set, with a default substituted when the
stored value is missing or unrecognised" (`get_choice`), paired with "raise
`ValueError` if a value outside the choice set is written" (`set_choice`).

**One deliberate, narrow behaviour tightening, called out explicitly and
covered by a new test:** the pre-refactor `exec_mode()`/`entry_mode()`
getters only checked truthiness (`val if val else default`) and would have
returned a garbage stored value as-is if one ever existed, whereas
`htf_enforce()` already validated membership (`val if val in CHOICES else
default`). Building all three on one `get_choice()` means `exec_mode()`/
`entry_mode()` now also validate membership. This is unreachable in normal
operation — the kv store for these three keys is only ever written by the
validating setters — and no existing test relied on the looser behaviour,
so it does not change any observed behaviour. Documented in
`test_exec_mode_and_entry_mode_reimplemented_on_get_choice`
(`tests/test_db_proposals.py`) and in `izi.md`.

`izi.md` was updated in the same commit (784bee5) per the izi-sync rule,
noting the new generic pair and the one behaviour note above.

## Test count

- Baseline: 527 passed, 1 deselected.
- After item 1: 528 passed, 1 deselected (+1: the twin-pinning test).
- After item 2: 528 passed, 1 deselected (no new tests; existing
  `/ui/overlays` tests re-verified).
- After item 3: 530 passed, 1 deselected (+2: `get_choice`/`set_choice`
  generic-pair test, and the exec/entry-mode degrade-to-default test).
- Final full run: **530 passed, 1 deselected**, ~85s.
  `test_pop_approved_command_concurrent_exactly_once` (the documented
  timing flake) was not observed to fail in any run this session.
