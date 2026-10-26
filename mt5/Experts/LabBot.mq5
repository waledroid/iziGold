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
#include <XauAssistant/SrZones.mqh>
#include <XauAssistant/SrBoxes.mqh>
#include <XauAssistant/StrategyRegistry.mqh>
#include <XauAssistant/Strategies/HalfTrendEma.mqh>
#include <XauAssistant/Strategies/BollStochRsi.mqh>
#include <XauAssistant/Strategies/SessionStructure.mqh>
#include <XauAssistant/ChartPanels.mqh>
#include <XauAssistant/Reconciler.mqh>
#include <XauAssistant/UiSink.mqh>
#include <XauAssistant/VolumeProfile.mqh>

enum ENUM_EXEC_MODE { EXEC_MANUAL, EXEC_AUTO };

input ENUM_EXEC_MODE ExecutionMode          = EXEC_AUTO;   // AUTO on attach (owner 2026-08-20). NOTE: the SERVICE is the authority — it sends `mode` every heartbeat and the EA obeys; this input only sets the mode until the first heartbeat lands.
input bool           AllowLiveTrading       = true;    // owner 2026-08-20: drag-and-go. WARNING: this is the ONLY gate stopping AUTO from trading a REAL account on attach. Set false to restore it.
input ENUM_TIMEFRAMES TradeTimeframe        = PERIOD_M15; // trading TF — chart TF is visual only
input string         ApiUrl                 = "http://127.0.0.1:9000/analyze";
input int            ApiTimeoutMs           = 3000;
input string         UiBaseUrl              = "http://127.0.0.1:9000";
// How the EA reaches the service. AUTO probes /health once and picks the
// working path, falling back to the file relay if WebRequest is refused (4014).
// On Linux/Wine the WebRequest allowlist does not persist across restarts, so
// the relay is what keeps the EA connected with no GUI work; on native Windows
// HTTP wins and nothing changes.
input ENUM_XAU_TRANSPORT TransportMode      = XAU_TRANSPORT_AUTO;
input int            HeartbeatSec           = 1;       // live service/UI heartbeat; DB persistence is downsampled
input int            UiTimeoutMs            = 1000;
input double         RiskPerTradePct        = 1.0; // raised 0.5→1.0 on 2026-08-09: week sweep +$610 vs +$421 at DD 7.2% vs 3.8%; 1.5%+ trips the kill switch
input double         MaxDrawdownPct         = 10.0;
input double         MaxDailyLossPct        = 3.0; // daily realized-loss brake (symbol+magic deals since server midnight); 0 = off
input bool           NewsGuardEnabled       = true; // block new exposure around high-importance USD calendar events (fail-open if no calendar data)
input int            NewsBlackoutMin        = 30;  // blackout radius: minutes before AND after the event
input bool           EnablePyramiding       = true;
input int            MaxPositions           = 3;
input double         AddTriggerATR          = 1.0;
input double         FirstAddTriggerATR     = 3.0;  // first pyramid add needs 3x ATR from entry; later adds keep AddTriggerATR
input double         ProfitTargetPct        = 2.0;  // basket banks at +2% of cycle balance; 0 = off
input double         TrailLockPct           = 50;  // keep this % of peak basket profit once armed; 0 = off
input double         TrailActivateR         = 1.0; // arm lock when peak profit >= this multiple of the per-trade risk budget
input double         StopAtrMult            = 2.0;
input double         MaxSpreadPoints        = 500;
input int            TradingWindowStartHour = 4;   // server time; skips rollover (23-01) + thin Tokyo open (01-04)
input int            TradingWindowEndHour   = 22;  // pre-rollover hour (22-23 server) cut: backtest 17mo showed it is the single worst hour (-$1.3k); window 4-22 nets +133% vs +90% at 4-23
input int            MaxDailyExposureMin    = 0;   // daily open-position minutes budget; 0 = off / unlimited
input int            FlattenBeforeBreakMin  = 5;   // close ALL positions this many min before the 23:59 break; 0 = off
input double         AdxTrendThreshold      = 20.0; // institutional momentum gate: filter dead tape and chop
input bool           DebugFireTestSignal    = false;
input long           MagicNumber            = 20261026; // distinct MagicNumber for Lab testing
input bool           ApplyChartTheme        = true;

enum ENUM_ENTRY_MODE { ENTRY_ADR = 0, ENTRY_FIXED = 1, ENTRY_HYBRID = 2 };
input ENUM_ENTRY_MODE EntryMode  = ENTRY_HYBRID; // ADR = 1% risk; FIXED = 0.03; HYBRID = risk-calibrated 0.03 + Multi-Stage TP
input bool           UseFixedLot = false;        // force fixed lot sizing (false lets EntryMode rule)
input double         FixedLots   = 0.03;         // fixed trade size (broker-clamped, default 0.03)

input group "Trade Protection & Multi-Stage TP (1:1 Ratio)"
input bool           SrPyramidGuard            = true; // lock out pyramid adds near opposing S/R walls
input double         SrPyramidRoomAtr          = 1.0;  // block pyramid add if opposing S/R wall is within this ATR
input bool           EnableMultiStageTp        = true; // enable 1:1 TP1 + TP2 + TP3 runner management
input double         Tp1RatioR                 = 1.0;  // TP1 at 1:1 ratio (1.0R): bank 50% profit & move SL to BE
input double         Tp1LotsPct                = 0.50; // 50% scale-out at TP1
input double         Tp2RatioR                 = 2.0;  // TP2 at 2.0R: bank 50% of runner (25% total) & lock SL at +1.0R
input double         Tp2LotsPct                = 0.50; // 50% scale-out of remaining runner
input double         Tp3RatioR                 = 0.0;  // TP3 full target in R (0 = let runner ride until HT flip)
input double         Tp3LotsPct                = 0.0;
input bool           EnableReversalExit        = true; // exit basket on heavy pullback from profit peak
input double         ReversalMinPeakAtr        = 1.5;  // minimum peak ATR advance required before reversal exit arms
input double         ReversalThresholdAtr      = 1.0;  // pullback ATR from peak that triggers reversal exit
input bool           EnableContinuationReentry = true; // re-enter when trend resumes after heavy reversal exit
input bool           EnableSrExit              = true; // exit trade when approaching/touching opposing S/R wall in profit
input double         SrExitRoomAtr             = 0.3;  // exit distance to opposing S/R wall (x ATR)
input double         SrExitMinProfitR          = 0.3;  // min profit advance in R before S/R exit activates

