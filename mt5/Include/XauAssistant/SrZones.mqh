// SrZones.mqh — support/resistance ZONE engine, three timeframe tiers
// (docs/superpowers/specs/2026-09-05-sr-zones-design.md). Pure state
// machine over closed bars of ONE timeframe per tier: candles in, zones
// out, no chart objects, no trade state. Twin of service/app/sr_zones.py —
// the processing order per closed bar is a CONTRACT shared with it:
//   1. death by close-through (close beyond the box by breakAtr x ATR)
//   2. the pivot confirmed by this bar (wing bars to its right) births a
//      box or merges into the nearest same-role box within mergeAtr x ATR
//   3. keep the keepPerRole nearest boxes per role (distance to close,
//      then more touches, then older)
//   4. sort by (born, lo) so merge tie-breaks are deterministic
// ATR is the terminal's own iATR(14) (an SMA of True Range), read at the
// confirming bar's shift, which the Python twin reproduces exactly.
// FAIL-OPEN: a tier that cannot read its bars/ATR simply stays empty.
#ifndef XAU_SRZONES_MQH
#define XAU_SRZONES_MQH
#include <XauAssistant/Strategy.mqh>

#define SR_ROLE_RES 0
#define SR_ROLE_SUP 1
#define SR_MAX_ZONES 64

struct SrZone
  {
   int      role;
   double   lo;
   double   hi;
   int      touches;
   datetime born;
   datetime lastTouch;
  };

struct SrParams
  {
   int    wing;
   double minHeightAtr;
   double maxHeightAtr;
   double mergeAtr;
   double breakAtr;
   int    keepPerRole;
  };

