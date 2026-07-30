# boll_stochrsi_v1 Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Second registry strategy — Bollinger Band trend zone + squeeze→expansion + Stochastic RSI cross — as `mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh`, registered in the EA.

**Architecture:** One new `CStrategy` subclass with strategy-internal virtual-position state (needed because its EXIT is not the opposite entry, and shadows have no real position). No service or db changes. Spec: `docs/superpowers/specs/2026-07-31-boll-stochrsi-strategy-design.md`.

**Tech Stack:** MQL5 only.

## Global Constraints

- MQL5 cannot be compiled here — each task ends with a recorded self-check; MetaEditor compile happens in the user's manual checklist (Task 3).
- Strategy id exact string: `"boll_stochrsi_v1"`.
- Inputs and defaults verbatim from the spec: `BbPeriod=20`, `BbDev=2.0`, `TrendCloses=2`, `SqueezeLookback=100`, `SqueezePctile=25`, `ExpansionBars=2`, `RsiPeriod=14`, `StochPeriod=14`, `KSmooth=3`, `DSmooth=3`.
- All evaluation on closed bars only (every read shift ≥ 1). Warm-up replay ~600 bars oldest→newest on first Evaluate; after warm-up, suppress any fresh-cross flag so no stale entry fires on attach.
- This strategy DOES emit `SIGNAL_EXIT` (close crossing the middle band against the virtual position). `StopPrice()` returns 0.0 (framework ATR default).
- Fail-open and no-martingale rules unchanged; the strategy only signals.
- Commit messages: repo style + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `Strategies/BollStochRsi.mqh`

**Files:**
- Create: `mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh`

**Interfaces:**
- Consumes: `CStrategy` (Strategy.mqh): `Id()`, `Evaluate()`, `ConditionStillTrue(ENUM_SIGNAL)`, `StopPrice(ENUM_SIGNAL)`.
- Produces: `CBollStochRsiStrategy(int bbPeriod, double bbDev, int trendCloses, int squeezeLookback, double squeezePctile, int expansionBars, int rsiPeriod, int stochPeriod, int kSmooth, int dSmooth)`. Task 2 constructs it with EA inputs in this exact parameter order.

Implementation notes for the transcriber: `CopyBuffer`/`CopyClose` fill arrays oldest→newest (index `count-1` = the requested shift bar) unless set as-series — the code below relies on that. The virtual position (`m_virtualDir`) tracks what the strategy last signaled, so EXIT works for shadows and MANUAL mode alike.

- [ ] **Step 1: Create the file with exactly this content:**

