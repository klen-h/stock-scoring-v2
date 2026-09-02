# -*- coding: utf-8 -*-
import io, sys, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

r = requests.get("http://localhost:8000/api/score/000567", timeout=30)
print("HTTP", r.status_code)
d = r.json()
print("keys:", list(d.keys()))
print("total_score:", d.get("total_score"), "signal:", d.get("signal"))
print("weight:", d.get("weights") or d.get("weight"))
for dim in d.get("dimensions") or []:
    print(f"  {dim.get('name')}: score={dim.get('score')} weight={dim.get('weight')} "
          f"weighted={dim.get('weighted_score')}")
# 价格/涨跌
for k in ("price", "change_pct", "prev_close", "name"):
    if k in d:
        print(f"{k} = {d[k]}")
