// Transport.mqh — the EA's single network path.
//
// Native Windows: plain WebRequest to http://127.0.0.1:9000, which works once
// the URL is in Tools > Options > Expert Advisors' allowlist (and that allowlist
// persists normally on Windows).
//
// Linux/Wine: MT5's WebRequest allowlist is GUI-only and does NOT persist across
// restarts (proved 2026-09-23: startup-ini keys, terminal.ini/common.ini
// [Experts] sections, and settings.ini snapshots all fail). So when WebRequest
// is refused (MQL5 error 4014) the EA transparently switches to a FILE RELAY:
// it writes the request to MQL5/Files/xau_relay_req.json and the host bridge
// (a normal Python process under Wine, not restricted by WebRequest) forwards it
// to the service over an ordinary socket and writes the reply to
// MQL5/Files/xau_relay_resp.json. No allowlist, no GUI, nothing to re-set.
//
// AUTO (default) probes /health once at startup and picks the working path, and
// falls back at runtime on 4014 — so the same build behaves correctly on both
// operating systems with no input changes.
#ifndef XAU_TRANSPORT_MQH
#define XAU_TRANSPORT_MQH

enum ENUM_XAU_TRANSPORT { XAU_TRANSPORT_AUTO, XAU_TRANSPORT_HTTP, XAU_TRANSPORT_FILE };

#define XAU_RELAY_REQ  "xau_relay_req.json"
#define XAU_RELAY_TMP  "xau_relay_req.tmp"
#define XAU_RELAY_RESP "xau_relay_resp.json"
#define XAU_RELAY_RTMP "xau_relay_resp.tmp"