```mql5
// BollStochRsi.mqh — Bollinger trend zone + squeeze->expansion + StochRSI cross.
// Adapted from a Binance BB+StochRSI strategy for XAUUSD M15 (spec 2026-07-31).
// Exit: close crossing the middle band against the position; stop: ATR default.
#ifndef XAU_STRAT_BOLL_STOCHRSI_MQH
#define XAU_STRAT_BOLL_STOCHRSI_MQH
#include <XauAssistant/Strategy.mqh>

class CBollStochRsiStrategy : public CStrategy
  {
private:
   int      m_bbPeriod;
   double   m_bbDev;
   int      m_trendCloses;
   int      m_squeezeLookback;
   double   m_squeezePctile;
   int      m_expansionBars;
   int      m_rsiPeriod, m_stochPeriod, m_kSmooth, m_dSmooth;
   int      m_warmupBars;

   int      m_bbHandle;
   int      m_rsiHandle;

   datetime m_lastProcessed;
   double   m_bw[];             // bandwidth history, newest last, capped at lookback
   double   m_prevBw;
   int      m_risingStreak, m_flatStreak;
   bool     m_armed;            // squeeze seen while not in expansion
   bool     m_expansion;
   int      m_longZoneCloses, m_shortZoneCloses;
   double   m_rawK[];           // last kSmooth raw stoch values
   double   m_kHist[];          // last dSmooth %K values
   double   m_k, m_d, m_prevK, m_prevD;
   bool     m_crossUp, m_crossDown;   // fresh cross on the last processed bar
   ENUM_SIGNAL m_virtualDir;    // what we last signaled: NONE/BUY/SELL

   void PushCapped(double &arr[], double v, int cap)
     {
      int n = ArraySize(arr);
      if(n < cap) { ArrayResize(arr, n + 1); arr[n] = v; return; }
      for(int i = 0; i < cap - 1; i++) arr[i] = arr[i + 1];
      arr[cap - 1] = v;
     }

   double Avg(const double &arr[])
     {
      int n = ArraySize(arr);
      if(n == 0) return 0;
      double s = 0;
      for(int i = 0; i < n; i++) s += arr[i];
      return s / n;
     }

   bool IsSqueeze(double bw)
     {
      int n = ArraySize(m_bw);
      if(n < m_squeezeLookback / 2) return false;   // not enough history yet
      int below = 0;
      for(int i = 0; i < n; i++) if(m_bw[i] <= bw) below++;
      return (100.0 * below / n) <= m_squeezePctile;
     }

   void ProcessClosedBar(int shift)
     {
      double upper[], middle[], lower[], close[];
      if(CopyBuffer(m_bbHandle, 1, shift, 1, upper)  != 1) return; // 1 = upper
      if(CopyBuffer(m_bbHandle, 0, shift, 1, middle) != 1) return; // 0 = middle
      if(CopyBuffer(m_bbHandle, 2, shift, 1, lower)  != 1) return; // 2 = lower
      if(CopyClose(_Symbol, PERIOD_CURRENT, shift, 1, close) != 1) return;
      double up = upper[0], mid = middle[0], lo = lower[0], cl = close[0];
      if(mid <= 0) return;

      // --- bandwidth + squeeze/expansion state machine
      double bw = (up - lo) / mid;
      bool squeeze = IsSqueeze(bw);
      bool rising = (m_prevBw > 0 && bw > m_prevBw);
      if(rising) { m_risingStreak++; m_flatStreak = 0; }
      else       { m_flatStreak++;  m_risingStreak = 0; }
      if(!m_expansion)
        {
         if(squeeze) m_armed = true;
         if(m_armed && m_risingStreak >= m_expansionBars) m_expansion = true;
        }
      else if(m_flatStreak >= m_expansionBars)
        { m_expansion = false; m_armed = false; }
      PushCapped(m_bw, bw, m_squeezeLookback);
      m_prevBw = bw;

      // --- trend-zone consecutive closes
      if(cl > mid && cl <= up) { m_longZoneCloses++;  m_shortZoneCloses = 0; }
      else if(cl < mid && cl >= lo) { m_shortZoneCloses++; m_longZoneCloses = 0; }
      else { m_longZoneCloses = 0; m_shortZoneCloses = 0; }

      // --- Stochastic RSI: raw stoch of RSI, then K = SMA(raw), D = SMA(K)
      double rsi[];
      if(CopyBuffer(m_rsiHandle, 0, shift, m_stochPeriod, rsi) != m_stochPeriod) return;
      double rmin = rsi[ArrayMinimum(rsi)], rmax = rsi[ArrayMaximum(rsi)];
      double cur = rsi[m_stochPeriod - 1];              // newest = requested shift bar
      double raw = (rmax - rmin > 0) ? (cur - rmin) / (rmax - rmin) * 100.0 : 50.0;
      PushCapped(m_rawK, raw, m_kSmooth);
      m_prevK = m_k; m_prevD = m_d;
      m_k = Avg(m_rawK);
      PushCapped(m_kHist, m_k, m_dSmooth);
      m_d = Avg(m_kHist);
      m_crossUp   = (m_prevK <= m_prevD && m_k > m_d);
      m_crossDown = (m_prevK >= m_prevD && m_k < m_d);

      // --- virtual-position exit: close crossing the middle band against us
      if(m_virtualDir == SIGNAL_BUY  && cl < mid) m_pendingExit = true;
      if(m_virtualDir == SIGNAL_SELL && cl > mid) m_pendingExit = true;
     }

   bool m_pendingExit;

public:
   CBollStochRsiStrategy(int bbPeriod, double bbDev, int trendCloses,
                         int squeezeLookback, double squeezePctile, int expansionBars,
                         int rsiPeriod, int stochPeriod, int kSmooth, int dSmooth)
      : m_bbPeriod(bbPeriod), m_bbDev(bbDev), m_trendCloses(trendCloses),
        m_squeezeLookback(squeezeLookback), m_squeezePctile(squeezePctile),
        m_expansionBars(expansionBars), m_rsiPeriod(rsiPeriod),
        m_stochPeriod(stochPeriod), m_kSmooth(kSmooth), m_dSmooth(dSmooth),
        m_warmupBars(600), m_lastProcessed(0), m_prevBw(0),
        m_risingStreak(0), m_flatStreak(0), m_armed(false), m_expansion(false),
        m_longZoneCloses(0), m_shortZoneCloses(0), m_k(50), m_d(50),
        m_prevK(50), m_prevD(50), m_crossUp(false), m_crossDown(false),
        m_virtualDir(SIGNAL_NONE), m_pendingExit(false)
     {
      m_bbHandle  = iBands(_Symbol, PERIOD_CURRENT, m_bbPeriod, 0, m_bbDev, PRICE_CLOSE);
      m_rsiHandle = iRSI(_Symbol, PERIOD_CURRENT, m_rsiPeriod, PRICE_CLOSE);
     }

   virtual string Id() { return "boll_stochrsi_v1"; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, PERIOD_CURRENT, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      if(m_lastProcessed == 0)
        {
         int avail = Bars(_Symbol, PERIOD_CURRENT) - m_stochPeriod - 2;
         int from = MathMin(m_warmupBars, MathMax(avail, 1));
         for(int s = from; s >= 1; s--) ProcessClosedBar(s);
         // suppress stale signals on attach: no entry without a fresh live cross,
         // and no exit for a virtual position that was never really signaled
         m_crossUp = false; m_crossDown = false; m_pendingExit = false;
         m_virtualDir = SIGNAL_NONE;
        }
      else
         ProcessClosedBar(1);
      m_lastProcessed = closed;

      if(m_pendingExit)
        { m_pendingExit = false; m_virtualDir = SIGNAL_NONE; return SIGNAL_EXIT; }
      if(m_virtualDir == SIGNAL_NONE)
        {
         if(m_expansion && m_crossUp && m_longZoneCloses >= m_trendCloses)
           { m_virtualDir = SIGNAL_BUY; return SIGNAL_BUY; }
         if(m_expansion && m_crossDown && m_shortZoneCloses >= m_trendCloses)
           { m_virtualDir = SIGNAL_SELL; return SIGNAL_SELL; }
        }
      return SIGNAL_NONE;
     }

   virtual bool ConditionStillTrue(ENUM_SIGNAL dir)
     {
      if(dir == SIGNAL_BUY)  return m_longZoneCloses  >= 1;
      if(dir == SIGNAL_SELL) return m_shortZoneCloses >= 1;
      return false;
     }

   virtual double StopPrice(ENUM_SIGNAL dir) { return 0.0; }  // ATR default
  };
#endif
```