input string ActiveStrategy = "iziGold15"; // which registered strategy trades

input group "Gold M5 (iziGold5)"
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
input bool   HtfChopOnly    = false;     // strengthened HTF filter: gate ALWAYS on higher-TF EMA (false = gate all day, true = in chop only)
input int    HtfChopBars    = 48;        // path-efficiency window, trade-TF bars (48 = 4h of M5)
input double HtfChopEffMax  = 0.08;      // below this efficiency the tape counts as choppy; 0.06-0.10 measured as one plateau
input double HtfConfirmBufferATR = 2.0;  // price must CLEAR that EMA by this x ATR(14); 0 = side-only (pre-2026-08-20). 2.0 = middle of the 1.0-5.0 plateau that is profitable in BOTH backtest halves
input bool   Ema200Confirm  = false;     // EMA-200 (own timeframe) agreement: BUY needs price above EMA200, SELL below (2026-08-22). ALWAYS evaluated/reported (M15 column's E200); this only controls whether it may BLOCK an entry -- default off, per owner "switch off by default ... we get them in reporting"

input group "Gold M15 (iziGold15) — second lane, owner runs ONE at a time via ActiveStrategy"
input int    M15Amplitude    = 4;                  // Half Trend amplitude (same as M5 default)
input int    M15EmaLength    = 50;                 // confirmation EMA. 45→50 on 2026-08-31 (owner request, sweep-checked same day: 50 nets +$10,069 vs 45's +$9,945 over 17 mo, better in BOTH halves, same dd — 45/50 sit on one plateau). 55→45 history: 2026-08-27 sweep, "55 calls the entry late on M15"
input int    M15ConfirmCloses = 1;                 // waiting bars after the HT arrow (M5 uses 2). 2026-08-25 trend-rider sweep (FIXED ride, 30 configs x 3 windows): confirm 1 + 1.5 ATR stop = +$9,674/17mo, BOTH halves positive, lowest dd ($1,365) — old 3 was tuned for ADR/target style and loses H1 in ride mode
input double M15ConfirmClearATR = 0.3;             // confirm close must CLEAR EMA by this x ATR(14), else the signal dies — a confirm hanging on the line is treated like a wrong-side one (owner 2026-09-01; sweep: net -0.9%, max dd -10%, dd lower in BOTH halves). 0 = plain side test
input double M15StopBufferATR = 1.75;              // pad wick stop by k*ATR(14) (M5 uses 0.75). 1.5→1.75 on 2026-08-27: MAE study over 17mo (293 trades, near-infinite stop) — 1.75 survives 95.1% of eventual winners' reversals vs 93.5%, net identical (+$9,670 vs +$9,674), win% 40.1 vs 39.4, same dd. Past 1.75 the net falls off — do not widen further without new evidence
input bool   M15CatchupEnabled     = true;  // take a missed entry after downtime if still valid (same as M5 default)
input int    M15CatchupMaxAgeBars  = 12;    // signal at most this many trade-TF bars old (same as M5 default)
input double M15CatchupMaxChaseATR = 1.0;   // max adverse run beyond the signal close, in ATR(14) (same as M5 default)
// No higher-timeframe agreement on this lane (owner 2026-08-22): the M15
// lane's only confirmation is its own EMA200 below -- the M15Htf* inputs
// that used to sit here are gone rather than left wired to nothing.
input bool   M15Ema200Confirm = true;    // EMA-200 (own timeframe) agreement: BUY needs price above EMA200, SELL below
input bool   M15RsiConfirm    = true;    // RSI(14) 70/30 agreement may BLOCK an M15 entry (owner "go" 2026-09-07). Sweep 2026-09-02: +7% net at -33% max dd, better in BOTH halves; the 2026-09-07 oversold SELL (RSI 26) that lost the day would have been refused. The M5 lane is NOT eligible (fails both halves) and only reports. Service /agree override wins over this input.
input bool   M15EnableAdds    = false;   // pyramid adds on the M15 lane (owner "go" 2026-09-07). false = pure ride, the recipe every trusted M15 number was measured on; the live ADR add + halfway ratchet turned a -$16 ride exit into -$56 on 2026-09-07. Global EnablePyramiding still applies. Service /agree override wins.

input group "Bitcoin M5 (iziBit5) — BTC/USDT M5 lane"
input int    Btc5Amplitude          = 4;                  // Half Trend amplitude
input int    Btc5EmaLength          = 55;                 // confirmation EMA
input int    Btc5ConfirmCloses      = 2;                  // waiting bars after HT arrow
input double Btc5StopBufferATR      = 1.0;                // pad wick stop by k*ATR
input int    Btc5HtfConfirmEma      = 55;                 // higher-TF bias EMA
input double Btc5HtfConfirmBufferATR= 1.5;                // price must clear higher-TF EMA by this x ATR
input double Btc5HtfChopEffMax      = 0.08;               // chop threshold
input int    Btc5HtfChopBars        = 48;                 // chop path window

input group "Bitcoin M15 (iziBit15) — BTC/USDT M15 lane"
input int    Btc15Amplitude         = 4;                  // Half Trend amplitude
input int    Btc15EmaLength         = 50;                 // confirmation EMA
input int    Btc15ConfirmCloses     = 1;                  // waiting bars after HT arrow
input double Btc15ConfirmClearATR   = 0.3;                // confirm close must clear EMA by k*ATR
input double Btc15StopBufferATR     = 2.00;               // pad wick stop by k*ATR (2.00 for crypto wicks)

input group "Multi-Asset Coexistence & Auto-Spawn"
input bool            AutoSpawnBtcChart      = true;     // Auto-spawn BTC chart if not open (runs once on Gold instance)
input string          BtcPreferredSymbol     = "BTCUSD"; // Preferred Bitcoin symbol name
input ENUM_TIMEFRAMES BtcTimeframe           = PERIOD_M15; // Timeframe for spawned Bitcoin chart (M15 default)

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

