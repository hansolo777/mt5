//+------------------------------------------------------------------+
//|  XAUUSD_AI_Trader_M5.mq5                                        |
//|  Claude AI — M5 Trigger + Full Correlation Suite  v4.0          |
//|                                                                  |
//|  v4.0 vs v2.2:                                                  |
//|  + ТРИГЕР: M5 вместо M1 (по-малко шум, по-качествени сигнали)  |
//|  + Silver (XAGUSD) корелация                                    |
//|  + Bollinger Bands изпращани към bridge                         |
//|  + VWAP (дневен) изпращан към bridge                            |
//|  + RSI / ATR / EMA (от v3)                                      |
//|  + DXY корелация (от v2)                                        |
//|  + Оптимизирани свещи: 5xM1, 20xM5, 10xH1, 6xH4               |
//|  + InpTokyo + London + NewYork сесии                            |
//+------------------------------------------------------------------+
#property copyright "XAUUSD AI Trader v4.0 — M5 Trigger + Full Suite"
#property version   "4.00"
#property strict

#include <Trade\Trade.mqh>

//--- Inputs: Bridge
input string  InpBridgeURL    = "http://127.0.0.1:5000/analyze";
input int     InpTimeoutMS    = 15000;

//--- Inputs: Свещи изпращани към AI
//    M1=5  — само за моментен timing (последните 5 минути)
//    M5=20 — основен тригер TF, 20 свещи = 100 мин контекст
//    H1=10 — intermediate trend, 10 свещи = 10 часа
//    H4=6  — macro trend, 6 свещи = 24 часа
input int     InpBarsM1       = 5;    // M1: само за immediate momentum
input int     InpBarsM5       = 20;   // M5: основен TF, 100 мин контекст
input int     InpBarsH1       = 24;   // H1: 24 свещи — нужни за ADX(10) в bridge
input int     InpBarsH4       = 6;    // H4: 24 часа макро тренд

//--- Inputs: DXY
input string  InpDXYSymbol    = "USDINDEX";
input int     InpBarsDXYM5    = 12;   // DXY M5: 60 мин
input int     InpBarsDXYH1    = 6;    // DXY H1: 6 часа

//--- Inputs: Silver (XAGUSD)
input bool    InpUseSilver    = true;
input string  InpSilverSymbol = "XAG_USD";
input int     InpBarsSilverM5 = 12;   // Silver M5: 60 мин

//--- Inputs: Индикатори
input int     InpRSIPeriod    = 14;
input int     InpBBPeriod     = 20;   // Bollinger Bands период
input double  InpBBDeviation  = 2.0;  // Bollinger Bands отклонение
input int     InpEMAFast      = 20;   // EMA fast
input int     InpEMASlow      = 50;   // EMA slow
input int     InpATRPeriod    = 14;

//--- Inputs: Signal
input int     InpMinConfidence= 78;

//--- Inputs: Lot & Risk
input double  InpRisk         = 1.0;
input double  InpFixedLot     = 1.0;   // 0 = автоматично по Risk%
input double  InpFixedSLPips  = 150.0;
input double  InpFixedTPPips  = 300.0;
input double  InpMinSLPoints  = 15.0;
input double  InpMinRR        = 2.0;

//--- Inputs: Protection
input double  InpMinMarginLvl = 150.0;
input double  InpMaxDailyLoss = 2.5;
input int     InpMaxTrades    = 5;
input ulong   InpMagic        = 9999999;

//--- Inputs: Сесии
input bool    InpLondon       = true;
input bool    InpNewYork      = true;
input bool    InpTokyo        = false;
input bool    InpAskAIManage  = true;

//--- Globals
CTrade   Trade;
int      g_hAdxH1 = INVALID_HANDLE;
int      g_hAdxM5 = INVALID_HANDLE;
datetime LastBar   = 0;
datetime DayStart  = 0;
int      DayCount  = 0;
double   DayBal    = 0;
bool     DXYAvail  = false;
bool     SilvAvail = false;