- [ ] **Step 2: Self-check** against the spec, item by item, and record results: id string exact; constructor parameter order matches the Interfaces block; all reads shift ≥ 1; warm-up oldest→newest with stale-signal suppression (crosses AND pending exit AND virtual dir cleared); EXIT only fires when a virtual position exists; entry requires expansion + fresh cross + zone closes; `iBands` buffer indices (0=middle, 1=upper, 2=lower) stated correctly; `m_pendingExit` declared before first use compiles in MQL5 (class member order is not a compile concern in MQL5 — confirm by reading).

- [ ] **Step 3: Commit**

```bash
git add mt5/Include/XauAssistant/Strategies/BollStochRsi.mqh
git commit -m "feat(mt5): BB+StochRSI squeeze-expansion strategy (boll_stochrsi_v1)"
```

---

### Task 2: EA registration + inputs

**Files:**
- Modify: `mt5/Experts/XauAssistant.mq5`

**Interfaces:**
- Consumes: `CBollStochRsiStrategy` constructor (Task 1 order): `(BbPeriod, BbDev, TrendCloses, SqueezeLookback, SqueezePctile, ExpansionBars, RsiPeriod, StochPeriod, KSmooth, DSmooth)`.

- [ ] **Step 1: Add include** next to the HalfTrendEma include:

