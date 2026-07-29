#property copyright "xau-assistant"
#property version   "0.1"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>

enum ENUM_EXEC_MODE { EXEC_MANUAL, EXEC_AUTO };

input ENUM_EXEC_MODE ExecutionMode          = EXEC_MANUAL;
input bool           AllowLiveTrading       = false;
input string         ApiUrl                 = "http://127.0.0.1:8000/analyze";
input int            ApiTimeoutMs           = 3000;
input double         RiskPerTradePct        = 0.5;
input double         MaxDrawdownPct         = 10.0;
input bool           EnablePyramiding       = true;
input int            MaxPositions           = 3;
input double         AddTriggerATR          = 1.0;
input double         ProfitTargetPct        = 2.0;
input double         StopAtrMult            = 2.0;
input double         MaxSpreadPoints        = 500;
input int            TradingWindowStartHour = 15;
input int            TradingWindowEndHour   = 18;
input int            MaxDailyExposureMin    = 60;
input double         AdxTrendThreshold      = 25.0;
input bool           DebugFireTestSignal    = false;
input long           MagicNumber            = 20260729;

CStrategy g_strategy;
CAlerts   g_alerts;
datetime  g_lastBar = 0;
bool      g_debugFired = false;

int OnInit()
  {
   if(ExecutionMode == EXEC_AUTO && !AllowLiveTrading &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     {
      g_alerts.Notify("XauAssistant: AUTO on LIVE account blocked (AllowLiveTrading=false)");
      return INIT_FAILED;
     }
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_lastBar) return;   // act once per new bar
   g_lastBar = bar;
   ProcessBar();
  }

void ProcessBar()
  {
   ENUM_SIGNAL sig = g_strategy.Evaluate();
   if(DebugFireTestSignal && !g_debugFired) { sig = SIGNAL_BUY; g_debugFired = true; }
   if(sig == SIGNAL_NONE) return;
   g_alerts.Draw(sig, "pipeline test");
   g_alerts.Notify("XauAssistant " + _Symbol + " " + SignalToString(sig));
  }