class CSrZoneTier
  {
private:
   string          m_name;
   ENUM_TIMEFRAMES m_tf;
   int             m_lookback;
   SrParams        m_p;
   int             m_atrHandle;
   datetime        m_lastBar;    // newest CLOSED tier bar already processed
   bool            m_seeded;
   SrZone          m_zones[SR_MAX_ZONES];
   int             m_count;

   double Distance(const SrZone &z, double price) const
     {
      if(price >= z.lo && price <= z.hi) return 0.0;
      return MathMin(MathAbs(price - z.lo), MathAbs(price - z.hi));
     }

   void Clamp(int role, double &lo, double &hi, double pivot, double atr) const
     {
      double minH = m_p.minHeightAtr * atr, maxH = m_p.maxHeightAtr * atr;
      if(hi - lo < minH) { lo = pivot - minH / 2.0; hi = pivot + minH / 2.0; }
      if(hi - lo > maxH)
        {
         if(role == SR_ROLE_RES) lo = hi - maxH;
         else                    hi = lo + maxH;
        }
     }

   void Remove(int idx)
     {
      for(int k = idx; k < m_count - 1; k++) m_zones[k] = m_zones[k + 1];
      m_count--;
     }

   void BirthOrMerge(int role, double lo, double hi, double pivot, datetime t, double atr)
     {
      Clamp(role, lo, hi, pivot, atr);
      double mergePx = m_p.mergeAtr * atr;
      int best = -1; double bestD = 0.0;
      for(int k = 0; k < m_count; k++)          // sorted by born: ties -> oldest
        {
         if(m_zones[k].role != role) continue;
         double d = Distance(m_zones[k], pivot);
         if(d <= mergePx && (best < 0 || d < bestD)) { best = k; bestD = d; }
        }
      if(best < 0)
        {
         if(m_count >= SR_MAX_ZONES) return;     // cannot happen after Bound(), guard anyway
         m_zones[m_count].role = role; m_zones[m_count].lo = lo; m_zones[m_count].hi = hi;
         m_zones[m_count].touches = 1; m_zones[m_count].born = t; m_zones[m_count].lastTouch = t;
         m_count++;
         return;
        }
      double nlo = MathMin(m_zones[best].lo, lo), nhi = MathMax(m_zones[best].hi, hi);
      double maxH = m_p.maxHeightAtr * atr;
      if(nhi - nlo > maxH)
        {
         if(role == SR_ROLE_RES) nlo = nhi - maxH;
         else                    nhi = nlo + maxH;
        }
      m_zones[best].lo = nlo; m_zones[best].hi = nhi;
      m_zones[best].touches++; m_zones[best].lastTouch = t;
     }

   // Strict fractal at shift p: higher than every high within wing bars
   // on BOTH sides (the right side p-1 .. p-wing must all be closed, i.e.
   // p - wing >= 1 -- the caller guarantees it).
   bool IsSwingHigh(int p) const
     {
      double h = iHigh(_Symbol, m_tf, p);
      for(int k = p - m_p.wing; k <= p + m_p.wing; k++)
        {
         if(k == p) continue;
         if(!(h > iHigh(_Symbol, m_tf, k))) return false;
        }
      return true;
     }

   bool IsSwingLow(int p) const
     {
      double l = iLow(_Symbol, m_tf, p);
      for(int k = p - m_p.wing; k <= p + m_p.wing; k++)
        {
         if(k == p) continue;
         if(!(l < iLow(_Symbol, m_tf, k))) return false;
        }
      return true;
     }

   // Keep the keepPerRole nearest per role: distance to close asc, touches
   // desc, born asc. Selection by repeated extraction keeps it simple.
   void Bound(double close)
     {
      SrZone kept[SR_MAX_ZONES]; int nk = 0;
      for(int role = SR_ROLE_RES; role <= SR_ROLE_SUP; role++)
        {
         bool used[SR_MAX_ZONES];
         for(int u = 0; u < SR_MAX_ZONES; u++) used[u] = false;
         for(int pick = 0; pick < m_p.keepPerRole; pick++)
           {
            int best = -1;
            for(int k = 0; k < m_count; k++)
              {
               if(used[k] || m_zones[k].role != role) continue;
               if(best < 0) { best = k; continue; }
               double dk = Distance(m_zones[k], close), db = Distance(m_zones[best], close);
               if(dk < db || (dk == db && (m_zones[k].touches > m_zones[best].touches
                  || (m_zones[k].touches == m_zones[best].touches && m_zones[k].born < m_zones[best].born))))
                  best = k;
              }
            if(best < 0) break;
            used[best] = true;
            kept[nk++] = m_zones[best];
           }
        }
      for(int k = 0; k < nk; k++) m_zones[k] = kept[k];
      m_count = nk;
      SortByBorn();
     }

   void SortByBorn()
     {
      for(int a = 1; a < m_count; a++)          // insertion sort, n <= 6
        {
         SrZone v = m_zones[a]; int b = a - 1;
         while(b >= 0 && (m_zones[b].born > v.born
               || (m_zones[b].born == v.born && m_zones[b].lo > v.lo)))
           { m_zones[b + 1] = m_zones[b]; b--; }
         m_zones[b + 1] = v;
        }
     }

   // One CLOSED bar at shift s (the confirming bar). Pivot candidate at
   // s + wing; its left wing reaches s + 2*wing.
   void ProcessBar(int s)
     {
      double ab[];
      if(CopyBuffer(m_atrHandle, 0, s, 1, ab) != 1) return;
      double atr = ab[0];
      if(atr <= 0 || atr == EMPTY_VALUE) return;
      double close = iClose(_Symbol, m_tf, s);
      for(int k = m_count - 1; k >= 0; k--)
        {
         bool dead = (m_zones[k].role == SR_ROLE_RES && close > m_zones[k].hi + m_p.breakAtr * atr)
                  || (m_zones[k].role == SR_ROLE_SUP && close < m_zones[k].lo - m_p.breakAtr * atr);
         if(dead) Remove(k);
        }
      int p = s + m_p.wing;
      datetime t = iTime(_Symbol, m_tf, p);
      double o = iOpen(_Symbol, m_tf, p), c = iClose(_Symbol, m_tf, p);
      double h = iHigh(_Symbol, m_tf, p), l = iLow(_Symbol, m_tf, p);
      if(IsSwingHigh(p)) BirthOrMerge(SR_ROLE_RES, MathMax(o, c), h, h, t, atr);
      if(IsSwingLow(p))  BirthOrMerge(SR_ROLE_SUP, l, MathMin(o, c), l, t, atr);
      Bound(close);
     }

public:
   CSrZoneTier() : m_name(""), m_tf(PERIOD_CURRENT), m_lookback(0),
                   m_atrHandle(INVALID_HANDLE), m_lastBar(0), m_seeded(false), m_count(0) {}

   void Init(string name, ENUM_TIMEFRAMES tf, int lookback, const SrParams &p)
     {
      m_name = name; m_tf = tf; m_lookback = lookback; m_p = p;
      m_atrHandle = iATR(_Symbol, m_tf, 14);
      m_lastBar = 0; m_seeded = false; m_count = 0;
     }

   // Processes every tier bar closed since the last call (all of the
   // lookback on the first call). True when the zone list may have changed.
   bool Update()
     {
      if(m_atrHandle == INVALID_HANDLE || m_p.wing < 1) return false;
      datetime closed = iTime(_Symbol, m_tf, 1);
      if(closed == 0 || closed == m_lastBar) return false;
      bool changed = false;
      if(!m_seeded)
        {
         // Both guards return BEFORE m_seeded/m_lastBar move: an empty seed
         // must never latch, or Seeded() would report a confident "clear"
         // from a tier that holds no boxes for the rest of the session.
         if(BarsCalculated(m_atrHandle) < 1) return false;  // ATR not ready: retry next tick
         int avail = Bars(_Symbol, m_tf) - 2 * m_p.wing - 2;
         if(avail < 1) return false;            // not enough history yet: retry next tick
         int from = MathMin(m_lookback, avail);
         if(avail < m_lookback)
            PrintFormat("SrZones %s: seeding from %d bars (wanted %d)", m_name, avail, m_lookback);
         for(int s = from; s >= 1; s--) ProcessBar(s);
         m_seeded = true; changed = true;
        }
      else
        {
         int last = iBarShift(_Symbol, m_tf, m_lastBar, true);
         if(last < 1) last = 2;                 // history reloaded under us: process the newest bar only
         for(int s = last - 1; s >= 1; s--) { ProcessBar(s); changed = true; }
        }
      m_lastBar = closed;
      return changed;
     }

   string Name() const   { return m_name; }
   bool   Seeded() const { return m_seeded; }
   int    Count() const  { return m_count; }
   // Deliberately NOT const: copying out of a const member array into a
   // non-const reference is the const construct MetaEditor is likeliest to
   // reject, and nothing needs Get() on a const CSrZoneTier.
   void   Get(int i, SrZone &out) { if(i >= 0 && i < m_count) out = m_zones[i]; }
  };

