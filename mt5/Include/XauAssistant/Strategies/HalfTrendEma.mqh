// HalfTrendEma.mqh — Half Trend (amplitude 4) + EMA 55 dual confirmation.
// Adapted from the Crypto9ite TradingView strategy for XAUUSD M15.
// Entry (STRICT WINDOW, 2026-08-17): Half Trend arrow bar, then ConfirmCloses
// waiting bar(s); the NEXT bar is the entry bar and it must OPEN on the
// trend's side of the EMA (== the last waiting bar CLOSED there). Decided
// exactly once; a miss kills the signal until the next flip — a later drift
// across the EMA never fires. Fired once per flip. Stop: wick extreme since
// the flip.
// Paints the trading 55 EMA (green) plus context EMAs 9/21/200 — the context
// lines are display-only and never touch signal logic.
#ifndef XAU_STRAT_HALFTREND_EMA_MQH
#define XAU_STRAT_HALFTREND_EMA_MQH
#include <XauAssistant/Strategy.mqh>

class CHalfTrendEmaStrategy : public CStrategy
  {
private:
   string   m_id;             // registry id -- distinct per instance so two
                               // HalfTrend lanes (e.g. M5 + M15) don't collide
                               // and SQLite rows stay tagged correctly
   ENUM_TIMEFRAMES m_tf;
   int      m_amplitude;
   int      m_emaLen;
   int      m_confirm;
   double   m_confirmClearAtr;  // confirm close must clear the EMA by this
                                // x ATR(14); 0 = plain side test (M5 lane)
   int      m_emaHandle;
   int      m_warmupBars;
   double   m_stopBufferAtr;
   int      m_atrHandle;

   int      m_trend;         // 0 = blue/up, 1 = red/down, -1 = not yet seeded
   int      m_nextTrend;
   double   m_maxLowPrice;
   double   m_minHighPrice;
   double   m_extreme;       // lowest low since flip to blue / highest high since flip to red
   int      m_consecAbove;
   int      m_consecBelow;
   int      m_barsSinceFlip; // 0 = the flip (arrow) bar itself
   bool     m_signalDead;    // strict window missed -> ignore until next flip
   bool     m_fired;         // one entry per Half Trend flip
   datetime m_lastProcessed;

   bool     m_catchupEnabled;
   int      m_catchupMaxAge;
   double   m_catchupMaxChaseAtr;
   int      m_confirmShift;    // shift where the CURRENT trend's entry first
   double   m_confirmClose;    // confirmed during processing; 0 = none yet
   datetime m_confirmTime;     // bar time of the confirm bar; 0 = none yet

   // Higher-timeframe agreement (owner request 2026-08-20, after the M5
   // replay showed the worst chop quarter losing -$4,256): an M5 entry is
   // refused unless the HIGHER timeframe agrees -- BUY needs price above the
   // HTF EMA, SELL below it. Measured over 516 days it cut that quarter's
   // loss by 59% (-4,255.64 -> -1,723.72) and was near-neutral elsewhere.
   bool     m_htfConfirm;
   ENUM_TIMEFRAMES m_htfTf;
   int      m_htfEmaLen;
   int      m_htfEmaHandle;
   // Price must CLEAR the HTF EMA by this x ATR(14), not merely sit on the
   // right side. Autopsy 2026-08-20: two losing sells passed the side-only
   // test by $0.46 and $1.85 -- in chop the M15 EMA sits where price is.
   double   m_htfBufferAtr;
   // The buffer applies ONLY in chop (owner 2026-08-20). efficiency =
   // |net move| / total path over m_chopBars closed bars; 1.0 = a straight
   // line, under ~0.10 is textbook chop. Above m_chopEffMax the HTF test
   // degrades to side-only, so trends are not filtered.
   int      m_lastHtfAgree;   // 1 agreed / 0 refused / -1 not evaluated
   // Runtime override from the service (/agree), which WINS over the EA
   // input: "" = follow the input, "off" = check and report but never block,
   // "M15"/"M30"/"H1" = enforce on that timeframe. Pushed every heartbeat so
   // the module can be toggled from Telegram without a recompile.
   string   m_htfOverride;
   bool     m_chopOnly;
   int      m_chopBars;
   double   m_chopEffMax;

   // EMA-200 (OWN-timeframe) agreement (owner request 2026-08-22, after HTF
   // was dropped from the M15 lane entirely): BUY needs price ABOVE the
   // strategy's own EMA200, SELL below. Unlike HtfAgrees/HtfEnforced above,
   // there is no higher timeframe and no chop-only buffer -- this is the
   // plain side test, read at the SAME bar/close the strict-window
   // confirmation used, so both verdicts describe the same instant. Same
   // verdict/enforcement split, same /agree-style runtime override, reused
   // on BOTH the M5 and M15 lanes (M5's own behaviour/handle is otherwise
   // untouched -- m_ema200Handle already existed for painting).
   bool     m_ema200Confirm;   // EA input: default false (report only)
   int      m_lastEma200Agree; // 1 agreed / 0 refused / -1 not evaluated
   double   m_confirmEma200;   // EMA-200 value at the confirm bar; 0 = none yet
   string   m_ema200Override;  // "" follow EA input, "off" report-only, "on" enforce

   int      m_ema9Handle;
   int      m_ema21Handle;
   int      m_ema200Handle;

   datetime m_prevPaintBar;
   double   m_prevHt;
   double   m_prevEma;
   double   m_prevEma9;
   double   m_prevEma21;
   double   m_prevEma200;

   void DrawSeg(string prefix, datetime t1, double v1, datetime t2, double v2,
                color clr, int width)
     {
      if(t1 == 0 || v1 == 0 || v2 == 0) return;
      string name = prefix + (string)(long)t2;
      if(ObjectFind(0, name) >= 0) return;
      if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, v1, t2, v2)) return;
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      // rolling window: drop the segment that just left the 500-bar window
      datetime old = t2 - 500 * PeriodSeconds(m_tf);
      ObjectDelete(0, prefix + (string)(long)old);
     }

   void PaintContextEma(int handle, string prefix, color clr, int shift,
                        datetime bt, double &prevVal)
     {
      double buf[];
      if(CopyBuffer(handle, 0, shift, 1, buf) != 1) return;
      if(buf[0] <= 0 || buf[0] == EMPTY_VALUE) return;   // MA not yet formed
      DrawSeg(prefix, m_prevPaintBar, prevVal, bt, buf[0], clr, 1);
      prevVal = buf[0];
     }

   void PaintBar(int shift, double emaVal)
     {
      if(!m_paint) return;
      datetime bt = iTime(_Symbol, m_tf, shift);
      double ht = (m_trend == 0) ? m_maxLowPrice : m_minHighPrice;
      color htClr = (m_trend == 0) ? clrDodgerBlue : clrOrangeRed;
      DrawSeg("xau_ht_", m_prevPaintBar, m_prevHt, bt, ht, htClr, 2);
      if(emaVal > 0)
         DrawSeg("xau_ema_", m_prevPaintBar, m_prevEma, bt, emaVal, clrLimeGreen, 2);
      // 9/21 are context-only: near-background tints (dark theme) so they
      // read on inspection without cluttering the chart
      PaintContextEma(m_ema9Handle,   "xau_ema9_",   C'82,72,48', shift, bt, m_prevEma9);
      PaintContextEma(m_ema21Handle,  "xau_ema21_",  C'82,52,52', shift, bt, m_prevEma21);
      PaintContextEma(m_ema200Handle, "xau_ema200_", clrWhite,  shift, bt, m_prevEma200);
      m_prevPaintBar = bt; m_prevHt = ht;
      if(emaVal > 0) m_prevEma = emaVal;
     }

   void ProcessClosedBar(int shift)
     {
      double hi[], lo[], cl[];
      if(CopyHigh(_Symbol, m_tf, shift, m_amplitude, hi) != m_amplitude) return;
      if(CopyLow(_Symbol, m_tf, shift, m_amplitude, lo)  != m_amplitude) return;
      if(CopyClose(_Symbol, m_tf, shift, 1, cl) != 1) return;
      double highPrice = hi[ArrayMaximum(hi)];
      double lowPrice  = lo[ArrayMinimum(lo)];
      double highma = 0, lowma = 0;
      for(int i = 0; i < m_amplitude; i++) { highma += hi[i]; lowma += lo[i]; }
      highma /= m_amplitude;
      lowma  /= m_amplitude;
      double close    = cl[0];
      double prevLow  = iLow(_Symbol, m_tf, shift + 1);
      double prevHigh = iHigh(_Symbol, m_tf, shift + 1);

      if(m_trend < 0)  // seed on the very first processed bar
        {
         m_trend = 0; m_nextTrend = 0;
         m_maxLowPrice = prevLow; m_minHighPrice = prevHigh;
         m_extreme = lowPrice;
         m_signalDead = true;   // a synthetic seed is not an arrow: never a confirm
        }

      int prevTrend = m_trend;
      if(m_nextTrend == 1)
        {
         m_maxLowPrice = MathMax(lowPrice, m_maxLowPrice);
         if(highma < m_maxLowPrice && close < prevLow)
           { m_trend = 1; m_nextTrend = 0; m_minHighPrice = highPrice; }
        }
      else
        {
         m_minHighPrice = MathMin(highPrice, m_minHighPrice);
         if(lowma > m_minHighPrice && close > prevHigh)
           { m_trend = 0; m_nextTrend = 1; m_maxLowPrice = lowPrice; }
        }

      double barLow  = iLow(_Symbol, m_tf, shift);
      double barHigh = iHigh(_Symbol, m_tf, shift);
      if(m_trend != prevTrend)
        {
         m_fired = false;   // a flip re-arms the once-per-trend entry
         m_signalDead = false;
         m_barsSinceFlip = 0;                   // this IS the arrow bar
         m_extreme = (m_trend == 0) ? barLow : barHigh;
         m_consecAbove = 0; m_consecBelow = 0;  // restart EMA count after flip
         m_confirmShift = 0; m_confirmClose = 0; m_confirmTime = 0; m_confirmEma200 = 0;
         if(m_lastProcessed != 0)   // live bar, not warm-up backfill
            Print(m_id + ": HalfTrend flip to ",
                  m_trend == 0 ? "UP (blue)" : "DOWN (red)",
                  " — strict window armed: the entry bar (", m_confirm,
                  " waiting bar(s) after the arrow) must OPEN ",
                  m_trend == 0 ? "above" : "below", " EMA", m_emaLen,
                  " (else the signal is ignored until the next flip)");
        }
      else
        {
         m_extreme = (m_trend == 0) ? MathMin(m_extreme, barLow)
                                    : MathMax(m_extreme, barHigh);
         m_barsSinceFlip++;
        }

      double emaBuf[];
      bool haveEma = (CopyBuffer(m_emaHandle, 0, shift, 1, emaBuf) == 1);
      if(haveEma)
        {
         if(close > emaBuf[0])      { m_consecAbove++; m_consecBelow = 0; }
         else if(close < emaBuf[0]) { m_consecBelow++; m_consecAbove = 0; }
         // STRICT 3-BAR WINDOW (owner's rule, 2026-08-17): the arrow bar,
         // then m_confirm waiting bar(s); the entry bar is the NEXT one and
         // it must OPEN on the trend's side of the EMA — i.e. the LAST
         // waiting bar must CLOSE there (open == previous close). This is
         // decided exactly ONCE, when that waiting bar closes. Pass ->
         // confirm recorded (entry fires on this closed bar = the entry
         // bar's open). Fail -> the signal is DEAD until the next flip; a
         // later drift across the EMA never revives it. m_consec* stay only
         // as diagnostics/paint inputs.
         if(!m_signalDead && m_confirmShift == 0 && m_barsSinceFlip == m_confirm)
           {
            // Confirm-quality clearance (owner 2026-09-01): the decision
            // close must clear the EMA by K x ATR(14), not merely sit on
            // the right side — a confirm "hanging on the line" dies like a
            // wrong-side one. 0 = off (M5 lane keeps the plain side test).
            // Sweep (17-mo, M15 FIXED, EMA-50, 1.75 ATR): K=0.3 -> net
            // -0.9%, max dd -10%, dd lower in BOTH halves, win% 40.4->41.3.
            double clearMargin = 0.0;
            if(m_confirmClearAtr > 0)
              {
               double atrBuf[];
               if(CopyBuffer(m_atrHandle, 0, shift, 1, atrBuf) == 1)
                  clearMargin = m_confirmClearAtr * atrBuf[0];
              }
            bool ok = (m_trend == 0) ? (close > emaBuf[0] + clearMargin)
                                     : (close < emaBuf[0] - clearMargin);
            if(ok)
              {
               m_confirmShift = shift; m_confirmClose = close; m_confirmTime = iTime(_Symbol, m_tf, shift);
               // Same instant as m_confirmClose -- read the OWN-timeframe
               // EMA200 (already built for painting) at this same shift, not
               // "now", so a later Evaluate() call (or a catch-up entry
               // firing bars after the confirm) still judges the bar that
               // actually decided, not whatever the EMA reads today.
               double e200Buf[];
               m_confirmEma200 = (CopyBuffer(m_ema200Handle, 0, shift, 1, e200Buf) == 1) ? e200Buf[0] : 0.0;
              }
            else
              {
               m_signalDead = true;
               if(m_lastProcessed != 0)
                  Print(m_id + ": ", m_trend == 0 ? "BUY" : "SELL",
                        " arrow — entry bar would open ",
                        clearMargin > 0 ? "without clearing" : "on the wrong side of",
                        " EMA", m_emaLen,
                        clearMargin > 0 ? StringFormat(" by %.2f (%.1f x ATR)", clearMargin, m_confirmClearAtr) : "",
                        " (", DoubleToString(close, 2), " vs ",
                        DoubleToString(emaBuf[0], 2), "): signal ignored until next flip");
              }
           }
        }
      PaintBar(shift, haveEma ? emaBuf[0] : 0);
     }

   // Per-symbol MT5 global holding the bar time of the last bar this
   // strategy processed while the EA was actually running (LIVE branch of
   // Evaluate only — never written during warm-up backfill). Same
   // "XAU_<name>_<login>_<symbol>" shape RiskManager uses.
   string LastLiveKey()
     {
      return "XAU_LASTLIVE_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol;
     }

   // Missed-entry catch-up guards, evaluated on CURRENT data. True = the
   // outage-spanning signal is still tradeable now. Every rejection prints
   // its reason once (this runs once, at warm-up).
   bool CatchupOk()
     {
      if(!m_catchupEnabled)
        { Print(m_id + ": catch-up disabled — stale entry suppressed"); return false; }
      if(m_confirmShift == 0 || m_confirmClose <= 0)
        { Print(m_id + ": catch-up — no confirm bar recorded, suppressed"); return false; }
      string liveKey = LastLiveKey();
      if(!GlobalVariableCheck(liveKey))
        { Print(m_id + ": catch-up — no live-bar watermark yet, suppressed (first run)"); return false; }
      datetime lastLive = (datetime)(long)GlobalVariableGet(liveKey);
      if(m_confirmTime <= lastLive)
        { Print(m_id + ": catch-up rejected — confirm happened while EA was live, not a missed signal"); return false; }
      int ageBars = m_confirmShift - 1;   // bars between confirm bar and newest closed bar
      if(ageBars > m_catchupMaxAge)
        {
         PrintFormat("%s: catch-up rejected — signal %d bars old (max %d)",
                     m_id, ageBars, m_catchupMaxAge);
         return false;
        }
      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, 1, 1, emaBuf) != 1 || emaBuf[0] <= 0)
        { Print(m_id + ": catch-up — EMA unavailable, suppressed"); return false; }
      double atrBuf[];
      if(CopyBuffer(m_atrHandle, 0, 1, 1, atrBuf) != 1 || atrBuf[0] <= 0)
        { Print(m_id + ": catch-up — ATR unavailable, suppressed"); return false; }
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(m_trend == 1)   // SELL thesis
        {
         if(bid >= emaBuf[0])
           { Print(m_id + ": catch-up rejected — price back above EMA, thesis gone"); return false; }
         if(m_confirmClose - bid > m_catchupMaxChaseAtr * atrBuf[0])
           { Print(m_id + ": catch-up rejected — price already ran, not chasing"); return false; }
        }
      else               // BUY thesis
        {
         if(bid <= emaBuf[0])
           { Print(m_id + ": catch-up rejected — price back below EMA, thesis gone"); return false; }
         if(bid - m_confirmClose > m_catchupMaxChaseAtr * atrBuf[0])
           { Print(m_id + ": catch-up rejected — price already ran, not chasing"); return false; }
        }
      PrintFormat("%s: catch-up entry — %s confirmed %d bars ago during downtime, guards passed",
                  m_id, m_trend == 1 ? "SELL" : "BUY", ageBars);
      return true;
     }

