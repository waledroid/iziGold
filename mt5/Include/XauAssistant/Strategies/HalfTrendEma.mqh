// HalfTrendEma.mqh — Half Trend (amplitude 4) + EMA 55 dual confirmation.
// Adapted from the Crypto9ite TradingView strategy for XAUUSD M15.
// Entry: Half Trend color + ConfirmCloses consecutive closes beyond the EMA,
// fired once per Half Trend flip. Stop: wick extreme since the flip.
// Paints the trading 55 EMA (green) plus context EMAs 9/21/200 — the context
// lines are display-only and never touch signal logic.
#ifndef XAU_STRAT_HALFTREND_EMA_MQH
#define XAU_STRAT_HALFTREND_EMA_MQH
#include <XauAssistant/Strategy.mqh>

class CHalfTrendEmaStrategy : public CStrategy
  {
private:
   int      m_amplitude;
   int      m_emaLen;
   int      m_confirm;
   int      m_emaHandle;
   int      m_warmupBars;

   int      m_trend;         // 0 = blue/up, 1 = red/down, -1 = not yet seeded
   int      m_nextTrend;
   double   m_maxLowPrice;
   double   m_minHighPrice;
   double   m_extreme;       // lowest low since flip to blue / highest high since flip to red
   int      m_consecAbove;
   int      m_consecBelow;
   bool     m_fired;         // one entry per Half Trend flip
   datetime m_lastProcessed;

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
      datetime old = t2 - 500 * PeriodSeconds(PERIOD_CURRENT);
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
      datetime bt = iTime(_Symbol, PERIOD_CURRENT, shift);
      double ht = (m_trend == 0) ? m_maxLowPrice : m_minHighPrice;
      color htClr = (m_trend == 0) ? clrDodgerBlue : clrOrangeRed;
      DrawSeg("xau_ht_", m_prevPaintBar, m_prevHt, bt, ht, htClr, 2);
      if(emaVal > 0)
         DrawSeg("xau_ema_", m_prevPaintBar, m_prevEma, bt, emaVal, clrLimeGreen, 2);
      PaintContextEma(m_ema9Handle,   "xau_ema9_",   clrOrange, shift, bt, m_prevEma9);
      PaintContextEma(m_ema21Handle,  "xau_ema21_",  clrRed,    shift, bt, m_prevEma21);
      PaintContextEma(m_ema200Handle, "xau_ema200_", clrWhite,  shift, bt, m_prevEma200);
      m_prevPaintBar = bt; m_prevHt = ht;
      if(emaVal > 0) m_prevEma = emaVal;
     }

   void ProcessClosedBar(int shift)
     {
      double hi[], lo[], cl[];
      if(CopyHigh(_Symbol, PERIOD_CURRENT, shift, m_amplitude, hi) != m_amplitude) return;
      if(CopyLow(_Symbol, PERIOD_CURRENT, shift, m_amplitude, lo)  != m_amplitude) return;
      if(CopyClose(_Symbol, PERIOD_CURRENT, shift, 1, cl) != 1) return;
      double highPrice = hi[ArrayMaximum(hi)];
      double lowPrice  = lo[ArrayMinimum(lo)];
      double highma = 0, lowma = 0;
      for(int i = 0; i < m_amplitude; i++) { highma += hi[i]; lowma += lo[i]; }
      highma /= m_amplitude;
      lowma  /= m_amplitude;
      double close    = cl[0];
      double prevLow  = iLow(_Symbol, PERIOD_CURRENT, shift + 1);
      double prevHigh = iHigh(_Symbol, PERIOD_CURRENT, shift + 1);

      if(m_trend < 0)  // seed on the very first processed bar
        {
         m_trend = 0; m_nextTrend = 0;
         m_maxLowPrice = prevLow; m_minHighPrice = prevHigh;
         m_extreme = lowPrice;
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

      double barLow  = iLow(_Symbol, PERIOD_CURRENT, shift);
      double barHigh = iHigh(_Symbol, PERIOD_CURRENT, shift);
      if(m_trend != prevTrend)
        {
         m_fired = false;   // a flip re-arms the once-per-trend entry
         m_extreme = (m_trend == 0) ? barLow : barHigh;
         m_consecAbove = 0; m_consecBelow = 0;  // restart EMA count after flip
        }
      else
         m_extreme = (m_trend == 0) ? MathMin(m_extreme, barLow)
                                    : MathMax(m_extreme, barHigh);

      double emaBuf[];
      bool haveEma = (CopyBuffer(m_emaHandle, 0, shift, 1, emaBuf) == 1);
      if(haveEma)
        {
         if(close > emaBuf[0])      { m_consecAbove++; m_consecBelow = 0; }
         else if(close < emaBuf[0]) { m_consecBelow++; m_consecAbove = 0; }
        }
      PaintBar(shift, haveEma ? emaBuf[0] : 0);
     }

public:
   CHalfTrendEmaStrategy(int amplitude, int emaLen, int confirmCloses)
      : m_amplitude(amplitude), m_emaLen(emaLen), m_confirm(confirmCloses),
        m_warmupBars(600), m_trend(-1), m_nextTrend(0),
        m_maxLowPrice(0), m_minHighPrice(0), m_extreme(0),
        m_consecAbove(0), m_consecBelow(0), m_fired(false), m_lastProcessed(0),
        m_prevPaintBar(0), m_prevHt(0), m_prevEma(0),
        m_prevEma9(0), m_prevEma21(0), m_prevEma200(0)
     {
      m_emaHandle    = iMA(_Symbol, PERIOD_CURRENT, m_emaLen, 0, MODE_EMA, PRICE_CLOSE);
      m_ema9Handle   = iMA(_Symbol, PERIOD_CURRENT, 9,   0, MODE_EMA, PRICE_CLOSE);
      m_ema21Handle  = iMA(_Symbol, PERIOD_CURRENT, 21,  0, MODE_EMA, PRICE_CLOSE);
      m_ema200Handle = iMA(_Symbol, PERIOD_CURRENT, 200, 0, MODE_EMA, PRICE_CLOSE);
     }

   virtual string Id() { return "halftrend_ema_v1"; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, PERIOD_CURRENT, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      if(m_lastProcessed == 0)
        {
         int avail = Bars(_Symbol, PERIOD_CURRENT) - m_amplitude - 2;
         int from = MathMin(m_warmupBars, MathMax(avail, 1));
         for(int s = from; s >= 1; s--) ProcessClosedBar(s);   // oldest -> newest

         // suppress stale entry: if this trend already confirmed during warm-up,
         // the real entry bar is long past — wait for the next flip
         if((m_trend == 0 && m_consecAbove >= m_confirm) ||
            (m_trend == 1 && m_consecBelow >= m_confirm))
            m_fired = true;
        }
      else
         ProcessClosedBar(1);
      m_lastProcessed = closed;

      if(!m_fired)
        {
         if(m_trend == 0 && m_consecAbove >= m_confirm)
           { m_fired = true; return SIGNAL_BUY; }
         if(m_trend == 1 && m_consecBelow >= m_confirm)
           { m_fired = true; return SIGNAL_SELL; }
        }
      return SIGNAL_NONE;
     }

   virtual bool ConditionStillTrue(ENUM_SIGNAL dir)
     {
      double emaBuf[];
      if(CopyBuffer(m_emaHandle, 0, 1, 1, emaBuf) != 1) return false;
      double close = iClose(_Symbol, PERIOD_CURRENT, 1);
      if(dir == SIGNAL_BUY)  return m_trend == 0 && close > emaBuf[0];
      if(dir == SIGNAL_SELL) return m_trend == 1 && close < emaBuf[0];
      return false;
     }

   virtual double StopPrice(ENUM_SIGNAL dir)
     {
      if(dir == SIGNAL_BUY  && m_trend == 0) return m_extreme;
      if(dir == SIGNAL_SELL && m_trend == 1) return m_extreme;
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