// За VWAP (reset на всеки ден)
double   VWAPSum   = 0;
double   VWAPVol   = 0;
datetime VWAPDay   = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpMagic);
   Trade.SetDeviationInPoints(100);

   // Инициализираме ADX Wilder хендъли глобално
   g_hAdxH1 = iADXWilder(_Symbol, PERIOD_H1, InpATRPeriod);
   g_hAdxM5 = iADXWilder(_Symbol, PERIOD_M5, InpATRPeriod);
   if(g_hAdxH1 == INVALID_HANDLE || g_hAdxM5 == INVALID_HANDLE)
   {
      PrintFormat("=== WARNING: ADX handles failed to initialize ===");
   }
   else
   {
      // Warmup — загряваме Wilder smoothing с 200 бара за точни стойности
      double warmup[];
      CopyBuffer(g_hAdxH1, 0, 0, 200, warmup);
      CopyBuffer(g_hAdxM5, 0, 0, 200, warmup);
      PrintFormat("=== ADX Wilder H1+M5 initialized and warmed up (period=%d, 200 bars) ===", InpATRPeriod);
   }

   DXYAvail = SymbolSelect(InpDXYSymbol, true) &&
              SymbolInfoDouble(InpDXYSymbol, SYMBOL_BID) > 0;
   PrintFormat("=== DXY '%s': %s ===", InpDXYSymbol, DXYAvail ? "НАМЕРЕН" : "НЕ Е НАМЕРЕН");

   if(InpUseSilver)
   {
      SilvAvail = SymbolSelect(InpSilverSymbol, true) &&
                  SymbolInfoDouble(InpSilverSymbol, SYMBOL_BID) > 0;
      PrintFormat("=== Silver '%s': %s ===", InpSilverSymbol, SilvAvail ? "НАМЕРЕН" : "НЕ Е НАМЕРЕН");
   }

   string headers = "Content-Type: application/json\r\n";
   char body[], response[];
   string respHeaders;
   int res = WebRequest("GET","http://127.0.0.1:5000/health",
                        headers, InpTimeoutMS, body, response, respHeaders);
   if(res == 200) Print("=== AI Bridge: ONLINE ✓ ===");
   else           PrintFormat("=== AI Bridge: OFFLINE (код=%d) ===", res);

   PrintFormat("=== M5 Trigger | DXY=%s | Silver=%s | MinConf=%d%% ===",
               DXYAvail?"ON":"OFF", SilvAvail?"ON":"OFF", InpMinConfidence);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
string GetSession()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   int h = dt.hour;
   bool london = (h >= 7  && h < 16);
   bool ny     = (h >= 12 && h < 21);
   if(london && ny)    return "LONDON_NY_OVERLAP";
   if(london)          return "LONDON";
   if(ny)              return "NEW_YORK";
   if(h >= 0 && h < 9) return "TOKYO";
   return "OFF_SESSION";
}

bool IsSessionActive()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   int h = dt.hour;
   if(InpLondon  && h >= 7  && h < 16) return true;
   if(InpNewYork && h >= 12 && h < 21) return true;
   if(InpTokyo   && h >= 0  && h <  9) return true;
   return false;
}

bool MarginCheck(ENUM_ORDER_TYPE orderType, double lot, double price)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   if(margin > 0 && equity/margin*100.0 < InpMinMarginLvl) return false;
   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double reqMargin  = 0;
   if(!OrderCalcMargin(orderType,_Symbol,lot,price,reqMargin) || reqMargin<=0) return false;
   if(freeMargin < reqMargin * 1.2) return false;
   return true;
}

double CalcLot(double slPoints)
{
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(InpFixedLot > 0)
   {
      double lot = MathMax(mn, MathMin(mx, InpFixedLot));
      return NormalizeDouble(MathRound(lot/step)*step, 2);
   }
   if(slPoints <= 0) return mn;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double tv  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv<=0||ts<=0) return mn;
   double lot = (bal * InpRisk / 100.0) / (slPoints / ts * tv);
   lot = MathMax(mn, MathMin(mx, lot));
   return NormalizeDouble(MathRound(lot/step)*step, 2);
}

bool HasPos(ENUM_POSITION_TYPE &posType, double &posProfit, double &posOpen)
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      posType   = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      posProfit = PositionGetDouble(POSITION_PROFIT);
      posOpen   = PositionGetDouble(POSITION_PRICE_OPEN);
      return true;
   }
   posType=POSITION_TYPE_BUY; posProfit=0; posOpen=0;
   return false;
}

