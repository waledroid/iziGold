#property copyright "xau-assistant"
#property version   "1.00"
#property strict

#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Alerts.mqh>
#include <XauAssistant/AiApi.mqh>
#include <XauAssistant/UiApi.mqh>
#include <XauAssistant/SignalManager.mqh>
#include <XauAssistant/NewsGuard.mqh>
#include <XauAssistant/RiskManager.mqh>
#include <XauAssistant/TradeManager.mqh>
#include <XauAssistant/TradeBoxes.mqh>
#include <XauAssistant/StrategyRegistry.mqh>
#include <XauAssistant/Strategies/HalfTrendEma.mqh>
#include <XauAssistant/Strategies/BollStochRsi.mqh>

enum ENUM_EXEC_MODE { EXEC_MANUAL, EXEC_AUTO };

input ENUM_EXEC_MODE ExecutionMode          = EXEC_AUTO;   // AUTO on attach (owner 2026-08-20). NOTE: the SERVICE is the authority — it sends `mode` every heartbeat and the EA obeys; this input only sets the mode until the first heartbeat lands.
input bool           AllowLiveTrading       = true;    // owner 2026-08-20: drag-and-go. WARNING: this is the ONLY gate stopping AUTO from trading a REAL account on attach. Set false to restore it.
input ENUM_TIMEFRAMES TradeTimeframe        = PERIOD_M5; // trading TF — chart TF is visual only
input string         ApiUrl                 = "http://127.0.0.1:9000/analyze";
input int            ApiTimeoutMs           = 3000;
input string         UiBaseUrl              = "http://127.0.0.1:9000";
input int            HeartbeatSec           = 5;
input int            UiTimeoutMs            = 1000;
input double         RiskPerTradePct        = 1.0; // raised 0.5→1.0 on 2026-08-09: week sweep +$610 vs +$421 at DD 7.2% vs 3.8%; 1.5%+ trips the kill switch
input double         MaxDrawdownPct         = 10.0;
input double         MaxDailyLossPct        = 3.0; // daily realized-loss brake (symbol+magic deals since server midnight); 0 = off
input bool           NewsGuardEnabled       = true; // block new exposure around high-importance USD calendar events (fail-open if no calendar data)
input int            NewsBlackoutMin        = 30;  // blackout radius: minutes before AND after the event
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
input int            MaxDailyExposureMin    = 360; // 6h/day: fits one long trend ride + normal trades (raised from 180 after it blocked post-win entries, 2026-08-07)
input int            FlattenBeforeBreakMin  = 5;   // close ALL positions this many min before the 23:59 break; 0 = off
input double         AdxTrendThreshold      = 10.0; // near-permissive: week sweep 2026-08-06 showed 10 beats 20/25 on P/L AND drawdown; blocks only dead-flat tape
input bool           DebugFireTestSignal    = false;
input long           MagicNumber            = 20260729;
input bool           ApplyChartTheme        = true;

enum ENUM_ENTRY_MODE { ENTRY_ADR = 0, ENTRY_FIXED = 1 };
input ENUM_ENTRY_MODE EntryMode  = ENTRY_ADR;  // ADR = 1% risk + adds/targets; FIXED = fixed lots, pure ride
input double          FixedLots  = 0.05;       // FIXED-mode entry size (broker-clamped)