class CTransport
  {
private:
   string m_base;
   int    m_timeout;
   string m_mode;        // "HTTP" | "FILE"
   long   m_nextId;
   datetime m_lastWarn;
   string m_reqFile;
   string m_tmpFile;
   string m_respFile;

   void WarnThrottled(string msg)
     {
      datetime now = TimeCurrent();
      if(now - m_lastWarn < 60) return;
      m_lastWarn = now;
      Print(msg);
     }

   static string B64(const uchar &data[], int size)
     {
      string T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
      string out = "";
      int i;
      for(i = 0; i + 2 < size; i += 3)
        {
         int n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
         out += StringSubstr(T, (n >> 18) & 63, 1) + StringSubstr(T, (n >> 12) & 63, 1) +
                StringSubstr(T, (n >> 6) & 63, 1) + StringSubstr(T, n & 63, 1);
        }
      if(i < size)
        {
         int rem = size - i;
         int n = data[i] << 16;
         if(rem == 2) n |= data[i + 1] << 8;
         out += StringSubstr(T, (n >> 18) & 63, 1) + StringSubstr(T, (n >> 12) & 63, 1) +
                (rem == 2 ? StringSubstr(T, (n >> 6) & 63, 1) : "=") + "=";
        }
      return out;
     }

   bool Http(string method, string path, string body, string &out, int &code)
     {
      char req[], res[];
      string headers = "Content-Type: application/json\r\n";
      StringToCharArray(body, req, 0, StringLen(body), CP_UTF8);
      string resp_headers;
      ResetLastError();
      code = WebRequest(method, m_base + path, headers, m_timeout, req, res, resp_headers);
      if(code != 200) return false;
      out = CharArrayToString(res, 0, WHOLE_ARRAY, CP_UTF8);
      return true;
     }

   // POST raw binary over HTTP (screenshots). Returns true on HTTP 200.
   bool HttpBinary(string path, const uchar &bytes[], int size)
     {
      char req[], res[];
      ArrayResize(req, size);
      for(int i = 0; i < size; i++) req[i] = (char)bytes[i];
      string resp_headers;
      ResetLastError();
      int code = WebRequest("POST", m_base + path,
                            "Content-Type: application/octet-stream\r\n",
                            m_timeout, req, res, resp_headers);
      return (code == 200);
     }

   bool FileRelay(string method, string path, string body, string bodyB64,
                  string &out, bool &ok)
     {
      long id = ++m_nextId;
      string req = "{\"id\":" + (string)id + ",\"method\":\"" + method +
                   "\",\"path\":\"" + path + "\"";
      if(bodyB64 != "") req += ",\"body_b64\":\"" + bodyB64 + "\"}";
      else if(body != "") req += ",\"body\":" + body + "}";
      else req += ",\"body\":null}";

      string targetTmp  = (m_tmpFile != "")  ? m_tmpFile  : XAU_RELAY_TMP;
      string targetReq  = (m_reqFile != "")  ? m_reqFile  : XAU_RELAY_REQ;
      string targetResp = (m_respFile != "") ? m_respFile : XAU_RELAY_RESP;

      int h = FileOpen(targetTmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
      if(h == INVALID_HANDLE)
        { WarnThrottled("Transport: cannot write relay request"); return false; }
      FileWriteString(h, req);
      FileClose(h);
      FileDelete(targetReq);
      if(!FileMove(targetTmp, 0, targetReq, FILE_REWRITE))
        { WarnThrottled("Transport: relay request move failed"); return false; }

      uint timeout = (uint)MathMax(m_timeout, 250);
      uint start = GetTickCount();
      while(GetTickCount() - start < timeout)
        {
         if(FileIsExist(targetResp))
           {
            int r = FileOpen(targetResp,
                             FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
            if(r != INVALID_HANDLE)
              {
               string line1 = FileReadString(r);            // "<id> <status>"
               string payload = FileReadString(r);          // raw body, one line
               FileClose(r);
               int sp = StringFind(line1, " ");
               long gotId = (sp > 0) ? StringToInteger(StringSubstr(line1, 0, sp)) : -1;
               long status = (sp > 0) ? StringToInteger(StringSubstr(line1, sp + 1)) : 0;
               if(gotId == id)
                 {
                  FileDelete(targetResp);
                  ok = (status == 200);
                  out = (ok ? payload : "");
                  return true;                              // a real answer, ok or not
                 }
              }
           }
         Sleep(20);
        }
      WarnThrottled(StringFormat("Transport: relay timeout after %dms (is the bridge running?)", timeout));
      return false;
     }

public:
   void Init(string baseUrl, int timeoutMs, ENUM_XAU_TRANSPORT mode)
     {
      m_base = baseUrl;
      m_timeout = timeoutMs;
      m_nextId = 0;
      m_lastWarn = 0;
      string sym = _Symbol;
      StringReplace(sym, "/", "_");
      m_reqFile  = "xau_relay_req_" + sym + ".json";
      m_tmpFile  = "xau_relay_req_" + sym + ".tmp";
      m_respFile = "xau_relay_resp_" + sym + ".json";
      if(mode == XAU_TRANSPORT_FILE)      m_mode = "FILE";
      else if(mode == XAU_TRANSPORT_HTTP) m_mode = "HTTP";
      else
        {
         // AUTO: probe /health once. 200 => WebRequest allowed (native Windows
         // or a terminal whose allowlist is set); anything else => file relay.
         char req[], res[];
         string resp_headers;
         ResetLastError();
         int code = WebRequest("GET", m_base + "/health", "", 1000, req, res, resp_headers);
         m_mode = (code == 200) ? "HTTP" : "FILE";
        }
      Print("Transport: mode=", m_mode, (m_mode == "FILE"
            ? " (file relay via the host bridge — no WebRequest allowlist needed)" : ""));
     }

   string Mode() { return m_mode; }

   // JSON request. Returns the response body, or "" on failure.
   string Request(string method, string path, string body)
     {
      if(m_mode == "HTTP")
        {
         string out; int code;
         if(Http(method, path, body, out, code)) return out;
         if(GetLastError() == 4014)
           {
            m_mode = "FILE";
            Print("Transport: WebRequest refused (4014) — switching to the file relay");
           }
         else
           {
            WarnThrottled(StringFormat("Transport: %s %s failed code=%d err=%d",
                                       method, path, code, GetLastError()));
            return "";
           }
        }
      string out; bool ok = false;
      if(FileRelay(method, path, body, "", out, ok)) return ok ? out : "";
      return "";
     }

   // Binary POST (screenshot). Relay mode base64-encodes the payload.
   bool PostBinary(string path, const uchar &bytes[], int size)
     {
      if(m_mode == "HTTP")
        {
         if(HttpBinary(path, bytes, size)) return true;
         if(GetLastError() == 4014)
           {
            m_mode = "FILE";
            Print("Transport: WebRequest refused (4014) — switching to the file relay");
           }
         else return false;
        }
      string out; bool ok = false;
      if(FileRelay("POST", path, "", B64(bytes, size), out, ok)) return ok;
      return false;
     }
  };

#endif
