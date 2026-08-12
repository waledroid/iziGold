# TradeTimeframe (Pin Trading to M5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every EA trading decision reads a new `TradeTimeframe` input (default `PERIOD_M5`) instead of the chart timeframe; the chart TF becomes visual-only.

**Architecture:** Thread an `ENUM_TIMEFRAMES` through the EA's OnInit into RiskManager, AiApi, UiApi, and both strategies (constructor param → `m_tf` member); replace every decision-path `PERIOD_CURRENT` with the threaded value. `TradeBoxes.mqh` (chart painting) deliberately keeps `PERIOD_CURRENT`.

**Tech Stack:** MQL5 only; MetaEditor CLI compile gate. No service code changes.

**Spec:** `docs/superpowers/specs/2026-08-12-trade-timeframe-design.md`

## Global Constraints

- After the change, `grep -rn PERIOD_CURRENT mt5/` must hit ONLY `mt5/Include/XauAssistant/TradeBoxes.mqh`.
- Compile gate: 0 errors / 0 warnings via the MetaEditor CLI procedure in izi.md's ops runbook (copy changed files to the MT5 data folder first; quote the log's Result line in the report).
- `/analyze`'s `timeframe` string must derive from `TradeTimeframe` (e.g. `"M5"`), never `_Period`.
- No trading-logic changes beyond the timeframe source; no service changes; izi.md updated in the same commit.
- Branch: `feat/trade-timeframe` from `main`.
- Service suite as regression gate: `cd service && source .venv/bin/activate && python -m pytest` (known flake `test_pop_approved_command_concurrent_exactly_once` — re-run once if it alone fails).

---

### Task 1: Thread TradeTimeframe through the EA

**Files:**
- Modify: `mt5/Experts/XauAssistant.mq5` (input block; OnInit wiring ~lines 230–258; OnTick bar detect ~line 262)
- Modify: `mt5/Include/XauAssistant/RiskManager.mqh` (`Init` ~line 40, `OnBarUpdate` ~line 63, `TodayRealized` ~line 87)
- Modify: `mt5/Include/XauAssistant/AiApi.mqh` (`Init`, `BuildJson` ~lines 20–30)
- Modify: `mt5/Include/XauAssistant/UiApi.mqh` (`Init`, `PostHeartbeat` bar-0 `CopyRates` ~line 145)
- Modify: `mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh`, `mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh` (constructor + all TF call sites)
- Modify: `.claude/agents/izi.md`
- NOT modified: `mt5/Include/XauAssistant/TradeBoxes.mqh` (painting stays chart-TF), `mt5/Include/XauAssistant/Strategy.mqh` base (no TF needed in the interface), all of `service/`.

**Interfaces:**
- Consumes: existing Init signatures (see file/line anchors above).
- Produces: `input ENUM_TIMEFRAMES TradeTimeframe = PERIOD_M5;` and a `tf`/`m_tf` parameter threaded into `CRiskManager::Init`, `CAiApi::Init`, `CUiApi::Init`, and both strategy constructors.

- [ ] **Step 0: Create the branch**

```bash
cd /mnt/c/Users/aatanda/Desktop/xau && git checkout -b feat/trade-timeframe main
```

- [ ] **Step 1: EA input + OnInit wiring (`XauAssistant.mq5`)**

Add to the input block (near the other trading inputs):

```mql5
input ENUM_TIMEFRAMES TradeTimeframe = PERIOD_M5; // trading TF — chart TF is visual only
```

In `OnInit`:
- Strategy registration gains the TF as the FIRST constructor argument:
  `new CHalfTrendEmaStrategy(TradeTimeframe, HtAmplitude, EmaLength, ConfirmCloses, StopBufferATR)` and
  `new CBollStochRsiStrategy(TradeTimeframe, BbPeriod, ...)`.
- `g_api.Init(ApiUrl, ApiTimeoutMs)` → `g_api.Init(ApiUrl, ApiTimeoutMs, TradeTimeframe)`.
- `g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber)` → `g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber, TradeTimeframe)`.
- `g_risk.Init(...)` gains `TradeTimeframe` as a new parameter (put it just before the `CNewsGuard*` default param).
- `g_atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14)` → `iATR(_Symbol, TradeTimeframe, 14)`.
- After the Init block, add the visibility print:

```mql5
   if(Period() != TradeTimeframe)
      PrintFormat("XauAssistant: trading TF %s (chart %s — visual only)",
                  StringSubstr(EnumToString(TradeTimeframe), 7),
                  StringSubstr(EnumToString(Period()), 7));
```

In `OnTick`: `iTime(_Symbol, PERIOD_CURRENT, 0)` → `iTime(_Symbol, TradeTimeframe, 0)`.

