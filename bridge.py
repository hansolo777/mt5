"""
XAUUSD AI Bridge v4.1 - M5 Edition + Macro Data
====================================
Нови корелации vs v4.0:
  - TLT (20Y Treasury Bond ETF) от Yahoo Finance
  - US10Y доходност (^TNX) от Yahoo Finance
  - TIPS real yield (TIP ETF) от Yahoo Finance
  - Кеш 5 минути — не забавя запитванията
  - Fallback ако Yahoo не отговаря
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import anthropic

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PORT           = 5000
LOG_FILE       = r"C:\XAUUSD_AI_Bridge\bridge.log"
FULL_LOG_FILE  = r"C:\XAUUSD_AI_Bridge\bridge.requests.log"
DECISIONS_FILE = r"C:\XAUUSD_AI_Bridge\decisions.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("bridge")

# Full request/response logger -> bridge.requests.log
full_log_handler = logging.FileHandler(FULL_LOG_FILE, encoding="utf-8")
full_log_handler.setLevel(logging.DEBUG)
full_log_handler.setFormatter(logging.Formatter("%(message)s"))
full_log = logging.getLogger("bridge.full")
full_log.setLevel(logging.DEBUG)
full_log.addHandler(full_log_handler)
full_log.propagate = False

app = Flask(__name__)

# ============================================================
# MACRO DATA FETCHER — TLT, US10Y, TIP от Yahoo Finance
# Кеш 5 минути за да не забавяме всяко запитване
# ============================================================

_macro_cache      = {}
_macro_cache_time = 0
_MACRO_TTL        = 300  # секунди (5 минути)
_macro_lock       = threading.Lock()

MACRO_TICKERS = {
    "TLT":  "TLT",    # iShares 20+ Year Treasury Bond ETF
    "US10Y": "^TNX",  # 10-Year Treasury Yield
    "TIP":  "TIP",    # iShares TIPS Bond ETF (real yields)
    "SPY":  "SPY",    # S&P 500 ETF (risk sentiment)
}


def fetch_yahoo_data(ticker: str) -> dict:
    """
    Взима от Yahoo Finance:
    - Текуща цена + дневна промяна %
    - Последните 10 дневни свещи (за тренд)
    - Дневен High/Low
    """
    if not _HAS_REQUESTS:
        return {}
    try:
        # 1. Дневни свещи — последните 10 дни
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "14d"}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = _requests.get(url, params=params, headers=headers, timeout=5)
        if r.status_code != 200:
            return {}
        data  = r.json()
        res   = data.get("chart", {}).get("result", [{}])[0]
        meta  = res.get("meta", {})
        q     = res.get("indicators", {}).get("quote", [{}])[0]
        ts    = res.get("timestamp", [])

        current  = float(meta.get("regularMarketPrice", 0) or 0)
        prev_cls = float(meta.get("chartPreviousClose",  0) or 0)
        day_high = float(meta.get("regularMarketDayHigh", 0) or 0)
        day_low  = float(meta.get("regularMarketDayLow",  0) or 0)

        chg_pct  = ((current - prev_cls) / prev_cls * 100) if prev_cls else 0

        # Последните 10 дневни свещи
        closes = q.get("close",  [])
        opens  = q.get("open",   [])
        highs  = q.get("high",   [])
        lows   = q.get("low",    [])
        candles = []
        for i in range(len(ts)):
            try:
                from datetime import datetime as _dt
                dt_str = _dt.utcfromtimestamp(ts[i]).strftime("%Y-%m-%d")
                o = round(float(opens[i]  or 0), 4)
                h = round(float(highs[i]  or 0), 4)
                lo= round(float(lows[i]   or 0), 4)
                cl= round(float(closes[i] or 0), 4)
                if cl > 0:
                    candles.append({"date": dt_str, "open": o, "high": h, "low": lo, "close": cl})
            except:
                continue
        candles = candles[-10:]  # последните 10 дни

        # Тренд от последните 5 дни
        trend = "N/A"
        if len(candles) >= 5:
            first = candles[-5]["close"]
            last  = candles[-1]["close"]
            move  = round(last - first, 4)
            pct   = round(move / first * 100, 2) if first else 0
            trend = f"{'UP' if move > 0 else 'DOWN'} {abs(pct):.2f}% over 5 days"

        return {
            "current":  current,
            "prev_close": prev_cls,
            "change_pct": round(chg_pct, 2),
            "day_high": day_high,
            "day_low":  day_low,
            "trend_5d": trend,
            "candles":  candles,
        }
    except Exception as e:
        log.warning(f"Yahoo fetch error for {ticker}: {e}")
        return {}


def get_macro_data() -> dict:
    """Връща кеширани macro данни. Обновява ако кешът е изтекъл."""
    global _macro_cache, _macro_cache_time
    with _macro_lock:
        now = time.time()
        if now - _macro_cache_time < _MACRO_TTL and _macro_cache:
            return _macro_cache

        log.info("[macro] Fetching macro data from Yahoo Finance...")
        result = {}
        for key, ticker in MACRO_TICKERS.items():
            price = fetch_yahoo_data(ticker)
            result[key] = price
            if isinstance(price, dict) and price.get("current", 0) > 0:
                cur = price.get("current", 0)
                chg = price.get("change_pct", 0)
                log.info(f"[macro] {key}: {cur:.4f} ({chg:+.2f}% today)")

        if any(v.get("current", 0) > 0 for v in result.values() if isinstance(v, dict)):
            _macro_cache      = result
            _macro_cache_time = now
        else:
            log.warning("[macro] All tickers returned 0 — Yahoo may be down, using cached data")

        return _macro_cache if _macro_cache else result


def get_client():
    key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is not set!")
    return anthropic.Anthropic(api_key=key)


def format_candles(candles: list, tf: str) -> str:
    if not candles:
        return f"  {tf}: (no data)\n"
    lines = [f"  {tf} ({len(candles)} candles, newest first):"]
    lines.append("    # | time            |   Open    High     Low   Close  Vol")
    lines.append("    " + "-" * 65)
    for i, c in enumerate(candles[:50]):
        vol = c.get("tick_volume", c.get("volume", 0))
        t   = c.get("time", "")
        lines.append(
            f"    {i:<2}| {t:<16}|"
            f" {c.get('open',0):<8.2f} {c.get('high',0):<8.2f}"
            f" {c.get('low',0):<8.2f} {c.get('close',0):<8.2f} {vol}"
        )
    return "\n".join(lines) + "\n"



def calc_sr_levels(h4_candles: list, h1_candles: list, bid: float) -> str:
    """
    Изчислява S/R нива от свещи:
    1. Swing Highs/Lows от H4 (макро нива)
    2. Swing Highs/Lows от H1 (интрадей нива)
    3. Previous Day High/Low/Close от H1 свещи
    4. Психологически нива (кръгли числа) близо до текущата цена
    """
    if not h4_candles and not h1_candles:
        return "  S/R Levels: not available (no candle data)"

    lines = []

    # --- Swing High/Low от H4 ---
    # Swing High: свещ чийто High е по-висок от 2 свещи вляво и вдясно
    # Swing Low:  свещ чийто Low е по-нисък от 2 свещи вляво и вдясно
    h4_swing_highs = []
    h4_swing_lows  = []
    candles = list(reversed(h4_candles))  # от най-стара към най-нова
    for i in range(2, len(candles) - 2):
        h = candles[i].get("high", 0)
        lo = candles[i].get("low", 0)
        if h > 0:
            if (h > candles[i-1].get("high",0) and h > candles[i-2].get("high",0) and
                h > candles[i+1].get("high",0) and h > candles[i+2].get("high",0)):
                h4_swing_highs.append(round(h, 2))
            if (lo < candles[i-1].get("low",9999) and lo < candles[i-2].get("low",9999) and
                lo < candles[i+1].get("low",9999) and lo < candles[i+2].get("low",9999)):
                h4_swing_lows.append(round(lo, 2))

    # Вземаме само близките до текущата цена (в рамките на 3%)
    def near(level, price, pct=0.03):
        return abs(level - price) / price < pct

    h4_res = sorted([l for l in h4_swing_highs if l > bid and near(l, bid)], reverse=False)[:3]
    h4_sup = sorted([l for l in h4_swing_lows  if l < bid and near(l, bid)], reverse=True)[:3]

    # --- Swing High/Low от H1 ---
    h1_swing_highs = []
    h1_swing_lows  = []
    h1c = list(reversed(h1_candles))
    for i in range(2, len(h1c) - 2):
        h = h1c[i].get("high", 0)
        lo = h1c[i].get("low", 0)
        if h > 0:
            if (h > h1c[i-1].get("high",0) and h > h1c[i-2].get("high",0) and
                h > h1c[i+1].get("high",0) and h > h1c[i+2].get("high",0)):
                h1_swing_highs.append(round(h, 2))
            if (lo < h1c[i-1].get("low",9999) and lo < h1c[i-2].get("low",9999) and
                lo < h1c[i+1].get("low",9999) and lo < h1c[i+2].get("low",9999)):
                h1_swing_lows.append(round(lo, 2))

    h1_res = sorted([l for l in h1_swing_highs if l > bid and near(l, bid)], reverse=False)[:3]
    h1_sup = sorted([l for l in h1_swing_lows  if l < bid and near(l, bid)], reverse=True)[:3]

    # --- Previous Day High/Low/Close от H1 свещи ---
    # Намираме свещите от предишния ден (groupby дата)
    prev_day_high  = 0
    prev_day_low   = 99999
    prev_day_close = 0
    today_date = ""
    if h1_candles:
        today_date = h1_candles[0].get("time", "")[:10]
    prev_day_candles = [c for c in h1_candles if c.get("time","")[:10] < today_date and c.get("time","")[:10] != ""]
    if prev_day_candles:
        # Намираме датата на предишния ден
        prev_date = max(c.get("time","")[:10] for c in prev_day_candles)
        pd_candles = [c for c in prev_day_candles if c.get("time","")[:10] == prev_date]
        if pd_candles:
            prev_day_high  = max(c.get("high", 0)    for c in pd_candles)
            prev_day_low   = min(c.get("low",  9999) for c in pd_candles)
            prev_day_close = pd_candles[0].get("close", 0)  # най-новата = close

    # --- Психологически нива (кръгли числа) ---
    # Намираме кръгли числа на 50 и 100 точки близо до цената
    psych_levels = []
    base = round(bid / 50) * 50
    for mult in range(-6, 7):
        level = round(base + mult * 50, 2)
        if level != round(bid, 0) and near(level, bid, 0.02):
            psych_levels.append(level)
    psych_res = sorted([l for l in psych_levels if l > bid])[:3]
    psych_sup = sorted([l for l in psych_levels if l < bid], reverse=True)[:3]

    # --- Форматираме изхода ---
    lines.append("KEY SUPPORT & RESISTANCE LEVELS:")
    lines.append(f"  Current price: {bid:.2f}")
    lines.append("")

    lines.append("  RESISTANCE (above price):")
    if h4_res:
        lines.append(f"    H4 Swing Highs: {', '.join(str(l) for l in h4_res)}")
    if h1_res:
        lines.append(f"    H1 Swing Highs: {', '.join(str(l) for l in h1_res)}")
    if psych_res:
        lines.append(f"    Psychological:  {', '.join(str(l) for l in psych_res)}")
    if prev_day_high > 0 and prev_day_high > bid:
        lines.append(f"    Prev Day High:  {prev_day_high:.2f}")
    if not h4_res and not h1_res and not psych_res:
        lines.append("    (none found in range)")

    lines.append("")
    lines.append("  SUPPORT (below price):")
    if h4_sup:
        lines.append(f"    H4 Swing Lows:  {', '.join(str(l) for l in h4_sup)}")
    if h1_sup:
        lines.append(f"    H1 Swing Lows:  {', '.join(str(l) for l in h1_sup)}")
    if psych_sup:
        lines.append(f"    Psychological:  {', '.join(str(l) for l in psych_sup)}")
    if prev_day_low < 99999 and prev_day_low < bid:
        lines.append(f"    Prev Day Low:   {prev_day_low:.2f}")
    if not h4_sup and not h1_sup and not psych_sup:
        lines.append("    (none found in range)")

    if prev_day_close > 0:
        rel = "above" if bid > prev_day_close else "below"
        lines.append("")
        lines.append(f"  Prev Day Close: {prev_day_close:.2f} (price is {rel} prev close)")

    lines.append("")
    lines.append("  S/R USAGE RULES:")
    lines.append("  - Place TP just BELOW nearest resistance (not above)")
    lines.append("  - Place SL just BELOW nearest support (for BUY) or ABOVE resistance (for SELL)")
    lines.append("  - H4 levels are stronger than H1 levels")
    lines.append("  - Psychological levels (50/100 pt intervals) often act as magnets")
    lines.append("  - Price near strong S/R = reduced confidence, wait for break or bounce")

    return "\n".join(lines)

def get_session_context(utc_now: datetime) -> str:
    h = utc_now.hour
    m = utc_now.minute
    total_min = h * 60 + m

    def mins_to(open_min):
        d = open_min - total_min
        return d if d >= 0 else d + 24 * 60

    def mins_since(open_min):
        d = total_min - open_min
        return d if d >= 0 else d + 24 * 60

    def is_open(o, c):
        if o < c: return o <= total_min < c
        return total_min >= o or total_min < c

    LO, LC   = 7*60,  16*60
    NO, NC   = 13*60, 21*60
    TKO, TKC = 0,     9*60

    london_on = is_open(LO, LC)
    ny_on     = is_open(NO, NC)
    xetra_on  = is_open(7*60, 15*60+30)
    tokyo_on  = is_open(TKO, TKC)
    overlap   = london_on and ny_on

    active = []
    if tokyo_on:  active.append("TOKYO")
    if xetra_on:  active.append("XETRA")
    if london_on: active.append("LONDON")
    if ny_on:     active.append("NEW YORK")
    if not active: active.append("OFF-HOURS")

    lines = [
        "=" * 60,
        f"SESSION CONTEXT — {utc_now.strftime('%H:%M UTC')}",
        "=" * 60,
        f"Active: {' + '.join(active)}"
    ]
    if overlap:
        lines.append("*** LONDON-NY OVERLAP — MAXIMUM INSTITUTIONAL ACTIVITY ***")
    lines.append("")

    if london_on:
        ms = mins_since(LO)
        lines.append(f"  London: OPEN {ms//60}h{ms%60:02d}m since open")
        if ms < 15:
            lines.append("  FIRST 15 MIN — DO NOT ENTER (stop hunts, false breakouts)")
        elif ms < 30:
            lines.append("  London 15-30 min — wait for direction confirmation")
        elif ms < 90:
            lines.append("  London prime window — highest quality setups")
        elif total_min >= 15*60:
            lines.append("  London approaching close (16:00) — position closing expected")
    else:
        mt = mins_to(LO)
        if mt <= 30:
            lines.append(f"  London: PRE-MARKET opens in {mt}min — AVOID new positions")
        else:
            lines.append(f"  London: closed — opens in {mt//60}h{mt%60:02d}m")

    if ny_on:
        ms = mins_since(NO)
        lines.append(f"  New York: OPEN {ms//60}h{ms%60:02d}m since open")
        if ms < 15:
            lines.append("  FIRST 15 MIN NY — high volatility, wait for candle close")
        elif ms < 45:
            lines.append("  NY open window — watch London-NY correlation")
    else:
        mt = mins_to(NO)
        if mt <= 30:
            lines.append(f"  New York: PRE-MARKET opens in {mt}min — volatility spike expected")
        else:
            lines.append(f"  New York: closed — opens in {mt//60}h{mt%60:02d}m")

    if tokyo_on:
        ms = mins_since(TKO)
        lines.append(f"  Tokyo: OPEN {ms//60}h{ms%60:02d}m — low volume, range-bound expected")

    lines.append("")

    if london_on and 15 <= mins_since(LO) < 90:
        lines.append("LONDON PATTERN:")
        lines.append("  - Asian range sweep often happens at open (false break then reverse)")
        lines.append("  - True direction set by 08:30-09:00 UTC")
        lines.append("  - High volume break = real move; low volume = likely fade")

    if ny_on and mins_since(NO) < 60:
        lines.append("NY-LONDON CORRELATION:")
        lines.append("  Case A: London trended strongly (>40pts) -> ~65% reversal at NY open")
        lines.append("  Case B: London trended moderately (15-40pts) -> ~60% continuation")
        lines.append("  Case C: London ranged -> NY creates the real direction")
        lines.append("  KEY: Watch first full M5 candle after 13:00 UTC for direction")

    if overlap:
        lines.append("OVERLAP: Strongest institutional moves — trend-following only")

    if not london_on and not ny_on:
        lines.append("OFF-HOURS: HOLD bias unless 5+/6 confluence")

    mt_l = mins_to(LO) if not london_on else None
    mt_n = mins_to(NO) if not ny_on else None
    if mt_l is not None and mt_l <= 30:
        lines.append(f"\nPRE-LONDON in {mt_l}min — avoid new entries")
    if mt_n is not None and mt_n <= 30:
        lines.append(f"\nPRE-NY in {mt_n}min — volatility spike expected")

    lines.append("=" * 60)
    return "\n".join(lines)


def build_macro_section(macro: dict) -> str:
    """Изгражда macro context секцията с TLT, US10Y, TIP, SPY — с история и тренд."""
    if not macro or not any(
        isinstance(v, dict) and v.get("current", 0) > 0
        for v in macro.values()
    ):
        return "  Macro data: not available (Yahoo Finance unreachable)"

    def fmt_instrument(key, d, correlation, rule_up, rule_down):
        if not isinstance(d, dict) or not d.get("current"):
            return f"  {key}: N/A"
        cur  = d["current"]
        chg  = d.get("change_pct", 0)
        hi   = d.get("day_high", 0)
        lo   = d.get("day_low", 0)
        tr5  = d.get("trend_5d", "N/A")
        cnd  = d.get("candles", [])
        sign = "+" if chg >= 0 else ""
        direction = "UP" if chg >= 0 else "DOWN"
        bias = rule_up if chg >= 0 else rule_down

        lines = [
            f"  {key}: {cur:.4f}  ({sign}{chg:.2f}% today, {direction})",
            f"    Day range: {lo:.4f} - {hi:.4f}",
            f"    5-day trend: {tr5}",
            f"    Correlation: {correlation}",
            f"    Today signal: {bias}",
        ]

        # Последните 5 дневни closes
        if len(cnd) >= 3:
            last5 = cnd[-5:] if len(cnd) >= 5 else cnd
            closes_str = " -> ".join(f"{x['close']:.3f}" for x in last5)
            lines.append(f"    Daily closes: {closes_str}")

        return "\n".join(lines)

    tlt   = macro.get("TLT",   {})
    us10y = macro.get("US10Y", {})
    tip   = macro.get("TIP",   {})
    spy   = macro.get("SPY",   {})

    sections = ["MACRO / BOND MARKET CONTEXT (Yahoo Finance, ~15min delayed):", ""]

    sections.append(fmt_instrument(
        "TLT (20Y Treasury Bond ETF)", tlt,
        correlation="POSITIVE ~70% with Gold",
        rule_up="TLT rising = yields falling = BULLISH for Gold",
        rule_down="TLT falling = yields rising = BEARISH for Gold"
    ))
    sections.append("")

    sections.append(fmt_instrument(
        "US 10Y Yield (^TNX)", us10y,
        correlation="NEGATIVE ~75% with Gold",
        rule_up="Yield rising = BEARISH pressure on Gold",
        rule_down="Yield falling = BULLISH pressure on Gold"
    ))
    # Extra context за yield level
    if isinstance(us10y, dict) and us10y.get("current"):
        y = us10y["current"]
        if y > 4.5:
            sections.append("    ELEVATED >4.5% = significant headwind for Gold BUY")
        elif y < 4.0:
            sections.append("    LOW <4.0% = supportive for Gold BUY")
    sections.append("")

    sections.append(fmt_instrument(
        "TIP (TIPS ETF / real yield proxy)", tip,
        correlation="STRONGLY NEGATIVE with Gold (real yields)",
        rule_up="TIP rising = real yields falling = BULLISH for Gold",
        rule_down="TIP falling = real yields rising = BEARISH for Gold"
    ))
    sections.append("")

    sections.append(fmt_instrument(
        "SPY (S&P 500 ETF / risk sentiment)", spy,
        correlation="CONTEXT-DEPENDENT with Gold",
        rule_up="SPY rising = risk-on = slight Gold headwind",
        rule_down="SPY falling = risk-off = Gold safe haven demand"
    ))
    sections.append("")

    # Обща оценка на macro bias
    bullish, bearish = 0, 0
    if isinstance(tlt,   dict): bullish += 1 if tlt.get("change_pct",0)   > 0 else 0; bearish += 1 if tlt.get("change_pct",0)   < 0 else 0
    if isinstance(us10y, dict): bullish += 1 if us10y.get("change_pct",0) < 0 else 0; bearish += 1 if us10y.get("change_pct",0) > 0 else 0
    if isinstance(tip,   dict): bullish += 1 if tip.get("change_pct",0)   > 0 else 0; bearish += 1 if tip.get("change_pct",0)   < 0 else 0

    if bullish > bearish:
        bias_str = f"MACRO BULLISH for Gold ({bullish}/3 indicators supportive)"
    elif bearish > bullish:
        bias_str = f"MACRO BEARISH for Gold ({bearish}/3 indicators negative)"
    else:
        bias_str = "MACRO NEUTRAL / MIXED for Gold"

    sections.append(f"  OVERALL MACRO BIAS: {bias_str}")
    sections.append("  (Use for directional bias only, not for entry timing)")

    return "\n".join(sections)

def build_indicators_section(ind: dict, bid: float) -> str:
    if not ind:
        return "  Indicators: not available"

    rsi_m5   = ind.get("rsi_m5")
    rsi_h1   = ind.get("rsi_h1")
    atr_m5   = ind.get("atr_m5")
    atr_h1   = ind.get("atr_h1")
    ema20_m5 = ind.get("ema20_m5")
    ema20_h1 = ind.get("ema20_h1")
    ema50_h1 = ind.get("ema50_h1")
    bb_upper = ind.get("bb_upper")
    bb_mid   = ind.get("bb_middle")
    bb_lower = ind.get("bb_lower")
    bb_width = ind.get("bb_width_pct")
    vwap     = ind.get("vwap")

    def rsi_tag(v):
        if v is None: return "N/A"
        if v >= 75: return f"{v:.1f} STRONGLY OVERBOUGHT"
        if v >= 70: return f"{v:.1f} OVERBOUGHT"
        if v <= 25: return f"{v:.1f} STRONGLY OVERSOLD"
        if v <= 30: return f"{v:.1f} OVERSOLD"
        if v >= 60: return f"{v:.1f} bullish momentum"
        if v <= 40: return f"{v:.1f} bearish momentum"
        return f"{v:.1f} neutral"

    lines = [f"RSI: M5={rsi_tag(rsi_m5)} | H1={rsi_tag(rsi_h1)}"]

    if ema20_h1 and ema50_h1:
        if bid > ema20_h1 > ema50_h1:
            lines.append(f"EMA H1: STRONG BULLISH — Price({bid:.2f}) > EMA20({ema20_h1:.2f}) > EMA50({ema50_h1:.2f})")
        elif bid < ema20_h1 < ema50_h1:
            lines.append(f"EMA H1: STRONG BEARISH — Price({bid:.2f}) < EMA20({ema20_h1:.2f}) < EMA50({ema50_h1:.2f})")
        elif ema20_h1 > ema50_h1:
            lines.append(f"EMA H1: bullish structure, EMA20({ema20_h1:.2f}) > EMA50({ema50_h1:.2f}), price at {bid:.2f}")
        else:
            lines.append(f"EMA H1: bearish structure, EMA20({ema20_h1:.2f}) < EMA50({ema50_h1:.2f}), price at {bid:.2f}")

    if ema20_m5:
        lines.append(f"EMA M5: price {'above' if bid > ema20_m5 else 'below'} EMA20({ema20_m5:.2f})")

    if bb_upper and bb_lower and bb_mid:
        lines.append(f"BB M5: Upper={bb_upper:.2f} Mid={bb_mid:.2f} Lower={bb_lower:.2f} Width={bb_width:.2f}%")
        if bid >= bb_upper * 0.999:
            lines.append("  BB: Price at UPPER band — overbought zone, avoid BUY")
        elif bid <= bb_lower * 1.001:
            lines.append("  BB: Price at LOWER band — oversold zone, avoid SELL")
        elif bb_width and bb_width < 0.5:
            lines.append("  BB SQUEEZE (<0.5%) — breakout imminent, wait for direction candle")
        elif bb_width and bb_width > 2.0:
            lines.append("  BB expanding — strong trend in progress")

    if vwap and vwap > 0:
        diff = bid - vwap
        lines.append(f"VWAP: {vwap:.2f} — price {'ABOVE' if diff > 0 else 'BELOW'} by {abs(diff):.2f} pts")
        lines.append(f"  {'Institutional BULLISH bias (buyers in control)' if diff > 0 else 'Institutional BEARISH bias (sellers in control)'}")

    if atr_m5: lines.append(f"ATR M5: {atr_m5:.2f} pts | ATR H1: {atr_h1:.2f} pts")

    lines.append("Rules: RSI H1>=70=no BUY | RSI H1<=30=no SELL | VWAP confirms direction | BB squeeze=wait")

    return "\n".join(lines)


def build_silver_section(data: dict) -> str:
    silver_bid = data.get("silver_bid", 0)
    silver_m5  = data.get("silver_bars_m5", [])
    if not silver_bid:
        return "  Silver: not available"
    lines = [f"Silver (XAGUSD): {silver_bid:.4f} — 85-90% positive correlation with Gold"]
    if len(silver_m5) >= 5:
        closes = [c.get("close", 0) for c in silver_m5[:5]]
        trend  = "BULLISH" if closes[0] > closes[-1] else "BEARISH"
        lines.append(f"  Silver M5 trend: {trend}")
        lines.append("  Divergence (Gold BUY but Silver BEARISH) = reduce confidence 15%")
    return "\n".join(lines)



def calc_adx(candles: list, period: int = 10) -> float:
    """
    Изчислява ADX от OHLC свещи.
    ADX > 25 = trending  |  ADX < 20 = ranging
    Period=10 за да работи с 10+ свещи от EA.
    Свещите идват newest-first от EA, reversed() ги наредва стара->нова.
    """
    if not candles or len(candles) < period + 2:
        return 0.0
    arr = list(reversed(candles))  # newest-first -> oldest-first за изчисление
    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(arr)):
        h  = arr[i].get("high", 0)
        lo = arr[i].get("low", 0)
        ph = arr[i-1].get("high", 0)
        pl = arr[i-1].get("low", 0)
        pc = arr[i-1].get("close", 0)
        tr  = max(h - lo, abs(h - pc), abs(lo - pc))
        pdm = max(h - ph, 0) if (h - ph) > (pl - lo) else 0
        ndm = max(pl - lo, 0) if (pl - lo) > (h - ph) else 0
        tr_list.append(tr)
        pdm_list.append(pdm)
        ndm_list.append(ndm)
    if len(tr_list) < period:
        return 0.0
    # Wilder smoothing
    def smooth(lst, p):
        s = sum(lst[:p])
        result = [s]
        for v in lst[p:]:
            s = s - s/p + v
            result.append(s)
        return result
    atr_s  = smooth(tr_list,  period)
    pdm_s  = smooth(pdm_list, period)
    ndm_s  = smooth(ndm_list, period)
    dx_list = []
    for a, p, n in zip(atr_s, pdm_s, ndm_s):
        if a == 0: continue
        pdi = 100 * p / a
        ndi = 100 * n / a
        denom = pdi + ndi
        if denom == 0: continue
        dx_list.append(100 * abs(pdi - ndi) / denom)
    if len(dx_list) < period:
        return 0.0
    adx = sum(dx_list[-period:]) / period
    return round(adx, 1)


def session_confidence_threshold(session: str, utc_hour: int) -> tuple:
    """
    Връща (min_confidence, note) по сесия.
    London prime:    78%
    London-NY:       80%
    NY only:         83%
    Tokyo:           88%
    Off-hours:       90%
    """
    if session == "LONDON_NY_OVERLAP":
        return 80, "London-NY overlap: 80% threshold"
    if session == "LONDON":
        if 7 <= utc_hour < 10:
            return 78, "London prime window (07-10 UTC): 78% threshold"
        return 80, "London late (10-16 UTC): 80% threshold"
    if session == "NEW_YORK":
        return 83, "NY only: 83% threshold"
    if session == "TOKYO":
        return 88, "Tokyo: 88% threshold (low volume)"
    return 90, "Off-hours: 90% threshold"

def build_prompt(data: dict) -> str:
    bid        = data.get("bid", 0)
    ask        = data.get("ask", 0)
    spread     = round(ask - bid, 2)
    balance    = data.get("balance", 0)
    equity     = data.get("equity", 0)
    margin_lvl = data.get("margin_level", 0)
    daily_pnl  = data.get("daily_pnl", 0)
    has_pos    = data.get("has_position", False)
    pos_type   = data.get("position_type", "NONE")
    pos_profit = data.get("position_profit", 0)
    pos_open   = data.get("position_open_price", 0)
    session    = data.get("session", "OFF_SESSION")

    m1 = data.get("bars_m1", [])
    m5 = data.get("bars_m5", [])
    h1 = data.get("bars_h1", [])
    h4 = data.get("bars_h4", [])

    dxy_bid = data.get("dxy_bid", 0)
    dxy_m5  = data.get("dxy_bars_m5", [])
    indicators = data.get("indicators", {})

    utc_now      = datetime.now(timezone.utc)
    session_ctx  = get_session_context(utc_now)
    ind_section  = build_indicators_section(indicators, bid)
    silv_section = build_silver_section(data)
    sr_section   = calc_sr_levels(h4, h1, bid)
    macro_data   = get_macro_data()
    macro_section = build_macro_section(macro_data)

    # ADX от MT5 индикатор (iADXWilder) — праща се от EA в indicators
    adx_h1 = indicators.get("adx_h1", 0.0)
    adx_m5 = indicators.get("adx_m5", 0.0)
    # Fallback към изчисление от свещи ако EA не е пратило
    if adx_h1 == 0.0:
        adx_h1 = calc_adx(h1, 10)
    if adx_m5 == 0.0:
        adx_m5 = calc_adx(m5, 10)
    if adx_h1 >= 25:
        adx_str = f"ADX(14) H1: {adx_h1} — TRENDING market (>25) — entries allowed"
        adx_ok  = True
    elif adx_h1 >= 20:
        adx_str = f"ADX(14) H1: {adx_h1} — WEAK TREND (20-25) — reduce size, high bar"
        adx_ok  = True
    else:
        adx_str = f"ADX(14) H1: {adx_h1} — RANGING market (<20) — AVOID new entries"
        adx_ok  = False
    if adx_m5 > 0:
        adx_str += f" | ADX M5: {adx_m5}"


    # Position block
    pos_block = ""
    if has_pos and pos_open > 0:
        pnl_pct = (pos_profit / balance * 100) if balance > 0 else 0
        dist    = abs(bid - pos_open)
        against = (pos_type == "BUY" and bid < pos_open) or (pos_type == "SELL" and bid > pos_open)
        pos_block = f"""
