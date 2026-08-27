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
#include <XauAssistant/Reconciler.mqh>
#include <XauAssistant/UiSink.mqh>

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

input group "HalfTrend M5 (halftrend_ema_v1)"
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
input bool   Ema200Confirm  = false;     // EMA-200 (own timeframe) agreement: BUY needs price above EMA200, SELL below (2026-08-22). ALWAYS evaluated/reported (M15 column's E200); this only controls whether it may BLOCK an entry -- default off, per owner "switch off by default ... we get them in reporting"

input group "HalfTrend M15 (halftrend_m15_v1) — second lane, owner runs ONE at a time via ActiveStrategy"
input int    M15Amplitude    = 4;                  // Half Trend amplitude (same as M5 default)
input int    M15EmaLength    = 55;                 // confirmation EMA (same as M5 default)
input int    M15ConfirmCloses = 1;                 // waiting bars after the HT arrow (M5 uses 2). 2026-08-25 trend-rider sweep (FIXED ride, 30 configs x 3 windows): confirm 1 + 1.5 ATR stop = +$9,674/17mo, BOTH halves positive, lowest dd ($1,365) — old 3 was tuned for ADR/target style and loses H1 in ride mode
input double M15StopBufferATR = 1.75;              // pad wick stop by k*ATR(14) (M5 uses 0.75). 1.5→1.75 on 2026-08-27: MAE study over 17mo (293 trades, near-infinite stop) — 1.75 survives 95.1% of eventual winners' reversals vs 93.5%, net identical (+$9,670 vs +$9,674), win% 40.1 vs 39.4, same dd. Past 1.75 the net falls off — do not widen further without new evidence
input bool   M15CatchupEnabled     = true;  // take a missed entry after downtime if still valid (same as M5 default)
input int    M15CatchupMaxAgeBars  = 12;    // signal at most this many trade-TF bars old (same as M5 default)
input double M15CatchupMaxChaseATR = 1.0;   // max adverse run beyond the signal close, in ATR(14) (same as M5 default)
// No higher-timeframe agreement on this lane (owner 2026-08-22): the M15
// lane's only confirmation is its own EMA200 below -- the M15Htf* inputs
// that used to sit here are gone rather than left wired to nothing.
input bool   M15Ema200Confirm = false;   // EMA-200 (own timeframe) agreement, same rule as M5's Ema200Confirm above; default off

input group "BollStochRsi (boll_stochrsi)"
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

// CUiSink (bridges TradeManager to the UI service) lives in UiSink.mqh; wired
// up (Init) in OnInit once its dependencies (registry/UI/boxes/reconciler)
// exist.
CUiSink     g_uiSink;
CReconciler g_recon;

// --- Single-instance guard (2026-08-24) ------------------------------------
// mt5-start.ini [StartUp] auto-attaches this EA to a fresh XAUUSD M5 chart on
// EVERY terminal boot, while the restored profile may still carry its own
// copy — without a guard both would trade the same magic. Rule: the NEWEST
// attachment wins. Every instance writes a random token into a per-symbol
// terminal global at init; an instance whose token no longer matches knows it
// was superseded and removes itself (ExpertRemove) on its next timer tick,
// and OnTick refuses to trade in the ≤5 s window before that tick lands.
// Tokens, not chart IDs: globals hold doubles, and chart IDs (~1.3e17) exceed
// double's exact-integer range, so two near-consecutive IDs could round to
// the same value. The global is never deleted — a stale token after a crash
// or manual removal is simply overwritten by the next attachment's claim.
// Side effect the owner relies on: attaching the EA to ANY chart "moves" it
// there — the previous instance steps down by itself.
double g_instToken = 0.0;
string OwnerKey()  { return "XAU_OWNER_" + _Symbol; }
bool   IsOwner()   { return GlobalVariableGet(OwnerKey()) == g_instToken; }
void ClaimOwnership()
  {
   MathSrand((int)GetMicrosecondCount() + (int)ChartID());
   // integer-valued, < 2^45 — exactly representable in the global's double
   g_instToken = (double)((long)GetMicrosecondCount() % 1000000000) * 32768.0
                 + (double)MathRand() + 1.0;
   GlobalVariableSet(OwnerKey(), g_instToken);
  }

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

// Global-variable-key migration and reconcile-on-reconnect (g_recon) live in
// Reconciler.mqh; wired up (Init) in OnInit before ReconcileOfflineCloses runs.

