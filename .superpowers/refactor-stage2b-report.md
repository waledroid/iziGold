# Refactor Stage 2b — extract reconciliation and CUiSink from XauAssistant.mq5

Branch: `refactor/modular-stage-1-2`. Pure move, no behaviour change, `mt5/` only.

## What moved

1. **Reconciliation** (`MigrateGlobalKey`, `MigrateGlobalKeys`, `ReconKey`,
   `AdvanceReconWatermark`, `NewestOwnClosingDeal`, `ReconcileOfflineCloses`,
   plus the two throttle timestamps `g_lastReconWarn`/`g_lastReconLookupWarn`)
   → new file `mt5/Include/XauAssistant/Reconciler.mqh`, class `CReconciler`.
   - Parameterised by magic number: the class stores `m_magic` (set via
     `Init(magic, ui, activeStrategyId)`) instead of reading the EA's
     `MagicNumber` input directly, so a future second trading lane can own its
     own `CReconciler` instance/watermark without touching this class. This
     task does **not** change the watermark key format itself (still
     login+symbol only, no magic in the string) — that stays out of scope per
     the brief.
   - `g_ui.PostTradeEvent(...)` calls became `m_ui.PostTradeEvent(...)` (an
     injected `CUiApi*`), and the `ActiveStrategy` EA input used inside
     `ReconcileOfflineCloses` became the injected `m_activeStrategyId` string
     (captured once in `Init`, matching the original's use of the immutable
     input value, not the live registry's active strategy).

2. **`CUiSink`** → new file `mt5/Include/XauAssistant/UiSink.mqh`. Same five
   dispatch concerns, same order: chart-box update (`m_boxes.OnOpen`/
   `OnClose`), the strategy's `OnBasketClosed` hook, the HTF-verdict lookup
   (`active.LastHtfAgree()`), the reconciliation-watermark advance
   (`m_recon.AdvanceReconWatermark`/`NewestOwnClosingDeal`), and the
   screenshot upload (`m_ui.UploadScreenshot`).
   - Converted from directly reading file-scope globals (`g_registry`,
     `g_ui`, `g_tradeBoxes`, and the free reconciliation functions) to
     dependency injection (`Init(registry, ui, boxes, recon)`), matching the
     codebase's established pattern (`CRiskManager` takes `CNewsGuard*`,
     `CTradeManager` takes `CTradeEventSink*`, etc.) — required because an
     Include file's `#include` sits above the `.mq5`'s global-variable
     declarations, so it cannot see them as free globals the way the old
     in-file class could.

## .mq5 changes (wiring only)

- Two new `#include`s added at the bottom of the include block.
- `class CUiSink { ... }; CUiSink g_uiSink;` replaced with `CUiSink g_uiSink;`
  plus a new `CReconciler g_recon;` global.
- The whole ~237-line reconciliation block replaced with a one-line comment
  pointing at `Reconciler.mqh`.
- `OnInit()`: `MigrateGlobalKeys()` → `g_recon.MigrateGlobalKeys()` (same
  position, before `ApplyDarkTheme`); after `g_ui.Init(...)`, two new wiring
  lines — `g_recon.Init(MagicNumber, &g_ui, ActiveStrategy)` and
  `g_uiSink.Init(&g_registry, &g_ui, &g_tradeBoxes, &g_recon)` — then
  `ReconcileOfflineCloses()` → `g_recon.ReconcileOfflineCloses()` (same
  position). `g_trades.Init(..., &g_uiSink)` unchanged (still passes the
  sink's address; the sink is now DI-wired first).
- `OnTimer()`: the 60s-throttled `ReconcileOfflineCloses()` call →
  `g_recon.ReconcileOfflineCloses()` (same position/throttle logic).
- `OnTradeTransaction()`'s `g_uiSink.OnTradeEvent(...)` call site is
  unchanged (only the sink's *declaration* moved, not its call sites).
- File size: 882 → 567 lines.

## Compile result

```
Result: 0 errors, 0 warnings, 3313 ms elapsed, cpu='X64 Regular'
```

Compiled via
`metaeditor64.exe /compile:".../MQL5/Experts/XauAssistant.mq5"` after copying
`XauAssistant.mq5`, `Reconciler.mqh`, and `UiSink.mqh` into the live MT5 data
folder (mirroring `Include/XauAssistant/...` and `Experts/...`). This is the
live chart's EA — the compile hot-reloaded it, as expected for this
environment.

## Inspection evidence

- **Global-variable key strings unchanged**: grepped `"XAU_` literals in the
  new `Reconciler.mqh` (`XAU_RECON_`, `XAU_KILL`, `XAU_HWM`, `XAU_CYCLE_BAL`,
  `XAU_PEAK`, `XAU_EXPO`) against the pre-move file at `HEAD` — byte-for-byte
  identical set and construction (login+symbol / login+symbol+day shapes
  untouched).
- **Reconciliation call order unchanged**: `git diff` of `XauAssistant.mq5`
  shows the moved bodies deleted verbatim (no logic edits) and the call sites
  re-inserted at the same relative positions in `OnInit`/`OnTimer` — same
  place before/after `ApplyDarkTheme`, `g_ui.Init`, and the 60s `OnTimer`
  throttle.
- **`CUiSink`'s five concerns fire in the same order**: box update → strategy
  `OnBasketClosed` → HTF-agree lookup for the open/add PostTradeEvent call →
  watermark advance on close → screenshot upload — identical statement order
  to the original, confirmed by diffing the extracted method body (only
  `g_x` → `m_x` renames from the DI conversion; every branch, comment, and
  gate (`basketGone`, `event == "close"`, ticket-vs-`NewestOwnClosingDeal`
  fallback) is untouched).
- Every comment from both blocks was preserved verbatim in the new files
  (incident notes on `XAU_RECON`, the `ticket=0 CloseAll` advance, the
  history-derived `isFinal` flag, the safe first-run seed, the ordered
  replay, and the five-concern bridging comment for `CUiSink`).
- `Strategy.mqh`, `StrategyRegistry.mqh`, `TradeManager.mqh`,
  `RiskManager.mqh` were not touched (`diff` confirms `StrategyRegistry.mqh`
  identical to the deployed copy; the other three were never opened for
  edit).

## Python suite

`cd service && .venv/bin/python -m pytest -q` → **553 passed, 2 failed, 1
deselected**. The 2 failures (`test_trades.py::test_report_rows_carry_m15_and_session`,
`test_trades.py::test_basket_grouping_preserves_the_m15_verdict`) are
`ImportError: cannot import name '_htf_flag' from 'app.miniapp'` — pre-existing
breakage from the other agent's concurrent, in-progress refactor of
`service/app/miniapp.py` (confirmed via `git status`: `miniapp.py`,
`test_basket_twins.py`, `test_trades.py` modified and a new untracked
`app/reports.py`, none of which this task touched). Not caused by this change.

## Concerns / anything left alone

- None found in the moved code itself. The one design note worth flagging:
  `ReconcileOfflineCloses` replays through `m_activeStrategyId` (the
  `ActiveStrategy` EA input's original, compile-time value) rather than the
  live active strategy — this was already the pre-existing behavior (the
  original code used the raw `ActiveStrategy` input the same way) and is
  preserved unchanged, not a new bug introduced by this move.
- Multi-magic support (making the watermark key itself lane-specific) is
  explicitly out of scope per the brief and was left alone; `CReconciler` is
  now shaped to make that a later, isolated change (just its `m_magic` field
  and a key-format edit) rather than an EA-wide rewrite.
