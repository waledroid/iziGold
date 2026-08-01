#property copyright "xau-assistant"
#property version   "1.00"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>
#include <XauAssistant/AiApi.mqh>
#include <XauAssistant/UiApi.mqh>
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
input string         UiBaseUrl              = "http://127.0.0.1:9000";
input int            HeartbeatSec           = 5;
input int            UiTimeoutMs            = 1000;
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
CUiApi         g_ui;
CSignalManager g_sm;
CRiskManager   g_risk;
CTradeManager  g_trades;
int            g_atrHandle = INVALID_HANDLE;
datetime       g_lastBar = 0;
bool           g_debugFired = false;
string         g_pendingSwitch = "";

// Bridges TradeManager (strategy-agnostic) to the UI service: reads the
// active strategy id at call time, posts the event, then screenshots the
// chart for open/close. Every step here is best-effort — see UiApi.mqh.
class CUiSink : public CTradeEventSink
  {
public:
   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0)
     {
      CStrategy *active = g_registry.Active();
      string strategyId = (active != NULL) ? active.Id() : "unknown";
      // A "close" event fires once per closed position/deal, but a
      // pyramided basket can stop out ONE LEG AT A TIME — each own
      // position carries its own broker-side breakeven SL (see
      // OnTradeTransaction below) — so a "close" here does not always mean
      // the whole basket is gone. Gate the sync hook and the screenshot on
      // g_trades.OpenCount() == 0 (no own positions left) so a partial
      // stop-out doesn't prematurely reset the active strategy's virtual
      // position or screenshot a basket that is still open; the final leg's
      // close event still fires the hook/screenshot once the basket is
      // actually empty. Every close event is still posted to the UI below
      // regardless of this gate, so per-deal telemetry/P&L is never lost.
      // Direction-matched (not unconditional) so a reversal's synchronous
      // close of the OLD basket can't clobber a virtual position a
      // strategy already flipped to the NEW direction this same bar.
      bool basketGone = (event != "close") || (g_trades.OpenCount() == 0);
      if(event == "close" && active != NULL && basketGone)
        {
         ENUM_SIGNAL closedDir = (dir == "BUY")  ? SIGNAL_BUY  :
                                 (dir == "SELL") ? SIGNAL_SELL : SIGNAL_NONE;
         active.OnBasketClosed(closedDir);
        }
      long id = g_ui.PostTradeEvent(event, strategyId, dir, lots, price, sl, reason, ticket, profit);
      if(id < 0) return;
      if(event == "open" || (event == "close" && basketGone))
         g_ui.UploadScreenshot(id);
     }
  };
CUiSink g_uiSink;

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
   g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber);
   g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold,
               TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin);
   g_trades.Init(&g_risk, MagicNumber, EnablePyramiding, MaxPositions,
                 AddTriggerATR, ProfitTargetPct, StopAtrMult, &g_uiSink);
   g_atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14);
   EventSetTimer(HeartbeatSec);
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_lastBar) return;   // act once per new bar
   g_lastBar = bar;
   ProcessBar();
  }

// Fires every HeartbeatSec seconds regardless of bar boundaries; only ever
// posts state and stashes a pending switch id — never touches trading state.
void OnTimer()
  {
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double floating_pl = equity - balance;
   CStrategy *active  = g_registry.Active();
   string activeId    = (active != NULL) ? active.Id() : "unknown";
   double spreadPts   = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId);
   if(sw != "") g_pendingSwitch = sw;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   g_registry.Clear();
  }

// Reports broker-side stop-loss / take-profit closes that never pass through
// TradeManager (e.g. the price touches SL/TP while nothing here calls
// CloseAll). Every other close reason is already reported by CTradeManager
// via the sink, so anything not DEAL_REASON_SL/TP is skipped here to avoid
// double-reporting. Pure telemetry: every path either returns early or ends
// in a best-effort sink call, never touches trading state or blocks OnTick.
// Scope: this handler only recognizes DEAL_ENTRY_OUT (hedging-mode closing
// deals, one per own position). DEAL_ENTRY_OUT_BY (netting-mode offsetting
// deals) is out of scope — this EA's own positions are opened/managed under
// hedging accounting, so OUT_BY deals should not occur here; if netting
// support is ever added, this handler needs a matching branch for it.
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0) return;
   if(!HistoryDealSelect(trans.deal)) return;

   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;
   if((long)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != MagicNumber) return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;

   ENUM_DEAL_REASON dealReason = (ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal, DEAL_REASON);
   string reason;
   if(dealReason == DEAL_REASON_SL)      reason = "stop-loss";
   else if(dealReason == DEAL_REASON_TP) reason = "profit target";
   else return;   // every other reason is TradeManager-initiated and already reported

   // The deal that closes a position carries the opposite type of the
   // position itself — a SELL deal closes a BUY position — so invert it to
   // report the position's own direction, matching every other trade event.
   long dealType = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   string dir = (dealType == DEAL_TYPE_SELL) ? "BUY" : "SELL";
   double price  = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   double lots   = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);

   g_uiSink.OnTradeEvent("close", dir, lots, price, 0.0, reason, (long)trans.deal, profit);
  }

void ProcessBar()
  {
   // Apply any pending remote strategy switch only at the bar boundary.
   if(g_pendingSwitch != "")
     {
      string sw = g_pendingSwitch;
      g_pendingSwitch = "";
      if(g_registry.SetActive(sw))
        {
         Print("XauAssistant: switched active strategy to '", sw, "'");
         g_alerts.Notify("switched to " + sw);
        }
      else
         Print("XauAssistant: remote switch requested unknown strategy id '", sw, "'");
     }

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
      bool opened = g_trades.OnSignal(sig, atrVal, active.StopPrice(sig));
      // A same-direction signal into an already-open basket is a legitimate
      // early return (false) — not a rejection, since the strategy's
      // virtual position is still validly tracking that basket (basket
      // direction == sig). Two failure shapes ARE a rejection, both
      // captured by "basket direction != sig": (a) nothing opened at all
      // (BasketDirection() == SIGNAL_NONE, which never equals a BUY/SELL
      // sig); (b) reversal-abort — CloseAll left the OLD-direction basket
      // partly open and no new position opened, so the real basket is
      // still the opposite direction of sig. Either way the strategy needs
      // to drop its virtual position rather than believe it holds sig.
      if(!opened && (sig == SIGNAL_BUY || sig == SIGNAL_SELL) && g_trades.BasketDirection() != sig)
         active.OnEntryRejected(sig);
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