int OnInit()
  {
   if(ExecutionMode == EXEC_AUTO && !AllowLiveTrading &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
     {
      g_alerts.Notify("XauAssistant: AUTO on LIVE account blocked (AllowLiveTrading=false)");
      return INIT_FAILED;
     }
   ClaimOwnership();   // newest attachment wins; any older instance steps down
   g_execMode = ExecutionMode;
   g_entryMode = EntryMode;
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   g_registry.Register(new CHalfTrendEmaStrategy("halftrend_ema_v1", TradeTimeframe, HtAmplitude, EmaLength,
                       ConfirmCloses, StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR,
                       HtfConfirm, HtfConfirmTf, HtfConfirmEma, HtfConfirmBufferATR,
                       HtfChopOnly, HtfChopBars, HtfChopEffMax, Ema200Confirm));
   // Second, independently-parameterised HalfTrend lane trading M15 (owner
   // request 2026-08-22): same rules, own inputs, shadow-evaluated every bar
   // like every other registered strategy. Only ActiveStrategy trades/alerts
   // — registering this does NOT make it live. M15ConfirmCloses=3 (measured
   // positive in BOTH halves of the 17-month M15 history). No HTF module on
   // this lane (owner 2026-08-22: "for this m15 the only confirmation is the
   // ema 200") -- htfConfirm=false with placeholder HTF args that are never
   // used (no M15Htf* inputs exist to source them from any more).
   g_registry.Register(new CHalfTrendEmaStrategy("halftrend_m15_v1", PERIOD_M15, M15Amplitude, M15EmaLength,
                       M15ConfirmCloses, M15StopBufferATR,
                       M15CatchupEnabled, M15CatchupMaxAgeBars, M15CatchupMaxChaseATR,
                       false, PERIOD_H1, 55, 0.0, false, 48, 0.08, M15Ema200Confirm));
   g_registry.Register(new CBollStochRsiStrategy(TradeTimeframe, BbPeriod, BbDev, TrendCloses,
                       SqueezeLookback, SqueezePctile, ExpansionBars,
                       RsiPeriod, StochPeriod, KSmooth, DSmooth));
   if(!g_registry.SetActive(ActiveStrategy))
     {
      g_alerts.Notify("XauAssistant: unknown ActiveStrategy '" + ActiveStrategy + "'");
      return INIT_FAILED;
     }
   g_recon.MigrateGlobalKeys();
   if(ApplyChartTheme) ApplyDarkTheme();
   g_registry.Active().EnablePaint(true);
   g_api.Init(ApiUrl, ApiTimeoutMs, TradeTimeframe);
   g_ui.Init(UiBaseUrl, UiTimeoutMs, MagicNumber, TradeTimeframe);
   g_recon.Init(MagicNumber, &g_ui, ActiveStrategy);
   g_uiSink.Init(&g_registry, &g_ui, &g_tradeBoxes, &g_recon);
   // Key shapes are settled (MigrateGlobalKeys) and g_ui is initialized
   // (base URL/timeout) — safe to back-fill any offline closes now.
   g_recon.ReconcileOfflineCloses();
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
   // Repaint fix (2026-08-26): g_lastBar survives a chart-timeframe switch
   // (MQL5 keeps module globals on REASON_CHARTCHANGE), so after OnDeinit
   // wiped the painted lines, OnTick's new-bar gate would defer the
   // warm-up repaint until the NEXT trading-TF bar — up to minutes of
   // blank chart. Resetting the latch makes the first tick after ANY
   // re-init run ProcessBar (warm-up replay -> full repaint), identical to
   // the already-guarded recompile/restart path.
   g_lastBar = 0;
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
   if(!IsOwner()) return;   // superseded — OnTimer will remove this instance
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
   // Single-instance guard: a newer attachment claimed ownership — step down.
   if(!IsOwner())
     {
      PrintFormat("XauAssistant: superseded by a newer instance on %s — removing this one (chart %I64d)",
                  _Symbol, ChartID());
      ExpertRemove();
      return;
     }
   // One-shot housekeeping ~15 s after attach (3rd tick — after any takeover
   // has settled): close leftover expert-less charts on OUR symbol+trade TF.
   // These are exactly the charts previous [StartUp] boots opened (or a
   // superseded instance vacated); without this they accumulate one per boot.
   // Charts on other timeframes (the owner's viewing charts) are never touched.
   static int  g_guardTicks = 0;
   static bool g_chartsCleaned = false;
   if(!g_chartsCleaned && ++g_guardTicks >= 3)
     {
      g_chartsCleaned = true;
      for(long cid = ChartFirst(); cid >= 0; cid = ChartNext(cid))
        {
         if(cid == ChartID()) continue;
         if(ChartSymbol(cid) != _Symbol || ChartPeriod(cid) != TradeTimeframe) continue;
         if(ChartGetString(cid, CHART_EXPERT_NAME) != "") continue;
         PrintFormat("XauAssistant: closing leftover %s chart without an EA (id %I64d)", _Symbol, cid);
         ChartClose(cid);
        }
     }
   // At most once per 60s: back-fill any close reports missed while MT5 or
   // the service was down (fail-open, throttled — see ReconcileOfflineCloses).
   static datetime g_lastRecon = 0;
   if(TimeCurrent() - g_lastRecon >= 60)
     {
      g_recon.ReconcileOfflineCloses();
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

   string mode = "", entryModeResp = "", cmd = "", cmdDir = "", htfEnforce = "", ema200Enforce = "";
   long cmdId = 0;
   bool algoTrading = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0;
   string entryModeStr = (g_entryMode == ENTRY_FIXED) ? "fixed" : "adr";
   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId, algoTrading, entryModeStr,
                                  mode, entryModeResp, cmd, cmdId, cmdDir, htfEnforce, ema200Enforce,
                                  g_risk.DailyLossUsedPct(), g_risk.BrakeResetToday(),
                                  g_news.UpcomingJson(), NewsBlackoutMin);
   // Push the /agree settings to every strategy (shadows included, so their
   // logged verdicts match what the active one would do).
   if(htfEnforce != "")
      for(int si = 0; si < g_registry.Count(); si++)
        {
         CStrategy *st = g_registry.Get(si);
         if(st != NULL) st.SetHtfOverride(htfEnforce);
        }
   if(ema200Enforce != "")
      for(int si2 = 0; si2 < g_registry.Count(); si2++)
        {
         CStrategy *st2 = g_registry.Get(si2);
         if(st2 != NULL) st2.SetEma200Override(ema200Enforce);
        }
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
   else if(cmd == "move_sl")
     {
      // Owner tapped [🔒 Move SL to here] on the target alert. Same guard
      // level as close_all: no live-account check — a stop move opens no
      // order, only tightens protection on an existing basket. Tighten-only
      // and stops-level handling live in TradeManager.MoveStopsTight; a
      // stale tap on a flat account reports "nothing open" honestly.
      string detail = "";
      bool ok = g_trades.MoveStopsTight(detail);
      g_ui.PostProposalResult(cmdId, ok, detail);
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