input string ActiveStrategy = "halftrend_ema_v1"; // which registered strategy trades
input int    HtAmplitude    = 4;                  // Half Trend amplitude
input int    EmaLength      = 55;                 // confirmation EMA
input int    ConfirmCloses  = 2;                  // waiting bars after the HT arrow; entry bar (next) must OPEN beyond EMA, else signal dead until next flip (2 since 2026-08-20: 1 backtested worst in every window)
input double StopBufferATR  = 0.75;               // pad wick stop by k*ATR(14); 0 = exact wick (old behavior)
input bool   CatchupEnabled     = true;  // take a missed entry after downtime if still valid
input int    CatchupMaxAgeBars  = 12;    // signal at most this many trade-TF bars old
input double CatchupMaxChaseATR = 1.0;   // max adverse run beyond the signal close, in ATR(14)
input bool   HtfConfirm     = true;       // require higher-TF agreement before an entry: BUY needs price ABOVE the HTF EMA, SELL below (2026-08-20)
input ENUM_TIMEFRAMES HtfConfirmTf = PERIOD_M15;  // the agreeing timeframe (M15 while trading M5)
input int    HtfConfirmEma  = 55;        // EMA length on HtfConfirmTf, read at shift 1 (last CLOSED HTF bar)
input bool   HtfChopOnly    = true;      // run the M15 check ONLY in choppy tape; in a trend it does not gate at all (2026-08-21)
input int    HtfChopBars    = 48;        // path-efficiency window, trade-TF bars (48 = 4h of M5)
input double HtfChopEffMax  = 0.08;      // below this efficiency the tape counts as choppy; 0.06-0.10 measured as one plateau
input double HtfConfirmBufferATR = 2.0;  // price must CLEAR that EMA by this x ATR(14); 0 = side-only (pre-2026-08-20). 2.0 = middle of the 1.0-5.0 plateau that is profitable in BOTH backtest halves
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
CNewsGuard     g_news;
CRiskManager   g_risk;
CTradeManager  g_trades;
CTradeBoxes    g_tradeBoxes;
int            g_atrHandle = INVALID_HANDLE;
datetime       g_lastBar = 0;
bool           g_debugFired = false;
string         g_pendingSwitch = "";
ENUM_EXEC_MODE g_execMode = EXEC_MANUAL;
ENUM_ENTRY_MODE g_entryMode = ENTRY_ADR;

// --- Per-bar spread telemetry (ea-scope spec §3) ---------------------------
// OnTimer samples SYMBOL_SPREAD every HeartbeatSec (5 s) into an
// accumulator; when OnTick sees a new bar it snapshots the accumulator as
// the CLOSED bar's min/avg/max (posted with /analyze) and resets it for the
// forming bar. All zeros when no samples landed (fresh attach, weekend).
int    g_sprSamples = 0;
double g_sprMin = 0.0, g_sprMax = 0.0, g_sprSum = 0.0;
double g_barSprMin = 0.0, g_barSprAvg = 0.0, g_barSprMax = 0.0;

void SampleSpread(double pts)
  {
   if(g_sprSamples == 0) { g_sprMin = pts; g_sprMax = pts; g_sprSum = 0.0; }
   if(pts < g_sprMin) g_sprMin = pts;
   if(pts > g_sprMax) g_sprMax = pts;
   g_sprSum += pts;
   g_sprSamples++;
  }

void RollSpreadBar()
  {
   if(g_sprSamples > 0)
     {
      g_barSprMin = g_sprMin;
      g_barSprAvg = g_sprSum / g_sprSamples;
      g_barSprMax = g_sprMax;
     }
   else
     {
      g_barSprMin = 0.0;
      g_barSprAvg = 0.0;
      g_barSprMax = 0.0;
     }
   g_sprSamples = 0;
  }

