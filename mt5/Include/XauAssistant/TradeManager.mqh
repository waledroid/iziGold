#ifndef XAU_TRADEMANAGER_MQH
#define XAU_TRADEMANAGER_MQH
#include <Trade/Trade.mqh>
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/RiskManager.mqh>

class CTradeManager
  {
private:
   CTrade        m_trade;
   CRiskManager *m_risk;
   long          m_magic;
   bool          m_pyramid;
   int           m_maxPos;
   double        m_addTriggerAtr, m_targetPct, m_stopAtrMult;
   double        m_lastEntryPrice;
   double        m_ratios[3];

   string CycleKey() { return "XAU_CYCLE_BAL_" + (string)AccountInfoInteger(ACCOUNT_LOGIN); }

   int CountOwn()
     {
      int n = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol) n++;
      return n;
     }

   double BasketProfit()
     {
      double p = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            p += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      return p;
     }

   long OwnType()
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 &&
            PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return PositionGetInteger(POSITION_TYPE);
      return -1;
     }

   void MoveStopsToBreakeven()
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         double open = PositionGetDouble(POSITION_PRICE_OPEN);
         m_trade.PositionModify(tk, open, PositionGetDouble(POSITION_TP));
        }
     }

public:
   void Init(CRiskManager *risk, long magic, bool pyramid, int maxPos,
             double addAtr, double targetPct, double stopAtrMult)
     {
      m_risk = risk; m_magic = magic; m_pyramid = pyramid; m_maxPos = maxPos;
      m_addTriggerAtr = addAtr; m_targetPct = targetPct; m_stopAtrMult = stopAtrMult;
      m_trade.SetExpertMagicNumber(magic);
      m_ratios[0] = 1.0; m_ratios[1] = 0.7; m_ratios[2] = 0.4;
     }

   int OpenCount() { return CountOwn(); }

   ENUM_SIGNAL BasketDirection()
     {
      long t = OwnType();
      if(t == POSITION_TYPE_BUY)  return SIGNAL_BUY;
      if(t == POSITION_TYPE_SELL) return SIGNAL_SELL;
      return SIGNAL_NONE;
     }

   void CloseAll(string reason)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            m_trade.PositionClose(tk);
        }
      Print("TradeManager: closed all (", reason, ")");
     }

   void OnSignal(ENUM_SIGNAL sig, double atr_value, double stopPrice = 0)
     {
      if(sig == SIGNAL_EXIT) { CloseAll("strategy EXIT"); return; }
      if(sig != SIGNAL_BUY && sig != SIGNAL_SELL) return;
      if(CountOwn() > 0)
        {
         long ptype = OwnType();
         bool opposite = (sig == SIGNAL_BUY  && ptype == POSITION_TYPE_SELL) ||
                         (sig == SIGNAL_SELL && ptype == POSITION_TYPE_BUY);
         if(!opposite) return;              // same direction: one cycle at a time
         CloseAll("reversal signal");       // stop-and-reverse, then enter below
         if(CountOwn() > 0)                 // guard: close incomplete
           { Print("TradeManager: reversal aborted — close incomplete, still ", CountOwn(), " open"); return; }
        }
      string why;
      if(!m_risk.CanEnter(why)) { Print("Entry blocked: ", why); return; }
      double price = (sig == SIGNAL_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl;
      bool validStop = stopPrice > 0 &&
                       ((sig == SIGNAL_BUY  && stopPrice < price) ||
                        (sig == SIGNAL_SELL && stopPrice > price));
      if(validStop) sl = stopPrice;
      else sl = (sig == SIGNAL_BUY) ? price - m_stopAtrMult * atr_value
                                    : price + m_stopAtrMult * atr_value;
      double sl_points = MathAbs(price - sl) / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[0]);
      if(lots <= 0) return;
      bool ok = (sig == SIGNAL_BUY) ? m_trade.Buy(lots, _Symbol, 0, sl)
                                    : m_trade.Sell(lots, _Symbol, 0, sl);
      if(ok)
        {
         m_lastEntryPrice = price;
         GlobalVariableSet(CycleKey(), AccountInfoDouble(ACCOUNT_BALANCE));
        }
     }

   void Manage(double atr_value, bool conditionStillTrue)
     {
      int n = CountOwn();
      if(n == 0) return;
      // profit target: close everything at +targetPct of cycle-start balance
      double cycleBal = GlobalVariableGet(CycleKey());
      if(cycleBal > 0 && BasketProfit() >= cycleBal * m_targetPct / 100.0)
        { CloseAll("profit target reached"); return; }
      // pyramid: add only in profit, only while condition true, shrinking size
      if(!m_pyramid || !conditionStillTrue || n >= m_maxPos) return;
      if(BasketProfit() <= 0) return;               // never add in loss
      long ptype = -1;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
           { ptype = PositionGetInteger(POSITION_TYPE); break; }
      double price = (ptype == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double advance = (ptype == POSITION_TYPE_BUY) ? price - m_lastEntryPrice
                                                    : m_lastEntryPrice - price;
      if(advance < m_addTriggerAtr * atr_value) return;
      double sl_points = m_stopAtrMult * atr_value / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[MathMin(n, 2)]);
      if(lots <= 0) return;
      bool ok = (ptype == POSITION_TYPE_BUY) ? m_trade.Buy(lots, _Symbol)
                                             : m_trade.Sell(lots, _Symbol);
      if(ok) { m_lastEntryPrice = price; MoveStopsToBreakeven(); }
     }
  };
#endif
