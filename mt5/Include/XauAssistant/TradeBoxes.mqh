// TradeBoxes.mqh — risk/reward rectangles for the current basket, plus a
// bounded history of past trades' boxes on the chart. Pure charting: reads
// trade state passed in by the caller, never touches trading/risk state,
// and every path is best-effort (missing data / missing object => skip,
// never crash).
//
// Semantics (docs/superpowers/specs/2026-08-03-trade-boxes-design.md):
//  - red risk box:   entry <-> sl, open-time <-> right edge (open) / close
//                     time (closed). Right edge tracks the current bar while
//                     the basket stays open.
//  - green profit box: entry <-> last-close (open) / entry <-> exit
//                     (closed), drawn ONLY while/if favorable, else absent.
//  - the box tracks the FIRST entry of the basket; pyramid "add" events
//    draw no new box (arrows already mark them).
#ifndef XAU_TRADEBOXES_MQH
#define XAU_TRADEBOXES_MQH

#define XAU_TR_PREFIX       "xau_tr_"
#define XAU_TR_RETAIN_COUNT 30

class CTradeBoxes
  {
private:
   bool     m_active;    // a basket is currently open and being tracked
   long     m_ticket;    // naming key, taken from the "open" event
   string   m_dir;       // "BUY" / "SELL"
   double   m_entry;
   double   m_sl;
   datetime m_openTime;

   color RiskColor()   { return C'66,32,36'; }
   color ProfitColor() { return C'26,56,44'; }

   string RiskName()   { return RiskNameFor(m_ticket); }
   string ProfitName() { return ProfitNameFor(m_ticket); }
   static string RiskNameFor(long ticket)   { return XAU_TR_PREFIX + (string)ticket + "_r"; }
   static string ProfitNameFor(long ticket) { return XAU_TR_PREFIX + (string)ticket + "_g"; }

   // Creates the rectangle on first use, otherwise just moves its anchors —
   // avoids ObjectDelete/ObjectCreate churn every bar.
   void DrawRect(string name, datetime t1, double p1, datetime t2, double p2, color clr)
     {
      if(ObjectFind(0, name) < 0)
        {
         if(!ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2)) return;
         ObjectSetInteger(0, name, OBJPROP_FILL, true);
         ObjectSetInteger(0, name, OBJPROP_BACK, true);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
        }
      else
        {
         ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
         ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p1);
         ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
         ObjectSetDouble(0, name, OBJPROP_PRICE, 1, p2);
        }
     }

   void ExtendRightEdge(string name, datetime t2)
     {
      if(ObjectFind(0, name) < 0) return;
      ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
     }

   // name = "xau_tr_<ticket>_r" or "xau_tr_<ticket>_g" -> ticket, or -1 if
   // the name doesn't match the expected shape.
   static long ParseTicket(string name)
     {
      int start = StringLen(XAU_TR_PREFIX);
      int end   = StringLen(name) - 2;   // trailing "_r" / "_g"
      if(end <= start) return -1;
      return StringToInteger(StringSubstr(name, start, end - start));
     }

   // Deletes box pairs for tickets older than the RETAIN_COUNT most recent.
   // Ticket numbers are broker-assigned and monotonically increasing, so
   // sorting the tickets found on the chart is a reliable age ordering with
   // no persisted state needed (self-healing across EA/terminal restarts).
   void Prune()
     {
      long tickets[];
      int total = ObjectsTotal(0, 0, OBJ_RECTANGLE);
      for(int i = 0; i < total; i++)
        {
         string name = ObjectName(0, i, 0, OBJ_RECTANGLE);
         if(StringFind(name, XAU_TR_PREFIX) != 0) continue;
         long tk = ParseTicket(name);
         if(tk <= 0) continue;
         bool seen = false;
         for(int j = 0; j < ArraySize(tickets); j++)
            if(tickets[j] == tk) { seen = true; break; }
         if(!seen)
           {
            int n = ArraySize(tickets);
            ArrayResize(tickets, n + 1);
            tickets[n] = tk;
           }
        }
      int n = ArraySize(tickets);
      if(n <= XAU_TR_RETAIN_COUNT) return;
      ArraySort(tickets);   // ascending -> oldest first
      int excess = n - XAU_TR_RETAIN_COUNT;
      for(int i = 0; i < excess; i++)
        {
         ObjectDelete(0, RiskNameFor(tickets[i]));
         ObjectDelete(0, ProfitNameFor(tickets[i]));
        }
     }

