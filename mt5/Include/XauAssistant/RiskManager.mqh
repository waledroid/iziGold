#ifndef XAU_RISKMANAGER_MQH
#define XAU_RISKMANAGER_MQH
#include <XauAssistant/NewsGuard.mqh>

class CRiskManager
  {
private:
   double m_riskPct, m_maxDdPct, m_maxSpread, m_adxThreshold;
   double m_maxDailyLossPct;
   int    m_winStart, m_winEnd, m_maxExpoMin;
   int    m_adxHandle;
   long   m_login, m_magic;
   ENUM_TIMEFRAMES m_tf;
   CNewsGuard *m_news;   // injected (may be NULL) — fail-open when absent
   // per-bar cache for the daily realized-loss scan (HistorySelect is not
   // free; entries/adds are bar-cadence anyway)
   datetime m_dlCacheBar;
   double   m_dlRealized;
   datetime m_dlLastWarn;   // throttle for the HistorySelect-failure warn

   // Once-per-hour Print, same shape as CNewsGuard::WarnThrottled — a broker
   // history outage must be visible in the Experts log, not silent.
   void WarnThrottled(string msg)
     {
      datetime now = TimeCurrent();
      if(now - m_dlLastWarn < 3600) return;   // once per hour
      m_dlLastWarn = now;
      Print(msg);
     }

   string Key(string tag) { return "XAU_" + tag + "_" + (string)m_login + "_" + _Symbol; }
   string ExpoKey()
     {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      return Key("EXPO") + "_" + StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
     }

public:
   void Init(double riskPct, double maxDdPct, double maxSpread, double adxThr,
             int winStart, int winEnd, int maxExpoMin,
             double maxDailyLossPct, long magic, ENUM_TIMEFRAMES tf, CNewsGuard *news = NULL)
     {
      m_riskPct = riskPct; m_maxDdPct = maxDdPct; m_maxSpread = maxSpread;
      m_adxThreshold = adxThr; m_winStart = winStart; m_winEnd = winEnd;
      m_maxExpoMin = maxExpoMin;
      m_maxDailyLossPct = maxDailyLossPct; m_magic = magic;
      m_tf = tf;
      m_news = news;
      m_dlCacheBar = 0; m_dlRealized = 0; m_dlLastWarn = 0;
      m_login = AccountInfoInteger(ACCOUNT_LOGIN);
      m_adxHandle = iADX(_Symbol, m_tf, 14);
     }

   void OnBarUpdate()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      if(eq > hwm) { hwm = eq; GlobalVariableSet(Key("HWM"), hwm); }
      if(hwm > 0 && eq <= hwm * (1.0 - m_maxDdPct / 100.0))
         GlobalVariableSet(Key("KILL"), 1);
      // accumulate exposure: bar minutes while a position of ours is open
      if(PositionsTotal() > 0)
        {
         double mins = GlobalVariableGet(ExpoKey());
         GlobalVariableSet(ExpoKey(), mins + PeriodSeconds(m_tf) / 60.0);
        }
     }

   bool KillSwitchTripped() { return GlobalVariableGet(Key("KILL")) > 0; }

   // Read-only wrappers for the UI heartbeat (Task 4) — expose internals without
   // changing risk-decision behavior.
   double HighWaterMark()      { return GlobalVariableGet(Key("HWM")); }
   int    ExposureMinutesUsed(){ return (int)GlobalVariableGet(ExpoKey()); }
   double RiskPct()             { return m_riskPct; }
   bool   InTradingWindow()
     {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      return dt.hour >= m_winStart && dt.hour < m_winEnd;
     }

   // TODAY's realized P/L from our own closed deals (symbol+magic) since
   // server midnight — broker history is the source of truth (no global-var
   // state, reload-safe). Includes profit AND swap/commission of every own
   // deal in the window (entry deals contribute their commission too).
   // Cached per bar: the HistorySelect scan runs at most once per new bar.
   double TodayRealized()
     {
      datetime bar = iTime(_Symbol, m_tf, 0);
      if(bar != 0 && bar == m_dlCacheBar) return m_dlRealized;
      double realized = 0;
      datetime now = TimeCurrent();
      datetime dayStart = now - now % 86400;                     // server midnight
      ResetLastError();
      if(HistorySelect(dayStart, now + 60))                      // fail-open on scan failure
        {
         for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
           {
            ulong tk = HistoryDealGetTicket(i);
            if(tk == 0) continue;
            if(HistoryDealGetString(tk, DEAL_SYMBOL) != _Symbol) continue;
            if(HistoryDealGetInteger(tk, DEAL_MAGIC) != m_magic) continue;
            realized += HistoryDealGetDouble(tk, DEAL_PROFIT)
                      + HistoryDealGetDouble(tk, DEAL_SWAP)
                      + HistoryDealGetDouble(tk, DEAL_COMMISSION);
           }
        }
      else
         WarnThrottled(StringFormat(
            "RiskManager: HistorySelect failed (error %d) — daily loss brake fails OPEN, today's realized P/L read as 0",
            GetLastError()));
      m_dlCacheBar = bar;
      m_dlRealized = realized;
      return realized;
     }

   // Drop the per-bar cache so the next TodayRealized() rescans history.
   // Called from the EA's OnTradeTransaction on every own closing deal:
   // broker-side stop-outs land mid-bar, and a Telegram-approved execute can
   // arrive seconds later via OnTimer — without this, CanEnter would read a
   // stale pre-loss figure for up to a full bar.
   void InvalidateDailyCache() { m_dlCacheBar = 0; }

   // Daily loss brake: true when today's realized loss has reached
   // MaxDailyLossPct% of the day's starting balance (approximated as
   // current balance minus today's realized P/L). Blocks NEW exposure only —
   // entries and pyramid adds — never exits. 0 = disabled.
   bool DailyLossBreached()
     {
      if(m_maxDailyLossPct <= 0) return false;
      double realized = TodayRealized();
      if(realized >= 0) return false;
      double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE) - realized;
      if(dayStartBal <= 0) return false;                         // fail-open on nonsense state
      return realized <= -dayStartBal * m_maxDailyLossPct / 100.0;
     }

   // News blackout: high-importance USD calendar event within ±blackout of
   // now (see NewsGuard.mqh — cached, fail-open). Like DailyLossBreached,
   // this gates NEW exposure only (entries here, pyramid adds explicitly in
   // TradeManager::Manage) and never blocks exits.
   bool NewsBlackout() { return m_news != NULL && m_news.InBlackout(); }

   bool TrendOK()
     {
      double adx[];
      if(CopyBuffer(m_adxHandle, 0, 1, 1, adx) != 1) return false;
      return adx[0] >= m_adxThreshold;
     }

   bool CanEnter(string &why)
     {
      why = "";
      if(KillSwitchTripped())                          { why = "kill switch tripped"; return false; }
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      if(dt.hour < m_winStart || dt.hour >= m_winEnd)  { why = "outside trading window"; return false; }
      if(GlobalVariableGet(ExpoKey()) >= m_maxExpoMin) { why = "daily exposure spent"; return false; }
      if(DailyLossBreached())                          { why = "daily loss limit"; return false; }
      if(NewsBlackout())                               { why = "news blackout"; return false; }
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > m_maxSpread)                         { why = "spread too wide"; return false; }
      if(!TrendOK())                                   { why = "ADX below threshold"; return false; }
      return true;
     }

   double CalcLots(double sl_points, double ratio)
     {
      double eq        = AccountInfoDouble(ACCOUNT_EQUITY);
      double tick_val  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick_size <= 0 || tick_val <= 0) return 0;
      double loss_per_lot = sl_points * _Point / tick_size * tick_val;
      if(loss_per_lot <= 0) return 0;
      double lots = (eq * m_riskPct / 100.0 * ratio) / loss_per_lot;
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
      lots = MathFloor(lots / step) * step;
      return MathMin(MathMax(lots, vmin), vmax);
     }

   string Status()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      double dd = (hwm > 0) ? (1.0 - eq / hwm) * 100.0 : 0.0;
      return StringFormat("MoneyWatch: risk %.2f%%/trade, DD %.1f%% of %.1f%% limit, expo %.0f/%d min",
                          m_riskPct, dd, m_maxDdPct, GlobalVariableGet(ExpoKey()), m_maxExpoMin);
     }
  };
#endif