class CSrZoneSet
  {
private:
   CSrZoneTier m_tiers[3];      // 0 = M15, 1 = H1, 2 = H4 (weakest first)
   int         m_version;

public:
   CSrZoneSet() : m_version(0) {}

   void Init(const SrParams &p, int lookbackH4, int lookbackH1, int lookbackM15)
     {
      m_tiers[0].Init("M15", PERIOD_M15, lookbackM15, p);
      m_tiers[1].Init("H1",  PERIOD_H1,  lookbackH1,  p);
      m_tiers[2].Init("H4",  PERIOD_H4,  lookbackH4,  p);
      m_version = 0;
     }

   bool Update()
     {
      bool changed = false;
      for(int i = 0; i < 3; i++) if(m_tiers[i].Update()) changed = true;
      if(changed) m_version++;
      return changed;
     }

   int  Version() const   { return m_version; }
   int  TierCount() const { return 3; }
   // Plain if/return rather than `cond ? GetPointer(...) : NULL` — MQL5's
   // conditional operator wants both branches of one type, and an object
   // pointer against NULL is exactly the mismatch it rejects.
   CSrZoneTier *Tier(int i)
     {
      if(i < 0 || i >= 3) return NULL;
      return GetPointer(m_tiers[i]);
     }
   bool Seeded() const
     {
      for(int i = 0; i < 3; i++) if(m_tiers[i].Seeded()) return true;
      return false;
     }

   // 1 clear / 0 wall / -1 not evaluated (no tier seeded yet). BUY looks
   // at resistance above `entry`, SELL at support below; a wall is a box
   // containing the entry or whose near edge is within `room` (price
   // units) ahead. tiers = "H4,H1,M15"-style list of tiers to honour;
   // tierOut = the STRONGEST wall's name (H4 > H1 > M15) or "".
   int Verdict(int dir, double entry, double room, string tiers, string &tierOut)
     {
      tierOut = "";
      if(!Seeded()) return -1;
      for(int i = 2; i >= 0; i--)               // strongest first
        {
         if(StringFind(tiers, m_tiers[i].Name()) < 0) continue;
         for(int k = 0; k < m_tiers[i].Count(); k++)
           {
            SrZone z; m_tiers[i].Get(k, z);
            bool wall = false;
            if(dir == SIGNAL_BUY && z.role == SR_ROLE_RES)
               wall = (entry >= z.lo && entry <= z.hi) || (z.lo > entry && z.lo - entry <= room);
            else if(dir == SIGNAL_SELL && z.role == SR_ROLE_SUP)
               wall = (entry >= z.lo && entry <= z.hi) || (z.hi < entry && entry - z.hi <= room);
            if(wall) { tierOut = m_tiers[i].Name(); return 0; }
           }
        }
      return 1;
     }
  };
#endif
