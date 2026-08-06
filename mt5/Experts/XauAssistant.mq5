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
#include <XauAssistant/TradeBoxes.mqh>
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
input double         ProfitTargetPct        = 2.0;  // basket banks at +2% of cycle balance; 0 = off
input double         TrailLockPct           = 50;  // keep this % of peak basket profit once armed; 0 = off
input double         TrailActivateR         = 1.0; // arm lock when peak profit >= this multiple of the per-trade risk budget
input double         StopAtrMult            = 2.0;
input double         MaxSpreadPoints        = 500;
input int            TradingWindowStartHour = 4;   // server time; skips rollover (23-01) + thin Tokyo open (01-04)
input int            TradingWindowEndHour   = 23;  // rollover/maintenance 23-01 stays excluded (hostile spreads)
input int            MaxDailyExposureMin    = 180; // ~3-4 trades/day across the 04-23 window
input int            FlattenBeforeBreakMin  = 5;   // close ALL positions this many min before the 23:59 break; 0 = off
input double         AdxTrendThreshold      = 10.0; // near-permissive: week sweep 2026-08-06 showed 10 beats 20/25 on P/L AND drawdown; blocks only dead-flat tape
input bool           DebugFireTestSignal    = false;
input long           MagicNumber            = 20260729;
input bool           ApplyChartTheme        = true;

input string ActiveStrategy = "halftrend_ema_v1"; // which registered strategy trades
input int    HtAmplitude    = 4;                  // Half Trend amplitude
input int    EmaLength      = 55;                 // confirmation EMA
input int    ConfirmCloses  = 1;                  // consecutive closes beyond EMA (1 suits XAU; 2 was tuned for crypto volatility)
input double StopBufferATR  = 0.75;               // pad wick stop by k*ATR(14); 0 = exact wick (old behavior)
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
CTradeBoxes    g_tradeBoxes;
int            g_atrHandle = INVALID_HANDLE;
datetime       g_lastBar = 0;
bool           g_debugFired = false;
string         g_pendingSwitch = "";
ENUM_EXEC_MODE g_execMode = EXEC_MANUAL;

// Bridges TradeManager (strategy-agnostic) to the UI service: reads the
// active strategy id at call time, posts the event, then screenshots the
// chart for open/close. Every step here is best-effort — see UiApi.mqh.
class CUiSink : public CTradeEventSink
  {
public:
   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0, double tp = 0.0,
                             bool isFinal = true)
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
      // The caller (TradeManager.CloseAll, or OnTradeTransaction for a
      // broker-side per-leg SL/TP close) is the authority on whether this
      // close empties the basket, via the `isFinal` parameter -- defaults
      // true so callers that don't pass it (CloseAll always empties the
      // whole basket) keep today's behavior.
      bool basketGone = (event != "close") || isFinal;
      // Chart risk/reward boxes: "open" starts the current basket's box,
      // and only the basket-FINAL close (same basketGone gate as the
      // strategy bookkeeping below) freezes it — a partial leg close of a
      // pyramided basket must not prematurely close out the box.
      if(event == "open")
         g_tradeBoxes.OnOpen(ticket, dir, price, sl);
      else if(event == "close" && basketGone)
         g_tradeBoxes.OnClose(price);
      if(event == "close" && active != NULL && basketGone)
        {
         ENUM_SIGNAL closedDir = (dir == "BUY")  ? SIGNAL_BUY  :
                                 (dir == "SELL") ? SIGNAL_SELL : SIGNAL_NONE;
         active.OnBasketClosed(closedDir);
        }
      long id = g_ui.PostTradeEvent(event, strategyId, dir, lots, price, sl, reason, ticket,
                                    profit, tp, basketGone);
      if(id < 0) return;
      if(event == "open" || (event == "close" && basketGone))
         g_ui.UploadScreenshot(id);
     }
  };
CUiSink g_uiSink;

void ApplyDarkTheme()
  {
   ChartSetInteger(0, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(0, CHART_COLOR_BACKGROUND, C'19,23,34');
   ChartSetInteger(0, CHART_COLOR_FOREGROUND, clrSilver);
   ChartSetInteger(0, CHART_COLOR_GRID, C'42,46,57');
   ChartSetInteger(0, CHART_COLOR_CHART_UP, clrMediumSeaGreen);
   ChartSetInteger(0, CHART_COLOR_CHART_DOWN, clrIndianRed);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BULL, clrMediumSeaGreen);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BEAR, clrIndianRed);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE, clrSilver);
   ChartSetInteger(0, CHART_COLOR_VOLUME, C'42,46,57');
   ChartSetInteger(0, CHART_SHOW_GRID, true);
   ChartSetInteger(0, CHART_SHOW_VOLUMES, CHART_VOLUME_HIDE);
   ChartSetInteger(0, CHART_SHOW_PERIOD_SEP, false);
   ChartRedraw();
  }