input group "SessionStructure (session_structure_v1) — shadow-only session drift lanes, SERVER hours"
input int    SsWin1StartHour = 1;    // Asia-drift LONG: documented 30y anomaly; hours 01-03 = +23.1% over this broker's own 17mo (bars_max.json study 2026-08-30)
input int    SsWin1EndHour   = 4;    // exit hour: drift measured negative from 04 (also where the live trading window starts — 01-04 was excluded for spreads)
input int    SsWin2StartHour = -1;   // optional 2nd LONG window, -1 = off. Best data-mined candidate is hour 09 (t=+2.24) but it has no academic prior — enable only after shadow stats earn it
input int    SsWin2EndHour   = -1;
input int    SsShortStartHour = -1;  // PM-fix fade SHORT, -1 = off. The academic prior says 16-17 server, but this broker's 17mo shows NO edge there (t=-0.67, bull regime) — ships disabled
input int    SsShortEndHour  = -1;

input group "Chart panels — plugin indicators below the candles (display only)"
input bool   ShowRsiPanel  = false;  // RSI removed from charts; true re-attaches RSI(14) in its own subwindow (chart TF)
input bool   ShowMacdPanel = false;  // MACD removed from charts; true re-attaches MACD(12,26,9) in its own subwindow (chart TF)

input group "S/R zones — three timeframe tiers drawn as outlines (spec 2026-09-05)"
input bool   ShowSrBoxes          = true;          // draw the zone outlines on the chart (light blue support / light yellow resistance; no fill)
input bool   SrNearestOnly        = true;          // battleground filter: draw only nearest active Resistance & Support (eliminates screen clutter)
input bool   SrEnforceEntry       = true;          // block new entries approaching or inside opposing S/R wall
input int    SrMaxSpanBars        = 40;            // max historical bars to span backwards (0 = full history; 40 keeps chart clean)
input double SrVisualMaxHeightPts = 250;           // max visual box height in points (250 pts = $2.50 / 25 pips; 0 = uncapped)
input string SrTiers              = "H4,H1,M15";   // tiers the verdict honours (drawing always shows all three)
input double SrRoomAtr            = 1.0;           // "wall" = opposing box within this x ATR(14, lane TF) of the entry, or containing it
input int    SrPivotWing          = 3;             // fractal wing: bars each side a swing must dominate
input double SrMinHeightAtr       = 0.05;          // box height floor, x tier ATR(14) (reduced from 0.15 for precision)
input double SrMaxHeightAtr       = 0.20;          // box height cap, x tier ATR(14) (reduced from 0.60 for precision)
input double SrMergeAtr      = 0.50;          // a pivot within this of a box merges into it (adds a touch)
input double SrBreakAtr      = 0.50;          // a CLOSE beyond a box by this kills it (a wick does not)
input int    SrKeepPerRole   = 3;             // nearest boxes kept per role per tier (3 -> at most 18 boxes)
input int    SrLookbackH4    = 300;           // seeding depth per tier, tier bars
input int    SrLookbackH1    = 400;
input int    SrLookbackM15   = 400;

input group "Volume Profile (VPVR)"
input bool            ShowVolumeProfile = true;   // Draw Volume Profile on chart
input ENUM_VP_ANCHOR  VpAnchor          = VP_ANCHOR_RIGHT_MARGIN; // Alignment: RIGHT_MARGIN (unobstructed candles), RANGE_BG, or ENTRY_BAR
input ENUM_VP_THEME   VpTheme           = VP_THEME_INSTITUTIONAL; // Palette: INSTITUTIONAL (slate/gold) or NEON_CLASSIC
input bool            VpShowDelta       = true;   // Show buy (emerald) vs sell (crimson) volume delta split
input int             VpLookbackBars    = 100;    // Lookback bars for Volume Profile calculation
input int             VpBins            = 28;     // Price bucket count
input double          VpValueAreaPct    = 0.70;   // Value Area volume percentage (0.70 = 70%)
input int             VpMaxBarWidth     = 16;     // Max histogram bar width in chart bars

CStrategyRegistry g_registry;
CAlerts        g_alerts;
CAiApi         g_api;
CUiApi         g_ui;
CTransport     g_transport;
CSignalManager g_sm;
CNewsGuard     g_news;
CRiskManager   g_risk;
CTradeManager  g_trades;
CTradeBoxes    g_tradeBoxes;
CVolumeProfile g_volumeProfile;
int            g_atrHandle = INVALID_HANDLE;
CChartPanels   g_chartPanels;
CSrZoneSet     g_srZones;   // shared by every lane: zones are not lane-specific
CSrBoxes       g_srBoxes;

// TradeManager's ATR must be the ACTIVE LANE's trade-TF ATR, not the EA's
// TradeTimeframe (2026-09-01, owner: "the adds are too quick for m15"): with
// the M15 lane active, adds were spacing on 1.0 x M5 ATR (~$5) instead of
// M15 ATR (~$9) — modeled over 17 months that costs ~80% of the lane's net
// (+$458/dd $817 vs +$2,407/dd $586) AND raises drawdown. Every backtest
// always used the trading TF's own ATR, so this also closes a live-vs-replay
// divergence. Called at init and after every successful lane switch.
void SyncAtrToActiveLane()
  {
   ENUM_TIMEFRAMES tf = TradeTimeframe;
   CStrategy *active = g_registry.Active();
   if(active != NULL && active.TradeTf() != PERIOD_CURRENT)
      tf = active.TradeTf();
   if(g_atrHandle != INVALID_HANDLE) IndicatorRelease(g_atrHandle);
   g_atrHandle = iATR(_Symbol, tf, 14);
   PrintFormat("XauAssistant: TradeManager ATR now %s (active lane)",
               StringSubstr(EnumToString(tf), 7));
  }
datetime       g_lastBar = 0;
bool           g_debugFired = false;
string         g_pendingSwitch = "";
ENUM_EXEC_MODE g_execMode = EXEC_MANUAL;
// Supervisory-agent governor: when true, NEW auto entries are refused while
// open-basket management, exits and every risk gate keep working. Distinct
// from MANUAL mode on purpose (the owner's mode is not overwritten).
bool g_agentPaused = false;
// Effective heartbeat interval. MT5 keeps a chart EA's SAVED input values
// across recompiles, so a changed source default never reaches a running
// instance; the service is authoritative and the EA re-arms its timer.
int g_heartbeatSec = 0;
ENUM_ENTRY_MODE g_entryMode = ENTRY_ADR;

