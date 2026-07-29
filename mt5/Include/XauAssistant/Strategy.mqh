// Strategy.mqh — the ONLY file the real strategy rules will touch.
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
public:
   // Called once per closed bar. Stub until the documented rules are extracted.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
  };
#endif
