# -*- coding: utf-8 -*-
"""端到端验证：score_batch 带财务数据（阶段1 筛选标准 == 阶段2 展示标准）。"""
import sys, os, time
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.finance import get_finance_batch
from app.scoring.engine import ScoreEngine

# 模拟阶段1：一批只有实时行情的股票（无技术指标 → 走简化评分）
STOCKS = [
    {"code": "600519", "name": "贵州茅台", "change_pct": 1.2, "pe": 25, "turnover_rate": 1.0},
    {"code": "300750", "name": "宁德时代", "change_pct": 2.5, "pe": 40, "turnover_rate": 3.0},
    {"code": "002594", "name": "比亚迪", "change_pct": -1.8, "pe": 60, "turnover_rate": 2.0},
    {"code": "999999", "name": "无财报股", "change_pct": 0.5, "pe": 30, "turnover_rate": 1.5},
]
fin = get_finance_batch([s["code"] for s in STOCKS])
print(f"预加载财报: {len(fin)}/4 只\n")

eng = ScoreEngine()
results = eng.score_batch(STOCKS, fin_map=fin)
print("阶段1 简化评分（带成长/质量）:")
for r in results:
    dims = "，".join(f"{d['name']}={d['score']}(w={d['weight']})" for d in r.dimensions)
    s = round(sum(d["weighted_score"] for d in r.dimensions), 2)
    ok = "OK" if abs(s - r.total_score) < 0.15 else "MISMATCH"
    print(f"  {r.name:<8} 总分={r.total_score:<6} [{dims}] 自洽={ok}")

print("\n关键验证：无财报股（999999）应只有 1 个维度且权重=1.0；"
      "有财报股应有 3 个维度（简化0.7+成长0.18+质量0.12）")
