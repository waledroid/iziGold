# Reconcile-on-Reconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Basket closes that happen while MT5/EA or the service is down are back-filled to `/trade-event` on reconnect, so no close is ever silent.

**Architecture:** A watermark (MT5 global `XAU_RECON_<login>_<symbol>` = last successfully reported closing-deal ticket) plus a reconciler that scans own deal history for newer closing deals and replays them through the existing `/trade-event` path, oldest first, advancing the watermark only on successful post. The live `OnTradeTransaction` path advances the same watermark. EA-only; the service is untouched.

**Tech Stack:** MQL5 only; MetaEditor CLI compile gate.

**Spec:** `docs/superpowers/specs/2026-08-12-reconcile-on-reconnect-design.md`

## Global Constraints

- Zero service changes. Reconciled events must be byte-compatible ordinary `TradeEventRequest` close events.
- Watermark advances ONLY after a successful post (`PostTradeEvent` returns a non-negative id); scan stops at the first failure. Oldest-first ordering.
- First run (key absent): seed to the newest own closing-deal ticket WITHOUT posting anything; one Print.
- Fail-open: reconciler failures never block trading/exits/heartbeat; warnings throttled (≤1/hour), matching the codebase's `WarnThrottled` pattern.
- Compile gate 0 errors / 0 warnings via izi.md's MetaEditor CLI runbook; quote the Result line.
- Branch: `feat/reconcile-closes` from `main` (rebase onto the merged TradeTimeframe work — this plan runs AFTER it).
- Service suite as regression gate (known flake rule applies). izi.md in the same commit.

---

### Task 1: Watermark + reconciler in the EA

**Files:**
- Modify: `mt5/Experts/XauAssistant.mq5` (global-key helper section near `MigrateGlobalKeys`; `OnInit`; `OnTimer`; `OnTradeTransaction` ~line 407)
- Modify: `.claude/agents/izi.md`
- NOT modified: anything in `service/`.

**Interfaces:**
- Consumes: `g_ui.PostTradeEvent(event, strategyId, dir, lots, price, sl, reason, ticket, profit, tp, isFinal)` — read its exact current signature in `UiApi.mqh` (~line 229) and the live close-path call in `OnTradeTransaction` (~line 443) and match them exactly.
- Produces: `ReconKey()` (string helper, `"XAU_RECON_" + login + "_" + _Symbol`), `AdvanceReconWatermark(long dealTicket)`, `ReconcileOfflineCloses()`.

- [ ] **Step 1: Key helper + watermark advance**

Next to the other per-symbol key builders in `XauAssistant.mq5`:

```mql5
string ReconKey()
  {
   return "XAU_RECON_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol;
  }

void AdvanceReconWatermark(long dealTicket)
  {
   if((double)dealTicket > GlobalVariableGet(ReconKey()))
      GlobalVariableSet(ReconKey(), (double)dealTicket);
  }
```

- [ ] **Step 2: Advance the watermark on the LIVE close path**

