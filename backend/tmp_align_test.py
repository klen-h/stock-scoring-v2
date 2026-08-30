# -*- coding: utf-8 -*-
"""诊断排行 vs 详情差异：用真实链路逐维度对比（不模拟）。"""
import sys, os, time
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.database import db
from app.tencent import get_stock, get_kline
from app.scoring.kline_cache import get_cached_klines
from app.scoring.indicator_cache import get_cached_technical
from app.scoring.engine import ScoreEngine
from app.finance import get_finance, get_finance_batch
from app.routers.scoring import _calc_technical, _calc_technical_fast

CODE = "600519"   # kline_cache 里有缓存
eng = ScoreEngine()

# ── 公共：实时行情 ──
info = get_stock(CODE)
print(f"行情: {'OK' if info else 'FAIL'} price={info.get('price') if info else '-'}")

# ═══ 路径A：详情（score_single 的真实逻辑）═══
kl_real = get_kline(CODE, period="day", count=500)
tech_a = _calc_technical(kl_real) if len(kl_real) >= 30 else kl_real
fin_a = get_finance(CODE)
fund_a = {
    "valuation": {
        "市盈率(动态)": info.get("pe", 0), "市净率": info.get("pb", 0),
        "总市值(亿)": round(info.get("market_cap", 0) / 10000, 2),
        "流通市值(亿)": round(info.get("float_cap", 0) / 10000, 2),
    },
    "financial": {"换手率": info.get("turnover_rate", 0)},
}
if fin_a:
    fund_a["growth"] = {"revenue_yoy": fin_a.get("revenue_yoy"),
                        "profit_yoy": fin_a.get("profit_yoy")}
    fund_a["quality"] = {"roe": fin_a.get("roe"), "debt_ratio": fin_a.get("debt_ratio"),
                         "gross_margin": fin_a.get("gross_margin")}
r_a = eng.score_stock(CODE, info.get("name", ""), tech_a, info, fund_a)

# ═══ 路径B：批量精算（_precise_score_sync 的真实逻辑）═══
tech_b = get_cached_technical(CODE)
src_b = "指标缓存"
if tech_b is None:
    kl_b = get_cached_klines(CODE) or get_kline(CODE, period="day", count=500)
    tech_b = _calc_technical_fast(kl_b) if len(kl_b) >= 30 else kl_b
    src_b = "K线缓存+fast计算"
fin_b = get_finance_batch([CODE]).get(CODE)
r_b = None
if tech_b and len(tech_b) >= 30:
    # 直接复用批量精算函数（保证和真实链路一字不差）
    from app.routers.scoring import _precise_score_sync
    b = _precise_score_sync(info, tech_b, fin_b)
    r_b = b

# ═══ 对比 ═══
print(f"\n实时K线: {len(kl_real)} 根, 最后 {kl_real[-1]['date'] if kl_real else '-'}")
kl_c = get_cached_klines(CODE)
print(f"缓存K线: {len(kl_c) if kl_c else 0} 根, 最后 {kl_c[-1]['date'] if kl_c else '-'}")

print(f"\n财务对比: get_finance={'有' if fin_a else '无'} | "
      f"get_finance_batch={'有' if fin_b else '无'}")
if fin_a and fin_b:
    print(f"  报告期: {fin_a.get('report_date')} vs {fin_b.get('report_date')} | "
          f"ROE: {fin_a.get('roe')} vs {fin_b.get('roe')}")

da = {d["name"]: d["score"] for d in r_a.dimensions}
db_ = r_b.get("dimensions", {}) if isinstance(r_b, dict) else {}
print(f"\n{'维度':<8}{'详情(A)':>10}{'批量(B)':>10}{'差异':>8}")
for k in ("技术面", "资金面", "基本面", "成长", "质量", "简化评分"):
    va, vb = da.get(k), db_.get(k)
    if va is None and vb is None:
        continue
    diff = "" if (va is not None and vb is not None and abs(va - vb) < 0.05) else "  ←"
    print(f"{k:<8}{str(va):>10}{str(vb):>10}{diff:>8}")
ta = r_a.total_score
tb = r_b.get("total_score") if isinstance(r_b, dict) else (r_b.total_score if r_b else None)
print(f"\n总分: 详情={ta} 批量={tb}  差={round(abs((ta or 0) - (tb or 0)), 2)}")
