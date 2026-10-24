// UiApi.mqh — posts a periodic heartbeat to the local UI service and returns
// any pending remote strategy-switch request. Mirrors AiApi.mqh's WebRequest
// and string-scan extraction style; every failure path returns "" silently.
#ifndef XAU_UIAPI_MQH
#define XAU_UIAPI_MQH
#include <XauAssistant/Transport.mqh>

// Implemented by the EA so TradeManager can report trade events without
// knowing about UiApi or the active strategy id — TradeManager stays
// strategy-agnostic and only calls this interface.
class CTradeEventSink
  {
public:
   // `isFinal` distinguishes a basket-ending close from a partial single-leg
   // stop-out that leaves survivors in a pyramided basket (a "close" event
   // that is NOT final -- `final` is a reserved word in MQL5, hence the
   // parameter name): defaults true so every pre-existing call site
   // (whole-basket CloseAll, open, add) keeps its old behavior unchanged.
   // `entryMode` is the basket's sticky mode ("adr"/"fixed"); defaults to ""
   // so any caller that doesn't know it (or predates entry modes) keeps
   // compiling and the service treats it as "unknown" (fail-open).
   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0, double tp = 0.0,
                             bool isFinal = true, string entryMode = "",
                             double slippagePts = 0.0, int execMs = 0,
                             double reqPrice = 0.0) = 0;
   // FIXED-mode target alert: the basket crossed the ADR profit target for
   // the first time — the ride continues, the owner gets a tap-to-exit
   // notice. Default no-op so sinks that don't care keep compiling.
   virtual void OnTargetAlert(double basketProfit) {}
  };

class CUiApi
  {
private:
   CTransport *m_t;
   int      m_timeout;
   long     m_magic;
   ENUM_TIMEFRAMES m_tf;
   datetime m_lastWarn;

   string ExtractString(string body, string key)
     {
      string pat = "\"" + key + "\":\"";
      int a = StringFind(body, pat);
      if(a < 0) return "";
      a += StringLen(pat);
      int b = StringFind(body, "\"", a);
      return (b > a) ? StringSubstr(body, a, b - a) : "";
     }

   double ExtractNumber(string body, string key)
     {
      string pat = "\"" + key + "\":";
      int a = StringFind(body, pat);
      if(a < 0) return -1.0;
      a += StringLen(pat);
      int b = a;
      while(b < StringLen(body))
        {
         ushort ch = StringGetCharacter(body, b);
         if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-') b++;
         else break;
        }
      if(b == a) return -1.0;
      return StringToDouble(StringSubstr(body, a, b - a));
     }

   // Positions owned by this EA on this symbol (filtered by magic), in the
   // exact field order/names of the service's Position pydantic model.
   string BuildPositionsJson()
     {
      string json = "[";
      bool first = true;
      int total = PositionsTotal();
      for(int i = 0; i < total; i++)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != m_magic) continue;
         string dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         double lots      = PositionGetDouble(POSITION_VOLUME);
         double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl        = PositionGetDouble(POSITION_SL);
         double profit    = PositionGetDouble(POSITION_PROFIT);
         if(!first) json += ",";
         first = false;
         json += "{\"ticket\":" + (string)(long)ticket +
                 ",\"direction\":\"" + dir + "\"" +
                 ",\"symbol\":\"" + _Symbol + "\"" +
                 ",\"lots\":" + DoubleToString(lots, 2) +
                 ",\"open_price\":" + DoubleToString(openPrice, _Digits) +
                 ",\"sl\":" + DoubleToString(sl, _Digits) +
                 ",\"profit\":" + DoubleToString(profit, 2) + "}";
        }
      return json + "]";
     }

   void WarnThrottled(string msg)
     {
      datetime now = TimeCurrent();
      if(now - m_lastWarn < 60) return;   // once per minute
      m_lastWarn = now;
      Print(msg);
     }

   // Shared request plumbing for the fire-and-forget JSON endpoints.
   // Returns true on HTTP 200 / relay success.
   bool Post(string path, string json)
     {
      return (m_t.Request("POST", path, json) != "");
     }

