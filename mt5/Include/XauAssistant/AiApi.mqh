#ifndef XAU_AIAPI_MQH
#define XAU_AIAPI_MQH
#include <XauAssistant/Strategy.mqh>

struct AiResponse
  {
   string direction;
   double confidence;
   string regime;
   string verdict;
   string mode;
   bool   ai_available;
  };

class CAiApi
  {
private:
   string m_url;
   int    m_timeout;

   string BuildJson(ENUM_SIGNAL sig, int count)
     {
      MqlRates rates[];
      // shift 1 = last CLOSED bar; the forming bar is never sent
      int copied = CopyRates(_Symbol, PERIOD_CURRENT, 1, count, rates);
      if(copied <= 0) return "";
      string tf = StringSubstr(EnumToString(_Period), 7); // "PERIOD_M15" -> "M15"
      string json = "{\"symbol\":\"" + _Symbol + "\",\"timeframe\":\"" + tf +
                    "\",\"signal\":\"" + SignalToString(sig) + "\",\"candles\":[";
      for(int i = 0; i < copied; i++)
        {
         if(i > 0) json += ",";
         json += "{\"t\":" + (string)(long)rates[i].time +
                 ",\"o\":" + DoubleToString(rates[i].open, _Digits) +
                 ",\"h\":" + DoubleToString(rates[i].high, _Digits) +
                 ",\"l\":" + DoubleToString(rates[i].low, _Digits) +
                 ",\"c\":" + DoubleToString(rates[i].close, _Digits) +
                 ",\"v\":" + (string)rates[i].tick_volume + "}";
        }
      return json + "]}";
     }

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
      if(a < 0) return 0.0;
      a += StringLen(pat);
      int b = a;
      while(b < StringLen(body))
        {
         ushort ch = StringGetCharacter(body, b);
         if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-') b++;
         else break;
        }
      return StringToDouble(StringSubstr(body, a, b - a));
     }

public:
   void Init(string url, int timeout_ms) { m_url = url; m_timeout = timeout_ms; }

   bool Analyze(ENUM_SIGNAL sig, AiResponse &out)
     {
      out.ai_available = false;
      string json = BuildJson(sig, 200);
      if(json == "") return false;
      char req[], res[];
      StringToCharArray(json, req, 0, StringLen(json), CP_UTF8);
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_url, "Content-Type: application/json\r\n",
                            m_timeout, req, res, resp_headers);
      if(code != 200)
        {
         Print("AiApi: WebRequest failed code=", code, " err=", GetLastError(),
               " (is the URL whitelisted in Tools>Options>Expert Advisors?)");
         return false;
        }
      string body = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
      out.direction    = ExtractString(body, "direction");
      out.confidence   = ExtractNumber(body, "confidence");
      out.regime       = ExtractString(body, "regime");
      out.verdict      = ExtractString(body, "verdict");
      out.mode         = ExtractString(body, "mode");
      out.ai_available = (StringFind(body, "\"ai_available\":true") >= 0);
      return true;
     }
  };
#endif
