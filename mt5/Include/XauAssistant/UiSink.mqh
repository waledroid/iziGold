// UiSink.mqh — bridges TradeManager (strategy-agnostic) to the UI service:
// reads the active strategy id at call time, posts the event, then
// screenshots the chart for open/close. Every step here is best-effort —
// see UiApi.mqh. Dispatches five concerns: chart-box update, the strategy's
// OnBasketClosed hook, the higher-timeframe verdict lookup for the trade
// log, the reconciliation-watermark advance, and the screenshot upload.
#ifndef XAU_UISINK_MQH
#define XAU_UISINK_MQH
#include <XauAssistant/StrategyRegistry.mqh>
#include <XauAssistant/UiApi.mqh>
#include <XauAssistant/TradeBoxes.mqh>
#include <XauAssistant/Reconciler.mqh>
#include <XauAssistant/NewsGuard.mqh>

class CUiSink : public CTradeEventSink
  {
private:
   CStrategyRegistry *m_registry;
   CUiApi            *m_ui;
   CTradeBoxes       *m_boxes;
   CReconciler       *m_recon;
   CNewsGuard        *m_news;

public:
   CUiSink() : m_registry(NULL), m_ui(NULL), m_boxes(NULL), m_recon(NULL), m_news(NULL) {}

   void Init(CStrategyRegistry *registry, CUiApi *ui, CTradeBoxes *boxes, CReconciler *recon,
             CNewsGuard *news = NULL)
     {
      m_registry = registry;
      m_ui       = ui;
      m_boxes    = boxes;
      m_recon    = recon;
      m_news     = news;
     }

   // FIXED-mode target alert: one Telegram notice with a tap-to-exit
   // button when the ride first crosses the ADR target. Fire-and-forget
   // (PostNotify is best-effort; TradeManager already latched the
   // once-per-basket flag before calling us).
   virtual void OnTargetAlert(double basketProfit)
     {
      m_ui.PostNotify(StringFormat(
         "🎯 FIXED ride hit the ADR target: +$%.2f. Exit now, lock it in with Move SL, or ignore to let it ride until the trend turns.",
         basketProfit), "target");
     }

   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0, double tp = 0.0,
                             bool isFinal = true, string entryMode = "")
     {
      CStrategy *active = m_registry.Active();
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
         m_boxes.OnOpen(ticket, dir, price, sl);
      else if(event == "close" && basketGone)
         m_boxes.OnClose(price);
      if(event == "close" && active != NULL && basketGone)
        {
         ENUM_SIGNAL closedDir = (dir == "BUY")  ? SIGNAL_BUY  :
                                 (dir == "SELL") ? SIGNAL_SELL : SIGNAL_NONE;
         active.OnBasketClosed(closedDir);
        }
      // The higher-timeframe (and EMA200) verdicts belong to the ENTRY
      // decision, so they are only meaningful on open/add rows; closes
      // carry -1 (unknown).
      int htfAgree = -1, ema200Agree = -1, newsBlackout = -1;
      if(event != "close" && active != NULL)
        {
         htfAgree = active.LastHtfAgree();
         ema200Agree = active.LastEma200Agree();
        }
      // News blackout stamp (owner 2026-09-01): entry-decision context, so
      // open/add rows only — closes stay -1 like the agree verdicts.
      if(event != "close" && m_news != NULL)
         newsBlackout = m_news.InBlackout() ? 1 : 0;
      long id = m_ui.PostTradeEvent(event, strategyId, dir, lots, price, sl, reason, ticket,
                                    profit, tp, basketGone, entryMode, htfAgree, ema200Agree,
                                    newsBlackout);
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
            m_recon.AdvanceReconWatermark(ticket);
         else
           {
            long newest = m_recon.NewestOwnClosingDeal();
            if(newest >= 0)
               m_recon.AdvanceReconWatermark(newest);
           }
        }
      if(event == "open" || (event == "close" && basketGone))
         m_ui.UploadScreenshot(id);
     }
  };
#endif