// Bridges TradeManager (strategy-agnostic) to the UI service: reads the
// active strategy id at call time, posts the event, then screenshots the
// chart for open/close. Every step here is best-effort — see UiApi.mqh.
class CUiSink : public CTradeEventSink
  {
public:
   // FIXED-mode target alert: one Telegram notice with a tap-to-exit
   // button when the ride first crosses the ADR target. Fire-and-forget
   // (PostNotify is best-effort; TradeManager already latched the
   // once-per-basket flag before calling us).
   virtual void OnTargetAlert(double basketProfit)
     {
      g_ui.PostNotify(StringFormat(
         "🎯 FIXED ride hit the ADR target: +$%.2f. Exit now, or ignore to let it ride until the trend turns.",
         basketProfit), "exit");
     }

   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0, double tp = 0.0,
                             bool isFinal = true, string entryMode = "")
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
      // The higher-timeframe verdict belongs to the ENTRY decision, so it is
      // only meaningful on open/add rows; closes carry -1 (unknown).
      int htfAgree = -1;
      if(event != "close" && active != NULL)
         htfAgree = active.LastHtfAgree();
      long id = g_ui.PostTradeEvent(event, strategyId, dir, lots, price, sl, reason, ticket,
                                    profit, tp, basketGone, entryMode, htfAgree);
      if(id < 0) return;
      // Live close reported successfully -> the reconciler never needs to
      // re-report this deal on the next MT5/service restart. CloseAll's
      // aggregate close (reversal/EXIT/profit target/profit lock/flatten/
      // remote exit) carries no per-deal ticket (0) -- resolve it to
      // whatever just closed instead, so the watermark still advances and
      // the reconciler doesn't duplicate-report every leg of a normal
      // online exit next pass.
      if(event == "close")
        {
         if(ticket != 0)
            AdvanceReconWatermark(ticket);
         else
           {
            long newest = NewestOwnClosingDeal();
            if(newest >= 0)
               AdvanceReconWatermark(newest);
           }
        }
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

// One-time migration of login-only global-variable keys to the per-symbol
// shape XAU_<name>_<login>_<symbol> (spec 2026-08-09 §5). Defensive: the old
// key is deleted only after the new key was successfully written, and an
// already-present new key is never overwritten.
void MigrateGlobalKey(const string oldKey, const string newKey)
  {
   if(!GlobalVariableCheck(oldKey) || GlobalVariableCheck(newKey)) return;
   double v = GlobalVariableGet(oldKey);
   if(GlobalVariableSet(newKey, v) > 0)
     {
      GlobalVariableDel(oldKey);
      PrintFormat("XauAssistant: migrated global %s -> %s (value %.2f)", oldKey, newKey, v);
     }
   else
      PrintFormat("XauAssistant: FAILED migrating global %s -> %s; old key kept", oldKey, newKey);
  }

void MigrateGlobalKeys()
  {
   string login = "_" + (string)AccountInfoInteger(ACCOUNT_LOGIN);
   string names[] = {"XAU_KILL", "XAU_HWM", "XAU_CYCLE_BAL", "XAU_PEAK"};
   for(int i = 0; i < ArraySize(names); i++)
      MigrateGlobalKey(names[i] + login, names[i] + login + "_" + _Symbol);
   // Exposure keys are dated; only today's key still matters (stale dated
   // keys are inert and expire via MT5's 4-week global-variable TTL).
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   string day = StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
   MigrateGlobalKey("XAU_EXPO" + login + "_" + day,
                    "XAU_EXPO" + login + "_" + _Symbol + "_" + day);
  }

// --- Reconcile-on-reconnect --------------------------------------------
// Back-fills /trade-event close reports for own closing deals the service
// never saw (MT5 was down -> OnTradeTransaction never fired; or the
// service was down -> the live post was dropped/failed). Watermark = last
// successfully reported closing-deal ticket, persisted per login+symbol so
// terminal restarts can't reset it (spec: risk/kill-switch state pattern).
string ReconKey()
  {
   return "XAU_RECON_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + _Symbol;
  }

void AdvanceReconWatermark(long dealTicket)
  {
   if((double)dealTicket > GlobalVariableGet(ReconKey()))
      GlobalVariableSet(ReconKey(), (double)dealTicket);
  }

// Throttles the "reconcile HistorySelect failed" warning to <=1/hour so a
// stuck terminal history cache can't spam the log/Telegram.
datetime g_lastReconWarn = 0;
// Separate throttle for the ticket==0 lookup below -- distinct failure mode
// (per-event, not per-60s-pass), kept on its own clock so a burst of
// ticket-less closes can't itself spam the log within one hour either.
datetime g_lastReconLookupWarn = 0;

