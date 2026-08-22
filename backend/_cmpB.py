# 前后端评分对照脚本（后端侧）：输出 score_single 总分与全部子项明细
# 用法: cd backend; python _cmpB.py [股票代码]   （默认 002479）
# 对照: node _cmpF.cjs [股票代码]，逐项比较 dims 下的子项分值
import asyncio
import json
import sys

sys.path.insert(0, ".")

from app.routers.scoring import score_single

code = sys.argv[1] if len(sys.argv) > 1 else "002479"
result = asyncio.run(score_single(code))
out = {"total": result.get("total_score"), "dims": {}}
for d in result.get("dimensions", []):
    dd = d if isinstance(d, dict) else d.__dict__
    out["dims"][dd["name"]] = {"score": dd["score"], "details": dd.get("details")}
print(json.dumps(out, ensure_ascii=False, indent=1))
with open(f"_cmpB_{code}.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
