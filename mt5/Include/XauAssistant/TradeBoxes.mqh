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

   color RiskColor()   { return C'44,29,37'; }   // blend(#e74c3c, #131722, .12) -- config/chart_spec.json
   color ProfitColor() { return C'22,45,43'; }   // blend(#2ecc71, #131722, .12) -- config/chart_spec.json

   string RiskName()   { return RiskNameFor(m_ticket); }
   string ProfitName() { return ProfitNameFor(m_ticket); }
   string TpName()     { return TpNameFor(m_ticket); }
   string Tp2Name()    { return Tp2NameFor(m_ticket); }
   string SlName()     { return SlNameFor(m_ticket); }
   string EntryName()  { return EntryNameFor(m_ticket); }
   string BadgeName()  { return BadgeNameFor(m_ticket); }
   static string RiskNameFor(long ticket)   { return XAU_TR_PREFIX + (string)ticket + "_r"; }
   static string ProfitNameFor(long ticket) { return XAU_TR_PREFIX + (string)ticket + "_g"; }
   static string TpNameFor(long ticket)     { return XAU_TR_PREFIX + (string)ticket + "_tp"; }
   static string Tp2NameFor(long ticket)    { return XAU_TR_PREFIX + (string)ticket + "_tp2"; }
   static string SlNameFor(long ticket)     { return XAU_TR_PREFIX + (string)ticket + "_sl"; }
   static string EntryNameFor(long ticket)  { return XAU_TR_PREFIX + (string)ticket + "_en"; }
   static string BadgeNameFor(long ticket)  { return XAU_TR_PREFIX + (string)ticket + "_b"; }

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
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_FILL, true);
         ObjectSetInteger(0, name, OBJPROP_BACK, true);
        }
     }

   void DrawTrendLine(string name, datetime t1, double p1, datetime t2, double p2, color clr, ENUM_LINE_STYLE style, string label)
     {
      if(ObjectFind(0, name) < 0)
        {
         if(!ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2)) return;
         ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
         ObjectSetInteger(0, name, OBJPROP_STYLE, style);
         ObjectSetString(0, name, OBJPROP_TEXT, label);
        }
      else
        {
         ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
         ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p1);
         ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
         ObjectSetDouble(0, name, OBJPROP_PRICE, 1, p2);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_STYLE, style);
         ObjectSetString(0, name, OBJPROP_TEXT, label);
        }
     }

   void DrawTextLabel(string name, datetime t, double p, string txt, color clr)
     {
      if(ObjectFind(0, name) < 0)
        {
         if(!ObjectCreate(0, name, OBJ_TEXT, 0, t, p)) return;
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
         ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
         ObjectSetString(0, name, OBJPROP_TEXT, txt);
         ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT);
        }
      else
        {
         ObjectSetInteger(0, name, OBJPROP_TIME, 0, t);
         ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p);
         ObjectSetString(0, name, OBJPROP_TEXT, txt);
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
         ObjectDelete(0, TpNameFor(tickets[i]));
         ObjectDelete(0, Tp2NameFor(tickets[i]));
         ObjectDelete(0, SlNameFor(tickets[i]));
         ObjectDelete(0, EntryNameFor(tickets[i]));
         ObjectDelete(0, BadgeNameFor(tickets[i]));
        }
     }

