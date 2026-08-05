#ifndef XAU_TRADEMANAGER_MQH
#define XAU_TRADEMANAGER_MQH
#include <Trade/Trade.mqh>
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/RiskManager.mqh>
#include <XauAssistant/UiApi.mqh>

class CTradeManager
  {
private:
   CTrade            m_trade;
   CRiskManager     *m_risk;
   CTradeEventSink  *m_sink;
   long          m_magic;
   bool          m_pyramid;
   int           m_maxPos;
   double        m_addTriggerAtr, m_targetPct, m_stopAtrMult;
   double        m_trailLockPct, m_trailActivateR;
   double        m_lastEntryPrice;
   double        m_ratios[3];

   string CycleKey() { return "XAU_CYCLE_BAL_" + (string)AccountInfoInteger(ACCOUNT_LOGIN); }
   string PeakKey() { return "XAU_PEAK_" + (string)AccountInfoInteger(ACCOUNT_LOGIN); }

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

   // Price at which the CURRENT basket's total profit reaches the cycle's
   // target$ (m_targetPct of the cycle-start balance). All own positions are
   // same-direction by construction (OnSignal never adds to an opposite
   // basket), so a single signed price delta from the current bid/ask
   // applies uniformly across legs. Returns 0.0 when there is nothing to
   // target (target disabled, no cycle balance, no positions), the target is
   // already met/exceeded, or a tick denominator is unusable.
   double BasketTargetPrice()
     {
      double cycleBal = GlobalVariableGet(CycleKey());
      if(m_targetPct <= 0 || cycleBal <= 0) return 0.0;

      double sumLots = 0;
      long   ptype = -1;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
           {
            sumLots += PositionGetDouble(POSITION_VOLUME);
            if(ptype == -1) ptype = PositionGetInteger(POSITION_TYPE);
           }
      if(ptype == -1 || sumLots <= 0) return 0.0;

      double tick_val  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick_size <= 0 || tick_val <= 0) return 0.0;
      double perPriceUnit = sumLots * tick_val / tick_size;   // $ profit per $1 price move, whole basket
      if(perPriceUnit <= 0) return 0.0;

      double remaining = cycleBal * m_targetPct / 100.0 - BasketProfit();
      if(remaining <= 0) return 0.0;                          // already at/past target

      double delta = remaining / perPriceUnit;                // unsigned price distance still needed
      double current = (ptype == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                     : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      return (ptype == POSITION_TYPE_BUY) ? current + delta : current - delta;
     }

   // Ratchet the whole basket's stop as each pyramid add lands. This function
   // runs right AFTER a successful add, so CountOwn() already includes the
   // new leg; addCount = CountOwn() - 1 counts positions beyond the original.
   //
   //  add1 (addCount == 1): halfway ratchet from the current stop toward the
   //  original entry, instead of snapping straight to breakeven. Instant
   //  breakeven parked the stop right inside normal retracement range, so a
   //  routine pullback would scratch out the old legs and — if it happened
   //  right after an add — leave the freshly added leg unprotected-in-profit
   //  until the next management tick. The halfway ratchet trades a little
   //  retained risk for retracement tolerance (user directive 2026-08-05).
   //
   //  add2+ (addCount >= 2): basket net-breakeven lock. The stop is set to
   //  the lot-weighted AVERAGE ENTRY across ALL own legs, not the previous
   //  add's halfway point or entry — later legs can carry more size than
   //  earlier ones, so only the weighted average reflects the basket's true
   //  breakeven price. User directive: once a basket has taken two adds, it
   //  can no longer close net-negative. This average is by construction on
   //  the profit side of the market price at add time (adds require
   //  BasketProfit() > 0 and a 1-ATR advance), so applying it only tightens
   //  the stop, never loosens it.
   //
   // The OLDEST own position (lowest ticket) is treated as the basket of
   // record for the add1 case: its POSITION_PRICE_OPEN is the original entry
   // E, and its current POSITION_SL is the basket stop S — read from the
   // broker (not cached) so this is correct even after an EA/terminal
   // reload. Every own leg (including the just-opened add, which has no SL
   // yet) is then modified to the same new stop N; each leg's TP is left
   // as-is.
   void RatchetBasketStop()
     {
      ulong  oldestTicket = 0;
      double E = 0, S = 0;
      bool   isBuy = false;
      double sumPriceVol = 0, sumVol = 0;
      int    count = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         count++;
         double vol = PositionGetDouble(POSITION_VOLUME);
         sumPriceVol += PositionGetDouble(POSITION_PRICE_OPEN) * vol;
         sumVol += vol;
         if(oldestTicket == 0 || tk < oldestTicket)
           {
            oldestTicket = tk;
            E = PositionGetDouble(POSITION_PRICE_OPEN);
            S = PositionGetDouble(POSITION_SL);
            isBuy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
           }
        }
      if(oldestTicket == 0) return;              // no own positions
      if(S == 0)
        {
         Print("TradeManager: ratchet skipped — oldest leg #", oldestTicket, " has no stop");
         return;                                 // never invent a stop
        }

      int addCount = count - 1;
      double N;
      if(addCount <= 1)
        {
         // add1: halfway ratchet toward original entry.
         N = isBuy ? S + (E - S) / 2.0 : S - (S - E) / 2.0;
         // Only move toward entry, never past it and never backward. If the
         // stop is already at/through entry (legacy breakeven-or-better
         // state from before this change), leave it alone.
         bool alreadyAtOrPastEntry = isBuy ? (S >= E) : (S <= E);
         bool advancesTowardEntry  = isBuy ? (N > S && N < E) : (N < S && N > E);
         if(alreadyAtOrPastEntry || !advancesTowardEntry) return;
        }
      else
        {
         // add2+: basket net-breakeven lock at the lot-weighted average entry.
         if(sumVol <= 0) return;
         N = NormalizeDouble(sumPriceVol / sumVol, _Digits);
         // Only move toward profit, never backward.
         bool advances = isBuy ? (N > S) : (N < S);
         if(!advances) return;
        }

      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(!m_trade.PositionModify(tk, N, PositionGetDouble(POSITION_TP)))
            Print("TradeManager: ratchet move failed #", tk,
                  " sl ", DoubleToString(N, _Digits),
                  " retcode ", m_trade.ResultRetcode());
        }
     }

