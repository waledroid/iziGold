//+------------------------------------------------------------------+
//| XauMaintenance.mq5                                               |
//| One-shot maintenance script for the XAU Assistant EA.            |
//| Drag onto a chart: lists every XAU_* terminal global variable    |
//| with a human interpretation for the CURRENT login+symbol, then   |
//| applies the requested resets (current login+symbol keys ONLY).   |
//| Pure inspection by default — all reset inputs are false.         |
//| Key shapes (spec 2026-08-09 §5, must match RiskManager.Key()/    |
//| ExpoKey() and TradeManager.CycleKey()/PeakKey()):                |
//|   XAU_<name>_<login>_<symbol>            (KILL, HWM, CYCLE_BAL,  |
//|                                           PEAK)                  |
//|   XAU_EXPO_<login>_<symbol>_<YYYYMMDD>   (dated, server day)     |
//+------------------------------------------------------------------+
#property script_show_inputs
#property description "Inspect and optionally reset the XAU Assistant's terminal global variables for this chart's login+symbol."

input bool ResetKillSwitch = false;  // Delete the kill-switch key (re-arms trading)
input bool ResetPeak       = false;  // Zero the peak basket profit (profit-lock reference)
input bool ResetCycle      = false;  // Re-seed cycle balance to current balance (profit-target base)
input bool ResetExposure   = false;  // Delete today's exposure-minutes key

void OnStart()
  {
   long   login  = AccountInfoInteger(ACCOUNT_LOGIN);
   string suffix = "_" + (string)login + "_" + _Symbol;

   // Server-clock day, same construction as RiskManager::ExpoKey().
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   string today = StringFormat("%04d%02d%02d", dt.year, dt.mon, dt.day);

   string killKey  = "XAU_KILL"      + suffix;
   string hwmKey   = "XAU_HWM"       + suffix;
   string cycleKey = "XAU_CYCLE_BAL" + suffix;
   string peakKey  = "XAU_PEAK"      + suffix;
   string expoBase = "XAU_EXPO"      + suffix + "_";   // + YYYYMMDD
   string expoKey  = expoBase + today;

   PrintFormat("XauMaintenance: inspecting globals for login %I64d, symbol %s, server day %s",
               login, _Symbol, today);

   int listed = 0;
   int total  = GlobalVariablesTotal();
   for(int i = 0; i < total; i++)
     {
      string name = GlobalVariableName(i);
      if(StringFind(name, "XAU_") != 0)
         continue;
      listed++;
      double value = GlobalVariableGet(name);
      string what;
      if(name == killKey)
         what = (value > 0) ? "kill switch TRIPPED — blocks all entries until reset"
                            : "kill switch key present but not tripped";
      else if(name == hwmKey)
         what = "equity high-water mark (drawdown kill-switch reference)";
      else if(name == cycleKey)
         what = "cycle start balance (profit-target base)";
      else if(name == peakKey)
         what = "peak basket profit (proportional profit-lock reference)";
      else if(name == expoKey)
         what = "TODAY's exposure minutes used";
      else if(StringFind(name, expoBase) == 0)
         what = "dated exposure key for another day (inert; expires via 4-week global TTL)";
      else
         what = "unknown/legacy (different login/symbol or old key shape)";
      PrintFormat("XauMaintenance: %s = %.2f — %s", name, value, what);
     }
   if(listed == 0)
      Print("XauMaintenance: no XAU_ globals found");

   int resets = 0;

   if(ResetKillSwitch)
     {
      if(GlobalVariableCheck(killKey))
        {
         GlobalVariableDel(killKey);
         PrintFormat("XauMaintenance: RESET kill switch — deleted %s", killKey);
         resets++;
        }
      else
         PrintFormat("XauMaintenance: kill switch reset requested but %s does not exist — nothing to do", killKey);
     }

   if(ResetPeak)
     {
      GlobalVariableSet(peakKey, 0);
      PrintFormat("XauMaintenance: RESET peak profit — %s set to 0", peakKey);
      resets++;
     }

   if(ResetCycle)
     {
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      GlobalVariableSet(cycleKey, bal);
      PrintFormat("XauMaintenance: RESET cycle balance — %s set to current balance %.2f", cycleKey, bal);
      resets++;
     }

   if(ResetExposure)
     {
      if(GlobalVariableCheck(expoKey))
        {
         GlobalVariableDel(expoKey);
         PrintFormat("XauMaintenance: RESET exposure — deleted today's key %s", expoKey);
         resets++;
        }
      else
         PrintFormat("XauMaintenance: exposure reset requested but %s does not exist — nothing to do", expoKey);
     }

   Alert(StringFormat("XauMaintenance: %d XAU_ globals listed, %d resets performed. Details in the Experts log.",
                      listed, resets));
  }
//+------------------------------------------------------------------+
