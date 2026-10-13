# -*- coding: utf-8 -*-
"""
================================================================================
主力节奏假设 · 首跑（**判据 v1.0 的机械执行者**，判据本体在 PLAN_MAINFORCE_EDGE.md）
================================================================================
【主假设（唯一）】吸筹（phase=accumulation）∧ 三绿（ready==3）的个股，此后
  T+5 取得相对**可取价池等权**的正超额？
【判据（冻结，禁止在本脚本里改）】
  · 选股 = `mf_edge_daily`（本地 SQLite，426 日回填，一致性闸 99.0% 通过）
    中 phase=='accumulation' ∧ ready==3；**质量过滤**：价 ≥3 元（历史精确可得）+
    当前流通市值 ≥50 亿（**代理**，历史逐日不可得 ⇒ 已在 §9 披露）；再乘行情：
  · 入场 = d 之后第 1 个交易日**开盘**；出场 = d 之后第 (hold+1) 个交易日**开盘**
    （T+5 ⇒ +6、T+10 ⇒ +11）；组合当日**等权**；
  · 基准 = 可取价池（`backtest_prices` 当日有价 A 股 0/3/6）等权、同两端口、含当日入选
    （基准均值在 SQL 侧算，与逐票内存均值数学等价）；超额 e(d) = 组合 − 基准；
  · **一字板剔除**（主判据）：入场开盘 ≥ 判定日收盘 ×(1 + 板幅 − 0.2%) ⇒ 剔除该票该日并计数
    （板幅用项目唯一口径 `backtest.engine._limit_pct`）；剔除率 >30% 强制警告行；
  · bootstrap = **按判定日整日重抽样**（B=10000，seed=20261011，两个持有期各重置同 seed）；
  · GO：均值 ≥+0.60pt ∧ 单侧 P ≤0.05 ∧ T+10 同向 ∧ 不重叠子样本方向一致；
    KILL：均值 ≤0 且 95% 上界 ≤+0.30pt，或 P(μ<0) ≤0.05（+方向一致）；
    PARTIAL 其余；INSUFFICIENT：样本闸（≥20 判定日 ∧ ≥120 观测）不满足（不打收益数字）。
  · 两个持有期（T+5 主 / T+10 次，次判据 α=0.025）**判定状态全报**。
【价格来源】个股价格 = 本地 pack（零 egress）+ 少量回退；基准 = SQL 窗口（428 行返回）。
================================================================================
"""
import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import db                                     # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DB = ROOT / "backend" / "data" / "mf_edge_daily.db"

# ── 冻结判据常量（PLAN_MAINFORCE_EDGE §4.2；改判据须先改文档并留痕）──
GO_PT = 0.60            # GO：超额均值门槛（pt）
GO_P_MAIN = 0.05        # 主判据单侧 P
GO_P_SEC = 0.025        # 次判据 Bonferroni（0.05/2，T+10）
GO_P_SEC20 = 0.05 / 3   # ★ §11 T+20 扩展：族内 3 轮检验 Bonferroni ≈ 0.0167
KILL_CI_PT = 0.30       # KILL ①：95% 上界 ≤ +0.30pt 且均值 ≤ 0
KILL_PNEG = 0.05        # KILL ②：显著为负（T+5/T+10 按 §4.2 原文 0.05；T+20 用族内 α，见 §11）
MIN_DAYS = 20           # 判定日门槛
MIN_OBS = 120           # 观测门槛（§4.2）
COST_PT = 0.30          # 往返成本（展示用净超额）
BOOT_N = 10000
BOOT_SEED = 20261011
LIMIT_TOL = 0.002
MIN_PRICE = 3.0         # 质量过滤：价 ≥3 元（历史精确）
MIN_CAP = 50.0          # 质量过滤：流通市值 ≥50 亿（当前值代理，见 §9 披露）
HOLDS = (5, 10, 20)     # T+20 = §11 扩展（判据 v1.0 冻结于 2026-10-14，先冻结后跑）


