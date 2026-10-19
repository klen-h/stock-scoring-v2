# -*- coding: utf-8 -*-
"""等 Render 部署后：① 新接口是否存在 ② Render 出站能否访问上交所 ③ 清单①是否带份额字段。"""
import json
import time
import urllib.request

BASE = "https://stock-scoring-v2.onrender.com/api/market"


def req(path, method="GET", timeout=90):
    r = urllib.request.Request(BASE + path, data=(b"" if method == "POST" else None),
                               headers={"User-Agent": "curl/8"}, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


for i in range(10):
    try:
        d = req("/policy-bottom/etf-shares/refresh", "POST")
        print(f"[OK] 第 {i + 1} 次：ok={d.get('ok')} count={d.get('count')} "
              f"confirmed={d.get('confirmed')} date={d.get('date')}")
        print("     text:", (d.get("text") or "")[:90])
        for r in (d.get("suspicious") or []):
            print(f"     ★ {r['code']} {r['name']} {r['chg_pct']:+.2f}% "
                  f"(前日 {r.get('prev_chg_pct')}%) streak={r.get('streak')}")
        pb = req("/policy-bottom")
        it = (pb.get("items") or {}).get("nation_team_ann") or {}
        print("     清单①: checked=", it.get("checked"), "etf_status=", it.get("etf_status"),
              "etf_hits=", len(it.get("etf_hits") or []), "source=", it.get("source"))
        print("     etf_text:", (it.get("etf_text") or "")[:90])
        break
    except Exception as e:
        print(f"[wait {i + 1}] {type(e).__name__}: {str(e)[:70]}")
        time.sleep(45)
else:
    print("[WARN] 超时（部署未完成或 Render 出站被拦）")
