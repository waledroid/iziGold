// VolumeProfile.mqh — Volume Profile (VPVR) calculation and chart rendering.
// Calculates Point of Control (POC), Value Area (VAH/VAL) ~70% of total volume,
// and renders an institutional vertical histogram on the chart.
// Supports:
//  - Right-margin alignment (TradingView / Sierra Chart style, never obscures candles)
//  - Institutional muted color grading (slate / graphite / amber gold)
//  - Buy vs Sell volume delta breakdown (emerald vs crimson)
//  - Direct price labels for POC, VAH, VAL
// Pure charting: best-effort, never crashes or touches risk/execution state.

#ifndef XAU_VOLUMEPROFILE_MQH
#define XAU_VOLUMEPROFILE_MQH

#define XAU_VP_PREFIX "xau_vp_"

enum ENUM_VP_ANCHOR
  {
   VP_ANCHOR_RIGHT_MARGIN = 0, // Right margin pinned (TradingView style, candles completely unobstructed)
   VP_ANCHOR_RANGE_BG     = 1, // Range background (behind historical candles)
   VP_ANCHOR_ENTRY_BAR    = 2  // Entry bar projection
  };

enum ENUM_VP_THEME
  {
   VP_THEME_INSTITUTIONAL = 0, // Sleek slate / graphite / amber gold (institutional)
   VP_THEME_NEON_CLASSIC  = 1  // Classic blue / gold
  };

class CVolumeProfile
  {
private:
   bool            m_enabled;
   int             m_lookback;
   int             m_bins;
   double          m_valAreaPct;
   ENUM_VP_ANCHOR  m_anchorMode;
   ENUM_VP_THEME   m_theme;
   bool            m_showDelta;
   int             m_maxBarWidth;

   void DrawRect(string name, datetime t1, double p1, datetime t2, double p2, color clr)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
     }

