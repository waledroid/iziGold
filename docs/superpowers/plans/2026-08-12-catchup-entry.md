# Guarded Catch-up Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After an outage, `halftrend_ema_v1` takes a missed entry through the normal signal path iff the thesis still holds at restart (trend intact, price still beyond EMA, no chase, fresh enough); otherwise suppresses as today with the failing guard named.

**Architecture:** Warm-up replay records where the current trend's confirm first fired; the blanket `m_fired = true` suppression becomes conditional on a `CatchupOk()` guard check over live data. A passing check leaves `m_fired = false`, so the first `Evaluate()` after warm-up emits the signal into the unchanged gate/sizing/reporting path.

**Tech Stack:** MQL5 only; MetaEditor CLI compile gate. No service changes.

**Spec:** `docs/superpowers/specs/2026-08-12-catchup-entry-design.md`

## Global Constraints

- Catch-up NEVER bypasses anything: the emitted signal flows through `CanEnter`, `StopPrice()`, 1% sizing, `/analyze`, proposals (MANUAL) exactly like a live signal. The change is confined to the suppression decision.
- Scope: `HalfTrendEma.mqh` + EA inputs/registration only. `BollStochRsi.mqh` keeps plain suppression.
- Defaults: `CatchupEnabled=true`, `CatchupMaxAgeBars=12`, `CatchupMaxChaseATR=1.0`.
- One Print on catch-up fire; one Print naming the failing guard on suppression (age / thesis / chase / disabled).
- Compile gate 0 errors / 0 warnings via izi.md's MetaEditor CLI runbook (quote the Result line). Service suite as regression gate (untouched; known flake rule).
- Branch: `feat/catchup-entry` from `main`. izi.md same commit.

---

### Task 1: Catch-up guard in HalfTrendEma warm-up

**Files:**
- Modify: `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh` (members ~line 30; `ProcessClosedBar` counter block ~line 144; constructor ~line 156; `Evaluate` warm-up branch ~line 177)
- Modify: `mt5/Experts/XauAssistant.mq5` (input block; strategy registration `new CHalfTrendEmaStrategy(...)`)
- Modify: `.claude/agents/izi.md`

**Interfaces:**
- Consumes: existing members `m_trend`, `m_consecAbove/Below`, `m_confirm`, `m_fired`, `m_emaHandle`, `m_atrHandle`, `m_tf`; EA registration call `new CHalfTrendEmaStrategy(TradeTimeframe, HtAmplitude, EmaLength, ConfirmCloses, StopBufferATR)`.
- Produces: constructor gains three trailing params `(..., bool catchupEnabled, int catchupMaxAgeBars, double catchupMaxChaseAtr)`; EA inputs `CatchupEnabled/CatchupMaxAgeBars/CatchupMaxChaseATR`.

- [ ] **Step 0: Create the branch**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau && git checkout -b feat/catchup-entry main
```

- [ ] **Step 1: EA inputs + registration (`XauAssistant.mq5`)**

Input block (near the strategy inputs):

```mql5
input bool   CatchupEnabled     = true;  // take a missed entry after downtime if still valid
input int    CatchupMaxAgeBars  = 12;    // signal at most this many trade-TF bars old
input double CatchupMaxChaseATR = 1.0;   // max adverse run beyond the signal close, in ATR(14)
```

Registration gains the three trailing arguments:

```mql5
   g_registry.Register(new CHalfTrendEmaStrategy(TradeTimeframe, HtAmplitude, EmaLength,
                       ConfirmCloses, StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR));
```

- [ ] **Step 2: Record the confirm bar during replay (`HalfTrendEma.mqh`)**

New members (next to `m_fired`):

```mql5
   bool     m_catchupEnabled;
   int      m_catchupMaxAge;
   double   m_catchupMaxChaseAtr;
   int      m_confirmShift;    // shift where the CURRENT trend's entry first
   double   m_confirmClose;    // confirmed during processing; 0 = none yet
```

Constructor: three trailing params stored; init `m_confirmShift(0), m_confirmClose(0)` in the initializer list.

In `ProcessClosedBar`, the flip block (`if(m_trend != prevTrend)`) additionally resets the record:

```mql5
         m_confirmShift = 0; m_confirmClose = 0;
```

and the counter block records the FIRST time the current trend's counter reaches `m_confirm`:

```mql5
      if(haveEma)
        {
         if(close > emaBuf[0])      { m_consecAbove++; m_consecBelow = 0; }
         else if(close < emaBuf[0]) { m_consecBelow++; m_consecAbove = 0; }
         if(m_confirmShift == 0 &&
            ((m_trend == 0 && m_consecAbove == m_confirm) ||
             (m_trend == 1 && m_consecBelow == m_confirm)))
           { m_confirmShift = shift; m_confirmClose = close; }
        }
```

(Recording also happens during live bars; it is only ever READ in the warm-up branch, so that is harmless by construction.)

- [ ] **Step 3: Conditional suppression + guards (`Evaluate` warm-up branch)**

Replace the existing suppression block:

```mql5
         // suppress stale entry: if this trend already confirmed during warm-up,
         // the real entry bar is long past — wait for the next flip
         if((m_trend == 0 && m_consecAbove >= m_confirm) ||
            (m_trend == 1 && m_consecBelow >= m_confirm))
            m_fired = true;
