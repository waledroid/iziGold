#ifndef XAU_NEWSGUARD_MQH
#define XAU_NEWSGUARD_MQH

// News-event guard (spec 2026-08-09 §1): blocks NEW exposure — entries and
// pyramid adds — within ±NewsBlackoutMin of a high-importance USD economic
// calendar event. Never blocks exits/flatten (it is only consulted from
// CanEnter and the pyramid-add path).
//
// FAIL-OPEN by design: the MQL5 calendar is unavailable on some demo servers
// and can fail transiently. Any failed or empty calendar query means "not in
// blackout" — the guard must never block trading on missing data. Failures
// are reported with ONE throttled Print per hour (same shape as
// CUiApi::WarnThrottled), not per call.
//
// CACHE: the calendar is queried at most once per 60 s. The matching event
// times found in the query window are cached, and InBlackout() answers from
// that cache between refreshes. Consequence of the 60 s granularity: an
// event can enter/leave the ±blackout window up to a minute late — irrelevant
// at a 30-minute blackout radius and bar-cadence entry checks.
class CNewsGuard
  {
private:
   bool     m_enabled;
   int      m_blackoutSec;
   datetime m_lastRefresh;      // 0 = never queried
   datetime m_lastWarn;
   datetime m_eventTimes[];     // cached HIGH-importance USD event times

   void WarnThrottled(string msg)
     {
      datetime now = TimeCurrent();
      if(now - m_lastWarn < 3600) return;   // once per hour
      m_lastWarn = now;
      Print(msg);
     }

   // Re-query the calendar window at most once per 60 s. Every early return
   // below leaves the cache EMPTY (fail-open: no data -> no blackout).
   void Refresh()
     {
      datetime now = TimeCurrent();
      if(m_lastRefresh != 0 && now - m_lastRefresh < 60) return;
      m_lastRefresh = now;
      ArrayResize(m_eventTimes, 0);

      MqlCalendarValue values[];
      ResetLastError();
      if(!CalendarValueHistory(values, now - m_blackoutSec, now + m_blackoutSec))
        {
         WarnThrottled(StringFormat(
            "NewsGuard: calendar query failed (err %d) — failing open, guard inactive",
            GetLastError()));
         return;
        }
      int total = ArraySize(values);
      if(total == 0)
        {
         // No calendar values AT ALL in the window (before importance/currency
         // filtering). Usually just a quiet stretch, but on servers without
         // calendar data it is permanent — hence the (hourly-throttled) note.
         WarnThrottled("NewsGuard: calendar returned no data for the blackout window — failing open");
         return;
        }
      for(int i = 0; i < total; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev)) continue;   // fail-open per value
         if(ev.importance != CALENDAR_IMPORTANCE_HIGH) continue;
         MqlCalendarCountry country;
         if(!CalendarCountryById(ev.country_id, country)) continue; // fail-open per value
         if(country.currency != "USD") continue;
         int n = ArraySize(m_eventTimes);
         ArrayResize(m_eventTimes, n + 1);
         m_eventTimes[n] = values[i].time;
        }
     }

public:
   void Init(bool enabled, int blackoutMin)
     {
      m_enabled = enabled;
      m_blackoutSec = blackoutMin * 60;
      m_lastRefresh = 0;
      m_lastWarn = 0;
      ArrayResize(m_eventTimes, 0);
     }

   // True when a cached high-importance USD event sits within ±blackout of
   // now. Cheap between refreshes: pure array scan over (at most a handful
   // of) cached times.
   bool InBlackout()
     {
      if(!m_enabled || m_blackoutSec <= 0) return false;
      Refresh();
      datetime now = TimeCurrent();
      for(int i = 0; i < ArraySize(m_eventTimes); i++)
         if(m_eventTimes[i] >= now - m_blackoutSec &&
            m_eventTimes[i] <= now + m_blackoutSec)
            return true;
      return false;
     }
  };
#endif
