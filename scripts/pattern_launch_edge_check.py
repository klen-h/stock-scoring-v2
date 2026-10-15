# -*- coding: utf-8 -*-
"""
================================================================================
「缩量调整 → 放量启动」形态 · **预登记检验**（前瞻样本）
================================================================================
判据冻结于 `PLAN_PATTERN_LAUNCH_EDGE.md` v1.0（2026-10-15）。本脚本**只实现判据、
不修改判据**——改判据必须在那份文件里新开一节。

【★ 为什么只算前瞻样本】
  形态在落地前已被看过三次全历史（A 参数网格 / B 成本可执行性 / 研究工具），
  同一段历史已无检验力（参数正是从它身上挑出来的）⇒ 判据只对
  `pattern_scan_daily.date >= FORWARD_START` 的前瞻信号生效；历史值仅作 in-sample 参考。

【判据（摘，全文见文档 §2）】
  GO  : 净超额 ≥ +1.00pp ∧ 聚类单侧 P ≤ 0.05 ∧ T+20 同向
  KILL: 等效性式——净超额 95% 上界 ≤ +0.30pp 或显著为负（P(μ≥基准) ≤ 0.05）
  闸门: ≥20 独立信号日 ∧ ≥120 观测（T+10 到期）
  口径: T+1 开盘建仓 → T+10 收盘；扣成本 0.30%；基准=同资格无条件；剔 T+1 一字板

【数据】全链路只读 Postgres（`pattern_scan_daily` + `backtest_prices`）⇒ 可注册
  `edge_verify` 月度复核（对照：`mf_edge_check`/LHB 因依赖本地大库**刻意不注册**）。

【结构化契约】`--json` ⇒ 正文照常打印，**末尾追加一行** JSON（供 `edge_verify` 解析）：
  {"script","verdict","n","need","unit","detail"}

用法：python scripts/pattern_launch_edge_check.py [--json]
================================================================================
"""
import argparse
import json
import os
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
for _line in open(BACKEND / ".env", encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db                     # noqa: E402
from app import pattern_launch as pl            # noqa: E402

# ── 预登记判据（冻结 2026-10-15；改判据须在 PLAN_PATTERN_LAUNCH_EDGE.md 新开一节）──
FORWARD_START = "2026-10-15"    # 前瞻起算：本功能上线后的扫描
FWD_MAIN, FWD_ALT = 10, 20
COST = 0.003                    # 双边成本（与 shrink_rebound_live 同源）
GO_EXCESS = 0.0100              # GO 线：净超额 ≥ +1.00pp
KILL_UPPER = 0.0030             # KILL 等效性上界：≤ +0.30pp
ALPHA = 0.05
GATE_DAYS, GATE_OBS = 20, 120   # 样本闸
SEED, B_BOOT = 20261015, 4000
UNIT = "T+10 到期观测"


def _load_bars_map():
    """一次性载入全池 bars（信号与前视都要用）。"""
    m = {}
    for code in pl.all_codes():
        bars = pl.load_bars(code)
        if bars:
            m[code] = bars
    return m


def _forward_samples(bars_map, fwd):
    """前瞻信号 → 到期观测。返回 [(date, ret_net, max_up, max_dd)] + 剔除记账。"""
    rows = db.fetch("SELECT date, code FROM pattern_scan_daily WHERE date >= %s "
                    "ORDER BY date", (FORWARD_START,)) or []
    out, rej = [], {"no_bars": 0, "not_matured": 0, "limit_open": 0}
    for r in rows:
        day, code = str(r["date"])[:10], r["code"]
        bars = bars_map.get(code)
        if not bars:
            rej["no_bars"] += 1
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == day), None)
        if idx is None or idx + 1 >= len(bars):
            rej["no_bars"] += 1
            continue
        # 可执行性：T+1 开盘即涨停 ⇒ 买不进（近似：+9.8%，无交易所涨停价列）
        entry_bar = bars[idx + 1]
        if entry_bar["open"] / bars[idx]["close"] - 1 >= 0.098:
            rej["limit_open"] += 1
            continue
        j = idx + 1 + fwd
        if j >= len(bars):
            rej["not_matured"] += 1
            continue
        entry = entry_bar["open"]
        if entry <= 0:
            rej["no_bars"] += 1
            continue
        seg = bars[idx + 1:j + 1]
        out.append((day, bars[j]["close"] / entry - 1 - COST,
                    max(x["high"] for x in seg) / entry - 1,
                    min(x["low"] for x in seg) / entry - 1))
    return out, rej, len(rows)


def _baseline(bars_map, fwd, min_idx, limit=4):
    """同资格无条件基准（同池 / 同入场 / 取样起点 ≥ pullback+base 根）。"""
    out = []
    for bars in bars_map.values():
        hi = len(bars) - fwd - 1
        if hi <= min_idx:
            continue
        idxs = list(range(min_idx, hi))
        random.Random(0).shuffle(idxs)
        for i in idxs[:limit]:
            entry = bars[i + 1]["open"]
            if entry > 0:
                out.append(bars[i + 1 + fwd]["close"] / entry - 1)
    return out