void CloseAll()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      Trade.PositionClose(t);
      Print("[AI] CLOSE: позицията затворена");
   }
}

//+------------------------------------------------------------------+
// VWAP — Volume Weighted Average Price (дневен reset)
//+------------------------------------------------------------------+
double CalcVWAP()
{
   MqlDateTime now; TimeToStruct(TimeGMT(), now);
   datetime todayStart = StringToTime(StringFormat("%d.%02d.%02d 00:00:00",
                                      now.year, now.mon, now.day));
   if(VWAPDay != todayStart)
   {
      VWAPSum = 0; VWAPVol = 0; VWAPDay = todayStart;
   }

   // Вземаме M5 свещи от началото на деня
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_M5, todayStart, TimeCurrent(), rates);
   if(copied <= 0) return 0;

   double sumPV = 0, sumV = 0;
   for(int i = 0; i < copied; i++)
   {
      double typical = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol     = (double)rates[i].tick_volume;
      sumPV += typical * vol;
      sumV  += vol;
   }
   return (sumV > 0) ? sumPV / sumV : 0;
}

//+------------------------------------------------------------------+
string BarsToJSON(string symbol, ENUM_TIMEFRAMES tf, int count)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(symbol, tf, 1, count, rates);
   if(copied <= 0) return "[]";
   string s = "[";
   for(int i=0; i<copied; i++)
   {
      if(i > 0) s += ",";
      MqlDateTime dt; TimeToStruct(rates[i].time, dt);
      s += StringFormat(
         "{\"time\":\"%04d-%02d-%02d %02d:%02d\","
         "\"open\":%.2f,\"high\":%.2f,\"low\":%.2f,\"close\":%.2f,"
         "\"tick_volume\":%d}",
         dt.year, dt.mon, dt.day, dt.hour, dt.min,
         rates[i].open, rates[i].high, rates[i].low, rates[i].close,
         (int)rates[i].tick_volume
      );
   }
   s += "]";
   return s;
}

