// ChartPanels.mqh — plugin-style chart sub-panels (owner 2026-09-02):
// attaches the terminal's OWN RSI(14) and MACD(12,26,9) indicators to the
// EA's chart, each in its own subwindow below the candles. Fully modular:
// one EA input per panel (ShowRsiPanel / ShowMacdPanel) — set false to
// remove a panel, or detach the EA (Deinit removes exactly what Init
// added, never a panel the user attached by hand). PERIOD_CURRENT, so the
// panel follows the chart's timeframe: the M5 chart shows M5 RSI/MACD,
// the M15 chart shows M15 — a chart-TF switch reinits the EA and the
// panels re-attach on the new timeframe automatically. Display only —
// nothing here ever touches a trading decision (the report-only rsi_agree
// verdict in HalfTrendEma.mqh has its own handle on the LANE timeframe).
#ifndef XAU_CHART_PANELS_MQH
#define XAU_CHART_PANELS_MQH

class CChartPanels
  {
private:
   int  m_rsiHandle;
   int  m_macdHandle;
   bool m_rsiAdded;
   bool m_macdAdded;

   bool AlreadyOnChart(string shortname)
     {
      int windows = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
      for(int w = 0; w < windows; w++)
        {
         int total = ChartIndicatorsTotal(0, w);
         for(int i = 0; i < total; i++)
            if(StringFind(ChartIndicatorName(0, w, i), shortname) == 0)
               return true;
        }
      return false;
     }

   bool AddPanel(int handle, string shortname)
     {
      if(handle == INVALID_HANDLE) return false;
      if(AlreadyOnChart(shortname)) return false;   // reinit-safe: no duplicates
      int sub = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
      return ChartIndicatorAdd(0, sub, handle);
     }

   void RemovePanel(string shortname)
     {
      int windows = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
      for(int w = windows - 1; w >= 1; w--)
        {
         int total = ChartIndicatorsTotal(0, w);
         for(int i = total - 1; i >= 0; i--)
           {
            string name = ChartIndicatorName(0, w, i);
            if(StringFind(name, shortname) == 0)
               ChartIndicatorDelete(0, w, name);
           }
        }
     }

public:
   CChartPanels() : m_rsiHandle(INVALID_HANDLE), m_macdHandle(INVALID_HANDLE),
                    m_rsiAdded(false), m_macdAdded(false) {}

   void Init(bool showRsi, bool showMacd)
     {
      if(showRsi)
        {
         m_rsiHandle = iRSI(_Symbol, PERIOD_CURRENT, 14, PRICE_CLOSE);
         m_rsiAdded = AddPanel(m_rsiHandle, "RSI(14)");
        }
      if(showMacd)
        {
         m_macdHandle = iMACD(_Symbol, PERIOD_CURRENT, 12, 26, 9, PRICE_CLOSE);
         m_macdAdded = AddPanel(m_macdHandle, "MACD(12,26,9)");
        }
     }

   // Remove only panels THIS instance added — a reinit whose AddPanel found
   // the panel already on the chart (m_*Added false) must not strip it on
   // the way out, and a user's hand-attached copy is never ours to delete.
   void Deinit()
     {
      if(m_rsiAdded)  { RemovePanel("RSI(14)");        m_rsiAdded = false; }
      if(m_macdAdded) { RemovePanel("MACD(12,26,9)");  m_macdAdded = false; }
      if(m_rsiHandle != INVALID_HANDLE)  { IndicatorRelease(m_rsiHandle);  m_rsiHandle = INVALID_HANDLE; }
      if(m_macdHandle != INVALID_HANDLE) { IndicatorRelease(m_macdHandle); m_macdHandle = INVALID_HANDLE; }
     }
  };
#endif