OPEN POSITION:
  Type: {pos_type} | Entry: {pos_open:.2f} | Current: {bid:.2f}
  Distance: {dist:.2f} pts ({"LOSING" if against else "WINNING"})
  P&L: {pos_profit:.2f} USD ({pnl_pct:.2f}%)
  EA has BreakEven+TrailingStop active. CLOSE only on H1+H4 reversal.
"""

    # DXY
    if dxy_bid > 0:
        dxy_last = dxy_m5[0].get("close", 0) if dxy_m5 else 0
        dxy_prev = dxy_m5[4].get("close", 0) if len(dxy_m5) > 4 else 0
        dxy_dir  = "RISING" if dxy_last > dxy_prev else "FALLING"
        dxy_block = f"DXY: {dxy_bid:.5f} ({dxy_dir}) — NEGATIVE ~85% corr | RISING=SELL pressure | FALLING=BUY pressure"
    else:
        dxy_block = "DXY: not available"

    m5_summary = ""
    if len(m5) >= 5:
        closes = [cc.get("close", 0) for cc in m5[:5]]
        trend  = "BULLISH" if closes[0] > closes[-1] else "BEARISH"
        move   = abs(closes[0] - closes[-1])
        m5_summary = f"M5 last 5: {trend} ({move:.1f} pts)"

    # VWAP distance check for pullback
    vwap = indicators.get("vwap", 0)
    pullback_note = ""
    if vwap and vwap > 0:
        dist_from_vwap = abs(bid - vwap)
        if dist_from_vwap > 20 and not has_pos:
            pullback_note = f"PULLBACK ALERT: Price is {dist_from_vwap:.1f} pts from VWAP ({vwap:.2f}). Wait for retest of VWAP or EMA20 before entry."

    return f"""You are a professional XAU/USD institutional trader, 20+ years experience.
