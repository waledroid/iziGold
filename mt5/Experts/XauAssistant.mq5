#property copyright "xau-assistant"
#property version   "0.1"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>
#include <XauAssistant/AiApi.mqh>
#include <XauAssistant/SignalManager.mqh>
#include <XauAssistant/RiskManager.mqh>
#include <XauAssistant/TradeManager.mqh>

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

CStrategy      g_strategy;
CAlerts        g_alerts;
CAiApi         g_api;
CSignalManager g_sm;
CRiskManager   g_risk;
CTradeManager  g_trades;
int            g_atrHandle = INVALID_HANDLE;
datetime       g_lastBar = 0;
bool           g_debugFired = false;

int OnInit()
  {
   if(ExecutionMode == EXEC_AUTO && !AllowLiveTrading &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     {
      g_alerts.Notify("XauAssistant: AUTO on LIVE account blocked (AllowLiveTrading=false)");
      return INIT_FAILED;
     }
   g_api.Init(ApiUrl, ApiTimeoutMs);
   g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold,
               TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin);
   g_trades.Init(&g_risk, MagicNumber, EnablePyramiding, MaxPositions,
                 AddTriggerATR, ProfitTargetPct, StopAtrMult);
   g_atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14);
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

   g_risk.OnBarUpdate();
   double atrBuf[];
   double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;

   // AUTO mode executes FIRST — the AI is never in the trade path (spec 2.2)
   if(ExecutionMode == EXEC_AUTO && atrVal > 0)
     {
      g_trades.OnSignal(sig, atrVal);
      g_trades.Manage(atrVal, g_strategy.ConditionStillTrue(sig));
     }

   if(sig == SIGNAL_NONE)
     {
      AiResponse quiet;
      g_api.Analyze(sig, quiet);   // keeps outcome-resolution data flowing (spec 6.3)
      return;
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, r);
   string report = g_sm.BuildReport(sig, r, ok) + "\n" + g_risk.Status();
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
  }