public:
   void Init(CRiskManager *risk, long magic, bool pyramid, int maxPos,
             double addAtr, double targetPct, double stopAtrMult,
             double trailLockPct, double trailActivateR,
             CTradeEventSink *sink = NULL)
     {
      m_risk = risk; m_magic = magic; m_pyramid = pyramid; m_maxPos = maxPos;
      m_addTriggerAtr = addAtr; m_targetPct = targetPct; m_stopAtrMult = stopAtrMult;
      m_trailLockPct = trailLockPct; m_trailActivateR = trailActivateR;
      m_sink = sink;
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
      // Capture the basket direction/size/profit BEFORE closing — after the
      // loop below there are no own positions left to read them from.
      long ptype = OwnType();
      double totalLots = 0;
      double basketProfit = BasketProfit();
      for(int i = PositionsTotal() - 1; i >= 0; i--)
         if(PositionGetTicket(i) > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            totalLots += PositionGetDouble(POSITION_VOLUME);

      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk > 0 && PositionGetInteger(POSITION_MAGIC) == m_magic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            m_trade.PositionClose(tk);
        }
      GlobalVariableSet(PeakKey(), 0);
      Print("TradeManager: closed all (", reason, ")");

      // One "close" event per CloseAll call (not per ticket), only when there
      // was actually a basket to close.
      if(m_sink != NULL && ptype != -1)
        {
         string dir = (ptype == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         double price = (ptype == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                      : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         m_sink.OnTradeEvent("close", dir, totalLots, price, 0.0, reason, 0, basketProfit, 0.0);
        }
     }

   // Returns true ONLY when an order was actually opened (used by the EA to
   // detect AUTO-mode entry rejections). EXIT closes rather than opens, so
   // it returns false; every blocked/no-op path returns false too.
   bool OnSignal(ENUM_SIGNAL sig, double atr_value, double stopPrice = 0)
     {
      if(sig == SIGNAL_EXIT) { CloseAll("strategy EXIT"); return false; }
      if(sig != SIGNAL_BUY && sig != SIGNAL_SELL) return false;
      bool wasReversal = false;
      if(CountOwn() > 0)
        {
         long ptype = OwnType();
         bool opposite = (sig == SIGNAL_BUY  && ptype == POSITION_TYPE_SELL) ||
                         (sig == SIGNAL_SELL && ptype == POSITION_TYPE_BUY);
         if(!opposite) return false;         // same direction: one cycle at a time
         CloseAll("reversal");               // stop-and-reverse, then enter below
         if(CountOwn() > 0)                 // guard: close incomplete
           { Print("TradeManager: reversal aborted — close incomplete, still ", CountOwn(), " open"); return false; }
         wasReversal = true;
        }
      string why;
      if(!m_risk.CanEnter(why)) { Print("Entry blocked: ", why); return false; }
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
      if(lots <= 0) return false;
      bool ok = (sig == SIGNAL_BUY) ? m_trade.Buy(lots, _Symbol, 0, sl)
                                    : m_trade.Sell(lots, _Symbol, 0, sl);
      if(ok)
        {
         m_lastEntryPrice = price;
         GlobalVariableSet(CycleKey(), AccountInfoDouble(ACCOUNT_BALANCE));
         GlobalVariableSet(PeakKey(), 0);
         if(m_sink != NULL)
           {
            string dir = (sig == SIGNAL_BUY) ? "BUY" : "SELL";
            string openReason = wasReversal ? "reversal" : ("signal " + dir);
            m_sink.OnTradeEvent("open", dir, lots, price, sl, openReason,
                                (long)m_trade.ResultOrder(), 0.0, BasketTargetPrice());
           }
        }
      return ok;
     }

   void Manage(double atr_value, bool conditionStillTrue)
     {
      int n = CountOwn();
      if(n == 0) return;
      // profit target: close everything at +targetPct of cycle-start balance
      double cycleBal = GlobalVariableGet(CycleKey());
      if(m_targetPct > 0 && cycleBal > 0 && BasketProfit() >= cycleBal * m_targetPct / 100.0)
        { CloseAll("profit target"); return; }
      // proportional profit lock: once peak basket profit reaches TrailActivateR
      // times the per-trade risk budget, close the basket if profit falls back
      // to TrailLockPct% of that peak — locks in gains without a fixed target.
      if(m_trailLockPct > 0)
        {
         double profit = BasketProfit();
         double peak = GlobalVariableGet(PeakKey());
         if(profit > peak) { peak = profit; GlobalVariableSet(PeakKey(), peak); }
         double riskBudget = GlobalVariableGet(CycleKey()) * m_risk.RiskPct() / 100.0;
         if(riskBudget > 0 && peak >= m_trailActivateR * riskBudget &&
            profit <= peak * m_trailLockPct / 100.0)
           { CloseAll("profit lock"); return; }
        }
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
      // The add carries the same ATR stop its lot size was computed from —
      // sent WITH the order so the leg is never live without broker-side
      // protection even before RatchetBasketStop below runs (a stop this
      // close to market may not always be modifiable to the new shared
      // level right away, so the leg still needs its own stop from birth).
      double addSl = (ptype == POSITION_TYPE_BUY) ? price - m_stopAtrMult * atr_value
                                                  : price + m_stopAtrMult * atr_value;
      bool ok = (ptype == POSITION_TYPE_BUY) ? m_trade.Buy(lots, _Symbol, 0, addSl)
                                             : m_trade.Sell(lots, _Symbol, 0, addSl);
      if(ok)
        {
         m_lastEntryPrice = price;
         RatchetBasketStop();
         if(m_sink != NULL)
           {
            string dir = (ptype == POSITION_TYPE_BUY) ? "BUY" : "SELL";
            m_sink.OnTradeEvent("add", dir, lots, price, addSl, "pyramid add",
                                (long)m_trade.ResultOrder(), 0.0, BasketTargetPrice());
           }
        }
     }
  };
#endif