// --- Per-bar spread telemetry (ea-scope spec §3) ---------------------------
// OnTimer samples SYMBOL_SPREAD every HeartbeatSec (1 s) into an
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
   // S/R zone engine (spec 2026-09-05): built before the lanes so both
   // HalfTrend instances can be handed the shared pointer. Fail-open by
   // construction — an unreadable tier stays empty and the verdict is -1.
   SrParams srp;
   srp.wing = SrPivotWing; srp.minHeightAtr = SrMinHeightAtr; srp.maxHeightAtr = SrMaxHeightAtr;
   srp.mergeAtr = SrMergeAtr; srp.breakAtr = SrBreakAtr; srp.keepPerRole = SrKeepPerRole;
   g_srZones.Init(srp, SrLookbackH4, SrLookbackH1, SrLookbackM15);
   g_srBoxes.Init(&g_srZones, ShowSrBoxes, SrNearestOnly, SrMaxSpanBars, SrVisualMaxHeightPts);
   g_registry.Register(new CStrategy());   // "stub" — kept as a shadow baseline
   CHalfTrendEmaStrategy *laneM5 = new CHalfTrendEmaStrategy("iziGold5", PERIOD_M5, HtAmplitude, EmaLength,
                       ConfirmCloses, StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR,
                       HtfConfirm, HtfConfirmTf, HtfConfirmEma, HtfConfirmBufferATR,
                       HtfChopOnly, HtfChopBars, HtfChopEffMax, Ema200Confirm);
   laneM5.SetSrZones(&g_srZones, SrTiers, SrRoomAtr);
   laneM5.SetSrConfirm(SrEnforceEntry);
   g_registry.Register(laneM5);
   // Second, independently-parameterised HalfTrend lane trading M15 (owner
   // request 2026-08-22): same rules, own inputs, shadow-evaluated every bar
   // like every other registered strategy. Only ActiveStrategy trades/alerts
   // — registering this does NOT make it live. M15ConfirmCloses=3 (measured
   // positive in BOTH halves of the 17-month M15 history). No HTF module on
   // this lane (owner 2026-08-22: "for this m15 the only confirmation is the
   // ema 200") -- htfConfirm=false with placeholder HTF args that are never
   // used (no M15Htf* inputs exist to source them from any more).
   CHalfTrendEmaStrategy *laneM15 = new CHalfTrendEmaStrategy("iziGold15", PERIOD_M15, M15Amplitude, M15EmaLength,
                       M15ConfirmCloses, M15StopBufferATR,
                       M15CatchupEnabled, M15CatchupMaxAgeBars, M15CatchupMaxChaseATR,
                       false, PERIOD_H1, 55, 0.0, false, 48, 0.08, M15Ema200Confirm,
                       M15ConfirmClearATR,
                       M15RsiConfirm, true,      // RSI 70/30: eligible to enforce (sweep-earned)
                       M15EnableAdds, true);     // adds switchable on this lane only
   laneM15.SetSrZones(&g_srZones, SrTiers, SrRoomAtr);
   laneM15.SetSrConfirm(SrEnforceEntry);
   g_registry.Register(laneM15);

   // Bitcoin M5 lane (iziBit5)
   CHalfTrendEmaStrategy *laneBtc5 = new CHalfTrendEmaStrategy("iziBit5", PERIOD_M5, Btc5Amplitude, Btc5EmaLength,
                       Btc5ConfirmCloses, Btc5StopBufferATR,
                       CatchupEnabled, CatchupMaxAgeBars, CatchupMaxChaseATR,
                       HtfConfirm, HtfConfirmTf, Btc5HtfConfirmEma, Btc5HtfConfirmBufferATR,
                       HtfChopOnly, Btc5HtfChopBars, Btc5HtfChopEffMax, Ema200Confirm);
   laneBtc5.SetSrZones(&g_srZones, SrTiers, SrRoomAtr);
   laneBtc5.SetSrConfirm(SrEnforceEntry);
   g_registry.Register(laneBtc5);

   // Bitcoin M15 lane (iziBit15)
   CHalfTrendEmaStrategy *laneBtc15 = new CHalfTrendEmaStrategy("iziBit15", PERIOD_M15, Btc15Amplitude, Btc15EmaLength,
                       Btc15ConfirmCloses, Btc15StopBufferATR,
                       M15CatchupEnabled, M15CatchupMaxAgeBars, M15CatchupMaxChaseATR,
                       false, PERIOD_H1, 55, 0.0, false, 48, 0.08, M15Ema200Confirm,
                       Btc15ConfirmClearATR,
                       M15RsiConfirm, true,
                       M15EnableAdds, true);
   laneBtc15.SetSrZones(&g_srZones, SrTiers, SrRoomAtr);
   laneBtc15.SetSrConfirm(SrEnforceEntry);
   g_registry.Register(laneBtc15);

   string effectiveStrat = ActiveStrategy;
   long   effectiveMagic = MagicNumber;

   // Multi-Asset smart defaults (Gold vs Bitcoin):
   // Bitcoin strictly trades on M15 (iziBit15) per operator mandate.
   // Offset MagicNumber so both Gold and BTC instances operate concurrently.
   if(StringFind(_Symbol, "BTC") >= 0 || StringFind(_Symbol, "btc") >= 0)
     {
      effectiveStrat = "iziBit15";
      if(effectiveMagic == 20260729)
         effectiveMagic = 20260730;
     }
   else
     {
      // Gold chart: map legacy aliases or select default based on chart timeframe
      if(effectiveStrat == "izi5") effectiveStrat = "iziGold5";
      else if(effectiveStrat == "izi15") effectiveStrat = "iziGold15";
      else if(effectiveStrat == "iziBit5" || effectiveStrat == "iziBit15")
        {
         effectiveStrat = (Period() == PERIOD_M15) ? "iziGold15" : "iziGold5";
        }
      else if(effectiveStrat == "iziGold5" && Period() == PERIOD_M15)
        {
         effectiveStrat = "iziGold15";
        }
     }

   if(!g_registry.SetActive(effectiveStrat))
     {
      g_alerts.Notify("iziBot: unknown ActiveStrategy '" + effectiveStrat + "'");
      return INIT_FAILED;
     }
   g_recon.MigrateGlobalKeys();
   if(ApplyChartTheme) ApplyDarkTheme();

   // Chart view configuration: shift end of chart from right border (20% margin) + autoscroll
   ChartSetInteger(0, CHART_SHIFT, true);
   ChartSetDouble(0, CHART_SHIFT_SIZE, 20.0);
   ChartSetInteger(0, CHART_AUTOSCROLL, true);
   g_registry.Active().EnablePaint(true);
   ENUM_TIMEFRAMES activeTf = TradeTimeframe;
   if(g_registry.Active() != NULL && g_registry.Active().TradeTf() != PERIOD_CURRENT)
      activeTf = g_registry.Active().TradeTf();
   g_transport.Init(UiBaseUrl, MathMax(ApiTimeoutMs, UiTimeoutMs), TransportMode);
   g_api.Init(GetPointer(g_transport), ApiTimeoutMs, activeTf);
   g_ui.Init(GetPointer(g_transport), UiTimeoutMs, effectiveMagic, activeTf);
   g_recon.Init(effectiveMagic, &g_ui, effectiveStrat);
   g_volumeProfile.Init(ShowVolumeProfile, VpLookbackBars, VpBins, VpValueAreaPct, VpAnchor, VpTheme, VpShowDelta, VpMaxBarWidth);
   g_uiSink.Init(&g_registry, &g_ui, &g_tradeBoxes, &g_recon, &g_news, &g_volumeProfile);
   // Key shapes are settled (MigrateGlobalKeys) and g_ui is initialized
   // (base URL/timeout) — safe to back-fill any offline closes now.
   g_recon.ReconcileOfflineCloses();
   g_news.Init(NewsGuardEnabled, NewsBlackoutMin);
   g_risk.Init(RiskPerTradePct, MaxDrawdownPct, MaxSpreadPoints, AdxTrendThreshold,
               TradingWindowStartHour, TradingWindowEndHour, MaxDailyExposureMin,
               MaxDailyLossPct, effectiveMagic, TradeTimeframe, &g_news);
   g_trades.Init(&g_risk, effectiveMagic, EnablePyramiding, MaxPositions,
                 AddTriggerATR, FirstAddTriggerATR, ProfitTargetPct, StopAtrMult,
                 TrailLockPct, TrailActivateR, &g_uiSink);
   g_trades.SetSrZones(&g_srZones, SrTiers, SrPyramidRoomAtr, SrPyramidGuard);
   g_trades.SetFixedSizing(UseFixedLot || (g_entryMode == ENTRY_FIXED), FixedLots);
   g_trades.SetPartialScaleOut(EnableMultiStageTp, Tp1RatioR, Tp1LotsPct, Tp2RatioR, Tp2LotsPct, Tp3RatioR, Tp3LotsPct);
   g_trades.SetReversalExit(EnableReversalExit, ReversalMinPeakAtr, ReversalThresholdAtr, EnableContinuationReentry);
   g_trades.SetSrExit(EnableSrExit, SrExitRoomAtr, SrExitMinProfitR);
   // Re-arm chart box tracking if a basket was already open before this
   // OnInit (recompile auto-reload, terminal restart, chart re-attach) —
   // otherwise the live box would never receive its final OnClose.
   g_tradeBoxes.RecoverFromPositions(effectiveMagic);
   // Repaint fix (2026-08-26): g_lastBar survives a chart-timeframe switch
   // (MQL5 keeps module globals on REASON_CHARTCHANGE), so after OnDeinit
   // wiped the painted lines, OnTick's new-bar gate would defer the
   // warm-up repaint until the NEXT trading-TF bar — up to minutes of
   // blank chart. Resetting the latch makes the first tick after ANY
   // re-init run ProcessBar (warm-up replay -> full repaint), identical to
   // the already-guarded recompile/restart path.
   g_lastBar = 0;
   SyncAtrToActiveLane();
   if(ShowVolumeProfile) g_volumeProfile.DrawOnEntry(0, 0, VpLookbackBars);
   g_chartPanels.Init(ShowRsiPanel, ShowMacdPanel);
   g_heartbeatSec = (HeartbeatSec >= 1 && HeartbeatSec <= 60) ? HeartbeatSec : 1;
   EventSetTimer(g_heartbeatSec);
   if(Period() != TradeTimeframe)
      PrintFormat("XauAssistant: trading TF %s (chart %s — visual only)",
                  StringSubstr(EnumToString(TradeTimeframe), 7),
                  StringSubstr(EnumToString(Period()), 7));
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   if(!IsOwner()) return;   // superseded — OnTimer will remove this instance
   ENUM_TIMEFRAMES barTf = TradeTimeframe;
   CStrategy *active = g_registry.Active();
   if(active != NULL && active.TradeTf() != PERIOD_CURRENT)
      barTf = active.TradeTf();
   datetime bar = iTime(_Symbol, barTf, 0);
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

