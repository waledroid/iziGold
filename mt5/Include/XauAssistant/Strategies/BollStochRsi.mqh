// BollStochRsi.mqh — Bollinger trend zone + squeeze->expansion + StochRSI cross.
// Adapted from a Binance BB+StochRSI strategy for XAUUSD M15 (spec 2026-07-31).
// Exit: close crossing the middle band against the position; stop: ATR default.
#ifndef XAU_STRAT_BOLL_STOCHRSI_MQH
#define XAU_STRAT_BOLL_STOCHRSI_MQH
#include <XauAssistant/Strategy.mqh>

class CBollStochRsiStrategy : public CStrategy
  {
private:
   ENUM_TIMEFRAMES m_tf;
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

   bool ProcessClosedBar(int shift)
     {
      m_crossUp = false;  // reset stale cross flags before any early return
      m_crossDown = false;
      double upper[], middle[], lower[], close[];
      if(CopyBuffer(m_bbHandle, 1, shift, 1, upper)  != 1) return false; // 1 = upper
      if(CopyBuffer(m_bbHandle, 0, shift, 1, middle) != 1) return false; // 0 = middle
      if(CopyBuffer(m_bbHandle, 2, shift, 1, lower)  != 1) return false; // 2 = lower
      if(CopyClose(_Symbol, m_tf, shift, 1, close) != 1) return false;
      double up = upper[0], mid = middle[0], lo = lower[0], cl = close[0];
      if(mid <= 0) return false;

      // --- bandwidth + squeeze/expansion state machine
      double bw = (up - lo) / mid;
      bool squeeze = IsSqueeze(bw);
      bool rising = (m_prevBw > 0 && bw > m_prevBw);
      if(rising) { m_risingStreak++; m_flatStreak = 0; }
      else       { m_flatStreak++;  m_risingStreak = 0; }
      if(!m_expansion)
        {
         if(squeeze && !m_armed) { m_armed = true; m_risingStreak = rising ? 1 : 0; }
         else if(squeeze) m_armed = true;
         else if(!rising) m_armed = false;   // chain broken: disarm
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
      if(CopyBuffer(m_rsiHandle, 0, shift, m_stochPeriod, rsi) != m_stochPeriod) return false;
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
      return true;
     }

   bool m_pendingExit;

public:
   CBollStochRsiStrategy(ENUM_TIMEFRAMES tf, int bbPeriod, double bbDev, int trendCloses,
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
      m_tf = tf;   // must be set before the handle-creating calls below
      m_bbHandle  = iBands(_Symbol, m_tf, m_bbPeriod, 0, m_bbDev, PRICE_CLOSE);
      m_rsiHandle = iRSI(_Symbol, m_tf, m_rsiPeriod, PRICE_CLOSE);
     }

   virtual string Id() { return "boll_stochrsi_v1"; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, m_tf, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      if(m_lastProcessed == 0)
        {
         int avail = Bars(_Symbol, m_tf) - m_stochPeriod - 2;
         int from = MathMin(m_warmupBars, MathMax(avail, 1));
         for(int s = from; s >= 1; s--) ProcessClosedBar(s);
         // suppress stale signals on attach: no entry without a fresh live cross,
         // and no exit for a virtual position that was never really signaled
         m_crossUp = false; m_crossDown = false; m_pendingExit = false;
         m_virtualDir = SIGNAL_NONE;
         m_lastProcessed = closed;
        }
      else if(ProcessClosedBar(1))
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

   // Sync hooks (AUTO-mode safety, spec Known Limitations): the virtual
   // position above only mirrors what this strategy *thinks* it signaled.
   // When the real basket goes flat for any reason other than our own
   // SIGNAL_EXIT — a risk-manager-blocked entry, a broker-side stop-loss,
   // TradeManager's own profit-target close, etc. — reset the virtual
   // state so the next bar starts clean instead of drifting out of sync.
   //
   // OnBasketClosed is direction-matched, not unconditional: on a
   // stop-and-reverse, Evaluate() already flips m_virtualDir to the NEW
   // direction before TradeManager.OnSignal runs, and that same OnSignal
   // call synchronously closes the OLD basket and reports it through this
   // hook. Resetting unconditionally there would clobber the direction
   // just set for the position about to open, killing its middle-band
   // exit. Only reset when the closed basket's direction matches (or is
   // unknown) — i.e. it's actually OUR virtual position that went flat.
   virtual void OnBasketClosed(ENUM_SIGNAL closedDir)
     {
      if(closedDir == SIGNAL_NONE || closedDir == m_virtualDir)
        { m_virtualDir = SIGNAL_NONE; m_pendingExit = false; }
     }
   virtual void OnEntryRejected(ENUM_SIGNAL dir) { m_virtualDir = SIGNAL_NONE; m_pendingExit = false; }
  };
#endif