You follow a strict hierarchical decision process. Never skip steps.
Patient, selective — wait for the market to come to you.
Let winning trades run. Never cap a winner. Never chase extended moves.

{session_ctx}

=== MARKET DATA ===
Bid: {bid:.2f} | Ask: {ask:.2f} | Spread: {spread:.2f} pts | {m5_summary}
Balance: {balance:.2f} | Equity: {equity:.2f} | Daily P&L: {daily_pnl:+.2f}
Active session: {session}
{pos_block}
=== STEP 1 — ADX TREND FILTER (MANDATORY GATE) ===
{adx_str}

{"RESULT: ADX < 20 = RANGING MARKET. OUTPUT HOLD IMMEDIATELY. Skip all further analysis." if not adx_ok else "RESULT: Trending market confirmed. Proceed to Step 2."}

=== STEP 2 — H4 MACRO TREND (absolute direction filter) ===
{format_candles(h4, "H4")}

=== STEP 3 — H1 INTERMEDIATE TREND ===
{format_candles(h1, "H1")}

=== STEP 4 — INDICATORS ===
{ind_section}
{adx_str}

=== STEP 5 — ENTRY ZONE CHECK ===
{sr_section}
{pullback_note if pullback_note else "Price within acceptable range of VWAP/EMA — entry zone OK."}

