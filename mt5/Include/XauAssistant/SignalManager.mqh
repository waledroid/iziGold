#ifndef XAU_SIGNALMANAGER_MQH
#define XAU_SIGNALMANAGER_MQH
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/AiApi.mqh>

class CSignalManager
  {
public:
   string BuildReport(ENUM_SIGNAL sig, AiResponse &r, bool api_ok)
     {
      string head = _Symbol + " " + SignalToString(sig);
      if(!api_ok || !r.ai_available)
         return head + " | AI unavailable — strategy signal stands (fail-open)";
      return head + " | AI: " + r.direction + " " +
             DoubleToString(r.confidence * 100, 0) + "% (" + r.verdict + ")" +
             " | regime: " + r.regime + " | mode: " + r.mode;
     }
  };
#endif
