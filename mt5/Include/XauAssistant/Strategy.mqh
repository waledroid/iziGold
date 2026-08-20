// Strategy.mqh — strategy interface. Concrete strategies live in
// Include/XauAssistant/Strategies/ and register in the EA's OnInit.
#ifndef XAU_STRATEGY_MQH
#define XAU_STRATEGY_MQH

enum ENUM_SIGNAL { SIGNAL_NONE, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_EXIT };

string SignalToString(ENUM_SIGNAL s)
  {
   switch(s)
     {
      case SIGNAL_BUY:  return "BUY";
      case SIGNAL_SELL: return "SELL";
      case SIGNAL_EXIT: return "EXIT";
     }
   return "NONE";
  }

class CStrategy
  {
protected:
   bool m_paint;
public:
   CStrategy() : m_paint(false) {}
   // Painting: the EA enables this on the ACTIVE strategy only. Strategies
   // that support it draw their indicator state per closed bar; default no-op.
   virtual void EnablePaint(bool on) { m_paint = on; if(!on) ClearPaint(); }

   // The higher-timeframe verdict behind the current signal, for the trade
   // log: 1 agreed, 0 refused, -1 not evaluated / strategy has no HTF gate.
   // Default -1 so strategies without one need not implement it.
   virtual int LastHtfAgree() const { return -1; }
   virtual void ClearPaint() {}
   // Stable identifier — flows through the API into SQLite per-strategy stats.
   virtual string      Id() { return "stub"; }
   // Called once per closed bar.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool        ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
   // Strategy's preferred stop for a new position; 0 = use the ATR default.
   virtual double      StopPrice(ENUM_SIGNAL dir) { return 0.0; }
   // Fired by the EA sink on every basket "close" event (TradeManager closes
   // and Task-5 stop-loss/TP transactions), for the ACTIVE strategy only.
   // closedDir is the direction of the basket that just closed (SIGNAL_BUY/
   // SIGNAL_SELL, or SIGNAL_NONE if the event carried no recognizable
   // direction). Direction-matched so a reversal's close of the OLD basket
   // doesn't clobber a virtual position a strategy already flipped to the
   // NEW direction for the position about to open in the same OnSignal call.
   virtual void        OnBasketClosed(ENUM_SIGNAL closedDir) {}
   // Fired by the EA (AUTO mode only) when a BUY/SELL signal failed to open
   // a position (blocked, no lots, order-send failure) and no basket is
   // open afterward — i.e. the rejection left nothing tracking the signal.
   virtual void        OnEntryRejected(ENUM_SIGNAL dir) {}
  };
#endif
