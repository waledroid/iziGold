#ifndef XAU_RISKMANAGER_MQH
#define XAU_RISKMANAGER_MQH

class CRiskManager
  {
private:
   double m_riskPct, m_maxDdPct, m_maxSpread, m_adxThreshold;
   int    m_winStart, m_winEnd, m_maxExpoMin;
   int    m_adxHandle;
   long   m_login;

   string Key(string tag) { return "XAU_" + tag + "_" + (string)m_login + "_" + _Symbol; }
   string ExpoKey()
     {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      return Key("EXPO") + "_" + StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);
     }

public:
   void Init(double riskPct, double maxDdPct, double maxSpread, double adxThr,
             int winStart, int winEnd, int maxExpoMin)
     {
      m_riskPct = riskPct; m_maxDdPct = maxDdPct; m_maxSpread = maxSpread;
      m_adxThreshold = adxThr; m_winStart = winStart; m_winEnd = winEnd;
      m_maxExpoMin = maxExpoMin;
      m_login = AccountInfoInteger(ACCOUNT_LOGIN);
      m_adxHandle = iADX(_Symbol, PERIOD_CURRENT, 14);
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
         GlobalVariableSet(ExpoKey(), mins + PeriodSeconds(PERIOD_CURRENT) / 60.0);
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