=== STEP 6 — CORRELATIONS ===
{dxy_block}
{silv_section}

=== STEP 7 — MACRO BIAS ===
{macro_section}

=== STEP 8 — M5 ENTRY SETUP ===
{format_candles(m5, "M5 PRIMARY")}
{format_candles(m1, "M1 TIMING (last 5 candles only)")}

=== DECISION FRAMEWORK ===

HARD STOPS — output HOLD immediately if ANY of these are true:
  1. ADX H1 < 20 (ranging market)
  2. Spread > 1.5 pts
  3. First 15 min after session open
  4. Pre-market (< 30 min before London or NY open)
  5. RSI H1 >= 70 and considering BUY
  6. RSI H1 <= 30 and considering SELL
  7. BB squeeze (width < 0.5%) — wait for breakout candle
  8. Price extended > 20 pts from VWAP without pullback

CONFLUENCE (need 4+/6 to enter):
  [ ] H4 + H1 trend aligned (same direction)
  [ ] ADX > 25 (strong trend)
  [ ] VWAP supports direction (price above VWAP for BUY, below for SELL)
  [ ] RSI neutral or in direction (not at extreme)
  [ ] DXY confirms (if available)
  [ ] Silver confirms (if available)

ENTRY QUALITY:
  - PREFER entries near VWAP, EMA20, or key S/R level (pullback entries)
  - AVOID entries when price is extended > 20 pts from VWAP
  - Best entry: price pulls back to VWAP or EMA20 in direction of H4 trend

