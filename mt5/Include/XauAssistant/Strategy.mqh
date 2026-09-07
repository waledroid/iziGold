// Strategy.mqh — strategy interface. Concrete strategies live in
// Include/XauAssistant/Strategies/ and register in the EA's OnInit.
#ifndef XAU_STRATEGY_MQH
#define XAU_STRATEGY_MQH

enum ENUM_SIGNAL { SIGNAL_NONE, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_EXIT };

string SignalToString(ENUM_SIGNAL s)
  {
   switch(s)
     {
      case SIGNAL_BUY:  return "BUY";
      case SIGNAL_SELL: return "SELL";
      case SIGNAL_EXIT: return "EXIT";
     }
   return "NONE";
  }

class CStrategy
  {
protected:
   bool m_paint;
public:
   CStrategy() : m_paint(false) {}
   // Painting: the EA enables this on the ACTIVE strategy only. Strategies
   // that support it draw their indicator state per closed bar; default no-op.
   virtual void EnablePaint(bool on) { m_paint = on; if(!on) ClearPaint(); }

   // The higher-timeframe verdict behind the current signal, for the trade
   // log: 1 agreed, 0 refused, -1 not evaluated / strategy has no HTF gate.
   // Default -1 so strategies without one need not implement it.
   virtual int LastHtfAgree() const { return -1; }

   // Runtime toggle for a strategy's higher-timeframe agreement module.
   // No-op for strategies that have none.
   virtual void SetHtfOverride(string v) {}

   // The EMA-200 (own-timeframe) verdict behind the current signal (owner
   // 2026-08-22): BUY agrees when price is above EMA-200, SELL below --
   // same split as LastHtfAgree/SetHtfOverride above (1 agreed, 0 refused,
   // -1 not evaluated / strategy has no EMA-200 gate).
   virtual int LastEma200Agree() const { return -1; }

   // Runtime toggle for a strategy's EMA-200 agreement module. No-op for
   // strategies that have none.
   virtual void SetEma200Override(string v) {}

   // RSI(14, own-timeframe) verdict behind the current signal (owner
   // 2026-09-02, after the sweep found M15+RSI-70 better in both halves):
   // BUY agrees when RSI < 70, SELL when RSI > 30. REPORT-ONLY — this
   // never blocks anything; it exists so live trades accumulate evidence.
   // 1 agreed, 0 refused, -1 not evaluated / strategy has no RSI module.
   virtual int LastRsiAgree() const { return -1; }
   // Runtime toggle for a strategy's RSI 70/30 enforcement (owner "go"
   // 2026-09-07; sweep: M15+RSI-70 +7% net at -33% dd, both halves; FAILS
   // on M5). "" follow the EA input, "off" report only, "on" enforce. A
   // lane that is not eligible (the M5 lane) ignores it. No-op by default.
   virtual void SetRsiOverride(string v) {}
   // May TradeManager pyramid-add into this lane's basket right now? The
   // EA asks the ACTIVE strategy before every Manage() pass. Default yes;
   // the M15 lane answers from its M15EnableAdds input / service override
   // (every trusted M15 result was a pure ride, no adds).
   virtual bool AllowsAdds() { return true; }
   virtual void SetAddsOverride(string v) {}

   // S/R zone verdict behind the current signal (owner 2026-09-05,
   // spec 2026-09-05-sr-zones-design.md): 1 = clear room ahead, 0 = the
   // entry sits inside or within SrRoomAtr of an opposing zone ("wall"),
   // -1 = not evaluated / no zone engine. REPORT-ONLY in increment 1 —
   // never blocks. LastSrTier() names the strongest wall ("H4"/"H1"/"M15")
   // behind a 0, "" otherwise.
   virtual int    LastSrAgree() const { return -1; }
   virtual string LastSrTier()  const { return ""; }
   virtual void ClearPaint() {}
   // Stable identifier — flows through the API into SQLite per-strategy stats.
   virtual string      Id() { return "stub"; }
   // The timeframe this strategy trades. PERIOD_CURRENT = "the EA's
   // TradeTimeframe input" (the default for strategies without their own
   // lane TF). The EA uses this to scale TradeManager's ATR to the ACTIVE
   // lane (2026-09-01: adds were spacing on M5 ATR while the M15 lane
   // traded — modeled at ~80% of the lane's net over 17 months).
   virtual ENUM_TIMEFRAMES TradeTf() { return PERIOD_CURRENT; }
   // Called once per closed bar.
   virtual ENUM_SIGNAL Evaluate() { return SIGNAL_NONE; }
   // True while the entry condition remains valid (pyramiding gate, spec 5b).
   virtual bool        ConditionStillTrue(ENUM_SIGNAL dir) { return false; }
   // Strategy's preferred stop for a new position; 0 = use the ATR default.
   virtual double      StopPrice(ENUM_SIGNAL dir) { return 0.0; }
   // Fired by the EA sink on every basket "close" event (TradeManager closes
   // and Task-5 stop-loss/TP transactions), for the ACTIVE strategy only.
   // closedDir is the direction of the basket that just closed (SIGNAL_BUY/
   // SIGNAL_SELL, or SIGNAL_NONE if the event carried no recognizable
   // direction). Direction-matched so a reversal's close of the OLD basket
   // doesn't clobber a virtual position a strategy already flipped to the
   // NEW direction for the position about to open in the same OnSignal call.
   virtual void        OnBasketClosed(ENUM_SIGNAL closedDir) {}
   // Fired by the EA (AUTO mode only) when a BUY/SELL signal failed to open
   // a position (blocked, no lots, order-send failure) and no basket is
   // open afterward — i.e. the rejection left nothing tracking the signal.
   virtual void        OnEntryRejected(ENUM_SIGNAL dir) {}
  };
#endif