int OnInit()
  {
   if(ExecutionMode == EXEC_AUTO && !AllowLiveTrading &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     {
      g_alerts.Notify("XauAssistant: AUTO on LIVE account blocked (AllowLiveTrading=false)");
      return INIT_FAILED;
     }
   g_execMode = ExecutionMode;
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   g_registry.Register(new CHalfTrendEmaStrategy(HtAmplitude, EmaLength, ConfirmCloses, StopBufferATR));
   g_registry.Register(new CBollStochRsiStrategy(BbPeriod, BbDev, TrendCloses,
                       SqueezeLookback, SqueezePctile, ExpansionBars,
                       RsiPeriod, StochPeriod, KSmooth, DSmooth));
   if(!g_registry.SetActive(ActiveStrategy))
     {
      g_alerts.Notify("XauAssistant: unknown ActiveStrategy '" + ActiveStrategy + "'");
      return INIT_FAILED;
     }
   if(ApplyChartTheme) ApplyDarkTheme();
   g_registry.Active().EnablePaint(true);
   g_api.Init(ApiUrl, ApiTimeoutMs);
   g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber);
   g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold,
               TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin);
   g_trades.Init(&g_risk, MagicNumber, EnablePyramiding, MaxPositions,
                 AddTriggerATR, ProfitTargetPct, StopAtrMult,
                 TrailLockPct, TrailActivateR, &g_uiSink);
   // Re-arm chart box tracking if a basket was already open before this
   // OnInit (recompile auto-reload, terminal restart, chart re-attach) —
   // otherwise the live box would never receive its final OnClose.
   g_tradeBoxes.RecoverFromPositions(MagicNumber);
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

// Fires every HeartbeatSec seconds regardless of bar boundaries: posts
// state, stashes a pending switch id, and — when the service hands back a
// MANUAL-mode command approved over Telegram ("execute"/"close_all") —
// executes it (guarded by the same live-account check OnInit enforces for
// AUTO) and reports the outcome back via PostProposalResult.
// Server-day of the last pre-break flatten, so it fires at most once per day.
datetime g_lastFlattenDay = 0;

// Close everything shortly BEFORE the daily 23:59-01:00 maintenance break
// (covers Friday close too — same wall-clock). Positions left open into the
// break cannot be closed ([market closed]) and sit exposed through the gap;
// the user mandate is: never hold through a break.
void FlattenBeforeBreak()
  {
   if(FlattenBeforeBreakMin <= 0) return;
   if(g_trades.OpenCount() == 0) return;
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.hour != 23 || dt.min < 59 - FlattenBeforeBreakMin) return;
   datetime day = TimeCurrent() - (TimeCurrent() % 86400);
   if(day == g_lastFlattenDay) return;
   g_trades.CloseAll("pre-break flatten");
   // Mark done only when truly flat — otherwise retry on the next 5s tick
   // while the market is still open.
   if(g_trades.OpenCount() == 0)
     {
      g_lastFlattenDay = day;
      g_ui.PostNotify("🌙 Pre-break flatten: all positions closed before the market break");
     }
  }

