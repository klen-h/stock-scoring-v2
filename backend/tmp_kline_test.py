# -*- coding: utf-8 -*-
"""量化前端与后端数据流差异对分数的影响：
A. 500 根（后端基准）
B. 150 根（前端 getKlines 的量）
C. 150 根 + 重复最后一根（模拟周日 appendTodayBar：实时价=周五收盘 → 重复 bar）
D. B/C + 无财报（前端财报加载失败时的三维度归一化）
"""
import sys, os, copy
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.scoring.kline_cache import get_cached_klines
from app.scoring.engine import ScoreEngine
from app.finance import get_finance
from app.routers.scoring import _calc_technical

CODE = "600519"
kl = list(get_cached_klines(CODE))
info = {"code": CODE, "name": "贵州茅台", "price": kl[-1]["close"], "change_pct": 0.5,
        "pe": 25, "pb": 8, "market_cap": 163100000, "float_cap": 163100000,
        "turnover_rate": 0.8, "amplitude": 2.5}
fin = get_finance(CODE)
fund = {
    "valuation": {"市盈率(动态)": 25, "市净率": 8, "总市值(亿)": 16310, "流通市值(亿)": 16310},
    "financial": {"换手率": 0.8},
    "growth": {"revenue_yoy": fin.get("revenue_yoy"), "profit_yoy": fin.get("profit_yoy")},
    "quality": {"roe": fin.get("roe"), "debt_ratio": fin.get("debt_ratio"),
                "gross_margin": fin.get("gross_margin")},
}
eng = ScoreEngine()

def run(tag, klines, fundamental):
    tech = _calc_technical(klines)
    r = eng.score_stock(CODE, "茅台", tech, info, fundamental)
    dims = {d["name"]: d["score"] for d in r.dimensions}
    print(f"{tag:<34} 总分={r.total_score:<6} 技术面={dims.get('技术面')}")
    return r.total_score

print("── K线根数影响 ──")
a = run("A: 500 根（后端基准）", kl, fund)
b = run("B: 150 根（前端拉取量）", kl[-150:], fund)
kl_dup = kl[-150:] + [copy.deepcopy(kl[-1])]   # 模拟周日 appendTodayBar 重复
c = run("C: 150 根 + 重复今日bar（周日场景）", kl_dup, fund)

print("\n── 财报缺失影响（前端没拿到财报时）──")
run("D: 150 根 + 无财报", kl[-150:], {"valuation": fund["valuation"], "financial": fund["financial"]})
run("E: 500 根 + 无财报（后端接口若失败）", kl,
    {"valuation": fund["valuation"], "financial": fund["financial"]})

print(f"\n结论: A-B={round(a-b,2)} (根数) | A-C={round(a-c,2)} (周日重复bar)")