public:
   void Init(CTransport *transport, int timeout_ms, long magic, ENUM_TIMEFRAMES tf)
     {
      m_t       = transport;
      m_timeout = timeout_ms;
      m_magic   = magic;
      m_tf      = tf;
      m_lastWarn = 0;
     }

   void SetTimeframe(ENUM_TIMEFRAMES tf) { m_tf = tf; }

   // Returns the requested strategy id to switch to, or "" if none/failed.
   // outputs: runtime execution mode ("auto"/"manual"/"" when absent),
   // runtime entry mode ("adr"/"fixed"/"" when absent), and at most one
   // command per beat (cmd "" when none). `entryMode` is the EA's current
   // entry mode, sent so the service's heartbeat contract stays symmetric
   // with `mode` (execution mode) even though today only entryMode_out
   // (the service's kv, which is the source of truth for remote switches)
   // drives EA behavior.
   string PostHeartbeat(double equity, double balance, double floating_pl,
                        bool kill_switch, double hwm, int exposure_min,
                        bool window_open, double spread_points, string active_strategy,
                         bool algo_trading, string entryMode,
                         int heartbeatSecIn,
                         string &mode, string &entryMode_out, string &cmd,
                        long &cmdId, string &cmdDir, string &htfEnforce_out,
                        string &ema200Enforce_out,
                         string &rsiEnforce_out, string &m15Adds_out,
                         bool &entriesPaused_out,
                         int &heartbeatSec_out,
                         double &riskPct_out, double &maxDdPct_out,
                         double &profitTargetPct_out, int &winStart_out,
                         int &winEnd_out, int &maxExpoMin_out,
                         double daily_loss_pct = 0.0, bool brake_reset = false,
                        string newsJson = "", int newsBlackoutMin = 0)
     {
      // Forming (bar 0) OHLC for the service's /chart real-time render.
      // Zeros on CopyRates failure -- the service treats 0 as "no forming
      // bar" (fail-open, old-service compatible: unknown JSON fields are
      // ignored by pydantic).
      MqlRates bar0[];
      long   bar_t = 0;
      double bar_o = 0, bar_h = 0, bar_l = 0, bar_c = 0;
      if(CopyRates(_Symbol, m_tf, 0, 1, bar0) == 1)
        {
         bar_t = (long)bar0[0].time;
         bar_o = bar0[0].open;
         bar_h = bar0[0].high;
         bar_l = bar0[0].low;
         bar_c = bar0[0].close;
        }

      // Field names/order match service/app/models.py HeartbeatRequest exactly.
      string json = "{\"equity\":" + DoubleToString(equity, 2) +
                    ",\"balance\":" + DoubleToString(balance, 2) +
                    ",\"floating_pl\":" + DoubleToString(floating_pl, 2) +
                    ",\"positions\":" + BuildPositionsJson() +
                    ",\"kill_switch\":" + (kill_switch ? "true" : "false") +
                    ",\"hwm\":" + DoubleToString(hwm, 2) +
                    ",\"exposure_min\":" + (string)exposure_min +
                    ",\"window_open\":" + (window_open ? "true" : "false") +
                    ",\"spread_points\":" + DoubleToString(spread_points, 1) +
                    ",\"active_strategy\":\"" + active_strategy + "\"" +
                    ",\"algo_trading\":" + (algo_trading ? "true" : "false") +
                    ",\"bar_t\":" + (string)bar_t +
                    ",\"bar_o\":" + DoubleToString(bar_o, 2) +
                    ",\"bar_h\":" + DoubleToString(bar_h, 2) +
                    ",\"bar_l\":" + DoubleToString(bar_l, 2) +
                    ",\"bar_c\":" + DoubleToString(bar_c, 2) +
                    ",\"entry_mode\":\"" + entryMode + "\"" +
                    ",\"heartbeat_sec\":" + (string)heartbeatSecIn +
                     ",\"daily_loss_pct\":" + DoubleToString(daily_loss_pct, 1) +
                     ",\"brake_reset\":" + (brake_reset ? "true" : "false") +
                     ",\"symbol\":\"" + _Symbol + "\"";
      // Upcoming high-impact USD events (NewsGuard.UpcomingJson) + blackout
      // radius, for /news and the pre-blackout heads-up. Omitted entirely
      // when the guard is off/empty-handed — pydantic defaults cover it.
      if(newsJson != "" && newsJson != "[]")
         json += ",\"news\":" + newsJson +
                 ",\"news_blackout_min\":" + (string)newsBlackoutMin;
      json += "}";

      string body = m_t.Request("POST", "/heartbeat", json);
      if(body == "")
        {
         WarnThrottled(StringFormat("UiApi: heartbeat failed (transport=%s)", m_t.Mode()));
         return "";
        }
      // "switch_to":null never matches the quoted-string pattern below, so it
      // naturally falls through to "" — no special-casing needed.
      string switch_to = ExtractString(body, "switch_to");
      string target_sym = ExtractString(body, "target_symbol");

      mode = ExtractString(body, "mode");
      entryMode_out = ExtractString(body, "entry_mode");
      // Both confirmation toggles are read here. htf_enforce was MISSING
      // from 2026-08-21 (when /agree was added) to 2026-08-22: the out
      // parameter was declared and threaded but never assigned, so the
      // EA never saw the service's setting and silently fell back to its
      // own HtfConfirm input -- i.e. /agree appeared to work and changed
      // nothing. A toggle that reports success and does nothing is worse
      // than no toggle.
      htfEnforce_out    = ExtractString(body, "htf_enforce");
      ema200Enforce_out = ExtractString(body, "ema200_enforce");
      // 2026-09-07: RSI 70/30 enforcement (M15 lane) and the M15 adds
      // switch ride the same heartbeat. An old service that sends neither
      // leaves them "" and the EA inputs stay in charge.
       rsiEnforce_out    = ExtractString(body, "rsi_enforce");
       m15Adds_out       = ExtractString(body, "m15_adds");
       // Supervisory-agent governor (2026-09-20): true = pause NEW auto
       // entries. Absent/false for an older service keeps trading exactly as
       // before (fail-open).
       entriesPaused_out = (StringFind(body, "\"entries_paused\":true") >= 0);
       // Service-authoritative heartbeat interval (0/absent = keep the EA's
       // own input, so an older service changes nothing).
       heartbeatSec_out = (int)ExtractNumber(body, "heartbeat_sec");
       riskPct_out       = ExtractNumber(body, "risk_per_trade_pct");
       maxDdPct_out      = ExtractNumber(body, "max_drawdown_pct");
       profitTargetPct_out = ExtractNumber(body, "profit_target_pct");
       winStart_out      = (int)ExtractNumber(body, "window_start_hour");
       winEnd_out        = (int)ExtractNumber(body, "window_end_hour");
       maxExpoMin_out    = (int)ExtractNumber(body, "max_daily_exposure_min");
      cmd = ""; cmdId = 0; cmdDir = "";
      int cpos = StringFind(body, "\"command\":{");
      if(cpos >= 0)
        {
         string tail = StringSubstr(body, cpos);
         cmd    = ExtractString(tail, "cmd");
         cmdDir = ExtractString(tail, "direction");
         int tkpos = StringFind(tail, "\"ticket\":");
         if(tkpos >= 0)
            cmdDir = (string)(long)ExtractNumber(tail, "ticket");
         int idpos = StringFind(tail, "\"proposal_id\":");
         if(idpos >= 0)
            cmdId = StringToInteger(StringSubstr(tail, idpos + 14));
         if(target_sym != "" && target_sym != _Symbol)
           {
            cmd = ""; cmdId = 0; cmdDir = "";
           }
        }

      return switch_to;
     }

   // Field names/order match service/app/models.py ProposalResultRequest.
   // Best-effort, fire-and-forget — the service side-effect (recording the
   // proposal outcome) is not on the trading critical path.
   void PostProposalResult(long proposalId, bool ok, string detail)
     {
      string body = "{\"proposal_id\":" + (string)proposalId +
                    ",\"ok\":" + (ok ? "true" : "false") +
                    ",\"detail\":\"" + detail + "\"}";
      Post("/proposal-result", body);
     }

   // Field names match service/app/models.py NotifyRequest exactly.
   // Best-effort, fire-and-forget — callers pass fixed-literal-plus-reason
   // strings (no embedded quotes), so no JSON escaping is needed here.
   // `button` selects the inline keyboard the service attaches (owner chat
   // only): "" none, "exit" (FIXED-mode target alert; also sent as the
   // legacy exit_button flag so an older service still shows it),
   // "reset_brake" (daily-loss-brake awareness → [Reset brake for today]).
   void PostNotify(string text, string button = "")
     {
      string body = "{\"text\":\"" + text + "\"" +
                    (button == "exit" ? ",\"exit_button\":true" : "") +
                    (button != "" ? ",\"button\":\"" + button + "\"" : "") + "}";
      Post("/notify", body);
     }

   // Field names/order match service/app/models.py TradeEventRequest exactly.
   // Returns the trade id on success, or -1 on any failure (no throw, no retry —
   // callers must treat -1 as "skip the screenshot, keep trading").
   // `entryMode` defaults to "" so the reconciler's existing call (replayed
   // offline closes, which don't track which basket-mode each deal belonged
   // to) keeps compiling unchanged.
   long PostTradeEvent(string event, string strategyId, string dir, double lots,
                       double price, double sl, string reason, long ticket,
                       double profit = 0.0, double tp = 0.0, bool isFinal = true,
                       string entryMode = "", int htfAgree = -1, int ema200Agree = -1,
                       int newsBlackout = -1, int rsiAgree = -1,
                       int srAgree = -1, string srTier = "",
                       double slippagePts = 0.0, int execMs = 0,
                       double reqPrice = 0.0)
     {
      string json = "{\"event\":\"" + event + "\"" +
                    ",\"strategy_id\":\"" + strategyId + "\"" +
                    ",\"direction\":\"" + dir + "\"" +
                    ",\"symbol\":\"" + _Symbol + "\"" +
                    ",\"lots\":" + DoubleToString(lots, 2) +
                    ",\"price\":" + DoubleToString(price, _Digits) +
                    ",\"sl\":" + DoubleToString(sl, _Digits) +
                    ",\"reason\":\"" + reason + "\"" +
                    ",\"ticket\":" + (string)ticket +
                    ",\"profit\":" + DoubleToString(profit, 2) +
                    ",\"tp\":" + DoubleToString(tp, _Digits) +
                    ",\"final\":" + (isFinal ? "true" : "false") +
                    ",\"entry_mode\":\"" + entryMode + "\"" +
                    ",\"htf_agree\":" + (string)htfAgree +
                    ",\"ema200_agree\":" + (string)ema200Agree +
                    ",\"news_blackout\":" + (string)newsBlackout +
                    ",\"rsi_agree\":" + (string)rsiAgree +
                    ",\"sr_agree\":" + (string)srAgree +
                    ",\"sr_tier\":\"" + srTier + "\"";
      if(slippagePts != 0.0) json += ",\"slippage_pts\":" + DoubleToString(slippagePts, 1);
      if(execMs > 0) json += ",\"exec_ms\":" + (string)execMs;
      if(reqPrice > 0.0) json += ",\"req_price\":" + DoubleToString(reqPrice, _Digits);
      json += "}";

      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string body = m_t.Request("POST", "/trade-event", json);
      if(body == "")
        {
         WarnThrottled(StringFormat("UiApi: trade-event failed (transport=%s)", m_t.Mode()));
         return -1;
        }
      double id = ExtractNumber(body, "id");
      return (id >= 0) ? (long)id : -1;
     }

   // GET /api/last-close-ticket — the newest close-deal ticket the service
   // has recorded, or -1 on any failure (the caller treats -1 as "no guard
   // available", fail-open). Added for the reconciler's replay guard
   // (incident 2026-08-31): a hard terminal kill can roll the MT5-global
   // watermark back (gvariables.dat saves on clean shutdown only), and this
   // is the independent record that stops a rolled-back watermark from
   // re-reporting closes the service already knows about.
   long GetLastCloseTicket()
     {
      string body = m_t.Request("GET", "/api/last-close-ticket", "");
      if(body == "")
        {
         WarnThrottled(StringFormat("UiApi: last-close-ticket failed (transport=%s)", m_t.Mode()));
         return -1;
        }
      double t = ExtractNumber(body, "ticket");
      return (t >= 0) ? (long)t : -1;
     }

   // Captures the current chart to MQL5\Files, uploads the raw PNG bytes, then
   // deletes the temp file. Every failure path is a silent no-op — screenshots
   // are best-effort and must never affect trading.
   void UploadScreenshot(long tradeId)
     {
      string filename = "xau_ui_" + (string)tradeId + ".png";
      if(!ChartScreenShot(0, filename, 1024, 768)) return;

      int fh = FileOpen(filename, FILE_READ | FILE_BIN);
      if(fh == INVALID_HANDLE) return;
      int size = (int)FileSize(fh);
      if(size <= 0) { FileClose(fh); FileDelete(filename); return; }

      uchar raw[];
      ArrayResize(raw, size);
      int readCount = (int)FileReadArray(fh, raw, 0, size);
      FileClose(fh);
      FileDelete(filename);
      if(readCount <= 0) return;

      // Transport handles binary: raw over HTTP, base64 in file-relay mode.
      if(!m_t.PostBinary("/screenshot?event=" + (string)tradeId, raw, readCount))
         WarnThrottled(StringFormat("UiApi: screenshot upload failed (transport=%s)",
                                    m_t.Mode()));
     }
  };
#endif