public:
   CHalfTrendEmaStrategy(string id, ENUM_TIMEFRAMES tf, int amplitude, int emaLen, int confirmCloses, double stopBufferAtr,
                         bool catchupEnabled, int catchupMaxAgeBars, double catchupMaxChaseAtr,
                         bool htfConfirm, ENUM_TIMEFRAMES htfTf, int htfEmaLen,
                         double htfBufferAtr, bool chopOnly, int chopBars,
                         double chopEffMax, bool ema200Confirm,
                         double confirmClearAtr = 0.0)
      : m_id(id), m_amplitude(amplitude), m_emaLen(emaLen), m_confirm(confirmCloses),
        m_confirmClearAtr(confirmClearAtr),
        m_warmupBars(600), m_stopBufferAtr(stopBufferAtr), m_trend(-1), m_nextTrend(0),
        m_maxLowPrice(0), m_minHighPrice(0), m_extreme(0),
        m_consecAbove(0), m_consecBelow(0), m_barsSinceFlip(0), m_signalDead(false), m_fired(false), m_lastProcessed(0),
        m_catchupEnabled(catchupEnabled), m_catchupMaxAge(catchupMaxAgeBars),
        m_catchupMaxChaseAtr(catchupMaxChaseAtr), m_confirmShift(0), m_confirmClose(0), m_confirmTime(0),
        m_htfConfirm(htfConfirm), m_htfEmaLen(htfEmaLen), m_htfEmaHandle(INVALID_HANDLE),
        m_htfBufferAtr(htfBufferAtr), m_lastHtfAgree(-1),
        m_htfOverride(""), m_chopOnly(chopOnly),
        m_chopBars(chopBars), m_chopEffMax(chopEffMax),
        m_ema200Confirm(ema200Confirm), m_lastEma200Agree(-1),
        m_confirmEma200(0), m_ema200Override(""),
        m_prevPaintBar(0), m_prevHt(0), m_prevEma(0),
        m_prevEma9(0), m_prevEma21(0), m_prevEma200(0)
     {
      m_tf = tf;   // must be set before the handle-creating calls below
      m_emaHandle    = iMA(_Symbol, m_tf, m_emaLen, 0, MODE_EMA, PRICE_CLOSE);
      m_ema9Handle   = iMA(_Symbol, m_tf, 9,   0, MODE_EMA, PRICE_CLOSE);
      m_ema21Handle  = iMA(_Symbol, m_tf, 21,  0, MODE_EMA, PRICE_CLOSE);
      m_ema200Handle = iMA(_Symbol, m_tf, 200, 0, MODE_EMA, PRICE_CLOSE);
      m_atrHandle    = iATR(_Symbol, m_tf, 14);
      m_htfTf        = htfTf;
      if(m_htfConfirm)
         m_htfEmaHandle = iMA(_Symbol, m_htfTf, m_htfEmaLen, 0, MODE_EMA, PRICE_CLOSE);
     }

   // True when the higher timeframe agrees with `dir` (or when the check is
   // off / unavailable). Reads shift 1 -- the last COMPLETED HTF bar -- so a
   // still-forming M15 candle can never flip the answer mid-bar, which is
   // also exactly what the replay models.
   // FAIL-OPEN by house rule: a missing handle or a failed CopyBuffer lets
   // the strategy's own signal stand rather than silently suppressing trades.
   // |net move| / total path over the last m_chopBars CLOSED bars.
   // Returns 0.0 (= "choppy", buffer stays on) if the data cannot be read.
   double ChopEfficiency()
     {
      if(m_chopBars < 2) return 0.0;
      double cl[];
      if(CopyClose(_Symbol, m_tf, 1, m_chopBars + 1, cl) != m_chopBars + 1)
         return 0.0;
      double path = 0.0;
      for(int q = 1; q <= m_chopBars; q++)
         path += MathAbs(cl[q] - cl[q - 1]);
      if(path <= 0.0) return 0.0;
      return MathAbs(cl[m_chopBars] - cl[0]) / path;
     }

   // The higher-timeframe verdict behind the CURRENT signal, for the trade
   // log: 1 agreed, 0 refused, -1 not evaluated yet.
   virtual int LastHtfAgree() const override { return m_lastHtfAgree; }

   // Is the tape choppy enough for the M15 verdict to BLOCK an entry?
   // ChopEfficiency() returns 0.0 (= choppy) when the data cannot be read,
   // so a failed read never silently opens the gate.
   bool HtfEnforced()
     {
      // The service's setting wins: "off" means the verdict is still
      // computed and reported, it simply may not touch the trade decision.
      if(m_htfOverride == "off") return false;
      if(m_htfOverride == "" && !m_htfConfirm) return false;
      if(!m_chopOnly)   return true;          // gate all day
      return ChopEfficiency() <= m_chopEffMax;
     }

   // Called from the heartbeat. An empty string leaves the EA input in
   // charge, so a service that never sends the field changes nothing.
   void SetHtfOverride(string v)
     {
      if(v == "off" || v == "M15" || v == "M30" || v == "H1" || v == "")
        {
         if(v != m_htfOverride && v != "")
            Print(m_id + ": higher-timeframe agreement -> ",
                  v == "off" ? "CHECK ONLY (will not block)" : "ENFORCING on " + v);
         m_htfOverride = v;
         // Enforcing on a different timeframe than the handle was built for
         // means rebuilding it; the verdict must come from the timeframe the
         // owner actually chose.
         if(v != "" && v != "off")
           {
            ENUM_TIMEFRAMES want = (v == "M30") ? PERIOD_M30
                                 : (v == "H1")  ? PERIOD_H1 : PERIOD_M15;
            if(want != m_htfTf)
              {
               m_htfTf = want;
               m_htfEmaHandle = iMA(_Symbol, m_htfTf, m_htfEmaLen, 0,
                                    MODE_EMA, PRICE_CLOSE);
              }
           }
        }
     }

   // Does M15 agree with `dir` at `price`? ALWAYS evaluated, in every
   // session, so the answer can be reported on the entry alert and stored on
   // the trade even when it is not allowed to block (owner 2026-08-21:
   // "always check and report ... regardless the market session").
   // The clearance buffer is a CHOP tool, so in a trend the verdict is the
   // plain side test; in chop it also requires m_htfBufferAtr x ATR(14).
   bool HtfAgrees(int dir, double price)
     {
      if(!m_htfConfirm || m_htfEmaHandle == INVALID_HANDLE) return true;
      double buf[];
      if(CopyBuffer(m_htfEmaHandle, 0, 1, 1, buf) != 1) return true;
      double pad = 0.0;
      if(m_htfBufferAtr > 0 && m_atrHandle != INVALID_HANDLE
         && HtfEnforced())
        {
         double atrBuf[];
         if(CopyBuffer(m_atrHandle, 0, 1, 1, atrBuf) == 1)
            pad = m_htfBufferAtr * atrBuf[0];
        }
      if(dir == SIGNAL_BUY)  return price > buf[0] + pad;
      if(dir == SIGNAL_SELL) return price < buf[0] - pad;
      return true;
     }

   // Does price agree with the strategy's OWN EMA200 at `dir`? ALWAYS
   // evaluated -- reported on the entry alert/trade log whether or not it is
   // allowed to block (owner 2026-08-22, same "always check and report"
   // rule as HtfAgrees above). Reads m_confirmEma200/m_confirmClose -- the
   // SAME instant the strict-window confirmation decided on -- not a fresh
   // "now" read, so a catch-up entry firing bars later still judges the bar
   // that actually confirmed. FAIL-OPEN: no confirm EMA200 recorded yet
   // (warm-up too short) lets the strategy's own signal stand.
   bool Ema200Agrees(int dir)
     {
      if(m_confirmEma200 <= 0) return true;
      if(dir == SIGNAL_BUY)  return m_confirmClose > m_confirmEma200;
      if(dir == SIGNAL_SELL) return m_confirmClose < m_confirmEma200;
      return true;
     }

   // May the EMA200 verdict BLOCK an entry? Same override shape as
   // HtfEnforced -- "off"/"on" from the service (/agree) wins over the EA
   // input; "" follows the EA input, which defaults to false (report only).
   // Unlike HtfEnforced there is no chop-only gate: this check is either
   // fully on or fully off, all day, whenever it's enforcing.
   bool Ema200Enforced()
     {
      if(m_ema200Override == "off") return false;
      if(m_ema200Override == "on")  return true;
      return m_ema200Confirm;
     }

   // Called from the heartbeat, same shape as SetHtfOverride. An empty
   // string leaves the EA input in charge.
   void SetEma200Override(string v)
     {
      if(v == "off" || v == "on" || v == "")
        {
         if(v != m_ema200Override && v != "")
            Print(m_id + ": EMA-200 agreement -> ",
                  v == "off" ? "CHECK ONLY (will not block)" : "ENFORCING");
         m_ema200Override = v;
        }
     }

   virtual int LastEma200Agree() const override { return m_lastEma200Agree; }

   virtual string Id() { return m_id; }
   virtual ENUM_TIMEFRAMES TradeTf() { return m_tf; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, m_tf, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      if(m_lastProcessed == 0)
        {
         int avail = Bars(_Symbol, m_tf) - m_amplitude - 2;
         int from = MathMin(m_warmupBars, MathMax(avail, 1));
         for(int s = from; s >= 1; s--) ProcessClosedBar(s);   // oldest -> newest

         // this trend's entry already confirmed during the gap: normally a
         // stale entry (suppress, wait for the next flip) — unless the
         // catch-up guards say the thesis is still intact right now, in
         // which case m_fired stays false and the first Evaluate() below
         // emits the signal through the normal gate path.
         // "Would I have entered if I had been here from the beginning?" —
         // the warm-up replay applied the SAME strict window to the real
         // bars, so a recorded confirm (m_confirmShift != 0) means yes and a
         // dead/unrecorded signal means no. Only a yes may be caught up.
         if(m_confirmShift != 0)
           {
            if(!CatchupOk())
               m_fired = true;
           }
         else if(m_signalDead || m_barsSinceFlip >= m_confirm)
            m_fired = true;   // decided (dead) or past the window -> nothing to enter
         // else: the arrow just happened (fewer than m_confirm waiting bars seen)
         // -> the decision is still PENDING; leave m_fired false so the next
         // live bar can record a legitimate bar-3 confirm (review 2026-08-17:
         // an unconditional m_fired=true here silently missed that entry
         // after every restart landing within one bar of an arrow).
        }
      else
        {
         ProcessClosedBar(1);
         GlobalVariableSet(LastLiveKey(), (double)(long)closed);
        }
      m_lastProcessed = closed;

      // Emit ONLY on a strict-window confirm (recorded by ProcessClosedBar
      // exactly once per flip, on the last waiting bar's close). A late
      // drift across the EMA can never fire: it never records a confirm.
      if(!m_fired && m_confirmShift != 0)
        {
         m_fired = true;
         int wanted = (m_trend == 0) ? SIGNAL_BUY : SIGNAL_SELL;
         // Verdict first and ALWAYS -- it is reported whether or not it is
         // allowed to act. Enforcement is separate and chop-only.
         bool htfOk = HtfAgrees(wanted, m_confirmClose);
         m_lastHtfAgree = htfOk ? 1 : 0;
         // EMA200 (own-timeframe) verdict -- same "always evaluated" rule,
         // computed alongside the HTF verdict so both describe this same
         // confirm event even if HTF ends up refusing the entry below.
         bool e200Ok = Ema200Agrees(wanted);
         m_lastEma200Agree = e200Ok ? 1 : 0;
         if(!htfOk && !HtfEnforced())
            Print(m_id + ": ", (wanted == SIGNAL_BUY ? "BUY" : "SELL"),
                  " — ", EnumToString(m_htfTf), " DISAGREES but the tape is "
                  "trending, so the check does not block; entering anyway");
         if(!e200Ok && !Ema200Enforced())
            Print(m_id + ": ", (wanted == SIGNAL_BUY ? "BUY" : "SELL"),
                  " — EMA200 DISAGREES but the check is report-only; entering anyway");
         if(!htfOk && HtfEnforced())
           {
            Print(m_id + ": ", (wanted == SIGNAL_BUY ? "BUY" : "SELL"),
                  " refused — ", EnumToString(m_htfTf), " disagrees (price ",
                  DoubleToString(m_confirmClose, 2), " on the wrong side of its EMA",
                  m_htfEmaLen, ")");
            return SIGNAL_NONE;
           }
         if(!e200Ok && Ema200Enforced())
           {
            Print(m_id + ": ", (wanted == SIGNAL_BUY ? "BUY" : "SELL"),
                  " refused — EMA200 disagrees (price ",
                  DoubleToString(m_confirmClose, 2), " on the wrong side of EMA200 ",
                  DoubleToString(m_confirmEma200, 2), ")");
            return SIGNAL_NONE;
           }
         if(m_trend == 0)
           {
            Print(m_id + ": BUY confirmed — entry bar opens above EMA",
                  m_emaLen, " (", DoubleToString(m_confirmClose, 2), ")");
            return SIGNAL_BUY;
           }
         Print(m_id + ": SELL confirmed — entry bar opens below EMA",
               m_emaLen, " (", DoubleToString(m_confirmClose, 2), ")");
         return SIGNAL_SELL;
        }
      return SIGNAL_NONE;
     }

   virtual bool ConditionStillTrue(ENUM_SIGNAL dir)
     {
      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, 1, 1, emaBuf) != 1) return false;
      double close = iClose(_Symbol, m_tf, 1);
      if(dir == SIGNAL_BUY)  return m_trend == 0 && close > emaBuf[0];
      if(dir == SIGNAL_SELL) return m_trend == 1 && close < emaBuf[0];
      return false;
     }

   // Pad the wick-extreme stop by k*ATR(14) so ordinary noise (one
   // ATR-fraction) can't snipe the exact wick; sizing is risk-based so
   // padding the stop distance does not change dollar risk per trade.
   virtual double StopPrice(ENUM_SIGNAL dir)
     {
      double atr = 0; double ab[];
      if(CopyBuffer(m_atrHandle, 0, 1, 1, ab) == 1) atr = ab[0];
      double pad = (m_stopBufferAtr > 0 && atr > 0) ? m_stopBufferAtr * atr : 0;
      if(dir == SIGNAL_BUY  && m_trend == 0) return m_extreme - pad;
      if(dir == SIGNAL_SELL && m_trend == 1) return m_extreme + pad;
      return 0.0;
     }

   virtual void ClearPaint()
     {
      ObjectsDeleteAll(0, "xau_ht_");
      ObjectsDeleteAll(0, "xau_ema_");
      ObjectsDeleteAll(0, "xau_ema9_");
      ObjectsDeleteAll(0, "xau_ema21_");
      ObjectsDeleteAll(0, "xau_ema200_");
      ChartRedraw();
     }

   // Reset the paint-chain start point whenever painting is (re)enabled, so a
   // strategy that was deactivated and later reactivated doesn't reconnect
   // its next segment back to a stale bar/price from before the gap.
   virtual void EnablePaint(bool on)
     {
      CStrategy::EnablePaint(on);
      if(on)
        {
         m_prevPaintBar = 0; m_prevHt = 0; m_prevEma = 0;
         m_prevEma9 = 0; m_prevEma21 = 0; m_prevEma200 = 0;
        }
     }
  };
#endif