# ── 选股与数据 ────────────────────────────────────────────────────────────────
def load_selections(use_cap: bool = True) -> Dict[str, List[dict]]:
    """判定日 → 入选个股（吸筹∧三绿 ∧ 价≥3 ∧〔可选〕当前市值≥50亿）。"""
    from app import pack_source
    caps = {}
    try:
        c = sqlite3.connect(str(pack_source.pack_file()))
        for code, name, cap in c.execute("SELECT code, name, market_cap FROM codes"):
            caps[code] = (name, float(cap or 0))
        c.close()
    except Exception as e:
        print(f"[warn] pack codes 读取失败（跳过市值项）: {e}")

    conn = sqlite3.connect(str(LOCAL_DB))
    conn.row_factory = sqlite3.Row
    out: Dict[str, List[dict]] = {}
    n_raw = n_price = n_cap = 0
    for r in conn.execute("SELECT date, code, name, payload FROM mf_edge_daily "
                          "WHERE phase = 'accumulation' AND ready = 3"):
        n_raw += 1
        try:
            close = float((json.loads(r["payload"]) or {}).get("close") or 0)
        except Exception:
            close = 0.0
        if not close or close < MIN_PRICE:
            continue
        n_price += 1
        if use_cap:
            cap_yi = (caps.get(r["code"]) or ("", 0))[1]   # pack.codes 已是亿元
            if cap_yi < MIN_CAP:
                continue
            n_cap += 1
        out.setdefault(str(r["date"])[:10], []).append(
            {"code": r["code"], "name": r["name"] or "", "close": close})
    conn.close()
    print(f"[选股] 吸筹∧三绿 = {n_raw} 只次 ⇒ 价≥3 后 {n_price} ⇒ "
          f"市值≥50亿(当前代理)后 {n_cap if use_cap else n_price}")
    return out


def trading_days() -> List[str]:
    """交易日历：`backtest_prices` 的 `sh000300`（与评分检验同源）。"""
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices WHERE code = 'sh000300' "
                    "ORDER BY date ASC") or []
    return [str(r["date"])[:10] for r in rows]


class PxSource:
    """个股价格（date → (open, close)）：本地 pack 优先，未覆盖代码回退 DB（少量）。"""

    def __init__(self, codes: set):
        self.data: Dict[str, Dict[str, tuple]] = {}
        missing = set()
        try:
            from app import pack_source
            c = sqlite3.connect(str(pack_source.pack_file()))
            have = {r[0] for r in c.execute("SELECT code FROM codes")}
            for code in codes:
                if code not in have:
                    missing.add(code)
                    continue
                self.data[code] = {str(r[0])[:10]: (r[1], r[2]) for r in c.execute(
                    "SELECT date, open, close FROM klines WHERE code = ?", (code,))}
            c.close()
            print(f"[价格] pack 覆盖 {len(codes) - len(missing)}/{len(codes)} 只（零 egress）")
        except Exception as e:
            print(f"[价格] pack 不可用（{e}）⇒ 全部走 DB")
            missing = set(codes)
        if missing:
            codes_l = sorted(missing)
            for i in range(0, len(codes_l), 50):
                chunk = codes_l[i:i + 50]
                rows = db.fetch("SELECT code, date, open, close FROM backtest_prices "
                                "WHERE code = ANY(%s) ORDER BY code, date", (chunk,)) or []
                for r in rows:
                    self.data.setdefault(r["code"], {})[str(r["date"])[:10]] = (
                        r["open"], r["close"])
            print(f"[价格] DB 回退 {len(missing)} 只（小量 egress）")

    def get(self, date: str, code: str):
        return (self.data.get(code) or {}).get(date)


def pool_returns(hold: int) -> Dict[str, float]:
    """可取价池 开盘→开盘 T+hold 的**日等权均值**（SQL 窗口，返回 ~426 行）。"""
    rows = db.fetch("""
        SELECT d, AVG(r) AS r, COUNT(r) AS n FROM (
          SELECT date AS d, code,
                 LEAD(open, %s) OVER w / NULLIF(LEAD(open, 1) OVER w, 0) - 1 AS r
          FROM backtest_prices
          WHERE date >= %s AND LEFT(code, 1) IN ('0', '3', '6')
          WINDOW w AS (PARTITION BY code ORDER BY date)
        ) t WHERE r IS NOT NULL GROUP BY d ORDER BY d
    """, (hold + 1, "2024-10-01")) or []
    return {str(r["d"])[:10]: float(r["r"]) for r in rows}