```

with:

```mql5
         // this trend's entry already confirmed during the gap: normally a
         // stale entry (suppress, wait for the next flip) — unless the
         // catch-up guards say the thesis is still intact right now, in
         // which case m_fired stays false and the first Evaluate() below
         // emits the signal through the normal gate path.
         if((m_trend == 0 && m_consecAbove >= m_confirm) ||
            (m_trend == 1 && m_consecBelow >= m_confirm))
           {
            if(!CatchupOk())
               m_fired = true;
           }
```

New private method:

```mql5
   // Missed-entry catch-up guards, evaluated on CURRENT data. True = the
   // outage-spanning signal is still tradeable now. Every rejection prints
   // its reason once (this runs once, at warm-up).
   bool CatchupOk()
     {
      if(!m_catchupEnabled)
        { Print("halftrend_ema_v1: catch-up disabled — stale entry suppressed"); return false; }
      if(m_confirmShift == 0 || m_confirmClose <= 0)
        { Print("halftrend_ema_v1: catch-up — no confirm bar recorded, suppressed"); return false; }
      int ageBars = m_confirmShift - 1;   // bars between confirm bar and newest closed bar
      if(ageBars > m_catchupMaxAge)
        {
         PrintFormat("halftrend_ema_v1: catch-up rejected — signal %d bars old (max %d)",
                     ageBars, m_catchupMaxAge);
         return false;
        }
      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, 1, 1, emaBuf) != 1 || emaBuf[0] <= 0)
        { Print("halftrend_ema_v1: catch-up — EMA unavailable, suppressed"); return false; }
      double atrBuf[];
      if(CopyBuffer(m_atrHandle, 0, 1, 1, atrBuf) != 1 || atrBuf[0] <= 0)
        { Print("halftrend_ema_v1: catch-up — ATR unavailable, suppressed"); return false; }
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(m_trend == 1)   // SELL thesis
        {
         if(bid >= emaBuf[0])
           { Print("halftrend_ema_v1: catch-up rejected — price back above EMA, thesis gone"); return false; }
         if(m_confirmClose - bid > m_catchupMaxChaseAtr * atrBuf[0])
           { Print("halftrend_ema_v1: catch-up rejected — price already ran, not chasing"); return false; }
        }
      else               // BUY thesis
        {
         if(bid <= emaBuf[0])
           { Print("halftrend_ema_v1: catch-up rejected — price back below EMA, thesis gone"); return false; }
         if(bid - m_confirmClose > m_catchupMaxChaseAtr * atrBuf[0])
           { Print("halftrend_ema_v1: catch-up rejected — price already ran, not chasing"); return false; }
        }
      PrintFormat("halftrend_ema_v1: catch-up entry — %s confirmed %d bars ago during downtime, guards passed",
                  m_trend == 1 ? "SELL" : "BUY", ageBars);
      return true;
     }
```

**Placement:** define `CatchupOk()` before `Evaluate` in the class body. Note `m_atrHandle` already exists (used by `StopPrice`).

- [ ] **Step 4: Verify the emission path unchanged**

Read the `if(!m_fired)` block below the warm-up branch and confirm no edits are needed: with `m_fired` left false and the counters already ≥ `m_confirm`, the first `Evaluate()` returns the signal, and the EA's normal `ProcessBar` flow (risk gates → execute/propose → `/analyze`) handles it. Confirm `BollStochRsi.mqh` is untouched.

- [ ] **Step 5: Copy to the MT5 data folder + compile (0 errors / 0 warnings)**

Per izi.md's MetaEditor CLI runbook; quote the `Result:` line. NOTE: new inputs take defaults on hot-reload — `CatchupEnabled=true` is the desired live state.

- [ ] **Step 6: Service suite regression gate**

Run: `cd service && source .venv/bin/activate && python -m pytest`
Expected: green (no service code touched).

- [ ] **Step 7: izi.md**

Same commit: the three inputs + guard list (age / thesis-now / no-chase, each with its Print), "fires within seconds of restart through the normal gate path (g_lastBar=0 → first tick processes)", MANUAL mode yields an ordinary proposal, BollStochRsi non-scope note, and history: born from the 08-11 blackout + the owner's "can it still jump on it?" question.

- [ ] **Step 8: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5 mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh .claude/agents/izi.md
git commit -m "feat(mt5): guarded catch-up entry — take a missed signal after downtime while the thesis holds"
```

---

## Self-Review Notes (applied)

- Spec guard set ↔ `CatchupOk()`: enabled, age, thesis-now (Bid vs current EMA), no-chase (ATR-capped adverse run) — all present with named rejection Prints; pass logs the spec's line.
- `m_confirmShift == m_confirm`-reach uses `==` (first reach only) and resets on flip — matches "first qualifying close after the flip".
- Age formula: confirm at shift s means s−1 closed bars elapsed since; ≤ 12 default = 1 h on M5.
- Emission stays on the normal path; no new order code, no gate bypass (Global Constraint restated in Step 4 as an explicit verification).
- ATR/EMA read at shift 1 (last closed bar) for determinism; Bid is the actual prospective entry price.