Check the rest of the .mq5 for further `PERIOD_CURRENT` uses (the flatten/window logic uses clock time, not bars — expect none, but verify with grep).

- [ ] **Step 2: RiskManager, AiApi, UiApi**

`RiskManager.mqh`: add `ENUM_TIMEFRAMES m_tf;` member; `Init(..., ENUM_TIMEFRAMES tf, CNewsGuard *news = NULL)` stores it; replace the three `PERIOD_CURRENT` uses (`iADX` handle, `PeriodSeconds` exposure accumulation, `TodayRealized`'s per-bar cache `iTime`) with `m_tf`.

`AiApi.mqh`: add `ENUM_TIMEFRAMES m_tf;`; `Init(string url, int timeout, ENUM_TIMEFRAMES tf)` stores it; `BuildJson`: `CopyRates(_Symbol, m_tf, 1, count, rates)` and the timeframe tag becomes

```mql5
      string tf = StringSubstr(EnumToString(m_tf), 7); // "PERIOD_M5" -> "M5"
```

`UiApi.mqh`: add `ENUM_TIMEFRAMES m_tf;`; `Init(..., ENUM_TIMEFRAMES tf)` stores it; `PostHeartbeat`'s forming-bar read becomes `CopyRates(_Symbol, m_tf, 0, 1, bar0)`.

- [ ] **Step 3: Both strategies**

Each strategy class gains `ENUM_TIMEFRAMES m_tf;` set as the FIRST constructor parameter (before the existing ones), assigned before any indicator handle is created. Then replace EVERY `PERIOD_CURRENT` in `HalfTrendEma.mqh` and `BollStochRsi.mqh` with `m_tf` — indicator handles (`iMA`/`iATR`/`iBands`/`iRSI`), bar data (`CopyHigh/CopyLow/CopyClose`, `iTime/iLow/iHigh/iClose`, `Bars`), and `PeriodSeconds`. If a handle is created in the constructor's initializer list or body, ensure `m_tf` is assigned first (reorder to assignment-then-handles if needed).

- [ ] **Step 4: Grep gate**

```bash
grep -rn PERIOD_CURRENT mt5/
```
Expected: hits ONLY in `mt5/Include/XauAssistant/TradeBoxes.mqh`. Anything else = incomplete threading; fix before compiling.

- [ ] **Step 5: Copy to the MT5 data folder and compile (0 errors / 0 warnings)**

Follow izi.md's MetaEditor CLI runbook exactly (copy ALL changed `.mqh` files + the `.mq5`, compile `XauAssistant.mq5`, iconv the UTF-16LE log). Quote the `Result:` line in your report. NOTE: the EA hot-reloads and the NEW input takes its default `PERIOD_M5` — which is the desired live state.

- [ ] **Step 6: Service suite regression gate**

Run: `cd service && source .venv/bin/activate && python -m pytest`
Expected: green (no service code touched).

- [ ] **Step 7: izi.md**

Same commit: `TradeTimeframe` input (default M5; chart TF is visual-only — switching the chart NEVER changes trading), the TradeBoxes painting exception, the grep invariant ("PERIOD_CURRENT allowed only in TradeBoxes.mqh"), and a history-worth-knowing entry: 2026-08-11 chart moved to M15 → EA silently traded M15 (uncalibrated) → −$41.97 M15 stop-out on 08-12 exposed it; this input is the permanent fix.

- [ ] **Step 8: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5 mt5/Include/XauAssistant/RiskManager.mqh mt5/Include/XauAssistant/AiApi.mqh mt5/Include/XauAssistant/UiApi.mqh mt5/Include/XauAssistant/Strategies/HalfTrendEma.mqh mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh .claude/agents/izi.md
git commit -m "feat(mt5): TradeTimeframe input pins trading to M5 — chart TF is visual only"
```

---

## Self-Review Notes (applied)

- Spec's "pinned" list ↔ Steps 1–3 cover every non-TradeBoxes `PERIOD_CURRENT` from the survey (XauAssistant ×2, RiskManager ×3, AiApi ×1 + TF tag, UiApi ×1, HalfTrendEma ×~14, BollStochRsi ×~5). The grep gate makes the list exhaustive by construction.
- `_Period`/`Period()` audit: the only `_Period` use found in the survey is AiApi's TF tag (Step 2 replaces it); Step 1's visibility print deliberately uses `Period()` for the CHART side. Implementer must also grep `_Period` to confirm no other decision-path use exists.
- One task, one commit: the change is atomic (a partial threading would trade mixed TFs); reviewer gates on grep + compile evidence.