In `OnTradeTransaction`, the existing close report goes through
`g_uiSink.OnTradeEvent("close", ...)` (~line 443). The sink's
`OnTradeEvent` (CUiSink, ~line 121) calls `g_ui.PostTradeEvent(...)` and
has the returned id (~line 162). Where that id indicates success
(non-negative — match the sink's existing success handling), and the event
is a "close" carrying a deal ticket, call `AdvanceReconWatermark(ticket)`.
Add the minimal plumbing the sink needs (it already receives the ticket).

- [ ] **Step 3: The reconciler**

In `XauAssistant.mq5`:

```mql5
// Back-fill close reports for own closing deals the service never saw
// (MT5 was down -> OnTradeTransaction never fired; or the service was
// down -> the live post was dropped). Watermark = last successfully
// reported closing-deal ticket. At-least-once, oldest-first; the scan
// stops at the first failed post so nothing is skipped.
datetime g_lastReconWarn = 0;

void ReconcileOfflineCloses()
  {
   if(!GlobalVariableCheck(ReconKey()))
     {
      // First run: seed to the newest own closing deal without reporting
      // history (no spam on install/migration).
      long newest = 0;
      if(HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent() + 60))
         for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
           {
            ulong t = HistoryDealGetTicket(i);
            if(t == 0) continue;
            if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
            if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber) continue;
            if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
            newest = (long)t;
            break;
           }
      GlobalVariableSet(ReconKey(), (double)newest);
      PrintFormat("XauAssistant: reconcile watermark seeded at deal %I64d", newest);
      return;
     }
   long watermark = (long)GlobalVariableGet(ReconKey());
   if(!HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent() + 60))
     {
      if(TimeCurrent() - g_lastReconWarn > 3600)
        {
         Print("XauAssistant: reconcile HistorySelect failed, err=", GetLastError());
         g_lastReconWarn = TimeCurrent();
        }
      return;   // fail-open: retry on the next pass
     }
   // Collect unreported own closing deals, oldest first (history is
   // time-ordered; iterate forward).
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0 || (long)t <= watermark) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      string dir = (HistoryDealGetInteger(t, DEAL_TYPE) == DEAL_TYPE_BUY)
                   ? "SELL" : "BUY";   // closing deal type is opposite the basket
      double lots   = HistoryDealGetDouble(t, DEAL_VOLUME);
      double price  = HistoryDealGetDouble(t, DEAL_PRICE);
      double profit = HistoryDealGetDouble(t, DEAL_PROFIT)
                    + HistoryDealGetDouble(t, DEAL_SWAP)
                    + HistoryDealGetDouble(t, DEAL_COMMISSION);
      string reason;
      switch((ENUM_DEAL_REASON)HistoryDealGetInteger(t, DEAL_REASON))
        {
         case DEAL_REASON_SL: reason = "stop-loss (reconciled)";   break;
         case DEAL_REASON_TP: reason = "take-profit (reconciled)"; break;
         default:             reason = "closed offline (reconciled)";
        }
      bool isFinal = !PositionSelect(_Symbol);   // flat now = this backlog ends flat
      // Replay through the same sink the live path uses so service-side
      // handling (report, render, db, channel mirror) is identical.
      long id = g_ui.PostTradeEvent("close", ActiveStrategy, dir, lots, price,
                                    0.0, reason, (long)t, profit, 0.0, isFinal);
      if(id < 0)
         return;                     // service still down -> retry next pass
      AdvanceReconWatermark((long)t);
      PrintFormat("XauAssistant: reconciled offline close deal %I64d (%s %.2f)",
                  (long)t, reason, profit);
     }
  }
```

**Adjust to reality:** read `PostTradeEvent`'s ACTUAL signature and success
convention in `UiApi.mqh` first (parameter order, whether `isFinal`/`tp`
exist, what it returns on failure) and adapt the call + the `id < 0` check
to match exactly. If the sink (`g_uiSink.OnTradeEvent`) is the cleaner
replay entry point (it also drives chart boxes — which reconciled closes
should NOT repaint), prefer the direct `g_ui.PostTradeEvent` call as shown
and say so in the report. `isFinal` for a mid-backlog deal of a multi-leg
basket: acceptable to send `final=false` for all but the last unreported
deal when flat — implement the simple flat-check shown unless the sink
signature makes per-deal finality trivial.

- [ ] **Step 4: Hooks**

- `OnInit`: after `MigrateGlobalKeys()` (so key shapes are settled), call `ReconcileOfflineCloses()`.
- `OnTimer`: call it at most once per 60 s (guard with a `static datetime g_lastRecon`; skip when `TimeCurrent() - g_lastRecon < 60`).

- [ ] **Step 5: Grep sanity + compile gate**

`grep -n "ReconKey\|ReconcileOfflineCloses\|AdvanceReconWatermark" mt5/Experts/XauAssistant.mq5` shows the helper, both hooks, and the live-path advance. Then copy to the data folder and compile via izi.md's runbook: **0 errors / 0 warnings**, quote the Result line.

- [ ] **Step 6: Service suite regression gate**

Run: `cd service && source .venv/bin/activate && python -m pytest`
Expected: green.

- [ ] **Step 7: izi.md**

Same commit: `XAU_RECON_<login>_<symbol>` key (add to the global-keys list and the maintenance-script section if it enumerates keys), reconciler behavior (OnInit + 60 s timer, oldest-first, watermark-on-success, first-run seeding), the live drill procedure (stop service → close position manually → restart service → `(reconciled)` report within ~60 s), and history-worth-knowing: 08-11's 6.1 h blackout closed a basket −$56.18 silently; this feature exists so that never repeats.

- [ ] **Step 8: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5 .claude/agents/izi.md
git commit -m "feat(mt5): reconcile-on-reconnect back-fills offline basket closes"
```

---

## Self-Review Notes (applied)

- Spec ↔ plan: watermark key, both trigger points, reason mapping, first-run seeding, ordering + stop-on-failure, fail-open throttled warning — all present in Task 1.
- The 30-day HistorySelect window bounds the scan; anything older than 30 days unreported is unreachable — acceptable (outages are hours, not weeks) and stated here deliberately.
- The plan directs the implementer to verify `PostTradeEvent`'s real signature rather than trusting the sketch — the one integration point that must match exactly.
- XauMaintenance.mq5 is NOT updated to reset the recon key: resetting it would re-report old closes (or with a fresh seed, skip nothing) — leave the key out of the reset script on purpose; izi.md documents its existence instead.
