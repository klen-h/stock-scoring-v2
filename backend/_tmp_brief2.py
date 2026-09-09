# 临时：v2 简报实测（用完即删）
import json
from app.trader_brief import generate_trader_brief

r = generate_trader_brief(force=True)
print("ok:", r.get("ok"), "| degraded:", r.get("degraded"))
print("items:", json.dumps(r.get("items") or [], ensure_ascii=False)[:300])
print("--- markdown 前 600 字 ---")
print((r.get("markdown") or "")[:600])
