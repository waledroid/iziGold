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
public:
   // Stable identifier — flows through the API into SQLite per-strategy stats.
   virtual string      Id() { return "stub"; }
   // Called once per closed bar.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool        ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
   // Strategy's preferred stop for a new position; 0 = use the ATR default.
   virtual double      StopPrice(ENUM_SIGNAL dir) { return 0.0; }
  };
#endif
