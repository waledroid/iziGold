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
   virtual void OnTradeEvent(string event, string dir, double lots, double price,
                             double sl, string reason, long ticket = 0,
                             double profit = 0.0) = 0;
  };

class CUiApi
  {
private:
   string   m_baseUrl;
   string   m_url;
   int      m_timeout;
   long     m_magic;
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

public:
   void Init(string baseUrl, int timeout_ms, long magic)
     {
      m_baseUrl = baseUrl;
      m_url     = baseUrl + "/heartbeat";
      m_timeout = timeout_ms;
      m_magic   = magic;
      m_lastWarn = 0;
     }

   // Returns the requested strategy id to switch to, or "" if none/failed.
   string PostHeartbeat(double equity, double balance, double floating_pl,
                        bool kill_switch, double hwm, int exposure_min,
                        bool window_open, double spread_points, string active_strategy)
     {
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
                    ",\"active_strategy\":\"" + active_strategy + "\"}";

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
      return ExtractString(body, "switch_to");
     }

   // Field names/order match service/app/models.py TradeEventRequest exactly.
   // Returns the trade id on success, or -1 on any failure (no throw, no retry —
   // callers must treat -1 as "skip the screenshot, keep trading").
   long PostTradeEvent(string event, string strategyId, string dir, double lots,
                       double price, double sl, string reason, long ticket,
                       double profit = 0.0)
     {
      string json = "{\"event\":\"" + event + "\"" +
                    ",\"strategy_id\":\"" + strategyId + "\"" +
                    ",\"direction\":\"" + dir + "\"" +
                    ",\"lots\":" + DoubleToString(lots, 2) +
                    ",\"price\":" + DoubleToString(price, _Digits) +
                    ",\"sl\":" + DoubleToString(sl, _Digits) +
                    ",\"reason\":\"" + reason + "\"" +
                    ",\"ticket\":" + (string)ticket +
                    ",\"profit\":" + DoubleToString(profit, 2) + "}";

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
