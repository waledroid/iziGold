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
   datetime m_lastUpcoming;     // 0 = never queried (UpcomingJson cache)
   datetime m_upTimes[];        // cached upcoming event times (next 24 h)
   string   m_upNames[];        // ...and their JSON-escaped names
   string   m_upcomingJson;

   // Minimal JSON string escape for calendar event names (quotes and
   // backslashes; names are plain ASCII in practice).
   string JsonEscape(string s)
     {
      StringReplace(s, "\\", "\\\\");
      StringReplace(s, "\"", "\\\"");
      return s;
     }

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
      // CalendarValueHistory returns an INT: the number of values on
      // success, -1 on failure (NOT a bool — see docs). Only -1 is a real
      // failure worth the throttled warn; zero values in a ±blackout window
      // is normal quiet tape, answered silently as "no blackout".
      int total = CalendarValueHistory(values, now - m_blackoutSec, now + m_blackoutSec);
      if(total < 0)
        {
         WarnThrottled(StringFormat(
            "NewsGuard: calendar query failed (err %d) — failing open, guard inactive",
            GetLastError()));
         return;
        }
      if(total == 0) return;   // empty window — a definitive answer, not missing data
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
      m_lastUpcoming = 0;
      m_upcomingJson = "";
      ArrayResize(m_upTimes, 0);
      ArrayResize(m_upNames, 0);
     }

   // Upcoming high-importance USD events over the next 24 h as a JSON array
   // of {"in_s":<relative seconds>,"name":"..."} for the heartbeat, so the
   // service can render /news and pre-blackout notices from the SAME feed
   // the guard blocks on. Relative seconds on purpose: the MT5 server clock
   // and the service clock disagree by hours; "seconds from now" is immune.
   // Own 10-minute cache (Refresh() only covers the ±blackout window).
   // Fail-open like everything here: any calendar failure returns "[]".
   string UpcomingJson()
     {
      if(!m_enabled) return "[]";
      datetime now = TimeCurrent();
      // Event TIMES are cached for 10 min; in_s is recomputed on every
      // call from the cached times, so countdowns never go stale.
      if(m_lastUpcoming == 0 || now - m_lastUpcoming >= 600)
        {
         m_lastUpcoming = now;
         ArrayResize(m_upTimes, 0);
         ArrayResize(m_upNames, 0);
         MqlCalendarValue values[];
         ResetLastError();
         int total = CalendarValueHistory(values, now, now + 24 * 3600);
         if(total > 0)
            for(int i = 0; i < total && ArraySize(m_upTimes) < 8; i++)
              {
               MqlCalendarEvent ev;
               if(!CalendarEventById(values[i].event_id, ev)) continue;
               if(ev.importance != CALENDAR_IMPORTANCE_HIGH) continue;
               MqlCalendarCountry country;
               if(!CalendarCountryById(ev.country_id, country)) continue;
               if(country.currency != "USD") continue;
               int n = ArraySize(m_upTimes);
               ArrayResize(m_upTimes, n + 1);
               ArrayResize(m_upNames, n + 1);
               m_upTimes[n] = values[i].time;
               m_upNames[n] = JsonEscape(ev.name);
              }
        }
      string json = "[";
      for(int i = 0; i < ArraySize(m_upTimes); i++)
        {
         long in_s = (long)(m_upTimes[i] - now);
         if(in_s <= 0) continue;
         if(StringLen(json) > 1) json += ",";
         json += "{\"in_s\":" + (string)in_s + ",\"name\":\"" + m_upNames[i] + "\"}";
        }
      json += "]";
      m_upcomingJson = json;
      return json;
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
