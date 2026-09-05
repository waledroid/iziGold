// SrBoxes.mqh — S/R zone rectangles on the MT5 chart. Pure charting, like
// TradeBoxes.mqh: reads CSrZoneSet, draws, never touches trading state,
// every path best-effort. Support = LIGHT BLUE, resistance = LIGHT YELLOW
// (owner 2026-09-05); tier = shade (H4 strongest). MT5 objects have no
// alpha, so the tints are pre-blended for the dark theme and drawn BACK
// (behind candles). Tiers are drawn weakest-first (M15, H1, H4) so a
// stronger tier's box covers a weaker one where they overlap. One prefix,
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
   int         m_drawnVersion;
   datetime    m_rightEdge;

   color Fill(string tier, int role) const
     {
      if(role == SR_ROLE_SUP)
        {
         if(tier == "H4") return C'120,170,220';
         if(tier == "H1") return C'90,130,175';
         return C'60,95,130';
        }
      if(tier == "H4") return C'235,225,140';
      if(tier == "H1") return C'185,175,105';
      return C'130,122,75';
     }

   string Name(string tier, int role, datetime born) const
     {
      return XAU_SR_PREFIX + tier + "_" + (role == SR_ROLE_RES ? "R" : "S") + "_" + (string)(long)born;
     }

   void DrawOne(string tier, const SrZone &z, datetime now)
     {
      string name = Name(tier, z.role, z.born);
      if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, z.born, z.hi, now, z.lo)) return;
      color clr = Fill(tier, z.role);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      string lbl = name + "_lbl";
      double y = (z.role == SR_ROLE_RES) ? z.hi : z.lo;
      if(ObjectCreate(0, lbl, OBJ_TEXT, 0, now, y))
        {
         ObjectSetString(0, lbl, OBJPROP_TEXT,
                         (z.role == SR_ROLE_RES ? "R " : "S ") + tier + " x" + (string)z.touches);
         ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 7);
         ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, z.role == SR_ROLE_RES ? ANCHOR_RIGHT_LOWER : ANCHOR_RIGHT_UPPER);
         ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, lbl, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, lbl, OBJPROP_BACK, false);
        }
     }

public:
   CSrBoxes() : m_zones(NULL), m_show(false), m_drawnVersion(-1), m_rightEdge(0) {}

   void Init(CSrZoneSet *zones, bool show)
     {
      m_zones = zones; m_show = show; m_drawnVersion = -1; m_rightEdge = 0;
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
      for(int i = 0; i < m_zones.TierCount(); i++)         // weakest first
        {
         CSrZoneTier *t = m_zones.Tier(i);
         if(t == NULL) continue;
         for(int k = 0; k < t.Count(); k++)
           {
            SrZone z; t.Get(k, z);
            DrawOne(t.Name(), z, now);
           }
        }
      m_drawnVersion = m_zones.Version();
      m_rightEdge = now;
      ChartRedraw();
     }

   void Clear()
     {
      ObjectsDeleteAll(0, XAU_SR_PREFIX);
      m_drawnVersion = -1; m_rightEdge = 0;
     }
  };
#endif
