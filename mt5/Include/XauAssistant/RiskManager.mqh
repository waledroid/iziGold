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
   long   m_login;
   // Magics whose deals count toward the daily-loss brake. A fixed-size
   // slot array (MQL5 has no set type) — Init() seeds slot 0 from its
   // single-magic parameter so the brake stays bit-identical to the old
   // `!= m_magic` test until AddMagic() registers a second lane.
   long   m_magics[4];
   int    m_magicCount;
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

   // Server day as a YYYYMMDD number (same construction as ExpoKey's suffix).
   double TodayNumber()
     {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      return (double)(dt.year * 10000 + dt.mon * 100 + dt.day);
     }

   // Brake reset base (2026-08-18): when the owner tapped [Reset brake for
   // today] the brake measures today's loss from the realized P/L AT RESET
   // TIME instead of from midnight. Both globals are ignored (treated
   // absent) unless XAU_BRAKE_RESET_<login>_<symbol> == today's server date,
   // so a rollover clears the reset implicitly and a stale/missing global
   // fails open to the plain since-midnight measure.
   double BrakeBase()
     {
      if(!BrakeResetToday()) return 0.0;
      // Hardening: the base is a realized LOSS at reset time (≤ 0). A
      // positive value can't loosen anything but is nonsense → clamp to 0;
      // a loss deeper than the whole balance is corrupt → treat as no reset
      // (fail-open to the plain since-midnight measure).
      double base = MathMin(GlobalVariableGet(Key("BRAKE_BASE")), 0.0);
      if(base < -AccountInfoDouble(ACCOUNT_BALANCE)) return 0.0;
      return base;
     }
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
      m_maxDailyLossPct = maxDailyLossPct;
      m_tf = tf;
      m_news = news;
      m_dlCacheBar = 0; m_dlRealized = 0; m_dlLastWarn = 0;
      m_login = AccountInfoInteger(ACCOUNT_LOGIN);
      m_adxHandle = iADX(_Symbol, m_tf, 14);
      m_magicCount = 0;
      AddMagic(magic);
     }

   // Register an additional magic number whose deals count toward the
   // daily-loss brake (e.g. a second trading lane sharing this account's
   // protection). Duplicates are ignored; once every slot is used, further
   // calls are silently ignored rather than overflowing the array — the
   // caller does not need to check capacity.
   void AddMagic(long m)
     {
      for(int i = 0; i < m_magicCount; i++)
         if(m_magics[i] == m) return;                  // already registered
      if(m_magicCount >= ArraySize(m_magics)) return;   // no free slot
      m_magics[m_magicCount++] = m;
     }

   // True if `m` is one of the registered magics.
   bool HasMagic(long m)
     {
      for(int i = 0; i < m_magicCount; i++)
         if(m_magics[i] == m) return true;
      return false;
     }

   void OnBarUpdate()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      if(eq > hwm) { hwm = eq; GlobalVariableSet(Key("HWM"), hwm); }
      if(hwm > 0 && eq <= hwm * (1.0 - m_maxDdPct / 100.0))
         GlobalVariableSet(Key("KILL"), 1);
      // Accumulate exposure: bar minutes while a position OF OURS is open.
      // PositionsTotal() alone counts every position on the ACCOUNT -- another
      // EA, another symbol, or a hand-placed trade -- so the budget could burn
      // down while this EA held nothing, and then refuse its own entries. The
      // comment always claimed "of ours"; the code did not (found 2026-08-21).
      // Now filtered on symbol AND any registered magic, which also makes it
      // correct for a second lane.
      if(OwnPositionOpen())
        {
         double mins = GlobalVariableGet(ExpoKey());
         GlobalVariableSet(ExpoKey(), mins + PeriodSeconds(m_tf) / 60.0);
        }
     }

   // Any position on THIS symbol carrying one of our magics.
   bool OwnPositionOpen()
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong tk = PositionGetTicket(i);
         if(tk == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(HasMagic(PositionGetInteger(POSITION_MAGIC))) return true;
        }
      return false;
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

   // TODAY's realized P/L from our own closed deals (symbol + any registered
   // magic) since server midnight — broker history is the source of truth
   // (no global-var state, reload-safe). Includes profit AND swap/commission
   // of every own deal in the window (entry deals contribute their
   // commission too).
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
            if(!HasMagic(HistoryDealGetInteger(tk, DEAL_MAGIC))) continue;
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
   // When the brake was reset today, `realized` is measured from the reset
   // base (realized − base): the brake re-arms after ANOTHER MaxDailyLossPct%
   // loss — a reset can never become unlimited bleeding.
   bool DailyLossBreached()
     {
      if(m_maxDailyLossPct <= 0) return false;
      double realized = TodayRealized() - BrakeBase();
      if(realized >= 0) return false;
      double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE) - realized;
      if(dayStartBal <= 0) return false;                         // fail-open on nonsense state
      return realized <= -dayStartBal * m_maxDailyLossPct / 100.0;
     }

   // Brake awareness (2026-08-18): today's realized loss (since the reset
   // base when reset today) as a % of the brake threshold. 0 when disabled,
   // in profit, or on nonsense state; 100+ once the brake is (or would be)
   // tripped. Read-only — shares DailyLossBreached's arithmetic exactly.
   double DailyLossUsedPct()
     {
      if(m_maxDailyLossPct <= 0) return 0.0;
      double realized = TodayRealized() - BrakeBase();
      if(realized >= 0) return 0.0;
      double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE) - realized;
      if(dayStartBal <= 0) return 0.0;
      double threshold = dayStartBal * m_maxDailyLossPct / 100.0;
      if(threshold <= 0) return 0.0;
      return -realized / threshold * 100.0;
     }
   // Dollar figures for the awareness messages (same base as above).
   double DailyLossUsedUsd()
     {
      double realized = TodayRealized() - BrakeBase();
      return (realized < 0) ? -realized : 0.0;
     }
   double DailyLossThresholdUsd()
     {
      if(m_maxDailyLossPct <= 0) return 0.0;
      double realized = TodayRealized() - BrakeBase();
      double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE) - MathMin(realized, 0.0);
      return (dayStartBal > 0) ? dayStartBal * m_maxDailyLossPct / 100.0 : 0.0;
     }
   double MaxDailyLossPct() { return m_maxDailyLossPct; }
   double MaxDdPct()        { return m_maxDdPct; }
   // Current drawdown from the equity high-water mark, in % (0 when no HWM).
   double DrawdownPct()
     {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double hwm = GlobalVariableGet(Key("HWM"));
      return (hwm > 0) ? MathMax(0.0, (1.0 - eq / hwm) * 100.0) : 0.0;
     }

   bool BrakeResetToday()
     {
      if(!GlobalVariableCheck(Key("BRAKE_RESET"))) return false;
      return GlobalVariableGet(Key("BRAKE_RESET")) == TodayNumber();
     }
   // Owner-approved [Reset brake for today] (Telegram brakereset: →
   // heartbeat cmd "reset_brake"): stamp today's server date and the
   // realized P/L now as the new measuring base. Drops the read cache so
   // the base reflects every deal up to this instant. Never touches the
   // kill switch / HWM — that stays a deliberate XauMaintenance action.
   void ResetDailyBrake()
     {
      InvalidateDailyCache();
      GlobalVariableSet(Key("BRAKE_BASE"), TodayRealized());
      GlobalVariableSet(Key("BRAKE_RESET"), TodayNumber());
     }

   // ---- Brake & kill-switch awareness latches (2026-08-18) ----------------
   // Each latch is a per-symbol MT5 global so an EA restart/recompile does
   // not re-warn. Brake latches store the server date (YYYYMMDD) they fired
   // on and are considered UNSET on any other date (rollover re-arms them);
   // the drawdown/kill latches store 1/0. A latch re-arms when its metric
   // drops back below the threshold (DD with a 1-pt hysteresis so equity
   // ticking around the line can't spam), then fires again on the next
   // crossing. Pure read/notify state — never touches a trading decision.
   bool LatchDated(string tag)   { return GlobalVariableCheck(Key(tag)) && GlobalVariableGet(Key(tag)) == TodayNumber(); }
   bool LatchFlag(string tag)    { return GlobalVariableGet(Key(tag)) > 0; }
   void SetLatchDated(string tag, bool on) { GlobalVariableSet(Key(tag), on ? TodayNumber() : 0.0); }
   void SetLatchFlag(string tag, bool on)  { GlobalVariableSet(Key(tag), on ? 1.0 : 0.0); }

   // Returns true and fills text/button when ONE awareness message should
   // be sent now (call in a loop until false — at most four per tick).
   // button ∈ {"", "reset_brake"}; the kill switch is deliberately NOT
   // resettable from Telegram (XauMaintenance only).
   bool PollAwareness(string &text, string &button)
     {
      text = ""; button = "";
      // 1. daily loss brake 70% warning
      if(m_maxDailyLossPct > 0)
        {
         double used = DailyLossUsedPct();
         bool warnOn = LatchDated("BRAKE_WARN70");
         if(used >= 70.0 && used < 100.0 && !warnOn)
           {
            SetLatchDated("BRAKE_WARN70", true);
            text = StringFormat("⚠️ Daily loss brake at %.0f%% (−$%.0f of −$%.0f) — one more loss ends the day",
                                used, DailyLossUsedUsd(), DailyLossThresholdUsd());
            button = "reset_brake";
            return true;
           }
         if(used < 70.0 && warnOn) SetLatchDated("BRAKE_WARN70", false);
         // 2. brake tripped
         bool tripOn = LatchDated("BRAKE_TRIPPED");
         bool tripped = DailyLossBreached();
         if(tripped && !tripOn)
           {
            SetLatchDated("BRAKE_TRIPPED", true);
            SetLatchDated("BRAKE_WARN70", true);   // 70% is moot once tripped
            text = "🛑 Daily loss brake TRIPPED — no new entries until midnight (server)";
            button = "reset_brake";
            return true;
           }
         if(!tripped && tripOn) SetLatchDated("BRAKE_TRIPPED", false);
        }
      // 3. drawdown at 80% of the kill distance
      if(m_maxDdPct > 0)
        {
         double dd = DrawdownPct();
         double warnAt = m_maxDdPct * 0.8;
         bool ddOn = LatchFlag("DD80");
         if(dd >= warnAt && !ddOn && !KillSwitchTripped())
           {
            SetLatchFlag("DD80", true);
            text = StringFormat("⚠️ Drawdown %.1f%% from peak — kill switch arms at %.0f%%", dd, m_maxDdPct);
            return true;
           }
         if(dd < warnAt - 1.0 && ddOn) SetLatchFlag("DD80", false);
        }
      // 4. kill switch tripped
      bool killOn = LatchFlag("KILLWARN");
      bool killed = KillSwitchTripped();
      if(killed && !killOn)
        {
         SetLatchFlag("KILLWARN", true);
         text = "⛔ KILL SWITCH TRIPPED — trading halted; reset via XauMaintenance";
         return true;
        }
      if(!killed && killOn) SetLatchFlag("KILLWARN", false);
      return false;
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
