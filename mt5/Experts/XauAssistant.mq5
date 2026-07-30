#property copyright "xau-assistant"
#property version   "0.1"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>
#include <XauAssistant/AiApi.mqh>
#include <XauAssistant/SignalManager.mqh>
#include <XauAssistant/RiskManager.mqh>
#include <XauAssistant/TradeManager.mqh>
#include <XauAssistant/StrategyRegistry.mqh>
#include <XauAssistant/Strategies/HalfTrendEma.mqh>
#include <XauAssistant/Strategies/BollStochRsi.mqh>

enum ENUM_EXEC_MODE { EXEC_MANUAL, EXEC_AUTO };

input ENUM_EXEC_MODE ExecutionMode          = EXEC_MANUAL;
input bool           AllowLiveTrading       = false;
input string         ApiUrl                 = "http://127.0.0.1:9000/analyze";
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

input string ActiveStrategy = "halftrend_ema_v1"; // which registered strategy trades
input int    HtAmplitude    = 4;                  // Half Trend amplitude
input int    EmaLength      = 55;                 // confirmation EMA
input int    ConfirmCloses  = 2;                  // consecutive closes beyond EMA
input int    BbPeriod        = 20;   // boll_stochrsi: Bollinger period
input double BbDev           = 2.0;  // boll_stochrsi: Bollinger deviation
input int    TrendCloses     = 2;    // boll_stochrsi: closes in trend zone
input int    SqueezeLookback = 100;  // boll_stochrsi: bandwidth history bars
input double SqueezePctile   = 25;   // boll_stochrsi: squeeze percentile
input int    ExpansionBars   = 2;    // boll_stochrsi: rising bars to confirm expansion
input int    RsiPeriod       = 14;   // boll_stochrsi: RSI period
input int    StochPeriod     = 14;   // boll_stochrsi: stochastic window over RSI
input int    KSmooth         = 3;    // boll_stochrsi: %K smoothing
input int    DSmooth         = 3;    // boll_stochrsi: %D smoothing

CStrategyRegistry g_registry;
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
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   g_registry.Register(new CHalfTrendEmaStrategy(HtAmplitude, EmaLength, ConfirmCloses));
   g_registry.Register(new CBollStochRsiStrategy(BbPeriod, BbDev, TrendCloses,
                       SqueezeLookback, SqueezePctile, ExpansionBars,
                       RsiPeriod, StochPeriod, KSmooth, DSmooth));
   if(!g_registry.SetActive(ActiveStrategy))
     {
      g_alerts.Notify("XauAssistant: unknown ActiveStrategy '" + ActiveStrategy + "'");
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

void OnDeinit(const int reason) { g_registry.Clear(); }

void ProcessBar()
  {
   CStrategy *active = g_registry.Active();
   ENUM_SIGNAL sig = SIGNAL_NONE;
   string shadowIds[];
   ENUM_SIGNAL shadowSigs[];
   for(int i = 0; i < g_registry.Count(); i++)
     {
      CStrategy *st = g_registry.Get(i);
      ENUM_SIGNAL s = st.Evaluate();     // every strategy evaluates every bar
      if(st == active) { sig = s; continue; }
      if(s == SIGNAL_NONE) continue;
      int n = ArraySize(shadowIds);
      ArrayResize(shadowIds, n + 1);
      ArrayResize(shadowSigs, n + 1);
      shadowIds[n] = st.Id();
      shadowSigs[n] = s;
     }
   if(DebugFireTestSignal && !g_debugFired) { sig = SIGNAL_BUY; g_debugFired = true; }

   g_risk.OnBarUpdate();
   double atrBuf[];
   double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;

   // AUTO mode executes FIRST — the AI is never in the trade path (spec 2.2)
   if(ExecutionMode == EXEC_AUTO && atrVal > 0)
     {
      g_trades.OnSignal(sig, atrVal, active.StopPrice(sig));
      ENUM_SIGNAL basketDir = g_trades.BasketDirection();
      g_trades.Manage(atrVal, active.ConditionStillTrue(basketDir == SIGNAL_NONE ? sig : basketDir));
     }

   if(sig == SIGNAL_NONE && ArraySize(shadowIds) == 0)
     {
      AiResponse quiet;
      g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, quiet);
      return;   // keeps outcome-resolution data flowing (spec 6.3)
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, r);
   if(sig == SIGNAL_NONE) return;        // shadows logged; nothing to alert
   string report = g_sm.BuildReport(sig, r, ok) + "\n" + g_risk.Status();
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
  }