def _clustered(sig_rets, sig_days, base_mean, b=B_BOOT):
    """按信号日聚类 bootstrap：返回 (超额, 单侧P(μ≤基准), 95% 上界)。"""
    cl = defaultdict(list)
    for d, r in zip(sig_days, sig_rets):
        cl[d].append(r)
    keys = list(cl)
    cnt = sum(len(v) for v in cl.values())
    m = statistics.mean(sig_rets)
    obs = m - base_mean
    rng = random.Random(SEED)
    diffs = []
    le = 0
    for _ in range(b):
        s = sum(sum(cl[rng.choice(keys)]) for _ in range(len(keys)))
        d = s / cnt - base_mean
        diffs.append(d)
        if d <= 0:
            le += 1
    diffs.sort()
    upper = diffs[int(0.95 * (len(diffs) - 1))]        # 单侧 95% 上界
    return obs, le / b, upper


def main(argv=None):
    ap = argparse.ArgumentParser(description="缩量启动形态 · 预登记检验（前瞻）")
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论")
    args = ap.parse_args(argv)

    cfg = pl.get_config()
    min_idx = cfg["pullback_days"] + cfg["vol_base_days"]
    bars_map = _load_bars_map()

    print("# 「缩量调整→放量启动」形态 · 预登记检验（前瞻样本）\n")
    print(f"> 判据冻结：`PLAN_PATTERN_LAUNCH_EDGE.md` v1.0 ｜ 前瞻起算 {FORWARD_START} "
          f"｜ 池 {len(bars_map)} 只 ｜ 参数 启动≥{cfg['launch_pct']}% / 缩量≤{cfg['vol_shrink']}\n")

    res = {}
    for fwd in (FWD_MAIN, FWD_ALT):
        obs, rej, n_sig = _forward_samples(bars_map, fwd)
        rets = [o[1] for o in obs]
        days = [o[0] for o in obs]
        base = _baseline(bars_map, fwd, min_idx)
        bm = statistics.mean(base) if base else 0.0
        res[fwd] = {"n": len(rets), "clusters": len(set(days)), "mean": (statistics.mean(rets) if rets else None),
                    "base": bm, "rej": rej, "n_sig": n_sig, "rets": rets, "days": days}
        print(f"## T+{fwd}")
        print(f"- 前瞻信号 {n_sig} 条 → 到期观测 **{len(rets)}**（独立信号日 {len(set(days))}）")
        print(f"- 剔除记账：未到期 {rej['not_matured']} ｜ T+1 一字板买不进 {rej['limit_open']} "
              f"｜ 无行情 {rej['no_bars']}")
        print(f"- 基准（同资格无条件）均 {bm*100:+.2f}%")
        if rets:
            print(f"- 净均收益 {statistics.mean(rets)*100:+.2f}% ｜ 中位 "
                  f"{statistics.median(rets)*100:+.2f}% ｜ 胜率 "
                  f"{sum(1 for r in rets if r > 0)/len(rets)*100:.1f}%")
        print()

    # ── 判定（只对主窗口 T+10；T+20 只作"同向"确认）──
    m = res[FWD_MAIN]
    n, cl = m["n"], m["clusters"]
    verdict, detail = "INSUFFICIENT", ""
    if n < GATE_OBS or cl < GATE_DAYS:
        detail = (f"前瞻样本未达闸门：{n}/{GATE_OBS} 观测、{cl}/{GATE_DAYS} 独立信号日"
                  f"（T+10 到期；前瞻起算 {FORWARD_START}，日均命中约 0~2 只 ⇒ 预计 1~3 个月）")
    else:
        obs, p_le, upper = _clustered(m["rets"], m["days"], m["base"])
        alt_mean = (statistics.mean(res[FWD_ALT]["rets"]) if res[FWD_ALT]["rets"] else None)
        alt_excess = (alt_mean - res[FWD_ALT]["base"]) if alt_mean is not None else None
        same_dir = (alt_excess is not None and alt_excess > 0)
        if obs >= GO_EXCESS and p_le <= ALPHA and same_dir:
            verdict = "PASS"
            detail = (f"净超额 {obs*100:+.2f}pp ≥ +1.00pp；聚类单侧 P={p_le:.3f}；"
                      f"T+20 同向（{alt_excess*100:+.2f}pp）⇒ GO")
        elif upper <= KILL_UPPER or (1 - p_le) <= ALPHA:
            verdict = "FAIL"
            detail = (f"净超额 {obs*100:+.2f}pp，95% 上界 {upper*100:+.2f}pp ≤ +0.30pp"
                      f" 或显著为负（P(μ≥基准)={1-p_le:.3f}）⇒ KILL")
        else:
            verdict = "PARTIAL"
            detail = (f"净超额 {obs*100:+.2f}pp（未达 +1.00pp 或 P={p_le:.3f}）；"
                      f"95% 上界 {upper*100:+.2f}pp > +0.30pp ⇒ 未证伪亦未达标")
    print("## 判定\n")
    print(f"- **{verdict}** ｜ {detail}")
    print(f"\n> in-sample 参考（**不作判据**，见文档 §0）：A/B 三关历史值 T+10 毛 +2.09pp / 净 +1.80pp、"
          f"网格 12/12 组合显著 —— 但参数正是从这段历史挑出 ⇒ 无检验力。")

    if args.json:
        print(json.dumps({"script": "pattern_launch_edge_check", "verdict": verdict,
                          "n": n, "need": GATE_OBS, "unit": UNIT,
                          "detail": detail, "clusters": cl, "need_days": GATE_DAYS},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
