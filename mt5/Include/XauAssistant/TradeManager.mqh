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

   string CycleKey() { return "XAU_CYCLE_BAL_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol; }
   string PeakKey() { return "XAU_PEAK_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol; }
   // Sticky per-basket entry mode (0=ADR, 1=FIXED). Written once at basket
   // open (OnSignal), read by Manage() every bar so a mid-trade restart
   // cannot turn a FIXED ride into ADR management or vice versa. Meaningful
   // only while a basket exists — stale after CloseAll until the next open
   // overwrites it, same pattern as CycleKey (never reset on close).
   string BasketModeKey() { return "XAU_BASKET_MODE_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol; }
   // 1 = this FIXED basket already sent its one target alert (restart-safe;
   // reset to 0 on every basket open)
   string TpAlertKey()    { return "XAU_TP_ALERTED_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol; }

   // "adr"/"fixed" string for the CURRENT basket's mode — used to tag trade
   // events (open/add/close) with the mode that basket was actually running
   // under, independent of whatever the runtime switch has moved on to.
   string CurrentEntryModeStr() { return (GlobalVariableGet(BasketModeKey()) > 0.5) ? "fixed" : "adr"; }

   // FixedLots is a direct target size (not a risk ceiling that must never
   // be exceeded like CalcLots' output), so round to the nearest step
   // rather than floor, then clamp to the broker's min/max.
   double ClampToVolume(double raw)
     {
      if(raw <= 0) return 0.0; // fail closed: a misconfigured 0.0 must not silently round up to broker min-lot
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      double lots = raw;
      if(step > 0) lots = MathRound(lots / step) * step;
      return MathMin(MathMax(lots, vmin), vmax);
     }

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
   // new leg. Own legs are sorted by ticket ascending (oldest first) into
   // index k = 0..count-1; k (the newest leg's index) equals addCount, the
   // number of positions beyond the original entry.
   //
   //  add1 (addCount == 1): halfway ratchet from the current stop toward the
   //  original entry, instead of snapping straight to breakeven. Instant
   //  breakeven parked the stop right inside normal retracement range, so a
   //  routine pullback would scratch out the old legs and — if it happened
   //  right after an add — leave the freshly added leg unprotected-in-profit
   //  until the next management tick. The halfway ratchet trades a little
   //  retained risk for retracement tolerance (user directive 2026-08-05).
   //
   //  add2+ (addCount >= 2): lagging halfway-entry ladder. The stop moves to
   //  the midpoint of the entries of the two legs BEFORE the newest one
   //  (add2 -> halfway(open, add1); add3 -> halfway(add1, add2); ...) —
   //  deliberately one step behind the newest add. This secures the basket's
   //  veteran legs while still giving the newest add room to breathe, and it
   //  pairs with the shrinking-size cap on adds (see Manage()) that keeps
   //  each new leg the smallest-risk leg in the basket. Replaces the
   //  short-lived lot-weighted-average-entry lock (user directive
   //  2026-08-05).
   //
   // The OLDEST own position (lowest ticket) is treated as the basket of
   // record: its POSITION_PRICE_OPEN is the original entry E, and its
   // current POSITION_SL is the basket stop S — read fresh from the broker
   // (not cached) each call, so this is correct even after an EA/terminal
   // reload. Every own leg (including the just-opened add, which has no SL
   // yet) is then modified to the same new stop N; each leg's TP is left
   // as-is.
   void RatchetBasketStop()
     {
      // Collect every own leg (magic+symbol match), oldest first by ticket.
      ulong  tickets[];
      double entries[];
      double sls[];
      bool   buys[];
      double tps[];
      int    count = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         count++;
         ArrayResize(tickets, count);
         ArrayResize(entries, count);
         ArrayResize(sls, count);
         ArrayResize(buys, count);
         ArrayResize(tps, count);
         tickets[count - 1] = tk;
         entries[count - 1] = PositionGetDouble(POSITION_PRICE_OPEN);
         sls[count - 1]     = PositionGetDouble(POSITION_SL);
         buys[count - 1]    = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
         tps[count - 1]     = PositionGetDouble(POSITION_TP);
        }
      if(count == 0) return;                      // no own positions

      // Insertion sort by ticket ascending (oldest first) — basket sizes are
      // small (bounded by m_maxPos), O(n^2) is plenty and keeps this
      // dependency-free.
      for(int i = 1; i < count; i++)
        {
         ulong tk = tickets[i]; double en = entries[i]; double sl = sls[i];
         bool  bd = buys[i];    double tp = tps[i];
         int j = i - 1;
         while(j >= 0 && tickets[j] > tk)
           {
            tickets[j + 1] = tickets[j]; entries[j + 1] = entries[j];
            sls[j + 1] = sls[j]; buys[j + 1] = buys[j]; tps[j + 1] = tps[j];
            j--;
           }
         tickets[j + 1] = tk; entries[j + 1] = en; sls[j + 1] = sl;
         buys[j + 1] = bd; tps[j + 1] = tp;
        }

      ulong  oldestTicket = tickets[0];
      double E = entries[0];
      double S = sls[0];
      bool   isBuy = buys[0];
      if(S == 0)
        {
         Print("TradeManager: ratchet skipped — oldest leg #", oldestTicket, " has no stop");
         return;                                 // never invent a stop
        }

      int k = count - 1;                          // index (and count) of adds beyond the original
      int addCount = k;
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
         // add2+: lagging halfway-entry ladder — midpoint of the two legs
         // BEFORE the newest one.
         N = NormalizeDouble((entries[k - 2] + entries[k - 1]) / 2.0, _Digits);
         // Only tighten, never loosen.
         bool advances = isBuy ? (N > S) : (N < S);
         if(!advances) return;
        }

      for(int i = 0; i < count; i++)
        {
         if(!m_trade.PositionModify(tickets[i], N, tps[i]))
            Print("TradeManager: ratchet move failed #", tickets[i],
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

   // Public read of the current basket's sticky mode ("adr"/"fixed"), for
   // callers outside TradeManager that report trade events it didn't
   // originate (e.g. the EA's OnTradeTransaction handler for a broker-side
   // SL/TP close).
   string EntryModeStr() { return CurrentEntryModeStr(); }

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
      string entryModeStr = CurrentEntryModeStr();
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
         m_sink.OnTradeEvent("close", dir, totalLots, price, 0.0, reason, 0, basketProfit, 0.0,
                             true, entryModeStr);
        }
     }

   // Returns true ONLY when an order was actually opened (used by the EA to
   // detect AUTO-mode entry rejections). EXIT closes rather than opens, so
   // it returns false; every blocked/no-op path returns false too.
   // entryModeFixed/fixedLots are passed per-call (trailing defaulted params,
   // same style as the existing stopPrice default) rather than threaded
   // through Init as persistent members: the runtime mode can change every
   // heartbeat (5s) while a basket can sit open for hours, so the EA already
   // has to re-evaluate "which mode applies to a NEW entry" at every
   // OnSignal call site anyway (AUTO's ProcessBar call and the MANUAL
   // Telegram-execute call in OnTimer) — passing it in keeps TradeManager
   // stateless about the runtime switch and makes "the reversal path uses
   // the CURRENT runtime mode for the new basket" automatic (same call,
   // same fresh param) rather than requiring a setter call ordered before
   // every OnSignal.
   bool OnSignal(ENUM_SIGNAL sig, double atr_value, double stopPrice = 0,
                bool entryModeFixed = false, double fixedLots = 0.0)
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
      double lots = entryModeFixed ? ClampToVolume(fixedLots) : m_risk.CalcLots(sl_points, m_ratios[0]);
      if(lots <= 0) return false;
      bool ok = (sig == SIGNAL_BUY) ? m_trade.Buy(lots, _Symbol, 0, sl)
                                    : m_trade.Sell(lots, _Symbol, 0, sl);
      if(ok)
        {
         m_lastEntryPrice = price;
         GlobalVariableSet(CycleKey(), AccountInfoDouble(ACCOUNT_BALANCE));
         GlobalVariableSet(PeakKey(), 0);
         GlobalVariableSet(BasketModeKey(), entryModeFixed ? 1 : 0);
         GlobalVariableSet(TpAlertKey(), 0);   // fresh basket, alert re-armed
         if(m_sink != NULL)
           {
            string dir = (sig == SIGNAL_BUY) ? "BUY" : "SELL";
            string openReason = wasReversal ? "reversal" : ("signal " + dir);
            m_sink.OnTradeEvent("open", dir, lots, price, sl, openReason,
                                (long)m_trade.ResultOrder(), 0.0,
                                entryModeFixed ? 0.0 : BasketTargetPrice(),
                                true, entryModeFixed ? "fixed" : "adr");
           }
        }
      return ok;
     }

   void Manage(double atr_value, bool conditionStillTrue)
     {
      int n = CountOwn();
      if(n == 0) return;
      // FIXED baskets skip adds + profit target + profit lock entirely —
      // pure trend ride to a confirmed reversal or the shared stop, neither
      // of which lives in Manage() (reversal is OnSignal's close-and-open;
      // the stop is broker-side on each leg). Nothing below this line has a
      // consumer without the lock/target/pyramid it belongs to (peak
      // tracking below exists only to feed the lock), so returning here is
      // a full skip, not a partial one — except the one-time TARGET ALERT:
      // the ride still WATCHES the ADR target level and tells the owner
      // (Telegram, with a tap-to-exit button) the first time it's crossed.
      // The ride itself continues; only the owner's tap ends it early.
      if(GlobalVariableGet(BasketModeKey()) > 0.5)
        {
         double cycleBalFixed = GlobalVariableGet(CycleKey());
         if(m_targetPct > 0 && cycleBalFixed > 0 &&
            GlobalVariableGet(TpAlertKey()) < 0.5 &&
            BasketProfit() >= cycleBalFixed * m_targetPct / 100.0)
           {
            // Flag BEFORE the sink call: a notify hiccup must not re-alert
            // every bar (fail-open — one shot per basket, delivered or not).
            GlobalVariableSet(TpAlertKey(), 1);
            if(m_sink != NULL)
               m_sink.OnTargetAlert(BasketProfit());
           }
         return;
        }
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
      // Daily loss brake gates adds too — an add is NEW exposure. Manage()
      // deliberately bypasses CanEnter (window/exposure/spread must not
      // strand a live basket mid-management), so this one money gate is
      // checked explicitly. Exits above are never blocked by it.
      if(m_risk.DailyLossBreached())
        { Print("TradeManager: pyramid add blocked — daily loss limit"); return; }
      // Same reasoning for the news blackout: an add is new exposure around a
      // high-impact release. Exits above are never blocked by it either.
      if(m_risk.NewsBlackout())
        { Print("TradeManager: pyramid add blocked — news blackout"); return; }
      double sl_points = m_stopAtrMult * atr_value / _Point;
      double lots = m_risk.CalcLots(sl_points, m_ratios[MathMin(n, 2)]);
      if(lots <= 0) return;
      // Cap each add at 70% of the previous (newest existing) own leg's
      // size — adds must never outweigh veterans. Growing late legs were the
      // root cause of two retracement losses. Re-apply the broker's lot
      // step/min floor the same way CalcLots does after the cap; the
      // min-lot floor may override the shrink on very small volumes —
      // acceptable, it's the broker's hard minimum.
      ulong  newestTicket = 0;
      double prevLots = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0 || PositionGetInteger(POSITION_MAGIC) != m_magic ||
            PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(newestTicket == 0 || tk > newestTicket)
           { newestTicket = tk; prevLots = PositionGetDouble(POSITION_VOLUME); }
        }
      lots = MathMin(lots, NormalizeDouble(prevLots * 0.7, 2));
      double volStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double volMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      lots = MathFloor(lots / volStep) * volStep;
      if(lots < volMin) lots = volMin;
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
                                (long)m_trade.ResultOrder(), 0.0, BasketTargetPrice(),
                                true, CurrentEntryModeStr());
           }
        }
     }
  };
#endif
