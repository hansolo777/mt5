"""
XAUUSD AI Decisions Analyzer v2.0
"""

import json
from datetime import datetime

DECISIONS_FILE = r"C:\XAUUSD_AI_Bridge\decisions.jsonl"
REPORT_FILE    = r"C:\XAUUSD_AI_Bridge\analysis_report.html"


def load_decisions():
    with open(DECISIONS_FILE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def analyze(decisions):
    results = []
    for i, d in enumerate(decisions):
        action     = d.get("action", "HOLD")
        bid        = d.get("bid", 0)
        sl         = d.get("sl_price", 0)
        tp         = d.get("tp_price", 0)
        confidence = d.get("confidence", 0)
        ts         = d.get("timestamp", "")[:19].replace("T", " ")
        session    = d.get("session", "")
        reason     = d.get("reason", "")[:120]

        future_bids = []
        for j in range(i + 1, min(i + 35, len(decisions))):
            b = decisions[j].get("bid", 0)
            if b > 0:
                future_bids.append(b)

        result  = "—"
        correct = None
        pnl_5   = None
        pnl_15  = None
        pnl_30  = None
        max_fav = 0.0
        max_adv = 0.0

        if action in ("BUY", "SELL") and bid > 0 and len(future_bids) >= 3:
            def get_pnl(n):
                if len(future_bids) >= n:
                    p = future_bids[n - 1]
                    return (p - bid) if action == "BUY" else (bid - p)
                return None

            pnl_5  = get_pnl(5)
            pnl_15 = get_pnl(15)
            pnl_30 = get_pnl(30)

            for fp in future_bids:
                move = (fp - bid) if action == "BUY" else (bid - fp)
                if move > 0: max_fav = max(max_fav, move)
                else:        max_adv = max(max_adv, abs(move))

            checks     = [p for p in [pnl_5, pnl_15, pnl_30] if p is not None]
            wins_count = sum(1 for p in checks if p > 0)

            if len(checks) == 0:  result, correct = "NO DATA", None
            elif wins_count >= 2: result, correct = "WIN",     True
            elif wins_count == 1: result, correct = "MIXED",   None
            else:                 result, correct = "LOSS",    False

        elif action == "HOLD":  result = "HOLD"
        elif action == "CLOSE": result = "CLOSE"

        results.append({
            "ts": ts, "session": session, "action": action,
            "conf": confidence, "bid": bid, "sl": sl, "tp": tp,
            "pnl_5": pnl_5, "pnl_15": pnl_15, "pnl_30": pnl_30,
            "max_fav": max_fav, "max_adv": max_adv,
            "result": result, "correct": correct, "reason": reason
        })
    return results


def conf_analysis(results, threshold):
    """Анализ за сигнали с confidence >= threshold."""
    grp  = [r for r in results if r["action"] in ("BUY","SELL")
            and r["conf"] >= threshold and r["correct"] is not None]
    wins = [r for r in grp if r["correct"] is True]
    loss = [r for r in grp if r["correct"] is False]
    pnl  = sum(r["pnl_30"] for r in grp if r.get("pnl_30") is not None)
    wr   = len(wins) / len(grp) * 100 if grp else 0
    return {
        "label":    f">= {threshold}%",
        "total":    len(grp),
        "wins":     len(wins),
        "losses":   len(loss),
        "win_rate": wr,
        "pnl_sum":  pnl,
    }


def fmt(val, decimals=2):
    if val is None or val == 0: return "—"
    return f"{val:.{decimals}f}"


def fmt_pnl(val):
    if val is None: return "—"
    sign  = "+" if val > 0 else ""
    color = "win" if val > 0 else "loss" if val < 0 else ""
    return f'<span class="{color}">{sign}{val:.1f}</span>'


def print_summary(results):
    trades   = [r for r in results if r["action"] in ("BUY","SELL") and r["correct"] is not None]
    wins     = [r for r in trades if r["correct"] is True]
    losses   = [r for r in trades if r["correct"] is False]
    mixed    = [r for r in results if r["result"] == "MIXED"]
    total    = len(trades)
    win_rate = len(wins) / total * 100 if total > 0 else 0

    def avg_pnl(key):
        vals = [r[key] for r in results if r["action"] in ("BUY","SELL") and r[key] is not None]
        return sum(vals) / len(vals) if vals else 0

    print("=" * 70)
    print("  XAUUSD AI DECISIONS ANALYSIS v2.0")
    print("  Метрика: посока на цената (5/15/30 мин след сигнал)")
    print("=" * 70)
    print(f"  Общо решения:          {len(results)}")
    print(f"  BUY/SELL сигнали:      {len([r for r in results if r['action'] in ('BUY','SELL')])}")
    print(f"  Оценени (>=3 данни):   {total}")
    print(f"  WIN (2/3 посоки OK):   {len(wins)}  ({win_rate:.1f}%)")
    print(f"  LOSS (2/3 грешни):     {len(losses)}")
    print(f"  MIXED (1/3):           {len(mixed)}")
    print(f"  Средно P&L @ 5мин:     {avg_pnl('pnl_5'):+.2f} pts")
    print(f"  Средно P&L @ 15мин:    {avg_pnl('pnl_15'):+.2f} pts")
    print(f"  Средно P&L @ 30мин:    {avg_pnl('pnl_30'):+.2f} pts")
    print("=" * 70)
    print()
    print("  ВАЖНО: Тези числа показват посоката на пазара след сигнала.")
    print("  Те НЕ отразяват реалния P&L — AI може да е затворил")
    print("  позицията по-рано или по-късно от контролните точки.")
    print("=" * 70)

    print()
    print("=" * 70)
    print("  АНАЛИЗ ПО CONFIDENCE НИВО")
    print("=" * 70)
    for threshold in [82, 85]:
        ca = conf_analysis(results, threshold)
        print(f"\n  Confidence >= {threshold}%:")
        print(f"    Сигнали:              {ca['total']}")
        print(f"    WIN:                  {ca['wins']}  ({ca['win_rate']:.1f}%)")
        print(f"    LOSS:                 {ca['losses']}")
        print(f"    P&L сума @ 30мин:     {ca['pnl_sum']:+.1f} pts")
    print("=" * 70)


def generate_html(results):
    trades   = [r for r in results if r["action"] in ("BUY","SELL") and r["correct"] is not None]
    wins     = [r for r in trades if r["correct"] is True]
    losses   = [r for r in trades if r["correct"] is False]
    mixed    = [r for r in results if r["result"] == "MIXED"]
    total    = len(trades)
    win_rate = len(wins) / total * 100 if total > 0 else 0

    def avg_pnl(key):
        vals = [r[key] for r in results if r["action"] in ("BUY","SELL") and r[key] is not None]
        return sum(vals) / len(vals) if vals else 0

    avg5  = avg_pnl("pnl_5")
    avg15 = avg_pnl("pnl_15")
    avg30 = avg_pnl("pnl_30")

    # Win rate по сесия
    sessions = {}
    for r in trades:
        s = r["session"]
        if s not in sessions:
            sessions[s] = {"total": 0, "wins": 0}
        sessions[s]["total"] += 1
        if r["correct"]:
            sessions[s]["wins"] += 1

    session_rows = ""
    for s, v in sorted(sessions.items()):
        wr    = v["wins"] / v["total"] * 100 if v["total"] > 0 else 0
        color = "#2ecc71" if wr >= 50 else "#e74c3c"
        session_rows += (
            f"<tr><td>{s}</td><td>{v['total']}</td>"
            f"<td>{v['wins']}</td>"
            f"<td style='color:{color}'><b>{wr:.1f}%</b></td></tr>"
        )

    # Confidence rows
    conf_rows = ""
    for threshold in [82, 85]:
        ca  = conf_analysis(results, threshold)
        wrc = "#2ecc71" if ca["win_rate"] >= 50 else "#e74c3c"
        sc  = "#2ecc71" if ca["pnl_sum"] >= 0 else "#e74c3c"
        conf_rows += (
            f"<tr>"
            f"<td><b>{ca['label']}</b></td>"
            f"<td>{ca['total']}</td>"
            f"<td style='color:#2ecc71'><b>{ca['wins']}</b></td>"
            f"<td style='color:#e74c3c'>{ca['losses']}</td>"
            f"<td style='color:{wrc}'><b>{ca['win_rate']:.1f}%</b></td>"
            f"<td style='color:{sc}'><b>{ca['pnl_sum']:+.1f} pts</b></td>"
            f"</tr>"
        )

    # Всички редове
    rows = ""
    for r in reversed(results):
        action_color = {
            "BUY": "#27ae60", "SELL": "#e74c3c",
            "HOLD": "#95a5a6", "CLOSE": "#f39c12"
        }.get(r["action"], "#fff")

        result_bg = ""
        if   r["result"] == "WIN":   result_bg = "background:#1a3a1a"
        elif r["result"] == "LOSS":  result_bg = "background:#3a1a1a"
        elif r["result"] == "MIXED": result_bg = "background:#2a2a1a"

        result_color = {
            "WIN": "#2ecc71", "LOSS": "#e74c3c",
            "MIXED": "#f0b429", "HOLD": "#95a5a6", "CLOSE": "#f39c12"
        }.get(r["result"], "#888")

        conf_color = "#2ecc71" if r["conf"] >= 85 else "#f0b429" if r["conf"] >= 82 else "#e0e0e0"

        rows += (
            f"<tr class='datarow' style='{result_bg}'>"
            f"<td>{r['ts']}</td>"
            f"<td>{r['session']}</td>"
            f"<td style='color:{action_color};font-weight:bold'>{r['action']}</td>"
            f"<td style='color:{conf_color}'>{r['conf']}%</td>"
            f"<td>{r['bid']:.2f}</td>"
            f"<td>{fmt(r['sl'])}</td>"
            f"<td>{fmt(r['tp'])}</td>"
            f"<td>{fmt_pnl(r['pnl_5'])}</td>"
            f"<td>{fmt_pnl(r['pnl_15'])}</td>"
            f"<td>{fmt_pnl(r['pnl_30'])}</td>"
            f"<td style='color:#2ecc71'>{fmt(r['max_fav'], 1)}</td>"
            f"<td style='color:#e74c3c'>{fmt(r['max_adv'], 1)}</td>"
            f"<td style='color:{result_color};font-weight:bold'>{r['result']}</td>"
            f"<td style='font-size:11px;color:#aaa;cursor:help' title='{r["reason"]}'>{r['reason'][:70]}</td>"
            f"</tr>"
        )

    wr_color  = "green" if win_rate >= 50 else "red"
    a5_color  = "green" if avg5  >= 0 else "red"
    a15_color = "green" if avg15 >= 0 else "red"
    a30_color = "green" if avg30 >= 0 else "red"
    total_rec = len(results)

    html = f"""<!DOCTYPE html>
<html lang="bg">
<head>
<meta charset="UTF-8">
<title>XAUUSD AI Analysis v2.0</title>
<style>
  body{{background:#0d0d0d;color:#e0e0e0;font-family:'Segoe UI',monospace;margin:20px}}
  h1{{color:#f0b429}}h2{{color:#aaa;border-bottom:1px solid #333;padding-bottom:6px}}
  .note{{background:#1a1a2a;border-left:4px solid #f0b429;padding:12px 16px;margin:16px 0;border-radius:4px;color:#bbb;font-size:13px}}
  .stats{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
  .card{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:14px 20px;min-width:120px;text-align:center}}
  .card .val{{font-size:26px;font-weight:bold}}.card .lbl{{color:#888;font-size:11px;margin-top:4px}}
  .green{{color:#2ecc71}}.red{{color:#e74c3c}}.yellow{{color:#f0b429}}
  .win{{color:#2ecc71}}.loss{{color:#e74c3c}}
  table{{border-collapse:collapse;width:100%;font-size:12px}}
  th{{background:#1a1a1a;color:#f0b429;padding:7px;text-align:left;position:sticky;top:0;white-space:nowrap;z-index:1}}
  td{{padding:5px 7px;border-bottom:1px solid #1a1a1a;white-space:nowrap}}
  tr:hover{{background:#1a1a2a !important}}
  .sm{{width:auto;margin-bottom:24px}}.sm td,.sm th{{padding:7px 16px}}
  .datarow{{display:none}}.datarow.visible{{display:table-row}}
  .pg{{display:flex;align-items:center;gap:8px;margin:16px 0;flex-wrap:wrap}}
  .pg button{{background:#1a1a1a;color:#e0e0e0;border:1px solid #444;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:13px}}
  .pg button:hover{{background:#2a2a2a}}.pg button.active{{background:#f0b429;color:#000;font-weight:bold;border-color:#f0b429}}
  .pg button:disabled{{opacity:.3;cursor:default}}.pi{{color:#888;font-size:13px}}
  .pps{{background:#1a1a1a;color:#e0e0e0;border:1px solid #444;padding:5px 8px;border-radius:4px;font-size:13px;cursor:pointer}}
</style>
</head>
<body>
<h1>XAUUSD AI Decisions Analysis v2.0</h1>
<p style="color:#888">Генериран: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Записи: {total_rec}</p>
<div class="note">
  <b>Метрика: Посока на цената 5/15/30 мин след сигнала</b><br>
  WIN = цената в правилна посока на поне 2 от 3 точки.<br>
  <b>Важно:</b> Тези числа НЕ са реалният P&L.
</div>
<div class="stats">
  <div class="card"><div class="val yellow">{total}</div><div class="lbl">Оценени</div></div>
  <div class="card"><div class="val green">{len(wins)}</div><div class="lbl">WIN</div></div>
  <div class="card"><div class="val red">{len(losses)}</div><div class="lbl">LOSS</div></div>
  <div class="card"><div class="val yellow">{len(mixed)}</div><div class="lbl">MIXED</div></div>
  <div class="card"><div class="val {wr_color}">{win_rate:.1f}%</div><div class="lbl">Win Rate</div></div>
  <div class="card"><div class="val {a5_color}">{avg5:+.1f}</div><div class="lbl">@5мин</div></div>
  <div class="card"><div class="val {a15_color}">{avg15:+.1f}</div><div class="lbl">@15мин</div></div>
  <div class="card"><div class="val {a30_color}">{avg30:+.1f}</div><div class="lbl">@30мин</div></div>
</div>
<h2>Win Rate по сесия</h2>
<table class="sm">
  <tr><th>Сесия</th><th>Сигнали</th><th>WIN</th><th>Win Rate</th></tr>
  {session_rows}
</table>
<h2>Анализ по Confidence ниво</h2>
<table class="sm">
  <tr><th>Confidence</th><th>Сигнали</th><th>WIN</th><th>LOSS</th><th>Win Rate</th><th>P&L сума @30мин</th></tr>
  {conf_rows}
</table>
<h2>Всички решения <span id="pt" style="color:#888;font-size:14px;font-weight:normal"></span></h2>
<div class="pg" id="pgT">
  <button onclick="prevPage()" id="bPrev">&#8592;</button>
  <span id="pBtns"></span>
  <button onclick="nextPage()" id="bNext">&#8594;</button>
  <span class="pi" id="pInfo"></span>
  <span style="color:#888;font-size:13px;margin-left:12px">Редове:</span>
  <select class="pps" onchange="changePerPage(this.value)">
    <option value="25">25</option><option value="50" selected>50</option>
    <option value="100">100</option><option value="200">200</option>
    <option value="99999">Всички</option>
  </select>
</div>
<table>
  <thead><tr>
    <th>Час</th><th>Сесия</th><th>Action</th><th>Conf</th>
    <th>Bid</th><th>SL</th><th>TP</th>
    <th>+5мин</th><th>+15мин</th><th>+30мин</th>
    <th>MaxFav</th><th>MaxAdv</th><th>Резултат</th><th>Причина</th>
  </tr></thead>
  <tbody id="tb">{rows}</tbody>
</table>
<div class="pg" style="margin-top:12px">
  <button onclick="prevPage()">&#8592;</button>
  <span id="pBtnsB"></span>
  <button onclick="nextPage()">&#8594;</button>
  <span class="pi" id="pInfoB"></span>
</div>
<script>
var cp=1,pp=50,ar=[];
function init(){{ar=Array.from(document.querySelectorAll('.datarow'));rp();}}
function rp(){{
  var t=ar.length,tp=pp>=99999?1:Math.ceil(t/pp);
  cp=Math.max(1,Math.min(cp,tp));
  var s=pp>=99999?0:(cp-1)*pp,e=pp>=99999?t:Math.min(s+pp,t);
  ar.forEach(function(r,i){{r.classList.toggle('visible',i>=s&&i<e);}});
  var inf="Показани "+(s+1)+"–"+e+" от "+t;
  document.getElementById('pInfo').textContent=inf;
  document.getElementById('pInfoB').textContent=inf;
  document.getElementById('pt').textContent="— стр. "+cp+" от "+tp;
  rb('pBtns',tp);rb('pBtnsB',tp);
  document.getElementById('bPrev').disabled=cp<=1;
  document.getElementById('bNext').disabled=cp>=tp;
}}
function rb(id,tp){{
  var c=document.getElementById(id);c.innerHTML='';
  if(tp<=1)return;
  var d=3,rng=[];
  for(var i=Math.max(1,cp-d);i<=Math.min(tp,cp+d);i++)rng.push(i);
  if(rng[0]>1){{ab(c,1);if(rng[0]>2)ad(c);}}
  rng.forEach(function(p){{ab(c,p);}});
  if(rng[rng.length-1]<tp){{if(rng[rng.length-1]<tp-1)ad(c);ab(c,tp);}}
}}
function ab(c,p){{
  var b=document.createElement('button');b.textContent=p;
  if(p===cp)b.classList.add('active');
  b.onclick=function(){{cp=p;rp();window.scrollTo(0,0);}};
  c.appendChild(b);
}}
function ad(c){{
  var s=document.createElement('span');s.textContent='...';
  s.style.color='#888';s.style.padding='0 4px';c.appendChild(s);
}}
function prevPage(){{cp--;rp();window.scrollTo(0,0);}}
function nextPage(){{cp++;rp();window.scrollTo(0,0);}}
function changePerPage(v){{pp=parseInt(v);cp=1;rp();}}
window.onload=init;
</script>
</body>
</html>"""

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML отчет: {REPORT_FILE}")


if __name__ == "__main__":
    decisions = load_decisions()
    print(f"Заредени {len(decisions)} решения")
    results = analyze(decisions)
    print_summary(results)
    generate_html(results)
    input("\nНатисни Enter за изход...")