//+------------------------------------------------------------------+
// Изчислява всички индикатори и ги връща като JSON string
//+------------------------------------------------------------------+
string CalcIndicatorsJSON()
{
   // RSI
   int hRsiM5 = iRSI(_Symbol, PERIOD_M5, InpRSIPeriod, PRICE_CLOSE);
   int hRsiH1 = iRSI(_Symbol, PERIOD_H1, InpRSIPeriod, PRICE_CLOSE);

   // ATR
   int hAtrM5 = iATR(_Symbol, PERIOD_M5, InpATRPeriod);
   int hAtrH1 = iATR(_Symbol, PERIOD_H1, InpATRPeriod);

   // EMA
   int hEmaFastH1 = iMA(_Symbol, PERIOD_H1, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);
   int hEmaSlowH1 = iMA(_Symbol, PERIOD_H1, InpEMASlow, 0, MODE_EMA, PRICE_CLOSE);
   int hEmaFastM5 = iMA(_Symbol, PERIOD_M5, InpEMAFast, 0, MODE_EMA, PRICE_CLOSE);

   // Bollinger Bands на M5
   int hBB = iBands(_Symbol, PERIOD_M5, InpBBPeriod, 0, InpBBDeviation, PRICE_CLOSE);

   // ADX Wilder — ползваме глобалните хендъли от OnInit()

   double rsiM5=0, rsiH1=0, atrM5=0, atrH1=0;
   double emaFastH1=0, emaSlowH1=0, emaFastM5=0;
   double bbUpper=0, bbMiddle=0, bbLower=0, bbWidth=0;
   double adxH1=0, adxM5=0;

   double buf[];
   ArraySetAsSeries(buf, true);
   ArrayResize(buf, 1);

   if(CopyBuffer(hRsiM5,    0, 1, 1, buf)>0) rsiM5     = buf[0];
   if(CopyBuffer(hRsiH1,    0, 1, 1, buf)>0) rsiH1     = buf[0];
   if(CopyBuffer(hAtrM5,    0, 1, 1, buf)>0) atrM5     = buf[0];
   if(CopyBuffer(hAtrH1,    0, 1, 1, buf)>0) atrH1     = buf[0];
   if(CopyBuffer(hEmaFastH1,0, 1, 1, buf)>0) emaFastH1 = buf[0];
   if(CopyBuffer(hEmaSlowH1,0, 1, 1, buf)>0) emaSlowH1 = buf[0];
   if(CopyBuffer(hEmaFastM5,0, 1, 1, buf)>0) emaFastM5 = buf[0];
   if(CopyBuffer(hBB, 1,    1, 1, buf)>0) bbUpper   = buf[0]; // Upper band
   if(CopyBuffer(hBB, 0,    1, 1, buf)>0) bbMiddle  = buf[0]; // Middle
   if(CopyBuffer(hBB, 2,    1, 1, buf)>0) bbLower   = buf[0]; // Lower band
   if(CopyBuffer(g_hAdxH1, 0, 1, 1, buf)>0) adxH1  = buf[0]; // ADX Wilder H1
   if(CopyBuffer(g_hAdxM5, 0, 1, 1, buf)>0) adxM5  = buf[0]; // ADX Wilder M5

   if(bbUpper > 0 && bbLower > 0)
      bbWidth = (bbUpper - bbLower) / bbMiddle * 100.0; // BB width as % of middle

   IndicatorRelease(hRsiM5); IndicatorRelease(hRsiH1);
   IndicatorRelease(hAtrM5); IndicatorRelease(hAtrH1);
   IndicatorRelease(hEmaFastH1); IndicatorRelease(hEmaSlowH1);
   IndicatorRelease(hEmaFastM5); IndicatorRelease(hBB);
   // ADX handles are global — do not release here

   // VWAP
   double vwap = CalcVWAP();

   return StringFormat(
      "\"indicators\":{"
      "\"rsi_m5\":%.2f,"
      "\"rsi_h1\":%.2f,"
      "\"atr_m5\":%.2f,"
      "\"atr_h1\":%.2f,"
      "\"ema20_m5\":%.2f,"
      "\"ema20_h1\":%.2f,"
      "\"ema50_h1\":%.2f,"
      "\"bb_upper\":%.2f,"
      "\"bb_middle\":%.2f,"
      "\"bb_lower\":%.2f,"
      "\"bb_width_pct\":%.2f,"
      "\"vwap\":%.2f,"
      "\"adx_h1\":%.2f,"
      "\"adx_m5\":%.2f"
      "}",
      rsiM5, rsiH1, atrM5, atrH1,
      emaFastM5, emaFastH1, emaSlowH1,
      bbUpper, bbMiddle, bbLower, bbWidth, vwap,
      adxH1, adxM5
   );
}

string ExtractJSON(const string &json, const string &key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0) return "";
   pos += StringLen(search);
   while(pos < StringLen(json) &&
         (StringGetCharacter(json,pos)==' ' || StringGetCharacter(json,pos)=='\t'))
      pos++;
   if(pos >= StringLen(json)) return "";
   ushort ch = StringGetCharacter(json, pos);
   if(ch == '"')
   {
      pos++;
      string result = "";
      while(pos < StringLen(json))
      {
         ushort c = StringGetCharacter(json, pos);
         if(c == '"') break;
         result += ShortToString(c);
         pos++;
      }
      return result;
   }
   string result = "";
   while(pos < StringLen(json))
   {
      ushort c = StringGetCharacter(json, pos);
      if(c==',' || c=='}' || c==']' || c=='\n') break;
      result += ShortToString(c);
      pos++;
   }
   StringTrimLeft(result); StringTrimRight(result);
   return result;
}

