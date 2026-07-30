// HalfTrendEma.mqh — Half Trend (amplitude 4) + EMA 55 dual confirmation.
// Adapted from the Crypto9ite TradingView strategy for XAUUSD M15.
// Entry: Half Trend color + ConfirmCloses consecutive closes beyond the EMA,
// fired once per Half Trend flip. Stop: wick extreme since the flip.
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
      if(CopyBuffer(m_emaHandle, 0, shift, 1, emaBuf) == 1)
        {
         if(close > emaBuf[0])      { m_consecAbove++; m_consecBelow = 0; }
         else if(close < emaBuf[0]) { m_consecBelow++; m_consecAbove = 0; }
        }
     }

public:
   CHalfTrendEmaStrategy(int amplitude, int emaLen, int confirmCloses)
      : m_amplitude(amplitude), m_emaLen(emaLen), m_confirm(confirmCloses),
        m_warmupBars(600), m_trend(-1), m_nextTrend(0),
        m_maxLowPrice(0), m_minHighPrice(0), m_extreme(0),
        m_consecAbove(0), m_consecBelow(0), m_fired(false), m_lastProcessed(0)
     {
      m_emaHandle = iMA(_Symbol, PERIOD_CURRENT, m_emaLen, 0, MODE_EMA, PRICE_CLOSE);
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
  };
#endif
