# -*- coding: utf-8 -*-
"""拉新浪全市场A股列表，算行业映射缺口。"""
import io, sys, json, time, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db
from app.sector_industry import _SINA_BASE, _SINA_HEADERS

all_stocks = {}
for page in range(1, 70):   # 全市场约 5400 只 / 100 = 54 页
    try:
        r = requests.get(f"{_SINA_BASE}/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                         params={"node": "hs_a", "num": 100, "page": page},
                         headers=_SINA_HEADERS, timeout=15)
        r.encoding = "gbk"
        arr = json.loads(r.text) or []
    except Exception as e:
        print(f"page {page} 异常 {e}")
        break
    for x in arr:
        code = str(x.get("code") or "").strip()
        if code and len(code) == 6 and code.isdigit():
            all_stocks.setdefault(code, str(x.get("name") or ""))
    if len(arr) < 100:
        break
    time.sleep(0.1)
print(f"全市场A股: {len(all_stocks)} 只")

have = {r["code"] for r in db.fetch("SELECT code FROM stock_industry")}
gap = {c: n for c, n in all_stocks.items() if c not in have}
print(f"已映射: {len(have)}  缺口: {len(gap)}")
# 缺口代码前缀分布
from collections import Counter
print("缺口前缀:", Counter(c[:3] for c in gap).most_common(10))
print("缺口样例:", list(gap.items())[:20])
