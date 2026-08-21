// Reconciler.mqh — global-variable-key migration and reconcile-on-reconnect.
// Self-contained: touches only per-symbol MT5 global variables and the UI
// service (via an injected CUiApi*). Parameterised by magic number rather
// than reading an EA input directly, so a future second trading lane can own
// its own CReconciler instance/watermark without this class changing.
#ifndef XAU_RECONCILER_MQH
#define XAU_RECONCILER_MQH
#include <XauAssistant/UiApi.mqh>

class CReconciler
  {
private:
   long     m_magic;
   CUiApi  *m_ui;
   string   m_activeStrategyId;

   // Throttles the "reconcile HistorySelect failed" warning to <=1/hour so a
   // stuck terminal history cache can't spam the log/Telegram.
   datetime m_lastReconWarn;
   // Separate throttle for the ticket==0 lookup below -- distinct failure mode
   // (per-event, not per-60s-pass), kept on its own clock so a burst of
   // ticket-less closes can't itself spam the log within one hour either.
   datetime m_lastReconLookupWarn;

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

public:
   CReconciler() : m_magic(0), m_ui(NULL), m_activeStrategyId(""),
                   m_lastReconWarn(0), m_lastReconLookupWarn(0) {}

   void Init(long magic, CUiApi *ui, string activeStrategyId)
     {
      m_magic = magic;
      m_ui = ui;
      m_activeStrategyId = activeStrategyId;
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

   void AdvanceReconWatermark(long dealTicket)
     {
      if((double)dealTicket > GlobalVariableGet(ReconKey()))
         GlobalVariableSet(ReconKey(), (double)dealTicket);
     }

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
         if(TimeCurrent() - m_lastReconLookupWarn > 3600)
           {
            Print("XauAssistant: recon newest-deal lookup HistorySelect failed, err=", GetLastError());
            m_lastReconLookupWarn = TimeCurrent();
           }
         return -1;
        }
      long newest = -1;
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         ulong t = HistoryDealGetTicket(i);
         if(t == 0) continue;
         if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(t, DEAL_MAGIC) != m_magic) continue;
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
            if(TimeCurrent() - m_lastReconWarn > 3600)
              {
               Print("XauAssistant: reconcile seed HistorySelect failed, err=", GetLastError());
               m_lastReconWarn = TimeCurrent();
              }
            return;   // fail-open: retry the seed next pass
           }
         long newest = 0;
         for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
           {
            ulong t = HistoryDealGetTicket(i);
            if(t == 0) continue;
            if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
            if(HistoryDealGetInteger(t, DEAL_MAGIC) != m_magic) continue;
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
         if(TimeCurrent() - m_lastReconWarn > 3600)
           {
            Print("XauAssistant: reconcile HistorySelect failed, err=", GetLastError());
            m_lastReconWarn = TimeCurrent();
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
         if(HistoryDealGetInteger(t, DEAL_MAGIC) != m_magic) continue;
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
         long id = m_ui.PostTradeEvent("close", m_activeStrategyId, dir, lots, price,
                                       0.0, reason, (long)t, profit, 0.0, isFinal);
         if(id < 0)
            return;                     // service still down -> retry next pass
         AdvanceReconWatermark((long)t);
         PrintFormat("XauAssistant: reconciled offline close deal %I64d (%s %.2f)",
                     (long)t, reason, profit);
        }
     }
  };
#endif