def bootstrap(excess: List[float]) -> dict:
    """按判定日整日重抽样（B=10000，seed 冻结）→ 单侧 P 与分位数。"""
    rng = random.Random(BOOT_SEED)
    n = len(excess)
    means = []
    for _ in range(BOOT_N):
        s = 0.0
        for _ in range(n):
            s += excess[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return {"p_pos": sum(1 for m in means if m <= 0.0) / BOOT_N,
            "p_neg": sum(1 for m in means if m >= 0.0) / BOOT_N,
            "up95": _pctile(means, 0.95),
            "lo2.5": _pctile(means, 0.025), "hi97.5": _pctile(means, 0.975)}


def _pctile(sorted_vals: List[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def limit_up_blocked(code: str, name: str, open_entry: float, close_prev: float) -> bool:
    """入场开盘 ≥ 判定日收盘 ×(1 + 板幅 − 容差) ⇒ 一字/接近涨停买不到（项目唯一口径）。"""
    try:
        from app.backtest.engine import _limit_pct
        lim = _limit_pct(code, name)
    except Exception:
        lim = 0.10
    return open_entry >= close_prev * (1.0 + lim - LIMIT_TOL)


def run_hold(hold: int, cal: List[str], idx: Dict[str, int], by_day: Dict[str, list],
             pool: Dict[str, float], px: PxSource) -> dict:
    """单个持有期的完整计算（不判定）。★ 出场 = cal[i+hold+1]（持有 hold 个交易日）。"""
    due = [d for d in sorted(by_day) if d in idx and idx[d] + hold + 1 <= len(cal) - 1]
    excess, per_day = [], []
    obs = n_blocked = n_all = miss = 0
    for d in due:
        i = idx[d]
        entry_day, exit_day = cal[i + 1], cal[i + hold + 1]
        rets, rets_all = [], []
        for s in by_day[d]:
            oe = px.get(entry_day, s["code"])
            ox = px.get(exit_day, s["code"])
            if not oe or not ox:
                miss += 1
                continue
            r = ox[0] / oe[0] - 1.0
            rets_all.append(r)
            n_all += 1
            if limit_up_blocked(s["code"], s["name"], oe[0], s["close"]):
                n_blocked += 1
            else:
                rets.append(r)
        br = pool.get(entry_day)        # 基准 = entry→exit 的可取价池等权（同两端口）
        if not rets_all or br is None:
            continue
        r_sel = sum(rets) / len(rets) if rets else 0.0
        excess.append(r_sel - br)
        obs += len(rets)
        per_day.append((d, r_sel * 100, br * 100, (r_sel - br) * 100, len(rets), n_blocked))
    return {"hold": hold, "due": due, "excess": excess, "obs": obs, "miss": miss,
            "n_all": n_all, "n_blocked": n_blocked, "per_day": per_day}


def evaluate(res: dict, is_primary: bool, other_mean: float = None) -> dict:
    """按冻结判据出判定（GO/KILL/PARTIAL/INSUFFICIENT）。不重叠子样本 = 步长 hold。"""
    st = bootstrap(res["excess"]) if res["excess"] else {}
    mean = (sum(res["excess"]) / len(res["excess"]) * 100) if res["excess"] else 0.0
    days, obs = len(res["due"]), res["obs"]
    if days < MIN_DAYS or obs < MIN_OBS:
        return {"verdict": "INSUFFICIENT", "mean": mean, "days": days, "obs": obs,
                "detail": "样本不足（%d/%d 天，%d/%d 观测）" % (days, MIN_DAYS, obs, MIN_OBS)}
    step = res["hold"]                      # 不重叠：窗口长 = 持有期
    non = [res["excess"][k] for k in range(0, days, step)]
    non_mean = sum(non) / len(non) * 100 if non else 0.0
    same_dir = (non_mean == 0) or ((mean > 0) == (non_mean > 0))
    # α：主判据 0.05 ｜ T+10（§4.3）0.025 ｜ T+20（§11）0.05/3 —— 冻结口径，勿改
    go_p = GO_P_MAIN if is_primary else (GO_P_SEC20 if res["hold"] >= 20 else GO_P_SEC)
    kill_neg = KILL_PNEG if res["hold"] < 20 else GO_P_SEC20
    is_go = (mean >= GO_PT and st["p_pos"] <= go_p and same_dir
             and (other_mean is None or other_mean > 0))
    is_kill = (((mean <= 0 and st["up95"] <= KILL_CI_PT) or st["p_neg"] <= kill_neg)
               and same_dir)
    verdict = "PASS" if is_go else ("FAIL" if is_kill else "PARTIAL")
    return {"verdict": verdict, "word": {"PASS": "GO", "FAIL": "KILL"}.get(verdict, "PARTIAL"),
            "mean": mean, "days": days, "obs": obs, "p_pos": st["p_pos"],
            "p_neg": st["p_neg"], "up95": st["up95"] * 100,
            "ci": (st["lo2.5"] * 100, st["hi97.5"] * 100),
            "net_mean": mean - COST_PT, "non_mean": non_mean, "same_dir": same_dir,
            "step": step, "n_blocked": res["n_blocked"], "n_all": res["n_all"],
            "miss": res["miss"], "per_day": res["per_day"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cap", action="store_true",
                    help="鲁棒性：去掉市值代理项（只留 价≥3）")
    args = ap.parse_args()

    print("=== 主力节奏假设 · 首跑（判据 = PLAN_MAINFORCE_EDGE.md §4 v1.0 冻结）===")
    by_day = load_selections(use_cap=not args.no_cap)
    cal = trading_days()
    idx = {d: i for i, d in enumerate(cal)}
    n_sel = sum(len(v) for v in by_day.values())
    print(f"[选股] 判定日 {len(by_day)} 个 ｜ 入选 {n_sel} 只次")

    codes = {s["code"] for v in by_day.values() for s in v}
    px = PxSource(codes)
    results = {}
    for hold in HOLDS:
        pool = pool_returns(hold)
        res = run_hold(hold, cal, idx, by_day, pool, px)
        results[hold] = {"res": res, "pool": pool}
        print(f"[计算] T+{hold}: 到期日 {len(res['due'])} ｜ 观测 {res['obs']} ｜ "
              f"剔除一字板 {res['n_blocked']}/{res['n_all']} 只次 ｜ 两端缺价 {res['miss']}")

    ev10 = evaluate(results[10]["res"], False, other_mean=None)
    ev20 = evaluate(results[20]["res"], False,
                    other_mean=(ev10.get("mean") if "mean" in ev10 else None))
    ev5 = evaluate(results[5]["res"], True, other_mean=ev10.get("mean"))
    for tag, ev, alpha in (("T+5（主判据）", ev5, GO_P_MAIN),
                           ("T+10（次判据）", ev10, GO_P_SEC),
                           ("T+20（§11 扩展）", ev20, GO_P_SEC20)):
        print(f"\n[{tag}　α={alpha:.4f}]")
        if ev["verdict"] == "INSUFFICIENT":
            print(f"  INSUFFICIENT：{ev['detail']}（欠功效 ⇒ 不出数字）")
            continue
        print(f"  均值 {ev['mean']:+.3f}pt（扣 0.30% 成本净 {ev['net_mean']:+.3f}pt）｜ "
              f"判定日 {ev['days']} ｜ 观测 {ev['obs']}")
        print(f"  单侧 P(μ>0) = {ev['p_pos']:.4f} ｜ P(μ<0) = {ev['p_neg']:.4f} ｜ "
              f"95%上界 = {ev['up95']:+.3f}pt ｜ 95%CI = [{ev['ci'][0]:+.3f}, {ev['ci'][1]:+.3f}]pt")
        print(f"  不重叠子样本（步长 {ev['step']} 日）均值 = {ev['non_mean']:+.3f}pt"
              f"（方向{'一致' if ev['same_dir'] else '★不一致'}）｜ "
              f"剔除一字板 {ev['n_blocked']} 只次 / 入选 {ev['n_all']}")
        rate = ev["n_blocked"] / ev["n_all"] if ev["n_all"] else 0
        if rate > 0.30:
            print(f"  ★警告：一字板剔除率 {rate:.0%} > 30% —— 可建仓容量有限")
        print(f"  ⇒ 判定：**{ev['word']}**（{ev['verdict']}）")

    # 鲁棒性（描述性，不参与判定）——三个持有期同报
    print("\n[鲁棒性（描述性，不改判定）]")
    for tag, ev in (("T+5", ev5), ("T+10", ev10), ("T+20", ev20)):
        if ev["verdict"] == "INSUFFICIENT" or not ev.get("per_day"):
            print(f"  {tag}: （样本不足，跳过）")
            continue
        pd = sorted(ev["per_day"], key=lambda x: x[3])
        cut = max(1, len(pd) // 5)
        mid = pd[cut:-cut] or pd
        top = " ｜ ".join(f"{x[0]} {x[3]:+.2f}pt({x[4]}只)" for x in pd[-3:])
        print(f"  {tag}: 剔首尾 20% 日期后均值 = "
              f"{sum(x[3] for x in mid) / len(mid):+.3f}pt（{len(pd)}→{len(mid)} 日）"
              f" ｜ 超额最高 3 日: {top}")

    if args.json:
        # edge_verify 契约：末行 JSON 必须含 verdict（主判据）+ n（进度计数）
        print(json.dumps({"verdict": ev5["verdict"], "n": ev5.get("days", 0),
                          "verdict_t10": ev10["verdict"], "days": ev5.get("days"),
                          "obs": ev5.get("obs"), "mean_t5": ev5.get("mean"),
                          "mean_t10": ev10.get("mean")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