void OnTimer()
  {
   FlattenBeforeBreak();
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double floating_pl = equity - balance;
   CStrategy *active  = g_registry.Active();
   string activeId    = (active != NULL) ? active.Id() : "unknown";
   double spreadPts   = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   string mode = "", cmd = "", cmdDir = "";
   long cmdId = 0;
   bool algoTrading = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0;
   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId, algoTrading,
                                  mode, cmd, cmdId, cmdDir);
   if(sw != "") g_pendingSwitch = sw;

   if(mode == "auto" || mode == "manual")
     {
      ENUM_EXEC_MODE want = (mode == "auto") ? EXEC_AUTO : EXEC_MANUAL;
      if(want == EXEC_AUTO && !AllowLiveTrading &&
         AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
        {
         if(g_execMode != EXEC_MANUAL)
            g_alerts.Notify("AUTO refused on live account (AllowLiveTrading=false)");
         g_execMode = EXEC_MANUAL;
        }
      else if(want != g_execMode)
        {
         g_execMode = want;
         Print("XauAssistant: execution mode -> ", mode);
        }
     }

   if(cmd == "execute" && (cmdDir == "BUY" || cmdDir == "SELL"))
     {
      // Mirrors the OnInit/OnTimer-mode-switch live-account guard: a
      // Telegram-approved MANUAL command must not be able to open a
      // real-money order when the account is real and AllowLiveTrading is
      // off, regardless of what the service side thinks the exec mode is.
      if(!AllowLiveTrading && AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
        {
         g_ui.PostProposalResult(cmdId, false, "live trading not allowed");
        }
      else
        {
         string why = "";
         if(!g_risk.CanEnter(why))
           {
            g_ui.PostProposalResult(cmdId, false, why);
           }
         else
           {
            ENUM_SIGNAL dir = (cmdDir == "BUY") ? SIGNAL_BUY : SIGNAL_SELL;
            double atrBuf[];
            double atrVal = (CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) == 1) ? atrBuf[0] : 0;
            CStrategy *act = g_registry.Active();
            bool opened = false;
            if(atrVal > 0 && act != NULL)
               opened = g_trades.OnSignal(dir, atrVal, act.StopPrice(dir));
            bool ok = opened || g_trades.BasketDirection() == dir;
            g_ui.PostProposalResult(cmdId, ok,
                                    ok ? "opened" : "blocked by risk checks");
           }
        }
     }
   else if(cmd == "close_all")
     {
      int before = g_trades.OpenCount();
      g_trades.CloseAll("remote exit");
      int left = g_trades.OpenCount();
      // Honest partial-close reporting: during the daily maintenance break
      // (or off quotes) some legs can be rejected [market closed]; the user
      // must see that legs remain rather than a false "closed".
      if(left == 0)
         g_ui.PostProposalResult(cmdId, true, "basket closed");
      else
         g_ui.PostProposalResult(cmdId, false,
            StringFormat("partial close - %d of %d legs still open (market closed?), retry shortly",
                         left, before));
     }
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   CStrategy *a = g_registry.Active();
   if(a != NULL) a.ClearPaint();
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

   // A leg stop-out with survivors still in the basket is NOT the basket's
   // final close -- OpenCount() reflects live state right after this deal
   // landed, so it tells us whether any own positions remain.
   g_uiSink.OnTradeEvent("close", dir, lots, price, 0.0, reason, (long)trans.deal, profit, 0.0,
                         g_trades.OpenCount() == 0);
  }

void ProcessBar()
  {
   // Apply any pending remote strategy switch only at the bar boundary.
   if(g_pendingSwitch != "")
     {
      string sw = g_pendingSwitch;
      g_pendingSwitch = "";
      // Capture the OLD active strategy before SetActive runs — once SetActive
      // succeeds, g_registry.Active() returns the NEW strategy, so calling
      // EnablePaint(false) via a post-switch Active() lookup would wrongly
      // target the new strategy instead of clearing the old one's paint.
      CStrategy *oldActive = g_registry.Active();
      if(g_registry.SetActive(sw))
        {
         if(oldActive != NULL) oldActive.EnablePaint(false);
         g_registry.Active().EnablePaint(true);
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

   if(g_trades.OpenCount() > 0) g_tradeBoxes.OnBarUpdate();

   // AUTO mode executes FIRST — the AI is never in the trade path (spec 2.2)
   if(g_execMode == EXEC_AUTO && atrVal > 0)
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
        {
         active.OnEntryRejected(sig);
         string why = "";
         if(!g_risk.CanEnter(why)) { /* why set */ }
         else why = "order send failed (check Algo Trading button / broker)";
         g_ui.PostNotify("🚫 AUTO " + SignalToString(sig) + " not executed: " + why);
        }
      ENUM_SIGNAL basketDir = g_trades.BasketDirection();
      g_trades.Manage(atrVal, active.ConditionStillTrue(basketDir == SIGNAL_NONE ? sig : basketDir));
     }
   // MANUAL-mode baskets (opened via a Telegram-approved "execute" command
   // in OnTimer) never pass through the AUTO branch above, so without this
   // they'd get no profit-target close, no breakeven-on-add, and no
   // pyramiding — Manage() must still run for them every bar. Note this can
   // pyramid-add into a winning MANUAL basket just like AUTO does; that's
   // existing, accepted Manage() behavior, not new risk introduced here.
   else if(g_execMode == EXEC_MANUAL && g_trades.OpenCount() > 0 && atrVal > 0)
     {
      g_trades.Manage(atrVal, active.ConditionStillTrue(g_trades.BasketDirection()));
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