public:
   CTradeBoxes() : m_active(false), m_ticket(0), m_dir(""), m_entry(0), m_sl(0), m_openTime(0) {}

   // Fires on the basket's FIRST entry only (TradeManager never emits "open"
   // for pyramid adds — those are a separate "add" event this class ignores).
   void OnOpen(long ticket, string dir, double entry, double sl)
     {
      m_active   = true;
      // A ticket <= 0 (defensive only — TradeManager always passes the real
      // order ticket) would collide box names across trades, so fall back to
      // a timestamp to keep naming unique rather than draw into a stale box.
      m_ticket   = (ticket > 0) ? ticket : (long)TimeCurrent();
      m_dir      = dir;
      m_entry    = entry;
      m_sl       = sl;
      m_openTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      datetime t2 = m_openTime + PeriodSeconds(PERIOD_CURRENT);
      if(sl > 0)
         DrawRect(RiskName(), m_openTime, MathMax(entry, sl), t2, MathMin(entry, sl), RiskColor());
      ObjectDelete(0, ProfitName());   // no green box until price moves favorable
      Prune();
     }

   // Re-arms tracking after an EA reload (recompile auto-reload, terminal
   // restart, chart re-attach) that happened while a basket was already
   // open. Without this, m_active resets to false on construction and the
   // live box would freeze stale forever — OnBarUpdate/OnClose both
   // early-return on !m_active, so nothing would ever finalize it at the
   // real exit. Call once from OnInit, after TradeManager.Init().
   //
   // Scans PositionsTotal() for this EA's own positions on _Symbol and
   // adopts the OLDEST one (lowest POSITION_TICKET) as the basket anchor —
   // same "tracks the FIRST entry" semantics OnOpen already uses, just
   // approximated after the fact for a pyramided basket since the earlier
   // legs' original open events are lost across the reload.
   //
   // Ticket source matches OnOpen's: TradeManager passes
   // (long)m_trade.ResultOrder() there, which — for a hedging-mode market
   // order producing a brand-new position — equals that position's
   // POSITION_TICKET, so reusing POSITION_TICKET here targets the SAME
   // object names OnOpen already created; DrawRect's ObjectFind guard means
   // this moves the existing rectangle rather than duplicating it.
   void RecoverFromPositions(long magic)
     {
      long     oldestTicket = -1;
      double   entry = 0, sl = 0;
      long     ptype = -1;
      datetime openTime = 0;
      for(int i = 0; i < PositionsTotal(); i++)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
         long thisTicket = (long)PositionGetInteger(POSITION_TICKET);
         if(oldestTicket < 0 || thisTicket < oldestTicket)
           {
            oldestTicket = thisTicket;
            entry        = PositionGetDouble(POSITION_PRICE_OPEN);
            sl           = PositionGetDouble(POSITION_SL);
            ptype        = PositionGetInteger(POSITION_TYPE);
            openTime     = (datetime)PositionGetInteger(POSITION_TIME);
           }
        }
      if(oldestTicket <= 0) return;   // nothing open — nothing to recover

      m_active   = true;
      m_ticket   = oldestTicket;
      m_dir      = (ptype == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      m_entry    = entry;
      m_sl       = sl;
      m_openTime = (openTime > 0) ? openTime : iTime(_Symbol, PERIOD_CURRENT, 0);
      // Re-anchor immediately rather than waiting for the next closed bar —
      // if the box objects still exist (normal case: only the in-memory
      // class state was lost, not the chart), this just moves their anchors;
      // if they were somehow removed too, this recreates them.
      if(m_sl > 0)
         DrawRect(RiskName(), m_openTime, MathMax(m_entry, m_sl),
                  iTime(_Symbol, PERIOD_CURRENT, 0), MathMin(m_entry, m_sl), RiskColor());
     }

   // Runs once per closed TRADE-TF bar (caller gates on OpenCount() > 0) — on
   // a chart displaying a higher timeframe this fires multiple times per
   // visible candle (e.g. 3x per M15 bar when trading M5); each call is
   // idempotent (re-anchors to the same box), so redraws are harmless.
   void OnBarUpdate()
     {
      if(!m_active) return;
      datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      double   lastClose = iClose(_Symbol, PERIOD_CURRENT, 1);
      if(barTime <= 0 || lastClose <= 0) return;

      if(m_sl > 0) ExtendRightEdge(RiskName(), barTime);

      bool favorable = (m_dir == "BUY")  ? (lastClose > m_entry) :
                       (m_dir == "SELL") ? (lastClose < m_entry) : false;
      if(favorable)
         DrawRect(ProfitName(), m_openTime, MathMax(m_entry, lastClose),
                  barTime, MathMin(m_entry, lastClose), ProfitColor());
      else
         ObjectDelete(0, ProfitName());
     }

   // Fires once, on the basket-FINAL close (caller gates on the same
   // basketGone condition CUiSink already computes for strategy bookkeeping).
   // Freezes the right edge and clears m_active so no later OnBarUpdate call
   // can reopen/redraw this basket's boxes.
   void OnClose(double exitPrice)
     {
      if(!m_active) return;
      datetime closeTime = TimeCurrent();
      if(m_sl > 0) ExtendRightEdge(RiskName(), closeTime);

      bool favorable = exitPrice > 0 &&
                       ((m_dir == "BUY"  && exitPrice > m_entry) ||
                        (m_dir == "SELL" && exitPrice < m_entry));
      if(favorable)
         DrawRect(ProfitName(), m_openTime, MathMax(m_entry, exitPrice),
                  closeTime, MathMin(m_entry, exitPrice), ProfitColor());
      else
         ObjectDelete(0, ProfitName());   // losing trade: red box only

      m_active = false;
     }
  };
#endif