public:
   CTradeBoxes() : m_active(false), m_ticket(0), m_dir(""), m_entry(0), m_sl(0), m_openTime(0) {}

   // Fires on the basket's FIRST entry only (TradeManager never emits "open"
   // for pyramid adds — those are a separate "add" event this class ignores).
   void OnOpen(long ticket, string dir, double entry, double sl)
     {
      m_active   = true;
      m_ticket   = (ticket > 0) ? ticket : (long)TimeCurrent();
      m_dir      = dir;
      m_entry    = entry;
      m_sl       = sl;
      m_openTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      datetime t2 = m_openTime + PeriodSeconds(PERIOD_CURRENT);
      if(sl > 0)
        {
         double rDist = MathAbs(entry - sl);
         if(rDist > 0)
           {
            double tpPrice = (dir == "BUY") ? entry + 1.5 * rDist : entry - 1.5 * rDist;

            // Pure shaded Risk (Red) and Target (Green) zones without border outlines - TradingView style
            DrawRect(RiskName(), m_openTime, MathMax(entry, sl), t2, MathMin(entry, sl), RiskColor());
            DrawRect(ProfitName(), m_openTime, MathMax(entry, tpPrice), t2, MathMin(entry, tpPrice), ProfitColor());

            // Target, SL, and Entry projection lines
            DrawTrendLine(TpName(), m_openTime, tpPrice, t2, tpPrice, C'46,204,113', STYLE_DASH, StringFormat("TP (1.5R): %.2f", tpPrice));
            DrawTrendLine(SlName(), m_openTime, sl, t2, sl, C'231,76,60', STYLE_DASH, StringFormat("SL: %.2f", sl));
            DrawTrendLine(EntryName(), m_openTime, entry, t2, entry, C'180,180,180', STYLE_DOT, StringFormat("Entry: %.2f", entry));
           }
         else
           {
            DrawRect(RiskName(), m_openTime, MathMax(entry, sl), t2, MathMin(entry, sl), RiskColor());
           }
        }
      Prune();
     }

   void RecoverLastTradeFromHistory(long magic)
     {
      datetime fromTime = TimeCurrent() - 7 * 86400;
      datetime toTime = TimeCurrent() + 60;
      if(!HistorySelect(fromTime, toTime)) return;

      int totalDeals = HistoryDealsTotal();
      long lastPosId = -1;
      datetime exitTime = 0;
      double exitPrice = 0;

      for(int i = totalDeals - 1; i >= 0; i--)
        {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(dealTicket <= 0) continue;
         if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != magic) continue;
         long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
         if(entryType == DEAL_ENTRY_OUT)
           {
            lastPosId = (long)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
            exitTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
            exitPrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
            break;
           }
        }
      if(lastPosId <= 0) return;

      datetime entryTime = 0;
      double entryPrice = 0;
      string dir = "";
      for(int i = 0; i < totalDeals; i++)
        {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(dealTicket <= 0) continue;
         if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != magic) continue;
         if((long)HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID) == lastPosId)
           {
            long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
            if(entryType == DEAL_ENTRY_IN)
              {
               entryTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
               entryPrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
               long ptype = HistoryDealGetInteger(dealTicket, DEAL_TYPE);
               dir = (ptype == DEAL_TYPE_BUY) ? "BUY" : "SELL";
               break;
              }
           }
        }
      if(entryTime <= 0 || entryPrice <= 0) return;

      double sl = 0;
      for(int i = 0; i < HistoryOrdersTotal(); i++)
        {
         ulong ordTicket = HistoryOrderGetTicket(i);
         if(ordTicket <= 0) continue;
         if((long)HistoryOrderGetInteger(ordTicket, ORDER_POSITION_ID) == lastPosId)
           {
            double ordSl = HistoryOrderGetDouble(ordTicket, ORDER_SL);
            if(ordSl > 0) { sl = ordSl; break; }
           }
        }

      if(sl <= 0)
        {
         string initialRKey = "XAU_INITIAL_R_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol;
         double initR = GlobalVariableGet(initialRKey);
         if(initR > 0)
            sl = (dir == "BUY") ? entryPrice - initR : entryPrice + initR;
        }

      if(sl <= 0) return;

      int inShift = iBarShift(_Symbol, PERIOD_CURRENT, entryTime, false);
      datetime openBar = (inShift >= 0) ? iTime(_Symbol, PERIOD_CURRENT, inShift) : entryTime;
      int outShift = iBarShift(_Symbol, PERIOD_CURRENT, exitTime, false);
      datetime exitBar = (outShift >= 0) ? iTime(_Symbol, PERIOD_CURRENT, outShift) + PeriodSeconds(PERIOD_CURRENT) : exitTime;

      double rDist = MathAbs(entryPrice - sl);
      if(rDist <= 0) return;
      double tpPrice = (dir == "BUY") ? entryPrice + 1.5 * rDist : entryPrice - 1.5 * rDist;

      long tk = lastPosId;
      DrawRect(RiskNameFor(tk), openBar, MathMax(entryPrice, sl), exitBar, MathMin(entryPrice, sl), RiskColor());
      DrawRect(ProfitNameFor(tk), openBar, MathMax(entryPrice, tpPrice), exitBar, MathMin(entryPrice, tpPrice), ProfitColor());

      DrawTrendLine(TpNameFor(tk), openBar, tpPrice, exitBar, tpPrice, C'46,204,113', STYLE_DASH, StringFormat("TP (1.5R): %.2f", tpPrice));
      DrawTrendLine(SlNameFor(tk), openBar, sl, exitBar, sl, C'231,76,60', STYLE_DASH, StringFormat("SL: %.2f", sl));
      DrawTrendLine(EntryNameFor(tk), openBar, entryPrice, exitBar, entryPrice, C'180,180,180', STYLE_DOT, StringFormat("Entry: %.2f", entryPrice));
      Prune();
     }

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
      if(oldestTicket <= 0)
        {
         RecoverLastTradeFromHistory(magic);
         return;
        }

      m_active   = true;
      m_ticket   = oldestTicket;
      m_dir      = (ptype == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      m_entry    = entry;
      m_sl       = sl;
      int shift = (openTime > 0) ? iBarShift(_Symbol, PERIOD_CURRENT, openTime, false) : 0;
      m_openTime = (shift >= 0) ? iTime(_Symbol, PERIOD_CURRENT, shift) : iTime(_Symbol, PERIOD_CURRENT, 0);
      datetime tNow = iTime(_Symbol, PERIOD_CURRENT, 0);
      datetime t2 = tNow + PeriodSeconds(PERIOD_CURRENT);
      if(m_sl > 0)
        {
         double rDist = MathAbs(m_entry - m_sl);
         if(rDist > 0)
           {
            double tpPrice  = (m_dir == "BUY") ? m_entry + 1.5 * rDist : m_entry - 1.5 * rDist;

            DrawRect(RiskName(), m_openTime, MathMax(m_entry, m_sl), t2, MathMin(m_entry, m_sl), RiskColor());
            DrawRect(ProfitName(), m_openTime, MathMax(m_entry, tpPrice), t2, MathMin(m_entry, tpPrice), ProfitColor());

            DrawTrendLine(TpName(), m_openTime, tpPrice, t2, tpPrice, C'46,204,113', STYLE_DASH, StringFormat("TP (1.5R): %.2f", tpPrice));
            DrawTrendLine(SlName(), m_openTime, m_sl, t2, m_sl, C'231,76,60', STYLE_DASH, StringFormat("SL: %.2f", m_sl));
            DrawTrendLine(EntryName(), m_openTime, m_entry, t2, m_entry, C'180,180,180', STYLE_DOT, StringFormat("Entry: %.2f", m_entry));
           }
         else
           {
            DrawRect(RiskName(), m_openTime, MathMax(m_entry, m_sl), t2, MathMin(m_entry, m_sl), RiskColor());
           }
        }
      Prune();
     }

   // Runs once per closed TRADE-TF bar (caller gates on OpenCount() > 0)
   void OnBarUpdate()
     {
      if(!m_active) return;
      datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      if(barTime <= 0) return;
      datetime t2 = barTime + PeriodSeconds(PERIOD_CURRENT);

      if(m_sl > 0)
        {
         ExtendRightEdge(RiskName(), t2);
         ExtendRightEdge(ProfitName(), t2);
         ExtendRightEdge(TpName(), t2);
         ExtendRightEdge(SlName(), t2);
         ExtendRightEdge(EntryName(), t2);
        }
     }

   // Fires once, on the basket-FINAL close
   void OnClose(double exitPrice)
     {
      if(!m_active) return;
      datetime closeTime = TimeCurrent();
      int exitShift = iBarShift(_Symbol, PERIOD_CURRENT, closeTime, false);
      datetime exitBar = (exitShift >= 0) ? iTime(_Symbol, PERIOD_CURRENT, exitShift) + PeriodSeconds(PERIOD_CURRENT) : closeTime;

      if(m_sl > 0)
        {
         ExtendRightEdge(RiskName(), exitBar);
         ExtendRightEdge(ProfitName(), exitBar);
         ExtendRightEdge(TpName(), exitBar);
         ExtendRightEdge(SlName(), exitBar);
         ExtendRightEdge(EntryName(), exitBar);
        }

      m_active = false;
     }

   void Clear()
     {
      m_active = false;
     }
  };
#endif