public:
   CVolumeProfile()
     : m_enabled(true),
       m_lookback(100),
       m_bins(28),
       m_valAreaPct(0.70),
       m_anchorMode(VP_ANCHOR_RIGHT_MARGIN),
       m_theme(VP_THEME_INSTITUTIONAL),
       m_showDelta(true),
       m_maxBarWidth(16)
     {}

   void Init(bool enabled,
             int lookback = 100,
             int bins = 28,
             double valAreaPct = 0.70,
             ENUM_VP_ANCHOR anchorMode = VP_ANCHOR_RIGHT_MARGIN,
             ENUM_VP_THEME theme = VP_THEME_INSTITUTIONAL,
             bool showDelta = true,
             int maxBarWidth = 16)
     {
      m_enabled      = enabled;
      m_lookback     = (lookback >= 20) ? lookback : 100;
      m_bins         = (bins >= 10 && bins <= 60) ? bins : 28;
      m_valAreaPct   = (valAreaPct >= 0.50 && valAreaPct <= 0.95) ? valAreaPct : 0.70;
      m_anchorMode   = anchorMode;
      m_theme        = theme;
      m_showDelta    = showDelta;
      m_maxBarWidth  = (maxBarWidth >= 6 && maxBarWidth <= 40) ? maxBarWidth : 16;
     }

   void Clear()
     {
      ObjectsDeleteAll(0, XAU_VP_PREFIX);
     }

   ENUM_VP_ANCHOR AnchorMode() const { return m_anchorMode; }

   // Draws the Volume Profile histogram, POC line, and Value Area bounds
   void DrawOnEntry(datetime anchorTime = 0, double entryPrice = 0.0, int lookback = 0)
     {
      if(!m_enabled) return;
      int barsToUse = (lookback > 0) ? lookback : m_lookback;

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int copied = CopyRates(_Symbol, _Period, 0, barsToUse, rates);
      if(copied < 15) return;

      double maxPrice = rates[0].high;
      double minPrice = rates[0].low;
      long totalVolume = 0;
      for(int i = 0; i < copied; i++)
        {
         if(rates[i].high > maxPrice) maxPrice = rates[i].high;
         if(rates[i].low < minPrice)  minPrice = rates[i].low;
         totalVolume += rates[i].tick_volume;
        }

      if(maxPrice <= minPrice || totalVolume <= 0) return;

      double binSize = (maxPrice - minPrice) / m_bins;
      if(binSize <= 0) return;

      double binVol[];
      double binBuyVol[];
      double binSellVol[];
      ArrayResize(binVol, m_bins);
      ArrayResize(binBuyVol, m_bins);
      ArrayResize(binSellVol, m_bins);
      ArrayInitialize(binVol, 0.0);
      ArrayInitialize(binBuyVol, 0.0);
      ArrayInitialize(binSellVol, 0.0);

      for(int i = 0; i < copied; i++)
        {
         double barHi = rates[i].high;
         double barLo = rates[i].low;
         long vol = rates[i].tick_volume;
         if(vol <= 0) continue;

         int startBin = (int)((barLo - minPrice) / binSize);
         int endBin   = (int)((barHi - minPrice) / binSize);
         if(startBin < 0) startBin = 0;
         if(endBin >= m_bins) endBin = m_bins - 1;

         int span = endBin - startBin + 1;
         if(span <= 0) span = 1;
         double volPerBin = (double)vol / span;

         bool isBuy = (rates[i].close >= rates[i].open);

         for(int b = startBin; b <= endBin; b++)
           {
            binVol[b] += volPerBin;
            if(isBuy) binBuyVol[b] += volPerBin;
            else      binSellVol[b] += volPerBin;
           }
        }

      // Point of Control (POC) = bin with max volume
      int pocBin = 0;
      double maxBinVol = binVol[0];
      for(int b = 1; b < m_bins; b++)
        {
         if(binVol[b] > maxBinVol)
           {
            maxBinVol = binVol[b];
            pocBin = b;
           }
        }

      double pocPrice = minPrice + (pocBin + 0.5) * binSize;

      // Value Area (VAH / VAL) calculation (70% of total volume around POC)
      double targetVol = totalVolume * m_valAreaPct;
      double currentVol = binVol[pocBin];
      int lowIdx = pocBin;
      int highIdx = pocBin;

      while(currentVol < targetVol && (lowIdx > 0 || highIdx < m_bins - 1))
        {
         double nextLowVol  = (lowIdx > 0) ? binVol[lowIdx - 1] : -1.0;
         double nextHighVol = (highIdx < m_bins - 1) ? binVol[highIdx + 1] : -1.0;

         if(nextHighVol >= nextLowVol && highIdx < m_bins - 1)
           {
            highIdx++;
            currentVol += binVol[highIdx];
           }
         else if(lowIdx > 0)
           {
            lowIdx--;
            currentVol += binVol[lowIdx];
           }
         else break;
        }

      double valPrice = minPrice + lowIdx * binSize;
      double vahPrice = minPrice + (highIdx + 1) * binSize;

      // Clear previous volume profile objects
      Clear();

      int barSecs = PeriodSeconds(_Period);
      if(barSecs <= 0) barSecs = 300;
      datetime now = rates[0].time;

      datetime baseStart = 0;
      datetime baseEnd   = 0;
      bool projectLeft   = false;

      if(m_anchorMode == VP_ANCHOR_RIGHT_MARGIN)
        {
         // Pinned to right margin: right edge shifted ahead of current candle
         baseEnd = now + (m_maxBarWidth + 4) * barSecs;
         projectLeft = true; // bars project leftward from baseEnd
        }
      else if(m_anchorMode == VP_ANCHOR_RANGE_BG)
        {
         baseStart = rates[copied - 1].time;
         projectLeft = false;
        }
      else // VP_ANCHOR_ENTRY_BAR
        {
         baseStart = (anchorTime > 0) ? anchorTime : now;
         projectLeft = false;
        }

      // Draw Key Levels: POC, VAH, VAL
      datetime lineStart = (m_anchorMode == VP_ANCHOR_RANGE_BG) ? rates[copied - 1].time : (now - 24 * barSecs);
      datetime lineEnd   = now + (m_maxBarWidth + 8) * barSecs;

      // 1. Draw POC Line (Golden Amber)
      color pocClr = (m_theme == VP_THEME_INSTITUTIONAL) ? C'245,158,11' : C'255,191,0';
      string pocName = XAU_VP_PREFIX + "poc";
      ObjectCreate(0, pocName, OBJ_TREND, 0, lineStart, pocPrice, lineEnd, pocPrice);
      ObjectSetInteger(0, pocName, OBJPROP_COLOR, pocClr);
      ObjectSetInteger(0, pocName, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, pocName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, pocName, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, pocName, OBJPROP_BACK, true);
      ObjectSetInteger(0, pocName, OBJPROP_SELECTABLE, false);

      string pocLbl = pocName + "_lbl";
      if(ObjectCreate(0, pocLbl, OBJ_TEXT, 0, lineEnd, pocPrice))
        {
         ObjectSetString(0, pocLbl, OBJPROP_TEXT, " POC " + DoubleToString(pocPrice, 2));
         ObjectSetInteger(0, pocLbl, OBJPROP_COLOR, pocClr);
         ObjectSetInteger(0, pocLbl, OBJPROP_FONTSIZE, 8);
         ObjectSetInteger(0, pocLbl, OBJPROP_ANCHOR, ANCHOR_LEFT);
         ObjectSetInteger(0, pocLbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, pocLbl, OBJPROP_HIDDEN, true);
        }

      // 2. Draw VAH Line
      color vaClr = (m_theme == VP_THEME_INSTITUTIONAL) ? C'80,120,165' : C'100,149,237';
      string vahName = XAU_VP_PREFIX + "vah";
      ObjectCreate(0, vahName, OBJ_TREND, 0, lineStart, vahPrice, lineEnd, vahPrice);
      ObjectSetInteger(0, vahName, OBJPROP_COLOR, vaClr);
      ObjectSetInteger(0, vahName, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, vahName, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetInteger(0, vahName, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, vahName, OBJPROP_BACK, true);
      ObjectSetInteger(0, vahName, OBJPROP_SELECTABLE, false);

      string vahLbl = vahName + "_lbl";
      if(ObjectCreate(0, vahLbl, OBJ_TEXT, 0, lineEnd, vahPrice))
        {
         ObjectSetString(0, vahLbl, OBJPROP_TEXT, " VAH " + DoubleToString(vahPrice, 2));
         ObjectSetInteger(0, vahLbl, OBJPROP_COLOR, vaClr);
         ObjectSetInteger(0, vahLbl, OBJPROP_FONTSIZE, 7);
         ObjectSetInteger(0, vahLbl, OBJPROP_ANCHOR, ANCHOR_LEFT);
         ObjectSetInteger(0, vahLbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, vahLbl, OBJPROP_HIDDEN, true);
        }

      // 3. Draw VAL Line
      string valName = XAU_VP_PREFIX + "val";
      ObjectCreate(0, valName, OBJ_TREND, 0, lineStart, valPrice, lineEnd, valPrice);
      ObjectSetInteger(0, valName, OBJPROP_COLOR, vaClr);
      ObjectSetInteger(0, valName, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, valName, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetInteger(0, valName, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, valName, OBJPROP_BACK, true);
      ObjectSetInteger(0, valName, OBJPROP_SELECTABLE, false);

      string valLbl = valName + "_lbl";
      if(ObjectCreate(0, valLbl, OBJ_TEXT, 0, lineEnd, valPrice))
        {
         ObjectSetString(0, valLbl, OBJPROP_TEXT, " VAL " + DoubleToString(valPrice, 2));
         ObjectSetInteger(0, valLbl, OBJPROP_COLOR, vaClr);
         ObjectSetInteger(0, valLbl, OBJPROP_FONTSIZE, 7);
         ObjectSetInteger(0, valLbl, OBJPROP_ANCHOR, ANCHOR_LEFT);
         ObjectSetInteger(0, valLbl, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, valLbl, OBJPROP_HIDDEN, true);
        }

      // Colors for histogram
      color clrVaIn  = (m_theme == VP_THEME_INSTITUTIONAL) ? C'38,55,80'  : C'30,144,255';
      color clrVaOut = (m_theme == VP_THEME_INSTITUTIONAL) ? C'24,30,42'  : C'50,65,85';
      color clrBuy   = (m_theme == VP_THEME_INSTITUTIONAL) ? C'34,115,74' : C'46,139,87';
      color clrSell  = (m_theme == VP_THEME_INSTITUTIONAL) ? C'140,42,50' : C'178,34,34';

      // 4. Draw Histogram Bars
      for(int b = 0; b < m_bins; b++)
        {
         if(binVol[b] <= 0 || maxBinVol <= 0) continue;
         double pLo = minPrice + b * binSize;
         double pHi = pLo + binSize;

         int widthInBars = (int)MathRound((binVol[b] / maxBinVol) * m_maxBarWidth);
         if(widthInBars < 1) widthInBars = 1;

         bool inVA = (b >= lowIdx && b <= highIdx);

         if(m_showDelta && binVol[b] > 0)
           {
            double buyRatio = binBuyVol[b] / binVol[b];
            int buyBars = (int)MathRound(widthInBars * buyRatio);
            int sellBars = widthInBars - buyBars;
            if(buyBars < 0) buyBars = 0;
            if(sellBars < 0) sellBars = 0;

            if(projectLeft)
              {
               datetime tSellEnd   = baseEnd;
               datetime tSellStart = baseEnd - sellBars * barSecs;
               datetime tBuyEnd    = tSellStart;
               datetime tBuyStart  = baseEnd - widthInBars * barSecs;

               if(sellBars > 0)
                  DrawRect(XAU_VP_PREFIX + "s_" + (string)b, tSellStart, pHi, tSellEnd, pLo, clrSell);
               if(buyBars > 0)
                  DrawRect(XAU_VP_PREFIX + "b_" + (string)b, tBuyStart, pHi, tBuyEnd, pLo, clrBuy);
              }
            else
              {
               datetime tBuyStart  = baseStart;
               datetime tBuyEnd    = baseStart + buyBars * barSecs;
               datetime tSellStart = tBuyEnd;
               datetime tSellEnd   = baseStart + widthInBars * barSecs;

               if(buyBars > 0)
                  DrawRect(XAU_VP_PREFIX + "b_" + (string)b, tBuyStart, pHi, tBuyEnd, pLo, clrBuy);
               if(sellBars > 0)
                  DrawRect(XAU_VP_PREFIX + "s_" + (string)b, tSellStart, pHi, tSellEnd, pLo, clrSell);
              }
           }
         else
           {
            color barClr = (b == pocBin) ? pocClr : (inVA ? clrVaIn : clrVaOut);
            datetime tStart, tEnd;
            if(projectLeft)
              {
               tEnd   = baseEnd;
               tStart = baseEnd - widthInBars * barSecs;
              }
            else
              {
               tStart = baseStart;
               tEnd   = baseStart + widthInBars * barSecs;
              }
            DrawRect(XAU_VP_PREFIX + "bar_" + (string)b, tStart, pHi, tEnd, pLo, barClr);
           }
        }
     }
  };

#endif
