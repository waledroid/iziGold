// UiApi.mqh — posts a periodic heartbeat to the local UI service and returns
// any pending remote strategy-switch request. Mirrors AiApi.mqh's WebRequest
// and string-scan extraction style; every failure path returns "" silently.
#ifndef XAU_UIAPI_MQH
#define XAU_UIAPI_MQH

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
                             bool isFinal = true, string entryMode = "") = 0;
   // FIXED-mode target alert: the basket crossed the ADR profit target for
   // the first time — the ride continues, the owner gets a tap-to-exit
   // notice. Default no-op so sinks that don't care keep compiling.
   virtual void OnTargetAlert(double basketProfit) {}
  };

class CUiApi
  {
private:
   string   m_baseUrl;
   string   m_url;
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

   // Shared WebRequest POST plumbing for the fire-and-forget JSON endpoints
   // (heartbeat excluded — it needs the response body back for parsing).
   // Returns true on HTTP 200, false (with a throttled warning) otherwise.
   bool Post(string path, string json)
     {
      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_baseUrl + path, "Content-Type: application/json\r\n",
                            m_timeout, req, res, resp_headers);
      if(code != 200)
        {
         WarnThrottled(StringFormat(
            "UiApi: %s WebRequest failed code=%d err=%d (is the URL whitelisted in Tools>Options>Expert Advisors?)",
            path, code, GetLastError()));
         return false;
        }
      return true;
     }

public:
   void Init(string baseUrl, int timeout_ms, long magic, ENUM_TIMEFRAMES tf)
     {
      m_baseUrl = baseUrl;
      m_url     = baseUrl + "/heartbeat";
      m_timeout = timeout_ms;
      m_magic   = magic;
      m_tf      = tf;
      m_lastWarn = 0;
     }

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
                        string &mode, string &entryMode_out, string &cmd,
                        long &cmdId, string &cmdDir)
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
                    ",\"entry_mode\":\"" + entryMode + "\"}";

      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_url, "Content-Type: application/json\r\n",
                            m_timeout, req, res, resp_headers);
      if(code != 200)
        {
         WarnThrottled(StringFormat(
            "UiApi: WebRequest failed code=%d err=%d (is the URL whitelisted in Tools>Options>Expert Advisors?)",
            code, GetLastError()));
         return "";
        }
      string body = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
      // "switch_to":null never matches the quoted-string pattern below, so it
      // naturally falls through to "" — no special-casing needed.
      string switch_to = ExtractString(body, "switch_to");

      mode = ExtractString(body, "mode");
      entryMode_out = ExtractString(body, "entry_mode");
      cmd = ""; cmdId = 0; cmdDir = "";
      int cpos = StringFind(body, "\"command\":{");
      if(cpos >= 0)
        {
         string tail = StringSubstr(body, cpos);
         cmd    = ExtractString(tail, "cmd");
         cmdDir = ExtractString(tail, "direction");
         int idpos = StringFind(tail, "\"proposal_id\":");
         if(idpos >= 0)
            cmdId = StringToInteger(StringSubstr(tail, idpos + 14));
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
   void PostNotify(string text, bool exitButton = false)
     {
      string body = "{\"text\":\"" + text + "\"" +
                    (exitButton ? ",\"exit_button\":true" : "") + "}";
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
                       string entryMode = "")
     {
      string json = "{\"event\":\"" + event + "\"" +
                    ",\"strategy_id\":\"" + strategyId + "\"" +
                    ",\"direction\":\"" + dir + "\"" +
                    ",\"lots\":" + DoubleToString(lots, 2) +
                    ",\"price\":" + DoubleToString(price, _Digits) +
                    ",\"sl\":" + DoubleToString(sl, _Digits) +
                    ",\"reason\":\"" + reason + "\"" +
                    ",\"ticket\":" + (string)ticket +
                    ",\"profit\":" + DoubleToString(profit, 2) +
                    ",\"tp\":" + DoubleToString(tp, _Digits) +
                    ",\"final\":" + (isFinal ? "true" : "false") +
                    ",\"entry_mode\":\"" + entryMode + "\"}";

      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_baseUrl + "/trade-event", "Content-Type: application/json\r\n",
                            m_timeout, req, res, resp_headers);
      if(code != 200)
        {
         WarnThrottled(StringFormat(
            "UiApi: trade-event WebRequest failed code=%d err=%d (is the URL whitelisted in Tools>Options>Expert Advisors?)",
            code, GetLastError()));
         return -1;
        }
      string body = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
      double id = ExtractNumber(body, "id");
      return (id >= 0) ? (long)id : -1;
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

      // WebRequest requires a char[] body; copy the raw bytes across.
      char bytes[];
      ArrayResize(bytes, readCount);
      for(int i = 0; i < readCount; i++) bytes[i] = (char)raw[i];

      char res[];
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_baseUrl + "/screenshot?event=" + (string)tradeId,
                            "Content-Type: application/octet-stream\r\n",
                            m_timeout, bytes, res, resp_headers);
      if(code != 200)
         WarnThrottled(StringFormat(
            "UiApi: screenshot WebRequest failed code=%d err=%d (is the URL whitelisted in Tools>Options>Expert Advisors?)",
            code, GetLastError()));
     }
  };
#endif