```mql5
#include <XauAssistant/Strategies/BollStochRsi.mqh>
```

- [ ] **Step 2: Add inputs** below the existing `ConfirmCloses` input:

```mql5
input int    BbPeriod        = 20;   // boll_stochrsi: Bollinger period
input double BbDev           = 2.0;  // boll_stochrsi: Bollinger deviation
input int    TrendCloses     = 2;    // boll_stochrsi: closes in trend zone
input int    SqueezeLookback = 100;  // boll_stochrsi: bandwidth history bars
input double SqueezePctile   = 25;   // boll_stochrsi: squeeze percentile
input int    ExpansionBars   = 2;    // boll_stochrsi: rising bars to confirm expansion
input int    RsiPeriod       = 14;   // boll_stochrsi: RSI period
input int    StochPeriod     = 14;   // boll_stochrsi: stochastic window over RSI
input int    KSmooth         = 3;    // boll_stochrsi: %K smoothing
input int    DSmooth         = 3;    // boll_stochrsi: %D smoothing
```

- [ ] **Step 3: Register** in `OnInit()` after the HalfTrendEma registration line:

```mql5
   g_registry.Register(new CBollStochRsiStrategy(BbPeriod, BbDev, TrendCloses,
                       SqueezeLookback, SqueezePctile, ExpansionBars,
                       RsiPeriod, StochPeriod, KSmooth, DSmooth));
```

- [ ] **Step 4: Self-check** — argument order matches the constructor exactly (10 args); `ActiveStrategy` default stays `halftrend_ema_v1` (boll_stochrsi starts as a shadow); no other EA logic touched.

- [ ] **Step 5: Commit**

```bash
git add mt5/Experts/XauAssistant.mq5
git commit -m "feat(mt5): register boll_stochrsi_v1 as second strategy"
```

---

### Task 3: Docs + verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README** — in the section-4 inputs table, after the halftrend row, add:

```markdown
| `BbPeriod`…`DSmooth` (10 inputs) | spec defaults | `boll_stochrsi_v1` parameters (starts as shadow) |
```

and in the strategies paragraph, after the halftrend sentence, add:

```markdown
Second strategy: `boll_stochrsi_v1` (Bollinger trend zone + squeeze→expansion
+ StochRSI cross, middle-band exit, ATR stop) — runs as a shadow until you
switch `ActiveStrategy`.
```

- [ ] **Step 2: Full fast pytest** from `service/` (`.venv/bin/python -m pytest -q`) — must stay green (nothing service-side changed; this is the regression gate).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: boll_stochrsi_v1 usage"
```

- [ ] **Step 4: Extend the user's manual MQL5 checklist** (report at the end): compile in MetaEditor (0 errors); spot-check %K/%D against TradingView Stoch RSI (14,14,3,3) on the same bars; Strategy Tester run of `boll_stochrsi_v1`; confirm shadow rows with `strategy_id='boll_stochrsi_v1'` appear in the db while halftrend stays active.
