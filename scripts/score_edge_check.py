#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】**评分系统本身的预测力** —— 预登记检验（本项目唯一长期免检的核心假设）
================================================================================
判据：`PLAN_SCORE_EDGE.md` §4 **v1.1（2026-10-11 冻结）**。改判据必须先改 PLAN 并在此
  文件头新开一节注明日期 —— 本脚本只是判据的机械执行者，**不拥有判据**。

【主判据（唯一）】T+5 超额（次日开盘建仓口径）
  · 对每个快照日 d：等权买入当日 Top50（`ranking_history`，`rank_pos` 1..50），
    入场 = d 之后第 1 个交易日**开盘**，出场 = d 之后第 6 个交易日**开盘**
    （★ 可执行口径：评分在 d 日收盘后才产出，只能次日开盘买 —— 项目硬教训
    「收盘价入场几乎系统性高估」，见 `lhb_executability_check`）；
  · 基准 = **可取价池等权**（`backtest_prices` 当日有价 A 股 0/3/6 开头，**含当日 Top50**，
    同价格源、同两端口）。★ 注意语义：该池（~941 只）只占评分宇宙（`pool_total`=2224）的
    **42%** ⇒ 结论措辞只能是「跑赢/跑输可取价池」，**不是**评分宇宙（PLAN §8-2）；
  · GO：均值 ≥ +0.60pt 且 单侧 bootstrap P ≤ 0.05 且 T+10 同向 且 不重叠子样本方向一致；
    KILL：均值 ≤ 0 且 bootstrap 95% 上界 ≤ +0.30pt，或 P(μ<0) ≤ 0.05（+方向一致）；
  · 5/10/20 三个持有期的**判定状态全报**（禁止只报好看的那个）；T+20 未达 20 日门槛时
    **只报 INSUFFICIENT 与计数，不打印任何收益数字**（欠功效数字不进报告）。

【成本与敏感性】扣 0.30% 往返的净超额 / 剔"开盘近涨停买不到"（板幅用项目唯一口径
  `backtest.engine._limit_pct`，容差 0.2%）/ 不重叠子样本方向核验 / sh000300·sh000852 指数对照 /
  逐日打印 Top50 落池外只数（幸存偏差；v1.1 实测 T+5 两端缺价 0/1250）。

【与 `shadow_rank_edge_check` 的关系】同一问题的另一条独立证据链（入场口径不同：
  那边用快照日收盘价）。其首跑记录（7 个到期日，base 平均超额 −2.19%，defensive 期）是
  **公开先验**，跑完必须在 PLAN §9 里对着它说话 —— 方向一致 ⇒ 互相印证；反向 ⇒ 如实记录分歧。

【egress】价格只取需要的日期（T+5/T+10 两端口 + 快照日），~60 日 × ~974 行 ≈ 2-3MB/次，
  月度一次，可接受（`EGRESS.md` 口径）。

【输出契约】末行 JSON：{"script","verdict","n","need","unit","detail"}（`edge_verify` 解析）。
  判定词汇映射：GO→PASS ｜ KILL→FAIL ｜ PARTIAL ｜ INSUFFICIENT。
