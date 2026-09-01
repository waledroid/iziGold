// SessionStructure.mqh — session-structure drift lanes (research 2026-08-30).
// The one gold intraday anomaly with 30+ years of academic documentation:
// gold drifts UP through Asian hours (physical buying in the East) and
// weakens through London/NY (paper selling in the West, London PM fix).
// Checked against 17 months of this broker's own M5 bars (bars_max.json,
// 101k bars, 2025-03 -> 2026-08, server clock):
//   server hours 01-03  +23.1% cumulative (the Asia drift, confirmed)
//   server hours 04-05  negative (drift dies exactly where the live
//                       trading window starts — those hours were excluded
//                       for hostile spreads, not for lack of drift)
//   PM-fix fade (16-17) t=-0.67 — NO edge in this sample (bull regime),
//                       so the short window ships DISABLED by default.
// Long-only by default: window 1 = the documented Asia drift. Window 2 and
// the short window are wired but off (-1) — window 2's best candidate
// (hour 09, t=+2.24) is data-mined, not documented, so it must earn its
// place through shadow stats before it gets a default.
// All hours are SERVER time. Entries once per window per day; the virtual
// position exits (SIGNAL_EXIT) at the window's end hour. Stop: ATR default.
#ifndef XAU_STRAT_SESSION_STRUCTURE_MQH
#define XAU_STRAT_SESSION_STRUCTURE_MQH
#include <XauAssistant/Strategy.mqh>

class CSessionStructureStrategy : public CStrategy
  {
private:
   ENUM_TIMEFRAMES m_tf;
   // window definitions, server hours; start < 0 disables the window
   int      m_w1Start, m_w1End;         // long window 1 (Asia drift)
   int      m_w2Start, m_w2End;         // long window 2 (optional, default off)
   int      m_wsStart, m_wsEnd;         // short window (PM-fix fade, default off)

   datetime m_lastProcessed;
   int      m_lastDay;                  // server day-of-year of the last bar
   bool     m_w1Fired, m_w2Fired, m_wsFired;
   ENUM_SIGNAL m_virtualDir;            // NONE/BUY/SELL — what we last signaled
   int      m_virtualEndHour;           // exit hour of the window that opened it

   bool InWindow(int hour, int start, int end)
     {
      if(start < 0) return false;
      if(start <= end) return (hour >= start && hour < end);
      return (hour >= start || hour < end);   // window wrapping midnight
     }

   // True when the window has already started (or finished) at this hour of
   // the current day — used only by the firstCall seed to declare it spent.
   bool WindowSpent(int hour, int start, int end)
     {
      if(start < 0) return false;
      if(InWindow(hour, start, end)) return true;
      if(start <= end) return (hour >= end);
      return true;   // wrapped window: some part of it is always "today"
     }

public:
   CSessionStructureStrategy(ENUM_TIMEFRAMES tf,
                             int w1Start, int w1End,
                             int w2Start, int w2End,
                             int wsStart, int wsEnd)
      : m_tf(tf),
        m_w1Start(w1Start), m_w1End(w1End),
        m_w2Start(w2Start), m_w2End(w2End),
        m_wsStart(wsStart), m_wsEnd(wsEnd),
        m_lastProcessed(0), m_lastDay(-1),
        m_w1Fired(false), m_w2Fired(false), m_wsFired(false),
        m_virtualDir(SIGNAL_NONE), m_virtualEndHour(-1) {}

   virtual string Id() { return "session_structure_v1"; }
   virtual ENUM_TIMEFRAMES TradeTf() { return m_tf; }

   virtual ENUM_SIGNAL Evaluate()
     {
      datetime closed = iTime(_Symbol, m_tf, 1);
      if(closed == 0 || closed == m_lastProcessed) return SIGNAL_NONE;
      // First call after attach: just seed the clock. No look-back catch-up —
      // a session entry taken hours late is a different trade than the
      // anomaly describes, so a missed window stays missed until tomorrow.
      bool firstCall = (m_lastProcessed == 0);
      m_lastProcessed = closed;

      MqlDateTime dt;
      TimeToStruct(closed, dt);
      if(dt.day_of_year != m_lastDay)
        {
         m_lastDay = dt.day_of_year;
         m_w1Fired = false; m_w2Fired = false; m_wsFired = false;
        }
      if(firstCall)
        {
         // Reinit tolerance (2026-09-01): a spontaneous EA reinit at 03:36
         // server (no compile, no MT5 restart — MT5 just does this) wiped
         // the in-memory fired flags and the Asia window re-fired a second
         // BUY the same day. Seeding mid-day therefore marks every window
         // that has already STARTED today as spent — same doctrine as the
         // no-catch-up rule above: a missed (or interrupted) window stays
         // missed until tomorrow.
         m_w1Fired = WindowSpent(dt.hour, m_w1Start, m_w1End);
         m_w2Fired = WindowSpent(dt.hour, m_w2Start, m_w2End);
         m_wsFired = WindowSpent(dt.hour, m_wsStart, m_wsEnd);
         return SIGNAL_NONE;
        }

      // Exit first: leaving the window closes the virtual position even on
      // the same bar a later window would open (windows must not chain a
      // stale position across sessions).
      if(m_virtualDir != SIGNAL_NONE && m_virtualEndHour >= 0
         && dt.hour >= m_virtualEndHour)
        {
         m_virtualDir = SIGNAL_NONE;
         m_virtualEndHour = -1;
         return SIGNAL_EXIT;
        }

      if(m_virtualDir == SIGNAL_NONE)
        {
         if(!m_w1Fired && InWindow(dt.hour, m_w1Start, m_w1End))
           {
            m_w1Fired = true; m_virtualDir = SIGNAL_BUY;
            m_virtualEndHour = m_w1End;
            return SIGNAL_BUY;
           }
         if(!m_w2Fired && InWindow(dt.hour, m_w2Start, m_w2End))
           {
            m_w2Fired = true; m_virtualDir = SIGNAL_BUY;
            m_virtualEndHour = m_w2End;
            return SIGNAL_BUY;
           }
         if(!m_wsFired && InWindow(dt.hour, m_wsStart, m_wsEnd))
           {
            m_wsFired = true; m_virtualDir = SIGNAL_SELL;
            m_virtualEndHour = m_wsEnd;
            return SIGNAL_SELL;
           }
        }
      return SIGNAL_NONE;
     }

   // Pyramiding gate: the entry condition is "inside the window", nothing more.
   virtual bool ConditionStillTrue(ENUM_SIGNAL dir)
     {
      return (dir == m_virtualDir && m_virtualDir != SIGNAL_NONE);
     }

   virtual double StopPrice(ENUM_SIGNAL dir) { return 0.0; }  // ATR default

   // Same sync discipline as BollStochRsi: if the real basket goes flat for
   // any reason other than our own SIGNAL_EXIT, reset the virtual position
   // (direction-matched — see the reversal note in BollStochRsi.mqh).
   virtual void OnBasketClosed(ENUM_SIGNAL closedDir)
     {
      if(closedDir == SIGNAL_NONE || closedDir == m_virtualDir)
        { m_virtualDir = SIGNAL_NONE; m_virtualEndHour = -1; }
     }
   virtual void OnEntryRejected(ENUM_SIGNAL dir)
     { m_virtualDir = SIGNAL_NONE; m_virtualEndHour = -1; }
  };
#endif
