// SrBoxes.mqh — S/R zone outlines on the MT5 chart. Pure charting, like
// TradeBoxes.mqh: reads CSrZoneSet, draws, never touches trading state,
// every path best-effort. Support = LIGHT BLUE outline, resistance =
// LIGHT YELLOW outline (owner 2026-09-05; outline-only since 2026-10-01).
// One prefix,
// "xau_sr_", so ObjectsDeleteAll clears everything; the whole set is
// rebuilt only when the engine's version changes or a new chart bar opens
// (right edge tracks the forming bar). Dead zones are DELETED, not kept as
// history — a stale S/R box is misinformation.
#ifndef XAU_SRBOXES_MQH
#define XAU_SRBOXES_MQH
#include <XauAssistant/SrZones.mqh>

#define XAU_SR_PREFIX "xau_sr_"

class CSrBoxes
  {
private:
   CSrZoneSet *m_zones;
   bool        m_show;
   bool        m_nearestOnly;
   int         m_maxSpanBars;
   double      m_maxVisualHeightPts;
   int         m_drawnVersion;
   datetime    m_rightEdge;

   color Outline(int role) const
     {
      // Unblended border colours pinned to config/chart_spec.json sr_zones --
      // enforced by test_chart_spec.py. Support #add8e6, resistance #fff59d.
      // Outline-only zones use one border colour per role, with no fill.
      return (role == SR_ROLE_SUP) ? C'173,216,230' : C'255,245,157';
     }

   string Name(string tier, int role, datetime born) const
     {
      return XAU_SR_PREFIX + tier + "_" + (role == SR_ROLE_RES ? "R" : "S") + "_" + (string)(long)born;
     }

   void DrawOne(string tier, const SrZone &z, datetime now)
     {
      string name = Name(tier, z.role, z.born);
      datetime startT = z.born;
      if(m_maxSpanBars > 0)
        {
         int barSecs = PeriodSeconds(_Period);
         datetime spanLimit = now - m_maxSpanBars * barSecs;
         if(startT < spanLimit) startT = spanLimit;
        }

      // Precise box height for top-down institutional analysis:
      // Resistance ceiling is anchored to the exact swing high (z.hi).
      // Support floor is anchored to the exact swing low (z.lo).
      // Clamped to a tight ribbon height so higher-TF boxes don't swallow the chart.
      double boxHi = z.hi;
      double boxLo = z.lo;
      if(m_maxVisualHeightPts > 0)
        {
         double maxH = m_maxVisualHeightPts * _Point;
         if(boxHi - boxLo > maxH)
           {
            if(z.role == SR_ROLE_RES) boxLo = boxHi - maxH;
            else                      boxHi = boxLo + maxH;
           }
        }

      if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, startT, boxHi, now, boxLo)) return;
      color clr = Outline(z.role);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FILL, false);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);

      string lbl = name + "_lbl";
      double y = (z.role == SR_ROLE_RES) ? boxHi : boxLo;
      int barSecs = PeriodSeconds(_Period);
      datetime lblTime = now + 3 * barSecs;
      if(ObjectCreate(0, lbl, OBJ_TEXT, 0, lblTime, y))
        {
         ObjectSetString(0, lbl, OBJPROP_TEXT,
                         (z.role == SR_ROLE_RES ? "R " : "S ") + tier + " x" + (string)z.touches);
         ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 7);
         ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, z.role == SR_ROLE_RES ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
         ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
        }
     }

public:
   CSrBoxes()
     : m_zones(NULL),
       m_show(false),
       m_nearestOnly(true),
       m_maxSpanBars(40),
       m_maxVisualHeightPts(250.0),
       m_drawnVersion(-1),
       m_rightEdge(0)
     {}

   void Init(CSrZoneSet *zones, bool show, bool nearestOnly = true, int maxSpanBars = 40, double maxVisualHeightPts = 250.0)
     {
      m_zones              = zones;
      m_show               = show;
      m_nearestOnly        = nearestOnly;
      m_maxSpanBars        = maxSpanBars;
      m_maxVisualHeightPts = maxVisualHeightPts;
      m_drawnVersion       = -1;
      m_rightEdge          = 0;
      if(!m_show) Clear();
     }

   // Call once per closed TRADE-TF bar (after CSrZoneSet.Update()).
   void Redraw()
     {
      if(!m_show || m_zones == NULL || !m_zones.Seeded()) return;
      datetime now = iTime(_Symbol, PERIOD_CURRENT, 0);
      if(now == 0) return;
      if(m_zones.Version() == m_drawnVersion && now == m_rightEdge) return;
      ObjectsDeleteAll(0, XAU_SR_PREFIX);

      double curPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(curPrice <= 0) curPrice = iClose(_Symbol, PERIOD_CURRENT, 0);

      if(!m_nearestOnly)
        {
         // Full legacy display: all zones
         for(int i = 0; i < m_zones.TierCount(); i++)
           {
            CSrZoneTier *t = m_zones.Tier(i);
            if(t == NULL) continue;
            for(int k = 0; k < t.Count(); k++)
              {
               SrZone z; t.Get(k, z);
               DrawOne(t.Name(), z, now);
              }
           }
        }
      else
        {
         // SOTA Clean display: only nearest active Resistance & Support per tier
         for(int i = 0; i < m_zones.TierCount(); i++)
           {
            CSrZoneTier *t = m_zones.Tier(i);
            if(t == NULL) continue;

            int bestResIdx = -1;
            int bestSupIdx = -1;
            double minResDist = 1e9;
            double minSupDist = 1e9;

            for(int k = 0; k < t.Count(); k++)
              {
               SrZone z; t.Get(k, z);
               if(z.role == SR_ROLE_RES)
                 {
                  // Resistance: above or containing price
                  double dist = (curPrice <= z.hi) ? MathMax(0.0, z.lo - curPrice) : (curPrice - z.hi + 1000.0);
                  if(dist < minResDist)
                    {
                     minResDist = dist;
                     bestResIdx = k;
                    }
                 }
               else if(z.role == SR_ROLE_SUP)
                 {
                  // Support: below or containing price
                  double dist = (curPrice >= z.lo) ? MathMax(0.0, curPrice - z.hi) : (z.lo - curPrice + 1000.0);
                  if(dist < minSupDist)
                    {
                     minSupDist = dist;
                     bestSupIdx = k;
                    }
                 }
              }

            if(bestResIdx >= 0)
              {
               SrZone zRes; t.Get(bestResIdx, zRes);
               DrawOne(t.Name(), zRes, now);
              }
            if(bestSupIdx >= 0)
              {
               SrZone zSup; t.Get(bestSupIdx, zSup);
               DrawOne(t.Name(), zSup, now);
              }
           }
        }

      m_drawnVersion = m_zones.Version();
      m_rightEdge = now;
      ChartRedraw();
     }

   void Clear()
     {
      ObjectsDeleteAll(0, XAU_SR_PREFIX);
      m_drawnVersion = -1;
      m_rightEdge = 0;
     }
  };
#endif