// TradeManager.CloseAll's aggregate "close" event (reversal, EXIT signal,
// profit target, profit lock, pre-break flatten, remote "close_all") does
// not carry a per-deal ticket (ticket=0) -- it can close several legs in
// one call. When the live path needs to advance the watermark for such an
// event, resolve it to the newest own closing deal actually on the books
// right now. Mirrors the first-run-seed filter set (symbol + magic +
// DEAL_ENTRY_OUT) but narrowed to ~24h since this only needs "whatever just
// closed", not the full reconcile lookback. Fail-open: HistorySelect
// failure or no match returns -1 and the caller skips the advance (a
// duplicate reconciled report is possible only in that rare case, and it's
// honest data, not silent loss).
long NewestOwnClosingDeal()
  {
   if(!HistorySelect(TimeCurrent() - 86400, TimeCurrent() + 60))
     {
      if(TimeCurrent() - g_lastReconLookupWarn > 3600)
        {
         Print("XauAssistant: recon newest-deal lookup HistorySelect failed, err=", GetLastError());
         g_lastReconLookupWarn = TimeCurrent();
        }
      return -1;
     }
   long newest = -1;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      if((long)t > newest) newest = (long)t;
     }
   return newest;
  }

// Back-fill close reports for own closing deals the service never saw
// (MT5 was down -> OnTradeTransaction never fired; or the service was
// down -> the live post was dropped). Watermark = last successfully
// reported closing-deal ticket. At-least-once, oldest-first; the scan
// stops at the first failed post so nothing is skipped.
void ReconcileOfflineCloses()
  {
   if(!GlobalVariableCheck(ReconKey()))
     {
      // First run: seed to the newest own closing deal without reporting
      // history (no spam on install/migration -- this permanently leaves
      // every PRE-DEPLOY close unreported, by design; see izi.md).
      // HistorySelect can fail at a cold terminal start (history not
      // ready yet) -- do NOT seed to 0 in that case, since every deal in
      // the next 30-day scan would then look "unreported" and the
      // reconciler would replay the whole history. Leave the key absent
      // so the very next 60s pass retries the seed from scratch.
      if(!HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent() + 60))
        {
         if(TimeCurrent() - g_lastReconWarn > 3600)
           {
            Print("XauAssistant: reconcile seed HistorySelect failed, err=", GetLastError());
            g_lastReconWarn = TimeCurrent();
           }
         return;   // fail-open: retry the seed next pass
        }
      long newest = 0;
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         ulong t = HistoryDealGetTicket(i);
         if(t == 0) continue;
         if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber) continue;
         if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
         newest = (long)t;
         break;
        }
      GlobalVariableSet(ReconKey(), (double)newest);
      PrintFormat("XauAssistant: reconcile watermark seeded at deal %I64d", newest);
      return;
     }
   long watermark = (long)GlobalVariableGet(ReconKey());
   if(!HistorySelect(TimeCurrent() - 30 * 86400, TimeCurrent() + 60))
     {
      if(TimeCurrent() - g_lastReconWarn > 3600)
        {
         Print("XauAssistant: reconcile HistorySelect failed, err=", GetLastError());
         g_lastReconWarn = TimeCurrent();
        }
      return;   // fail-open: retry on the next pass
     }
   // Build the backlog of unreported own closing deals from the SAME
   // HistorySelect window, tracking a running net own volume (symbol +
   // magic; DEAL_ENTRY_IN adds, DEAL_ENTRY_OUT subtracts) as we go. A
   // qualifying deal (ticket > watermark) is final=true exactly when that
   // running tally lands back on zero AT that deal -- i.e. "no own
   // position remains open after this deal", a fact fixed in history, NOT
   // "am I flat right now": the latter breaks if a NEW basket had already
   // opened by the time the reconciler ran (the old basket's true-final
   // close would post final=false forever), and it can't distinguish an
   // own position from another magic/manual position on the same symbol.
   // Hedging-mode own positions only ever use IN/OUT (see
   // OnTradeTransaction's scope comment above) -- DEAL_ENTRY_INOUT/OUT_BY
   // are out of scope here for the same reason.
   ulong  qTickets[];
   bool   qFinal[];
   double netVol = 0.0;
   for(int i = 0; i < HistoryDealsTotal(); i++)
     {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber) continue;
      ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(t, DEAL_ENTRY);
      double vol = HistoryDealGetDouble(t, DEAL_VOLUME);
      if(entry == DEAL_ENTRY_IN)
         netVol += vol;
      else if(entry == DEAL_ENTRY_OUT)
        {
         netVol -= vol;
         if((long)t > watermark)
           {
            int n = ArraySize(qTickets);
            ArrayResize(qTickets, n + 1);
            ArrayResize(qFinal, n + 1);
            qTickets[n] = t;
            qFinal[n]   = (MathAbs(netVol) < 0.0000001);   // flat immediately after this deal
           }
        }
     }
   // Explicit ascending sort by ticket (insertion sort -- backlog sizes
   // here are tiny, hours of outage not weeks, never worth a faster sort).
   // HistoryDealsTotal() index order is assumed to equal ticket order but
   // is not RELIED on: without this sort, a mid-backlog post failure right
   // after an out-of-order higher ticket had already posted (and advanced
   // the watermark) would strand the lower ticket behind it forever.
   for(int a = 1; a < ArraySize(qTickets); a++)
     {
      ulong tk = qTickets[a];
      bool  fn = qFinal[a];
      int b = a - 1;
      while(b >= 0 && qTickets[b] > tk)
        {
         qTickets[b + 1] = qTickets[b];
         qFinal[b + 1]   = qFinal[b];
         b--;
        }
      qTickets[b + 1] = tk;
      qFinal[b + 1]   = fn;
     }
   // Post oldest-first; the scan stops at the first failed post so nothing
   // is skipped.
   for(int k = 0; k < ArraySize(qTickets); k++)
     {
      ulong t = qTickets[k];
      string dir = (HistoryDealGetInteger(t, DEAL_TYPE) == DEAL_TYPE_BUY)
                   ? "SELL" : "BUY";   // closing deal type is opposite the basket
      double lots   = HistoryDealGetDouble(t, DEAL_VOLUME);
      double price  = HistoryDealGetDouble(t, DEAL_PRICE);
      double profit = HistoryDealGetDouble(t, DEAL_PROFIT)
                    + HistoryDealGetDouble(t, DEAL_SWAP)
                    + HistoryDealGetDouble(t, DEAL_COMMISSION);
      string reason;
      switch((ENUM_DEAL_REASON)HistoryDealGetInteger(t, DEAL_REASON))
        {
         case DEAL_REASON_SL: reason = "stop-loss (reconciled)";   break;
         case DEAL_REASON_TP: reason = "take-profit (reconciled)"; break;
         default:             reason = "closed offline (reconciled)";
        }
      bool isFinal = qFinal[k];   // history-derived: flat immediately after this deal
      // Replay directly through g_ui.PostTradeEvent rather than the
      // g_uiSink/CUiSink path: the sink also drives chart risk/reward boxes
      // and per-strategy basket bookkeeping (OnBasketClosed) intended for
      // LIVE closes only — reconciled (backlog) closes must not repaint
      // those. Service-side handling (report, render, db, channel mirror)
      // is identical either way since both call the same /trade-event
      // endpoint.
      long id = g_ui.PostTradeEvent("close", ActiveStrategy, dir, lots, price,
                                    0.0, reason, (long)t, profit, 0.0, isFinal);
      if(id < 0)
         return;                     // service still down -> retry next pass
      AdvanceReconWatermark((long)t);
      PrintFormat("XauAssistant: reconciled offline close deal %I64d (%s %.2f)",
                  (long)t, reason, profit);
     }
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
   g_entryMode = EntryMode;
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   g_registry.Register(new CHalfTrendEmaStrategy(TradeTimeframe, HtAmplitude, EmaLength,
                       ConfirmCloses, StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR,
                       HtfConfirm, HtfConfirmTf, HtfConfirmEma, HtfConfirmBufferATR,
                       HtfChopOnly, HtfChopBars, HtfChopEffMax));
   g_registry.Register(new CBollStochRsiStrategy(TradeTimeframe, BbPeriod, BbDev, TrendCloses,
                       SqueezeLookback, SqueezePctile, ExpansionBars,
                       RsiPeriod, StochPeriod, KSmooth, DSmooth));
   if(!g_registry.SetActive(ActiveStrategy))
     {
      g_alerts.Notify("XauAssistant: unknown ActiveStrategy '" + ActiveStrategy + "'");
      return INIT_FAILED;
     }
   MigrateGlobalKeys();
   if(ApplyChartTheme) ApplyDarkTheme();
   g_registry.Active().EnablePaint(true);
   g_api.Init(ApiUrl, ApiTimeoutMs, TradeTimeframe);
   g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber, TradeTimeframe);
   // Key shapes are settled (MigrateGlobalKeys) and g_ui is initialized
   // (base URL/timeout) — safe to back-fill any offline closes now.
   ReconcileOfflineCloses();
   g_news.Init(NewsGuardEnabled, NewsBlackoutMin);
   g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold,
               TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin,
               MaxDailyLossPct, MagicNumber, TradeTimeframe, &g_news);
   g_trades.Init(&g_risk, MagicNumber, EnablePyramiding, MaxPositions,
                 AddTriggerATR, ProfitTargetPct, StopAtrMult,
                 TrailLockPct, TrailActivateR, &g_uiSink);
   // Re-arm chart box tracking if a basket was already open before this
   // OnInit (recompile auto-reload, terminal restart, chart re-attach) —
   // otherwise the live box would never receive its final OnClose.
   g_tradeBoxes.RecoverFromPositions(MagicNumber);
   g_atrHandle = iATR(_Symbol, TradeTimeframe, 14);
   EventSetTimer(HeartbeatSec);
   if(Period() != TradeTimeframe)
      PrintFormat("XauAssistant: trading TF %s (chart %s — visual only)",
                  StringSubstr(EnumToString(TradeTimeframe), 7),
                  StringSubstr(EnumToString(Period()), 7));
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   datetime bar = iTime(_Symbol, TradeTimeframe, 0);
   if(bar == 0 || bar == g_lastBar) return;   // 0 = transient resync (non-chart-TF series); act once per new bar
   g_lastBar = bar;
   RollSpreadBar();               // freeze the closed bar's spread aggregates
   ProcessBar();
  }

