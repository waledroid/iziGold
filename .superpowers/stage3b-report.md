# Stage 3b — telegram.py command/callback registry

Scope: `service/app/telegram.py` and its tests only. Branch
`feat/multi-magic-rails`.

## What was extracted

1. **Inline command bodies → `_format_X` helpers.** `/mode`, `/agree`,
   `/strategy`, `/config`, `/channel` had their reply bodies inline in the
   `handle_command` if/elif chain (alongside `/status`, `/bal`, `/stats`,
   `/history`, `/switch`, which already delegated). Extracted verbatim,
   byte-for-byte, into:
   - `_format_mode(app)` -> `(text, keyboard)`
   - `_format_agree(app)` -> `(text, keyboard)`
   - `_format_strategy(app)` -> `(text, keyboard | None)`
   - `_format_config(app, redacted=False)` -> `str`
   - `_format_channel(app, args)` -> `str`

2. **`COMMANDS` registry** — `dict[str, CommandSpec]` mapping each command
   string to a `CommandSpec(handler, help, arg_hint="")`, where `handler`
   has the uniform signature `(app, parts, redacted) -> reply`. Ten thin
   `_cmd_X` wrappers adapt each `_format_X` helper (and the pre-existing
   ones) to that signature so `/switch`'s and `/channel`'s arg-taking shape
   and `/status`/`/bal`/`/config`'s `redacted` need didn't have to be forced
   into one form — the wrapper absorbs the difference.
   `handle_command` is now just a 3-line dict lookup + call.

3. **`format_pinned_help()` is generated from `COMMANDS`**, in registry
   order, as `f"{cmd}{arg_hint} — {help}"` per line, plus the two trailing
   proposal-legend lines (unchanged, still hand-written — they aren't
   commands). One exception: **`/chart` is not in `COMMANDS`** — it's
   special-cased in `main.py`'s poller *before* `handle_command` is even
   called, because it needs to send a photo / open the mini-app
   asynchronously, not return a text reply. It still needs a pinned-help
   line, so a small `_PINNED_EXTRA = {"/config": ["/chart — open the live
   chart"]}` map inserts that one line verbatim at the right position
   (after `/config`, matching the original text) without pretending it's
   dispatched through the registry.

4. **Drift-safety test** —
   `test_pinned_help_and_command_registry_cannot_drift` in
   `tests/test_telegram_commands.py`: parses every `/`-leading line out of
   `format_pinned_help()`, and asserts it equals `set(COMMANDS) |
   {tokens from _PINNED_EXTRA}` exactly — every registered command is
   documented, and nothing is documented that isn't either dispatched or
   the one explained exception.

5. **Callbacks** — `handle_callback`'s if/elif chain replaced with a
   `CALLBACKS` dict keyed by `parts[0]` (`mode`, `tmode`, `agree`, `strat`,
   `prop`, `exitnow`, `brakereset`, `chan`), each mapped to an `_cb_X(parts,
   app, db, message_id)` handler. Handlers were **not** forced to one
   signature/arity — each keeps its own validation and, where the original
   condition included an arity/value check (all except `brakereset`, which
   never had one), returns `(None, "unknown")` on failure, exactly
   reproducing what falling through the old elif chain to the final
   `return (None, "unknown")` did.

## Verification

- Full suite: **557 passed, 1 deselected** (baseline was 555; +2 new tests:
  the drift-safety test and `test_every_registered_command_returns_something`).
  One run hit the documented flake
  (`test_pop_approved_command_concurrent_exactly_once` —
  `sqlite3.OperationalError: cannot start a transaction within a
  transaction` under thread-timing pressure); an immediate re-run passed
  clean at 557/1. Unrelated to telegram.py — it's in `test_db_proposals.py`
  against `app/db.py`.
- **Help-text diff**: captured `format_pinned_help()` from
  `git show HEAD:service/app/telegram.py` (executed against the pre-refactor
  source) and from the refactored module, `diff`'d the two outputs —
  **empty diff, byte-identical**. `PINNED_HELP_VERSION` was left at `"8"`
  (not bumped) since the generated text is unchanged.
- **Every command exercised**: ran `handle_command(cmd, app)` for all ten
  registry keys against a real wired app/db (equity, positions, kill
  switch, etc. populated) — all ten returned non-`None` (`/mode`, `/agree`,
  `/strategy` return tuples; the rest strings). This check is now also a
  permanent regression test
  (`test_every_registered_command_returns_something`).
- Pre-existing per-command/per-callback tests
  (`test_mode_command_returns_buttons`, `test_mode_callback_switches`,
  `test_strategy_*`, `test_config_command_*`, `test_agree_*`,
  `test_proposal_callback_*`, `test_exit_button.py`
  (`exitnow:`/`brakereset:`), `test_channel.py` (`chan:link`/`chan:ignore`),
  `test_entry_mode.py` (`tmode:`)) all still pass unmodified — they were
  the real behavioral proof that each extracted handler and each callback
  handler still does exactly what it did before.

## Left alone / found but not touched

- **`/chart` is pinned-help-only, never in `COMMANDS`.** This is the one
  place the "registry = single source of truth" story has an asterisk: the
  photo/mini-app command genuinely can't be a synchronous `handle_command`
  return value, so it's intercepted one level up in `main.py` before
  `handle_command` is ever called. Rather than pretend otherwise (e.g. by
  registering a dead handler in `COMMANDS` that would never actually run),
  I documented the exception explicitly in code (`_PINNED_EXTRA` + a
  comment on `COMMANDS`) and made the drift test aware of it, so this stays
  a visible, tested exception rather than a silent one. Flagging this in
  case a future stage wants `main.py`'s dispatch unified with this
  registry too — that would be a `main.py` change, out of scope here.
- No other behavioral or structural issues found in the file during this
  pass.

## Commit

`refactor(service): telegram command/callback dispatch tables`