//+------------------------------------------------------------------+
void AskAI(bool hasPos, ENUM_POSITION_TYPE posType, double posProfit, double posOpen)
{
   double bid      = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask      = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bal      = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq       = AccountInfoDouble(ACCOUNT_EQUITY);
   double marg     = AccountInfoDouble(ACCOUNT_MARGIN);
   double mLvl     = (marg > 0) ? eq/marg*100.0 : 9999.0;
   double dailyPnl = eq - DayBal;
   string posStr   = hasPos ? (posType==POSITION_TYPE_BUY ? "BUY" : "SELL") : "NONE";

   // DXY
   string dxyBlock = "";
   if(DXYAvail)
   {
      dxyBlock = StringFormat(
         ",\"dxy_bid\":%.5f,\"dxy_bars_m5\":%s,\"dxy_bars_h1\":%s",
         SymbolInfoDouble(InpDXYSymbol, SYMBOL_BID),
         BarsToJSON(InpDXYSymbol, PERIOD_M5, InpBarsDXYM5),
         BarsToJSON(InpDXYSymbol, PERIOD_H1, InpBarsDXYH1)
      );
   }

   // Silver
   string silvBlock = "";
   if(SilvAvail)
   {
      silvBlock = StringFormat(
         ",\"silver_bid\":%.4f,\"silver_bars_m5\":%s",
         SymbolInfoDouble(InpSilverSymbol, SYMBOL_BID),
         BarsToJSON(InpSilverSymbol, PERIOD_M5, InpBarsSilverM5)
      );
   }

   // Индикатори
   string indBlock = "," + CalcIndicatorsJSON();

   string payload = StringFormat(
      "{"
      "\"bid\":%.2f,\"ask\":%.2f,"
      "\"balance\":%.2f,\"equity\":%.2f,"
      "\"margin_level\":%.1f,\"daily_pnl\":%.2f,"
      "\"session\":\"%s\","
      "\"trigger_tf\":\"M5\","
      "\"has_position\":%s,"
      "\"position_type\":\"%s\","
      "\"position_profit\":%.2f,"
      "\"position_open_price\":%.2f,"
      "\"bars_m1\":%s,"
      "\"bars_m5\":%s,"
      "\"bars_h1\":%s,"
      "\"bars_h4\":%s"
      "%s%s%s"
      "}",
      bid, ask, bal, eq, mLvl, dailyPnl,
      GetSession(),
      hasPos ? "true" : "false",
      posStr, posProfit, posOpen,
      BarsToJSON(_Symbol, PERIOD_M1, InpBarsM1),
      BarsToJSON(_Symbol, PERIOD_M5, InpBarsM5),
      BarsToJSON(_Symbol, PERIOD_H1, InpBarsH1),
      BarsToJSON(_Symbol, PERIOD_H4, InpBarsH4),
      dxyBlock, silvBlock, indBlock
   );

   string headers = "Content-Type: application/json\r\n";
   char   bodyArr[], respArr[];
   StringToCharArray(payload, bodyArr, 0, StringLen(payload));
   string respHeaders;

   int code = WebRequest("POST", InpBridgeURL, headers, InpTimeoutMS,
                         bodyArr, respArr, respHeaders);
   if(code != 200) { PrintFormat("[AI] HTTP error: %d", code); return; }

   string respStr = CharArrayToString(respArr);
   Print("[AI RAW] ", StringSubstr(respStr, 0, 200));

   string action     = ExtractJSON(respStr, "action");
   int    confidence = (int)StringToDouble(ExtractJSON(respStr, "confidence"));
   double sl_pips    = StringToDouble(ExtractJSON(respStr, "sl_pips"));
   double tp_pips    = StringToDouble(ExtractJSON(respStr, "tp_pips"));
   string reason     = ExtractJSON(respStr, "reason");

   PrintFormat("[AI] %s | conf=%d%% | SL=%.2f TP=%.2f | %s",
               action, confidence, sl_pips, tp_pips, StringSubstr(reason,0,100));

   if(confidence < InpMinConfidence && (action=="BUY" || action=="SELL"))
   { PrintFormat("[AI] Skip: conf %d%% < %d%%", confidence, InpMinConfidence); return; }

   if(action == "CLOSE" && hasPos) { CloseAll(); return; }
   if(action == "HOLD") return;

   if(action == "BUY" && !hasPos)
   {
      if(DayBal>0 && (DayBal-AccountInfoDouble(ACCOUNT_BALANCE))/DayBal*100.0 >= InpMaxDailyLoss)
      { Print("[AI] Skip BUY: MaxDailyLoss"); return; }
      if(DayCount >= InpMaxTrades) { Print("[AI] Skip BUY: MaxTrades"); return; }

      double sl_dist = (sl_pips > 0 ? sl_pips : InpFixedSLPips) * _Point;
      double tp_dist = (tp_pips > 0 ? tp_pips : InpFixedTPPips) * _Point;
      if(sl_dist < InpMinSLPoints * _Point) { Print("[AI] Skip BUY: SL too tight"); return; }
      if(tp_dist < sl_dist * InpMinRR)      { Print("[AI] Skip BUY: RR too low");   return; }

      double lot = CalcLot(sl_dist);
      if(!MarginCheck(ORDER_TYPE_BUY, lot, ask)) return;
      double sl = NormalizeDouble(ask - sl_dist, _Digits);
      double tp = NormalizeDouble(ask + tp_dist, _Digits);
      PrintFormat("[AI] → BUY %.2f @ %.2f | SL=%.2f TP=%.2f", lot, ask, sl, tp);
      if(Trade.Buy(lot, _Symbol, 0, sl, tp, "AI_BUY_M5"))
      { DayCount++; PrintFormat("✓ BUY #%d", Trade.ResultOrder()); }
      else PrintFormat("✗ BUY FAIL: %s", Trade.ResultRetcodeDescription());
   }
   else if(action == "SELL" && !hasPos)
   {
      if(DayBal>0 && (DayBal-AccountInfoDouble(ACCOUNT_BALANCE))/DayBal*100.0 >= InpMaxDailyLoss)
      { Print("[AI] Skip SELL: MaxDailyLoss"); return; }
      if(DayCount >= InpMaxTrades) { Print("[AI] Skip SELL: MaxTrades"); return; }

      double sl_dist = (sl_pips > 0 ? sl_pips : InpFixedSLPips) * _Point;
      double tp_dist = (tp_pips > 0 ? tp_pips : InpFixedTPPips) * _Point;
      if(sl_dist < InpMinSLPoints * _Point) { Print("[AI] Skip SELL: SL too tight"); return; }
      if(tp_dist < sl_dist * InpMinRR)      { Print("[AI] Skip SELL: RR too low");   return; }

      double lot = CalcLot(sl_dist);
      if(!MarginCheck(ORDER_TYPE_SELL, lot, bid)) return;
      double sl = NormalizeDouble(bid + sl_dist, _Digits);
      double tp = NormalizeDouble(bid - tp_dist, _Digits);
      PrintFormat("[AI] → SELL %.2f @ %.2f | SL=%.2f TP=%.2f", lot, bid, sl, tp);
      if(Trade.Sell(lot, _Symbol, 0, sl, tp, "AI_SELL_M5"))
      { DayCount++; PrintFormat("✓ SELL #%d", Trade.ResultOrder()); }
      else PrintFormat("✗ SELL FAIL: %s", Trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   // === ТРИГЕР: нова M5 свещ ===
   datetime cb = iTime(_Symbol, PERIOD_M5, 0);
   if(cb == LastBar) return;
   LastBar = cb;

   // Daily reset
   MqlDateTime now; TimeToStruct(TimeGMT(), now);
   datetime today = StringToTime(StringFormat("%d.%02d.%02d 00:00:00",
                                 now.year, now.mon, now.day));
   if(DayStart < today)
   {
      DayStart = today;
      DayCount = 0;
      DayBal   = AccountInfoDouble(ACCOUNT_BALANCE);
      Print("=== New Day === Balance=", DayBal);
   }

   double curBal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(DayBal > 0 && (DayBal-curBal)/DayBal*100.0 >= InpMaxDailyLoss)
   { Print("[GUARD] MaxDailyLoss — спряно за деня"); return; }

   ENUM_POSITION_TYPE posType;
   double posProfit, posOpen;
   bool hasPos = HasPos(posType, posProfit, posOpen);

   if(!hasPos && !IsSessionActive()) return;
   if(!hasPos && DayCount >= InpMaxTrades) return;
   if(hasPos && !InpAskAIManage) return;

   AskAI(hasPos, posType, posProfit, posOpen);
}
//+------------------------------------------------------------------+