// Fires every HeartbeatSec seconds regardless of bar boundaries: posts
// state, stashes a pending switch id, and — when the service hands back a
// MANUAL-mode command approved over Telegram ("execute"/"close_all"/
// "reset_brake") — executes it (guarded by the same live-account check OnInit enforces for
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
   // At most once per 60s: back-fill any close reports missed while MT5 or
   // the service was down (fail-open, throttled — see ReconcileOfflineCloses).
   static datetime g_lastRecon = 0;
   if(TimeCurrent() - g_lastRecon >= 60)
     {
      ReconcileOfflineCloses();
      g_lastRecon = TimeCurrent();
     }
   FlattenBeforeBreak();
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double floating_pl = equity - balance;
   CStrategy *active  = g_registry.Active();
   string activeId    = (active != NULL) ? active.Id() : "unknown";
   double spreadPts   = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   SampleSpread(spreadPts);

   string mode = "", entryModeResp = "", cmd = "", cmdDir = "";
   long cmdId = 0;
   bool algoTrading = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0;
   string entryModeStr = (g_entryMode == ENTRY_FIXED) ? "fixed" : "adr";
   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId, algoTrading, entryModeStr,
                                  mode, entryModeResp, cmd, cmdId, cmdDir,
                                  g_risk.DailyLossUsedPct(), g_risk.BrakeResetToday());
   if(sw != "") g_pendingSwitch = sw;

   // Brake & kill-switch awareness (2026-08-18): once-per-crossing Telegram
   // notices (70% of the daily brake / brake tripped — with [Reset brake for
   // today]; 80% of the kill drawdown / kill tripped — no button). Latches
   // live in per-symbol globals (see RiskManager.PollAwareness); pure
   // notify path, fail-open, never touches a trading decision.
   string awText = "", awButton = "";
   for(int aw = 0; aw < 4 && g_risk.PollAwareness(awText, awButton); aw++)
      g_ui.PostNotify(awText, awButton);

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

   // Runtime entry-mode switch (Telegram tmode:adr/tmode:fixed). Applies to
   // the NEXT entry only — any already-open basket keeps running under the
   // mode captured in its sticky global (TradeManager.BasketModeKey) at
   // open, untouched here.
   if(entryModeResp == "adr" || entryModeResp == "fixed")
     {
      ENUM_ENTRY_MODE want = (entryModeResp == "fixed") ? ENTRY_FIXED : ENTRY_ADR;
      if(want != g_entryMode)
        {
         g_entryMode = want;
         Print("XauAssistant: entry mode -> ", (want == ENTRY_FIXED ? "FIXED" : "ADR"),
               " (from Telegram) — applies to the next trade");
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
               opened = g_trades.OnSignal(dir, atrVal, act.StopPrice(dir),
                                          g_entryMode == ENTRY_FIXED, FixedLots);
            bool ok = opened || g_trades.BasketDirection() == dir;
            g_ui.PostProposalResult(cmdId, ok,
                                    ok ? "opened" : "blocked by risk checks");
           }
        }
     }
   else if(cmd == "reset_brake")
     {
      // Owner tapped [Reset brake for today] (Telegram brakereset: →
      // pre-approved proposal → this command). Re-bases the daily loss
      // brake at the realized P/L of this instant (it re-arms after ANOTHER
      // MaxDailyLossPct% loss); never touches the kill switch. Same guard
      // level as close_all: no live-account check — a reset opens no order
      // by itself, and every entry path still runs the AllowLiveTrading and
      // CanEnter gates.
      // Authoritative guard: the [Reset] button on an old notice (yesterday's,
      // or the 70% notice after a reset already happened) stays tappable
      // forever — refuse unless the brake is actually ≥70% spent right now,
      // so a stale tap can't silently re-base today's measure.
      double brakePct = g_risk.DailyLossUsedPct();
      if(brakePct < 70.0)
         g_ui.PostProposalResult(cmdId, false,
            StringFormat("brake at %.0f%% — nothing to reset", brakePct));
      else
        {
         g_risk.ResetDailyBrake();
         g_ui.PostProposalResult(cmdId, true,
            StringFormat("Brake reset for today — re-arms after another %.1f%%",
                         g_risk.MaxDailyLossPct()));
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
// in a best-effort sink call, never touches trading state or blocks OnTick
// (sole side effect: dropping the daily-loss brake's read cache on own
// closing deals so the next CanEnter rescans — see below).
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

   // Every own closing deal changes today's realized P/L — drop the daily
   // loss brake's per-bar cache BEFORE the reason filter below, so mid-bar
   // broker-side stop-outs (and any other close) are seen by the next
   // CanEnter even within the same bar (e.g. a Telegram-approved execute
   // arriving via OnTimer seconds after the stop-out).
   g_risk.InvalidateDailyCache();

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
                         g_trades.OpenCount() == 0, g_trades.EntryModeStr());
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
      bool opened = g_trades.OnSignal(sig, atrVal, active.StopPrice(sig),
                                      g_entryMode == ENTRY_FIXED, FixedLots);
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
      g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, quiet,
                    g_barSprMin, g_barSprAvg, g_barSprMax);
      return;   // keeps outcome-resolution data flowing (spec 6.3)
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, r,
                           g_barSprMin, g_barSprAvg, g_barSprMax);
   if(sig == SIGNAL_NONE) return;        // shadows logged; nothing to alert
   string report = g_sm.BuildReport(sig, r, ok) + "\n" + g_risk.Status();
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
  }
