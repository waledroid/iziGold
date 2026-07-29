#ifndef XAU_ALERTS_MQH
#define XAU_ALERTS_MQH
#include <XauAssistant/Strategy.mqh>

class CAlerts
  {
public:
   void Draw(ENUM_SIGNAL sig, string grade)
     {
      if(sig == SIGNAL_NONE) return;
      string name = "xau_sig_" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      int    code  = (sig == SIGNAL_BUY) ? 233 : (sig == SIGNAL_SELL) ? 234 : 251;
      color  clr   = (sig == SIGNAL_BUY) ? clrLime : (sig == SIGNAL_SELL) ? clrRed : clrYellow;
      if(ObjectCreate(0, name, OBJ_ARROW, 0, TimeCurrent(), price))
        {
         ObjectSetInteger(0, name, OBJPROP_ARROWCODE, code);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
         ObjectSetString(0, name, OBJPROP_TOOLTIP, grade);
        }
     }
   void Notify(string text) { Alert(text); Print(text); }
  };
#endif
