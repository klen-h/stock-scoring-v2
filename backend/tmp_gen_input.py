# -*- coding: utf-8 -*-
"""生成前后端引擎对比测试的输入（同一份数据），并算出 Python 侧结果。"""
import sys, os, json, time
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
kl = get_cached_klines(CODE)
tech = _calc_technical(list(kl))
print(f"K线 {len(kl)} 根 → 技术指标 {len(tech)} 条")

stock_info = {
    "code": CODE, "name": "贵州茅台",
    "price": 1297.4, "change_pct": 0.5, "pe": 25, "pb": 8,
    "market_cap": 163100000,   # 万元
    "float_cap": 163100000,
    "turnover_rate": 0.8, "amplitude": 2.5,
    "volume": 3500000,
}
fin = get_finance(CODE)
fundamental = {
    "valuation": {"市盈率(动态)": 25, "市净率": 8, "总市值(亿)": 16310, "流通市值(亿)": 16310},
    "financial": {"换手率": 0.8},
    "growth": {"revenue_yoy": fin.get("revenue_yoy"), "profit_yoy": fin.get("profit_yoy")},
    "quality": {"roe": fin.get("roe"), "debt_ratio": fin.get("debt_ratio"),
                "gross_margin": fin.get("gross_margin")},
}

eng = ScoreEngine()
r = eng.score_stock(CODE, "贵州茅台", tech, stock_info, fundamental)
print(f"\n[Python] 总分 {r.total_score}")
for d in r.dimensions:
    print(f"  {d['name']}={d['score']} (w={d['weight']}, ws={d['weighted_score']})")

# 写输入给 Node 测试用（同一份 technicalData + stockInfo + finance）
input_data = {
    "code": CODE, "name": "贵州茅台",
    "technicalData": tech,
    "stockInfo": {**stock_info, "finance": {
        "revenue_yoy": fin.get("revenue_yoy"), "profit_yoy": fin.get("profit_yoy"),
        "roe": fin.get("roe"), "debt_ratio": fin.get("debt_ratio"),
        "gross_margin": fin.get("gross_margin"),
    }},
    "finance": {
        "revenue_yoy": fin.get("revenue_yoy"), "profit_yoy": fin.get("profit_yoy"),
        "roe": fin.get("roe"), "debt_ratio": fin.get("debt_ratio"),
        "gross_margin": fin.get("gross_margin"),
    },
}
with open(os.path.join(os.path.dirname(__file__), "tmp_engine_input.json"), "w",
          encoding="utf-8") as f:
    json.dump(input_data, f, ensure_ascii=False)
print("\n输入已写入 tmp_engine_input.json（喂给 Node 侧前端引擎）")