bool ApplyIziTemplate(long chartId)
  {
   if(chartId <= 0) return false;
   if(ChartApplyTemplate(chartId, "iziBot.tpl")) return true;
   if(ChartApplyTemplate(chartId, "default.tpl")) return true;
   if(ChartApplyTemplate(chartId, "XauAssistant.tpl")) return true;
   if(ChartApplyTemplate(chartId, "\\Files\\iziBot.tpl")) return true;
   if(ChartApplyTemplate(chartId, "\\Files\\default.tpl")) return true;
   if(ChartApplyTemplate(chartId, "\\Files\\XauAssistant.tpl")) return true;
   return false;
  }

bool IsGoldSymbol(const string sym)
  {
   return (StringFind(sym, "XAU") >= 0 || StringFind(sym, "xau") >= 0 ||
           StringFind(sym, "GOLD") >= 0 || StringFind(sym, "gold") >= 0);
  }

bool IsBtcSymbol(const string sym)
  {
   return (StringFind(sym, "BTC") >= 0 || StringFind(sym, "btc") >= 0);
  }

void OnTimer()
  {
   // Single-instance guard: a newer attachment claimed ownership — step down and close duplicate chart.
   if(!IsOwner())
     {
      PrintFormat("iziBot: superseded by a newer instance on %s — closing duplicate chart (id %I64d)",
                  _Symbol, ChartID());
      ChartClose(ChartID());
      return;
     }

   // Continuous strict 2-chart housekeeping:
   // MT5 must ONLY have exactly two charts: one Gold chart and one Bitcoin chart.
   // Any duplicate chart of our asset or non-whitelisted chart is closed.
   static datetime g_lastHousekeepingTime = 0;
   datetime nowHb = TimeCurrent();
   if(nowHb - g_lastHousekeepingTime >= 5)
     {
      g_lastHousekeepingTime = nowHb;
      long cid = ChartFirst();
      while(cid >= 0)
        {
         long nextCid = ChartNext(cid);
         if(cid != ChartID())
           {
            string cSym = ChartSymbol(cid);
            bool isGold = IsGoldSymbol(cSym);
            bool isBtc  = IsBtcSymbol(cSym);

            if(IsGoldSymbol(_Symbol) && isGold)
              {
               PrintFormat("iziBot: closing duplicate Gold chart (sym=%s, id=%I64d)", cSym, cid);
               ChartClose(cid);
              }
            else if(IsBtcSymbol(_Symbol) && isBtc)
              {
               PrintFormat("iziBot: closing duplicate Bitcoin chart (sym=%s, id=%I64d)", cSym, cid);
               ChartClose(cid);
              }
            else if(!isGold && !isBtc)
              {
               PrintFormat("iziBot: closing non-whitelisted chart (sym=%s, id=%I64d)", cSym, cid);
               ChartClose(cid);
              }
           }
         cid = nextCid;
        }
     }

   // Periodic auto-spawn / template re-attachment of Bitcoin chart from the Gold instance
   static datetime g_lastBtcSpawnCheck = 0;
   if(AutoSpawnBtcChart && IsGoldSymbol(_Symbol))
     {
      datetime now = TimeCurrent();
      if(now - g_lastBtcSpawnCheck >= 15)
        {
         g_lastBtcSpawnCheck = now;
         bool btcRunning = false;
         for(long cid = ChartFirst(); cid >= 0; cid = ChartNext(cid))
           {
            string sym = ChartSymbol(cid);
            if(IsBtcSymbol(sym))
              {
               string ea = ChartGetString(cid, CHART_EXPERT_NAME);
               if(ea != "")
                 {
                  btcRunning = true;
                  break;
                 }
               else
                 {
                  PrintFormat("iziBot: found %s chart (id %I64d) without EA, applying template...", sym, cid);
                  if(ApplyIziTemplate(cid))
                    {
                     btcRunning = true;
                     break;
                    }
                  else
                    {
                     PrintFormat("iziBot: ApplyIziTemplate failed on chart %I64d (err=%d)", cid, GetLastError());
                    }
                 }
              }
           }
         if(!btcRunning)
           {
            string btcCandidates[] = {BtcPreferredSymbol, "BTCUSD", "BTCUSDT", "BTCUSD.a", "BTCUSDm", "BTCUSDto"};
            string btcSym = "";
            bool is_custom = false;
            for(int i = 0; i < ArraySize(btcCandidates); i++)
              {
               if(SymbolExist(btcCandidates[i], is_custom))
                 {
                  btcSym = btcCandidates[i];
                  break;
                 }
              }
            if(btcSym != "")
              {
               SymbolSelect(btcSym, true);
               long btcCid = ChartOpen(btcSym, BtcTimeframe);
               if(btcCid > 0)
                 {
                  PrintFormat("iziBot: auto-spawned %s %s chart (id %I64d)",
                              btcSym, EnumToString(BtcTimeframe), btcCid);
                  if(!ApplyIziTemplate(btcCid))
                     PrintFormat("iziBot: ApplyIziTemplate failed on %I64d (err=%d)", btcCid, GetLastError());
                 }
              }
            else
              {
               Print("iziBot: auto-spawn BTC skipped (no matching Bitcoin symbol found in Market Watch)");
              }
           }
        }
     }

   // Periodic auto-spawn of Gold M15 chart from the Bitcoin instance
   static datetime g_lastXauSpawnCheck = 0;
   if(AutoSpawnBtcChart && IsBtcSymbol(_Symbol))
     {
      datetime now = TimeCurrent();
      if(now - g_lastXauSpawnCheck >= 15)
        {
         g_lastXauSpawnCheck = now;
         bool xauRunning = false;
         for(long cid = ChartFirst(); cid >= 0; cid = ChartNext(cid))
           {
            string sym = ChartSymbol(cid);
            if(IsGoldSymbol(sym))
              {
               string ea = ChartGetString(cid, CHART_EXPERT_NAME);
               if(ea != "")
                 {
                  xauRunning = true;
                  break;
                 }
               else
                 {
                  PrintFormat("iziBot: found %s chart (id %I64d) without EA, applying template...", sym, cid);
                  if(ApplyIziTemplate(cid))
                    {
                     xauRunning = true;
                     break;
                    }
                  else
                    {
                     PrintFormat("iziBot: ApplyIziTemplate failed on chart %I64d (err=%d)", cid, GetLastError());
                    }
                 }
              }
           }
         if(!xauRunning)
           {
            string xauCandidates[] = {"XAUUSD", "GOLD", "XAUUSD.a", "XAUUSDm", "XAUUSDto"};
            string xauSym = "";
            bool is_custom = false;
            for(int i = 0; i < ArraySize(xauCandidates); i++)
              {
               if(SymbolExist(xauCandidates[i], is_custom))
                 {
                  xauSym = xauCandidates[i];
                  break;
                 }
              }
            if(xauSym != "")
              {
               SymbolSelect(xauSym, true);
               long xauCid = ChartOpen(xauSym, PERIOD_M15);
               if(xauCid > 0)
                 {
                  PrintFormat("iziBot: auto-spawned %s M15 chart (id %I64d)", xauSym, xauCid);
                  if(!ApplyIziTemplate(xauCid))
                     PrintFormat("iziBot: ApplyIziTemplate failed on %I64d (err=%d)", xauCid, GetLastError());
                 }
              }
            else
              {
               Print("iziBot: auto-spawn Gold skipped (no matching Gold symbol found in Market Watch)");
              }
           }
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
   double floating_pl = (g_trades.OpenCount() > 0) ? g_trades.BasketProfit() : 0.0;
   CStrategy *active  = g_registry.Active();
   string activeId    = (active != NULL) ? active.Id() : "unknown";
   double spreadPts   = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   SampleSpread(spreadPts);

   string mode = "", entryModeResp = "", cmd = "", cmdDir = "", htfEnforce = "", ema200Enforce = "";
   string rsiEnforce = "", m15Adds = "", srEnforce = "";
    bool   entriesPausedResp = g_agentPaused;
    int    heartbeatSecResp = 0;
    double runtimeRiskPct = -1.0, runtimeMaxDdPct = -1.0;
    double runtimeProfitTargetPct = -1.0;
    int runtimeWinStart = -1, runtimeWinEnd = -1, runtimeMaxExpoMin = -1;
   long cmdId = 0;
   bool algoTrading = TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0;
   string entryModeStr = (g_entryMode == ENTRY_HYBRID) ? "hybrid" : ((g_entryMode == ENTRY_FIXED) ? "fixed" : "adr");
   string sw = g_ui.PostHeartbeat(equity, balance, floating_pl,
                                  g_risk.KillSwitchTripped(), g_risk.HighWaterMark(),
                                  g_risk.ExposureMinutesUsed(), g_risk.InTradingWindow(),
                                  spreadPts, activeId, algoTrading, entryModeStr,
                                  g_heartbeatSec,
                                  mode, entryModeResp, cmd, cmdId, cmdDir, htfEnforce, ema200Enforce,
                                   rsiEnforce, m15Adds,
                                   srEnforce,
                                   entriesPausedResp,
                                   heartbeatSecResp,
                                   runtimeRiskPct, runtimeMaxDdPct,
                                   runtimeProfitTargetPct, runtimeWinStart,
                                   runtimeWinEnd, runtimeMaxExpoMin,
                                   g_risk.DailyLossUsedPct(), g_risk.BrakeResetToday(),
                                   g_news.UpcomingJson(), NewsBlackoutMin);
    g_risk.ApplyRuntimeConfig(runtimeRiskPct, runtimeMaxDdPct,
                              runtimeWinStart, runtimeWinEnd, runtimeMaxExpoMin);
    g_trades.SetProfitTargetPct(runtimeProfitTargetPct);
    // Supervisory-agent governor: update only on valid heartbeat responses and real changes
    if(mode != "" && entriesPausedResp != g_agentPaused)
      {
       g_agentPaused = entriesPausedResp;
       Print("XauAssistant: agent governor -> ",
             (g_agentPaused ? "new entries PAUSED" : "new entries ACTIVE"));
      }
    // Service-authoritative heartbeat interval: re-arm only on a real change
    // so a 1 s beat does not churn the timer every tick.
    if(heartbeatSecResp >= 1 && heartbeatSecResp <= 60 && heartbeatSecResp != g_heartbeatSec)
      {
       g_heartbeatSec = heartbeatSecResp;
       EventKillTimer();
       EventSetTimer(g_heartbeatSec);
       Print("XauAssistant: heartbeat interval -> ", g_heartbeatSec, "s (from service)");
      }
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
   // RSI 70/30 enforcement + M15 adds switch (2026-09-07): pushed to every
   // lane; ineligible lanes (M5) ignore them by construction.
   if(rsiEnforce != "")
      for(int si3 = 0; si3 < g_registry.Count(); si3++)
        {
         CStrategy *st3 = g_registry.Get(si3);
         if(st3 != NULL) st3.SetRsiOverride(rsiEnforce);
        }
   if(m15Adds != "")
      for(int si4 = 0; si4 < g_registry.Count(); si4++)
        {
         CStrategy *st4 = g_registry.Get(si4);
         if(st4 != NULL) st4.SetAddsOverride(m15Adds);
        }
   if(srEnforce != "")
     {
      for(int si5 = 0; si5 < g_registry.Count(); si5++)
        {
         CStrategy *st5 = g_registry.Get(si5);
         if(st5 != NULL) st5.SetSrOverride(srEnforce);
        }
      g_trades.SetSrExitOverride(srEnforce);
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
   if(entryModeResp == "adr" || entryModeResp == "fixed" || entryModeResp == "hybrid")
     {
      ENUM_ENTRY_MODE want = (entryModeResp == "hybrid") ? ENTRY_HYBRID : ((entryModeResp == "fixed") ? ENTRY_FIXED : ENTRY_ADR);
      if(want != g_entryMode)
        {
         g_entryMode = want;
         string mstr = (want == ENTRY_HYBRID ? "HYBRID" : (want == ENTRY_FIXED ? "FIXED" : "ADR"));
         Print("iziBot: entry mode -> ", mstr, " — applies to the next trade");
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
         // newsOverride=true: an approved execute during a blackout is the
         // owner's explicit decision (2026-09-01 blackout-goes-manual flow).
         if(!g_risk.CanEnter(why, true))
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
                                          (g_entryMode == ENTRY_FIXED), FixedLots,
                                          true, (int)g_entryMode);   // owner-approved
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
   else if(cmd == "move_sl_be")
     {
      string detail = "";
      bool ok = g_trades.MoveStopsBreakeven(detail);
      g_ui.PostProposalResult(cmdId, ok, detail);
     }
   else if(cmd == "close_half")
     {
      string detail = "";
      bool ok = g_trades.CloseHalf(detail);
      g_ui.PostProposalResult(cmdId, ok, detail);
     }

   else if(cmd == "close_ticket")
     {
      ulong tk = (ulong)StringToInteger(cmdDir);
      bool ok = false;
      if(tk > 0)
        {
         ok = g_trades.CloseTicket(tk);
         if(!ok)
           {
            for(int pi = PositionsTotal() - 1; pi >= 0; pi--)
              {
               if(PositionGetTicket(pi) == tk)
                 {
                  ok = g_trades.CloseTicket(tk);
                  break;
                 }
              }
           }
        }
      if(!ok && g_trades.OpenCount() == 1)
        {
         g_trades.CloseAll("remote exit fallback");
         ok = (g_trades.OpenCount() == 0);
        }
      g_ui.PostProposalResult(cmdId, ok, ok ? "position closed" : "position close failed");
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
   g_chartPanels.Deinit();
   g_srBoxes.Clear();
   g_volumeProfile.Clear();
   g_tradeBoxes.Clear();
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
   // Advance the S/R zone tiers that closed a bar with this one, then
   // repaint the boxes (cheap: nothing is redrawn unless a tier changed or
   // the chart opened a new bar). Runs BEFORE the strategies evaluate so a
   // confirm on this bar is judged against zones that include it.
   g_srZones.Update();
   g_srBoxes.Redraw();
   if(ShowVolumeProfile && g_volumeProfile.AnchorMode() == VP_ANCHOR_RIGHT_MARGIN)
      g_volumeProfile.DrawOnEntry(0, 0, VpLookbackBars);

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
         SyncAtrToActiveLane();
         if(g_registry.Active().TradeTf() != PERIOD_CURRENT)
           {
            g_api.SetTimeframe(g_registry.Active().TradeTf());
            g_ui.SetTimeframe(g_registry.Active().TradeTf());
           }
         // Print only — no MT5 Alert() popup (owner 2026-09-02: the service
         // re-asserts the lane after EVERY EA reinit, so the popup fired on
         // every restart and became noise). The switch is still visible in
         // the expert log and confirmed on Telegram's /mode.
         Print("XauAssistant: switched active strategy to '", sw, "'");
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
      // Supervisory-agent governor: a paused agent refuses NEW entries only.
      // The strategy is told the entry was rejected (same contract as a risk
      // refusal) so it cannot hold a phantom position; Manage() below still
      // runs, so an open basket keeps its target/lock/stop management.
      bool opened = false;
      if(!g_agentPaused)
        {
         opened = g_trades.OnSignal(sig, atrVal, active.StopPrice(sig),
                                    (g_entryMode == ENTRY_FIXED), FixedLots,
                                    false, (int)g_entryMode);
        }
      else if(sig == SIGNAL_BUY || sig == SIGNAL_SELL)
        {
         active.OnEntryRejected(sig);
         string symLabel = (StringFind(_Symbol, "BTC") >= 0 ? "₿ BTC" : "🥇 Gold");
         PrintFormat("iziBot: %s %s signal skipped — %s auto-entries are currently PAUSED (tap [%s: ▶️ PLAY] in Telegram to resume)",
                     _Symbol, SignalToString(sig), _Symbol, symLabel);
         g_ui.PostNotify(StringFormat("⏸️ AUTO %s (%s) skipped: auto-entries PAUSED\nTap [ %s: ▶️ PLAY ] to resume.",
                                      SignalToString(sig), _Symbol, symLabel));
        }
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
      if(!g_agentPaused && !opened && (sig == SIGNAL_BUY || sig == SIGNAL_SELL) && g_trades.BasketDirection() != sig)
        {
         active.OnEntryRejected(sig);
         string why = "";
         if(!g_risk.CanEnter(why)) { /* why set */ }
         else why = "order send failed (check Algo Trading button / broker)";
         g_ui.PostNotify("🚫 AUTO " + SignalToString(sig) + " not executed: " + why);
        }
      ENUM_SIGNAL basketDir = g_trades.BasketDirection();
      // The active lane decides whether pyramid adds are allowed this bar
      // (2026-09-07: the M15 lane rides without adds by default).
      g_trades.SetAddsAllowed(active.AllowsAdds());
      g_trades.Manage(atrVal, active.ConditionStillTrue(basketDir == SIGNAL_NONE ? sig : basketDir), g_risk.GetCurrentAdx());

      // Check continuation re-entry if basket exited on heavy reversal but trend is still intact
      if(g_trades.OpenCount() == 0 && !g_agentPaused)
        {
         ENUM_SIGNAL armedDir = g_trades.GetReentryDir();
         if(armedDir != SIGNAL_NONE && active.ConditionStillTrue(armedDir))
           {
            ENUM_SIGNAL reSig = SIGNAL_NONE;
            double reSl = 0.0;
            if(g_trades.CheckContinuationReentry(atrVal, true, reSig, reSl))
              {
               bool reOpened = g_trades.OnSignal(reSig, atrVal, reSl,
                                                 (g_entryMode == ENTRY_FIXED), FixedLots,
                                                 false, (int)g_entryMode);
               if(reOpened)
                  g_ui.PostNotify("🔄 AUTO continuation re-entry: " + SignalToString(reSig));
              }
           }
        }
     }
   // MANUAL-mode baskets (opened via a Telegram-approved "execute" command
   // in OnTimer) never pass through the AUTO branch above, so without this
   // they'd get no profit-target close, no breakeven-on-add, and no
   // pyramiding — Manage() must still run for them every bar. Note this can
   // pyramid-add into a winning MANUAL basket just like AUTO does; that's
   // existing, accepted Manage() behavior, not new risk introduced here.
   else if(g_execMode == EXEC_MANUAL && g_trades.OpenCount() > 0 && atrVal > 0)
     {
      g_trades.SetAddsAllowed(active.AllowsAdds());
      g_trades.Manage(atrVal, active.ConditionStillTrue(g_trades.BasketDirection()), g_risk.GetCurrentAdx());
     }


   if(sig == SIGNAL_NONE && ArraySize(shadowIds) == 0)
     {
      AiResponse quiet;
      g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, quiet,
                    g_barSprMin, g_barSprAvg, g_barSprMax, g_risk.NewsBlackout());
      return;   // keeps outcome-resolution data flowing (spec 6.3)
     }
   AiResponse r;
   bool ok = g_api.Analyze(sig, active.Id(), shadowIds, shadowSigs, r,
                           g_barSprMin, g_barSprAvg, g_barSprMax, g_risk.NewsBlackout());
   if(sig == SIGNAL_NONE) return;        // shadows logged; nothing to alert
   if(ShowVolumeProfile)
     {
      double entryP = (sig == SIGNAL_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                          : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      g_volumeProfile.DrawOnEntry(g_lastBar, entryP, VpLookbackBars);
     }
   string report = g_sm.BuildReport(sig, r, ok) + "\n" + g_risk.Status();
   g_alerts.Draw(sig, report);
   g_alerts.Notify(report);
  }