SL/TP:
  - SL: behind nearest swing H/L OR 1.5x ATR(M5) — WHICHEVER IS WIDER — min 15 pts
  - TP: at next S/R level — NO cap on distance — min 2x SL
  - Tokyo only: max SL 25 pts

=== RESPOND WITH VALID JSON ONLY — NO OTHER TEXT ===
{{
  "action": "BUY",
  "confidence": 85,
  "sl_price": 0.0,
  "tp_price": 0.0,
  "confluence": "H4+H1+ADX+VWAP = 4/6",
  "reason": "single concise line without quotes or special chars"
}}
"""


def ask_claude(prompt: str) -> dict:
    client = get_client()

    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sep = "=" * 80
    full_log.debug(f"\n{sep}\n[REQUEST] {ts}\n{sep}\n{prompt}\n")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system="You are a JSON-only trading signal generator. NEVER write explanatory text, analysis, or reasoning outside the JSON object. Your ENTIRE response must be a single valid JSON object starting with { and ending with }. No markdown, no backticks, no preamble, no postamble.",
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()

    usage = response.usage
    full_log.debug(
        f"\n{sep}\n[RESPONSE] {ts}\n"
        f"Input tokens: {usage.input_tokens} | Output tokens: {usage.output_tokens}\n"
        f"{sep}\n{raw}\n{sep}\n"
    )

    log.info(f"Claude raw: {raw[:300]}")

    # Robust JSON extraction — Claude понякога мисли на глас преди JSON-а
    clean = None

    # 1. Търсим JSON блок в ```json ... ```
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                clean = part
                break

    # 2. Търсим { ... } директно в текста (Claude може да пише текст преди JSON)
    if not clean:
        start = raw.find("{")
        end   = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            clean = raw[start:end+1]

    # 3. Fallback — целият raw
    if not clean:
        clean = raw

    try:
        result = json.loads(clean)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e} | raw: {raw[:200]}")
        return {"action": "HOLD", "confidence": 0,
                "sl_price": 0.0, "tp_price": 0.0,
                "reason": f"JSON parse error: {e}"}

    if "decision" in result and "action" not in result:
        result["action"] = result["decision"]

    result["sl_pips"] = result.get("sl_price", 0.0)
    result["tp_pips"] = result.get("tp_price", 0.0)

    if "reason" not in result or not result["reason"]:
        result["reason"] = result.get("confluence", "No reason")

    if result.get("action") in ("BUY", "SELL"):
        if not result.get("sl_price") or not result.get("tp_price"):
            log.warning("BUY/SELL missing SL/TP - converting to HOLD")
            result["action"] = "HOLD"
            result["confidence"] = 0

    return result


def log_decision(data: dict, decision: dict):
    record = {
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "session":    data.get("session"),
        "bid":        data.get("bid"),
        "action":     decision.get("action"),
        "confidence": decision.get("confidence"),
        "sl_price":   decision.get("sl_price"),
        "tp_price":   decision.get("tp_price"),
        "reason":     decision.get("reason", "")[:300],
    }
    try:
        with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"Could not write decision log: {e}")


@app.route("/health", methods=["GET"])
def health():
    macro = get_macro_data()
    return jsonify({
        "status":      "ok",
        "bridge":      "XAUUSD AI Bridge v4.1 - M5 + Macro",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "api_key_set": bool(ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")),
        "macro_data":  macro,
    })


@app.route("/analyze", methods=["POST"])
def analyze():
    t0 = time.time()
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Empty body"}), 400

        ind = data.get('indicators', {})
        log.info(f"[DEBUG indicators] {ind}")
        log.info(
            f"[req] session={data.get('session')} bid={data.get('bid')} "
            f"m5={len(data.get('bars_m5',[]))} dxy={data.get('dxy_bid',0):.3f} "
            f"silver={data.get('silver_bid',0):.3f} ind={'YES' if ind else 'NO'} "
            f"adx_h1={ind.get('adx_h1','MISSING')} adx_m5={ind.get('adx_m5','MISSING')}"
        )

        if not (ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")):
            return jsonify({"action": "HOLD", "confidence": 0,
                            "sl_price": 0.0, "tp_price": 0.0,
                            "sl_pips": 0.0, "tp_pips": 0.0,
                            "reason": "ERROR: ANTHROPIC_API_KEY not set"})

        # ── PRE-FLIGHT HARD STOPS — проверяваме ПРЕДИ да пращаме към Claude ──
        ind     = data.get('indicators', {})
        bid     = data.get('bid', 0)
        ask     = data.get('ask', 0)
        spread  = round(ask - bid, 2) if ask and bid else 0
        session = data.get('session', 'OFF_SESSION')
        adx_h1  = ind.get('adx_h1', 0.0)
        rsi_h1  = ind.get('rsi_h1', 50.0)
        bb_w    = ind.get('bb_width_pct', 1.0)
        vwap    = ind.get('vwap', 0.0)
        has_pos = data.get('has_position', False)

        hard_stop_reason = None

        # 1. ADX < 20 = ranging market
        if adx_h1 > 0 and adx_h1 < 20:
            hard_stop_reason = f"ADX H1={adx_h1:.1f} < 20 (ranging market)"

        # 2. Spread > 1.5 pts
        elif spread > 1.5:
            hard_stop_reason = f"Spread={spread:.2f} > 1.5 pts"

        # 3. BB squeeze < 0.5%
        elif bb_w > 0 and bb_w < 0.5:
            hard_stop_reason = f"BB squeeze={bb_w:.2f}% < 0.5% (wait for breakout)"

        # 4. Само за нови позиции: price extended > 20 pts от VWAP
        elif not has_pos and vwap > 0 and abs(bid - vwap) > 20:
            hard_stop_reason = f"Price {abs(bid-vwap):.1f} pts from VWAP (extended, wait pullback)"

        if hard_stop_reason:
            log.info(f"[hard_stop] {hard_stop_reason} — skipping Claude API")
            decision = {
                "action":     "HOLD",
                "confidence": 0,
                "sl_price":   0.0,
                "tp_price":   0.0,
                "sl_pips":    0.0,
                "tp_pips":    0.0,
                "reason":     f"Hard stop: {hard_stop_reason}"
            }
        else:
            prompt   = build_prompt(data)
            decision = ask_claude(prompt)

        if decision.get("action") not in {"BUY", "SELL", "HOLD", "CLOSE"}:
            decision["action"] = "HOLD"
            decision["confidence"] = 0

        # Dynamic session confidence filter
        if decision.get("action") in ("BUY", "SELL"):
            req_conf, _ = session_confidence_threshold(
                data.get("session", "OFF_SESSION"),
                datetime.now(timezone.utc).hour
            )
            actual_conf = decision.get("confidence", 0)
            if actual_conf < req_conf:
                log.info(
                    f"[session_filter] {decision['action']} blocked: "
                    f"conf={actual_conf}% < required={req_conf}% "
                    f"for session={data.get('session')}"
                )
                decision["action"] = "HOLD"
                decision["confidence"] = actual_conf
                decision["reason"] = (
                    f"Session filter: {actual_conf}% < {req_conf}% required "
                    f"for {data.get('session', 'UNKNOWN')} session"
                )

        log_decision(data, decision)

        elapsed = round((time.time() - t0) * 1000)
        log.info(
            f"[res] {decision['action']} conf={decision.get('confidence')}% "
            f"SL={decision.get('sl_price')} TP={decision.get('tp_price')} ({elapsed}ms)"
        )
        log.info(f"[reason] {decision.get('reason','')[:200]}")

        return jsonify(decision)

    except anthropic.AuthenticationError:
        return jsonify({"action": "HOLD", "confidence": 0,
                        "sl_price": 0.0, "tp_price": 0.0,
                        "reason": "ERROR: Invalid API key"})
    except anthropic.RateLimitError:
        return jsonify({"action": "HOLD", "confidence": 0,
                        "sl_price": 0.0, "tp_price": 0.0,
                        "reason": "Rate limit"})
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return jsonify({"action": "HOLD", "confidence": 0,
                        "sl_price": 0.0, "tp_price": 0.0,
                        "reason": f"Bridge error: {str(e)[:100]}"})


@app.route("/history", methods=["GET"])
def history():
    n = int(request.args.get("n", 20))
    try:
        with open(DECISIONS_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        records = [json.loads(l) for l in lines[-n:] if l.strip()]
        return jsonify({"count": len(records), "decisions": records})
    except FileNotFoundError:
        return jsonify({"count": 0, "decisions": []})


if __name__ == "__main__":
    print("=" * 60)
    print("  XAUUSD AI Bridge v4.1 — M5 + Macro Edition")
    print("  + TLT (20Y Treasury Bond ETF)")
    print("  + US10Y yield (^TNX)")
    print("  + TIP (TIPS ETF / real yield proxy)")
    print("  + SPY (S&P 500 risk sentiment)")
    print("  + Silver, Bollinger, VWAP, RSI, ATR, EMA")
    print("  + DXY correlation")
    print("  Source: Yahoo Finance (cached 5 min)")
    print("=" * 60)
    key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("  WARNING: ANTHROPIC_API_KEY is not set!")
    else:
        print(f"  API Key: {key[:12]}...{key[-4:]}")

    # Test macro fetch при стартиране
    print("\n  Testing macro data fetch...")
    macro = get_macro_data()
    for k, v in macro.items():
        cur = v.get("current", 0) if isinstance(v, dict) else 0
        chg = v.get("change_pct", 0) if isinstance(v, dict) else 0
        print(f"  {k}: {cur:.4f} ({chg:+.2f}% today)" if cur > 0 else f"  {k}: N/A")

    print(f"\n  Listening: http://127.0.0.1:{PORT}")
    print("=" * 60)
    app.run(host="127.0.0.1", port=PORT, debug=False)


