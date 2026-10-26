#ifndef XAU_AIAPI_MQH
#define XAU_AIAPI_MQH
#include <XauAssistant/Strategy.mqh>
#include <XauAssistant/Transport.mqh>

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
   CTransport *m_t;
   int    m_timeout;
   ENUM_TIMEFRAMES m_tf;

   string BuildJson(ENUM_SIGNAL sig, int count, string strategyId,
                    string &shadowIds[], ENUM_SIGNAL &shadowSigs[],
                    double sprMin, double sprAvg, double sprMax,
                    bool newsBlackout, string refusalReason = "")
     {
      MqlRates rates[];
      // shift 1 = last CLOSED bar; the forming bar is never sent
      int copied = CopyRates(_Symbol, m_tf, 1, count, rates);
      if(copied <= 0) return "";
      string tf = StringSubstr(EnumToString(m_tf), 7); // "PERIOD_M5" -> "M5"
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
      json += "],\"strategy_id\":\"" + strategyId + "\",\"shadows\":[";
      for(int i = 0; i < ArraySize(shadowIds); i++)
        {
         if(i > 0) json += ",";
         json += "{\"strategy_id\":\"" + shadowIds[i] + "\",\"signal\":\"" +
                 SignalToString(shadowSigs[i]) + "\"}";
        }
      // Closed-bar spread telemetry (points); all zeros = no samples.
      json += "],\"spread_min\":" + DoubleToString(sprMin, 2) +
              ",\"spread_avg\":" + DoubleToString(sprAvg, 2) +
              ",\"spread_max\":" + DoubleToString(sprMax, 2) +
              ",\"news_blackout\":" + (newsBlackout ? "true" : "false");
      if(refusalReason != "")
        {
         string clean = refusalReason;
         StringReplace(clean, "\\", "\\\\");
         StringReplace(clean, "\"", "\\\"");
         StringReplace(clean, "\r", "");
         StringReplace(clean, "\n", " ");
         json += ",\"refusal_reason\":\"" + clean + "\"";
        }
      return json + "}";
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
   void Init(CTransport *transport, int timeout_ms, ENUM_TIMEFRAMES tf)
     { m_t = transport; m_timeout = timeout_ms; m_tf = tf; }

   void SetTimeframe(ENUM_TIMEFRAMES tf) { m_tf = tf; }

   bool Analyze(ENUM_SIGNAL sig, string strategyId, string &shadowIds[],
                ENUM_SIGNAL &shadowSigs[], AiResponse &out,
                double sprMin = 0.0, double sprAvg = 0.0, double sprMax = 0.0,
                bool newsBlackout = false, string refusalReason = "")
     {
      out.ai_available = false;
      string json = BuildJson(sig, 300, strategyId, shadowIds, shadowSigs,
                              sprMin, sprAvg, sprMax, newsBlackout, refusalReason);   // 300 bars so EMA200 has warmup in renders
      if(json == "") return false;
      string body = m_t.Request("POST", "/analyze", json);
      if(body == "")
        {
         Print("AiApi: analyze failed (transport=", m_t.Mode(), ")");
         return false;
        }
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