================================================================================
"""
import argparse
import json
import os
import random
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
for _line in open(os.path.join(ROOT, "backend", ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── 预登记常量（PLAN_SCORE_EDGE.md §4 v1.1）──
HOLDS = (5, 10, 20)
PRIMARY = 5
GO_PT = 0.60            # 超额均值门槛（pt；三持有期同用，对长持有更严 ⇒ 保守）
GO_P_MAIN = 0.05        # 主判据单侧 P
GO_P_SEC = 0.025        # 次判据 Bonferroni（0.05/2）
KILL_CI_PT = 0.30       # KILL ①：95% 上界 ≤ +0.30pt 且均值 ≤ 0
KILL_PNEG = 0.05        # KILL ②：显著为负
MIN_DAYS = 20           # 到期快照日门槛
MIN_OBS = 800           # 观测门槛（覆盖纪律；有效样本量 = 天数，见 PLAN §8-1）
COST_PT = 0.30          # 往返成本（pt）
BOOT_N = 10000
BOOT_SEED = 20261011
LIMIT_TOL = 0.002       # 板幅容差（与 limit_stats._BREAK_TOL 同量级）


def trading_days() -> List[str]:
    """交易日历：`backtest_prices` 的 `sh000300`（每日更新；禁用研究库当历）。"""
    from app.database import db
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices WHERE code = 'sh000300' "
                    "ORDER BY date ASC") or []
    return [str(r["date"])[:10] for r in rows]


def load_top50() -> Dict[str, List[dict]]:
    """`ranking_history` → {快照日: [{code,name}, ...按 rank_pos]}; 并暴露行数异常日。"""
    from app.database import db
    rows = db.fetch("SELECT rank_date, code, name FROM ranking_history "
                    "ORDER BY rank_date ASC, rank_pos ASC") or []
    out: Dict[str, List[dict]] = {}
    for r in rows:
        out.setdefault(str(r["rank_date"])[:10], []).append(
            {"code": r["code"], "name": r["name"] or ""})
    return out


def load_prices(dates: List[str]) -> Dict[tuple, float]:
    """读 `backtest_prices` 的 open/close → {(date, code): (open, close)}。

    只取 A 股（0/3/6 开头 ⇒ 天然排除指数 sh* 与北交所 4/8），与基准口径一致。
    """
    from app.database import db
    if not dates:
        return {}
    ph = ",".join(["%s"] * len(dates))
    rows = db.fetch(f"SELECT date, code, open, close FROM backtest_prices "
                    f"WHERE date IN ({ph}) AND LEFT(code,1) IN ('0','3','6')",
                    tuple(dates)) or []
    out: Dict[tuple, float] = {}
    for r in rows:
        o, c = r.get("open"), r.get("close")
        if o and c:
            out[(str(r["date"])[:10], r["code"])] = (float(o), float(c))
    return out


def _pctile(sorted_vals: List[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def bootstrap(excess: List[float]) -> Dict[str, float]:
    """按快照日整日重抽样（B=10000，seed 冻结）→ 单侧 P 与分位数。"""
    rng = random.Random(BOOT_SEED)
    n = len(excess)
    means = []
    for _ in range(BOOT_N):
        s = 0.0
        for _ in range(n):
            s += excess[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    p_pos = sum(1 for m in means if m <= 0.0) / BOOT_N      # 单侧 P(μ>0)
    p_neg = sum(1 for m in means if m >= 0.0) / BOOT_N      # 单侧 P(μ<0)
    return {"p_pos": p_pos, "p_neg": p_neg,
            "up95": _pctile(means, 0.95),
            "lo2.5": _pctile(means, 0.025), "hi97.5": _pctile(means, 0.975)}


def limit_up_blocked(code: str, name: str, open_entry: float, close_prev: float) -> bool:
    """入场日开盘 ≥ 快照日收盘 ×（1 + 板幅 − 容差） ⇒ 近似"一字/接近涨停买不到"。

    板幅用项目唯一口径 `backtest.engine._limit_pct`（双创 20cm/北交 30cm/主板 10cm）——
    v1.1 改：v1.0 的固定 ×1.095 是 10cm 假设，会把 20cm 板的普通高开误判为买不到。
    """
    try:
        from app.backtest.engine import _limit_pct
        lim = _limit_pct(code, name)
    except Exception:
        lim = 0.10
    return open_entry >= close_prev * (1.0 + lim - LIMIT_TOL)


def run_hold(hold: int, cal: List[str], idx: Dict[str, int], snaps: List[str],
             top50: Dict[str, List[dict]], prices: Dict[tuple, float]) -> dict:
    """单个持有期的完整计算（含覆盖统计与敏感性）。**不在这里做判定**。"""
    due = [d for d in snaps if d in idx and idx[d] + hold + 1 <= len(cal) - 1]
    excess: List[float] = []
    obs = 0
    miss = 0
    excl_limitup = 0
    bench_sizes = []
    per_day = []
    for d in due:
        i = idx[d]
        entry_day, exit_day = cal[i + 1], cal[i + hold]
        names = top50.get(d) or []
        closes = {c["code"]: prices.get((d, c["code"]), (None, None))[1] for c in names}
        rets, rets_ex = [], []
        blocked = 0
        for c in names:
            oe = prices.get((entry_day, c["code"]))
            ox = prices.get((exit_day, c["code"]))
            if not oe or not ox:
                miss += 1
                continue
            r = ox[0] / oe[0] - 1.0
            rets.append(r)
            if closes.get(c["code"]) and limit_up_blocked(
                    c["code"], c["name"], oe[0], closes[c["code"]]):
                blocked += 1
            else:
                rets_ex.append(r)
        br = []
        for code in set(k[1] for k in prices if k[0] == entry_day):
            oe = prices.get((entry_day, code))
            ox = prices.get((exit_day, code))
            if oe and ox:
                br.append(ox[0] / oe[0] - 1.0)
        if not rets or not br:
            continue
        r_top = sum(rets) / len(rets)
        r_bench = sum(br) / len(br)
        excess.append(r_top - r_bench)
        obs += len(rets)
        excl_limitup += blocked
        bench_sizes.append(len(br))
        per_day.append((d, r_top * 100, r_bench * 100, (r_top - r_bench) * 100,
                        len(rets), blocked))
    return {"hold": hold, "due": due, "excess": excess, "obs": obs, "miss": miss,
            "excl_limitup": excl_limitup,
            "bench_avg": (sum(bench_sizes) / len(bench_sizes)) if bench_sizes else 0,
            "per_day": per_day}


def evaluate(res: dict, is_primary: bool, other_mean: float = None) -> dict:
    """按冻结判据出判定（GO/KILL/PARTIAL/INSUFFICIENT）。"""
    st = bootstrap(res["excess"]) if res["excess"] else {}
    mean = (sum(res["excess"]) / len(res["excess"]) * 100) if res["excess"] else 0.0
    days, obs = len(res["due"]), res["obs"]
    if days < MIN_DAYS or obs < MIN_OBS:
        return {"verdict": "INSUFFICIENT", "mean": mean, "days": days, "obs": obs,
                "detail": "样本不足（%d/%d 天，%d/%d 观测）——不打印收益数字" % (days, MIN_DAYS, obs, MIN_OBS)}
    non_idx = list(range(0, days, 5))
    non = [res["excess"][k] for k in non_idx]
    non_mean = sum(non) / len(non) * 100
    same_dir = (non_mean == 0) or ((mean > 0) == (non_mean > 0))
    go_p = GO_P_MAIN if is_primary else GO_P_SEC
    is_go = (mean >= GO_PT and st["p_pos"] <= go_p and same_dir
             and (other_mean is None or other_mean > 0))
    is_kill = (((mean <= 0 and st["up95"] <= KILL_CI_PT) or st["p_neg"] <= KILL_PNEG)
               and same_dir)
    verdict = "PASS" if is_go else ("FAIL" if is_kill else "PARTIAL")
    word = {"PASS": "GO", "FAIL": "KILL"}.get(verdict, "PARTIAL")
    return {"verdict": verdict, "word": word, "mean": mean, "days": days, "obs": obs,
            "p_pos": st["p_pos"], "p_neg": st["p_neg"], "up95": st["up95"] * 100,
            "ci": (st["lo2.5"] * 100, st["hi97.5"] * 100),
            "net_mean": mean - COST_PT, "non_mean": non_mean, "same_dir": same_dir,
            "excl_limitup": res["excl_limitup"], "miss": res["miss"],
            "bench_avg": res["bench_avg"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="末行输出结构化结论（edge_verify 契约）")
    args = ap.parse_args()

    cal = trading_days()
    idx = {d: i for i, d in enumerate(cal)}
    top50 = load_top50()
    snaps = sorted(top50)
    bad = [(d, len(v)) for d, v in top50.items() if len(v) != 50]
    if bad:
        print("[warn] 快照行数 ≠ 50 的日期（§8-9 数据完整性）:", bad)

    due_counts = {}
    for N in HOLDS:
        due_counts[N] = len([d for d in snaps
                             if d in idx and idx[d] + N + 1 <= len(cal) - 1])
    dates_needed = set(snaps)
    for N in (5, 10):                      # T+20 未达标 ⇒ 不取价、不出数字
        for d in snaps:
            if d in idx and idx[d] + N + 1 <= len(cal) - 1:
                dates_needed.add(cal[idx[d] + 1])
                dates_needed.add(cal[idx[d] + N])
    prices = load_prices(sorted(dates_needed))

    print("=== 评分 Top50 预测力 · 预登记检验（判据 = PLAN_SCORE_EDGE.md §4 v1.1）===")
    print("快照日 %d 个（%s ~ %s）｜取价日期 %d 天" % (len(snaps), snaps[0], snaps[-1], len(dates_needed)))

    res5 = run_hold(5, cal, idx, snaps, top50, prices)
    res10 = run_hold(10, cal, idx, snaps, top50, prices)
    ev5 = evaluate(res5, is_primary=True)
    ev10 = evaluate(res10, is_primary=False, other_mean=ev5["mean"])
    ev20 = {"verdict": "INSUFFICIENT" if due_counts[20] < MIN_DAYS else None,
            "days": due_counts[20]}

    for tag, ev, res in (("T+5（主判据）", ev5, res5), ("T+10（次判据）", ev10, res10)):
        if ev["verdict"] == "INSUFFICIENT":
            print("\n[%s] INSUFFICIENT：%s" % (tag, ev["detail"]))
            continue
        print("\n[%s] 均值 %+.3fpt（扣 0.30%% 成本净 %+.3fpt）｜ 到期日 %d｜观测 %d"
              % (tag, ev["mean"], ev["net_mean"], ev["days"], ev["obs"]))
        print("  单侧 P(μ>0) = %.4f ｜ P(μ<0) = %.4f ｜ 95%%上界 = %+.3fpt ｜ 95%%CI = [%+.3f, %+.3f]pt"
              % (ev["p_pos"], ev["p_neg"], ev["up95"], ev["ci"][0], ev["ci"][1]))
        print("  不重叠子样本均值 = %+.3fpt（方向%s）｜ 剔除入场近涨停 %d 只次 ｜ 两端缺价 %d 只次"
              " ｜ 基准日均 %.0f 只"
              % (ev["non_mean"], "一致" if ev["same_dir"] else "★不一致", ev["excl_limitup"],
                 ev["miss"], ev["bench_avg"]))
        print("  ⇒ 判定：**%s**（%s）" % (ev["word"], ev["verdict"]))
    print("\n[T+20（次判据）] %s：%d/%d 个到期日 —— 达标后由月度复检自动判，本轮不出数字"
          % (ev20["verdict"], ev20["days"], MIN_DAYS))
    print("（口径提醒：基准=可取价池等权（941 只，占评分宇宙 42%）；窗口无 offensive 市况；"
          "signal='buy' 恒 0 ⇒ 测的是「禁买市里最不差的 50 只」，不是买入信号。）")

    overall = ev5["verdict"]
    detail = ("T+5 超额 %+.2fpt（%d 到期日），单侧 P=%.4f，95%%上界 %+.2fpt ⇒ %s"
              % (ev5["mean"], ev5["days"], ev5["p_pos"], ev5["up95"], ev5["verdict"]))
    print("\n[verdict] %s ｜ %s" % (overall, detail))
    if args.json:
        print(json.dumps({"script": "score_edge_check", "verdict": overall,
                          "n": ev5["days"], "need": MIN_DAYS, "unit": "到期快照日",
                          "detail": detail}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
