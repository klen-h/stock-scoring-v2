#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】板块联动筛选的预登记检验（判据冻结于 PLAN_SECTOR_LINKAGE.md v1.0）
================================================================================
回答两个问题（顺序不可颠倒，且**不得用结果反推参数**）：
  A. 判据层：行业 ret5 前 30% 且 > 0 ⇒ 该行业未来 5 日是否跑赢「同日全部行业等权」？
     （数据：本机 data/zzshare_daily.db，21 年、全市场；零 egress）
  B. 榜单层：把同一判据套到既有 31 个评分快照日上，过滤版 Top50 的 T+5 超额
     是否优于原版 Top50？（数据：PG ranking_history + backtest_prices）

【预登记判定（与 PLAN_SECTOR_LINKAGE.md §5.3 逐字对应）】
  · GO：过滤版超额 − 原版超额的 T+5 配对 bootstrap 95%CI **下界 > 0** 且点估计 ≥ **+1.0pt**，
        且 T+10 同号 ⇒ PASS
  · KILL：95%CI **上界 < 0** ⇒ FAIL
  · 样本闸：A ≥ 250 个交易日；B ≥ 20 个到期快照日。不足 ⇒ INSUFFICIENT。
  · 其余 ⇒ PARTIAL（有方向但不达标）。

⚠️ 参数一律从 `app.sector_strength` import（单一事实源）——改那边的常量会让本预登记失效，
   必须同时在 PLAN 文档新开一节并注明日期。
⚠️ 本脚本**只读**：不写任何生产表。
================================================================================
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)

from app.sector_strength import (RET_WINDOW, TOP_PCT, MIN_MEMBERS,  # noqa: E402
                                 BULL_RATIO, BULL_DAYS, MIN_INDUSTRIES)

MIN_DAYS_A = 250          # A 的样本闸：交易日
MIN_DAYS_B = 20           # B 的样本闸：到期快照日
B_BOOT = 10000
SEED = 20261019
ZZ_DB = os.path.join(ROOT, "data", "zzshare_daily.db")


def _code6(x) -> str:
    """统一成 6 位数字码：本地库是 `000001.SZ`，PG 的 ETF 是 `sz159825`。

    ★ 2026-10-19 实测踩到：不做归一化时 A 段 join 命中 0、B 段 industry 全为 None
    （⇒ hit/miss 只剩一侧 ⇒ 样本 0）。凡"按 code 关联行业映射"都要先过这里。
    """
    import re as _re
    digits = _re.sub(r"\D", "", str(x or ""))
    return digits[-6:] if len(digits) >= 6 else digits


def _cum(seq: List[float]) -> Optional[float]:
    v = 1.0
    for x in seq:
        v *= (1 + x / 100.0)
    return (v - 1) * 100.0


def _rank_strong(ret5: Dict[str, float], member: Dict[str, int]) -> List[str]:
    rated = [(k, v) for k, v in ret5.items()
             if v is not None and member.get(k, 0) >= MIN_MEMBERS]
    rated.sort(key=lambda x: -x[1])
    k = max(1, int(round(len(rated) * TOP_PCT + 0.4999)))
    return [name for name, v in rated[:k] if v > 0]


def _bootstrap(vals: List[float]) -> Tuple[float, float, float]:
    """按样本（日期聚簇）有放回重采样：返回 (mean, lo95, hi95)。"""
    if not vals:
        return 0.0, 0.0, 0.0
    rnd = random.Random(SEED)
    n = len(vals)
    means = []
    for _ in range(B_BOOT):
        s = 0.0
        for _ in range(n):
            s += vals[rnd.randrange(n)]
        means.append(s / n)
    means.sort()
    return (sum(vals) / n, means[int(0.025 * B_BOOT)],
            means[int(0.975 * B_BOOT) - 1])


# ── A. 21 年行业指数预测力（本机库）──────────────────────────────────────────
def _eval_a(by_date: Dict[str, Dict[str, float]],
            cnt: Dict[Tuple[str, str], int]) -> dict:
    """行业强度 → 未来 5 日超额（同日全部行业等权）。供本机库/PG 两种价源复用。"""
    dates = sorted(by_date)
    idx = {d: i for i, d in enumerate(dates)}
    events: List[float] = []
    for d in dates:
        i = idx[d]
        if i < RET_WINDOW or i + RET_WINDOW >= len(dates):
            continue
        win = dates[i - RET_WINDOW + 1:i + 1]
        ret5 = {}
        for ind in by_date[d]:
            seq = [by_date[x].get(ind) for x in win]
            if any(v is None for v in seq):
                continue          # 窗口内缺日 ⇒ 不参评（缺失 ≠ 0）
            ret5[ind] = _cum(seq)
        member = {ind: cnt.get((d, ind), 0) for ind in by_date[d]}
        strong = _rank_strong(ret5, member)
        if not strong:
            continue
        fwd_dates = dates[i + 1:i + 1 + RET_WINDOW]
        fwd = {}
        for ind in by_date[d]:
            seq = [by_date[x].get(ind) for x in fwd_dates]
            if any(x is None for x in seq):
                continue
            fwd[ind] = _cum(seq)
        if len(fwd) < MIN_INDUSTRIES:
            continue
        base = sum(fwd.values()) / len(fwd)
        vals = [fwd[ind] - base for ind in strong if ind in fwd]
        if vals:
            events.append(sum(vals) / len(vals))

    mean, lo, hi = _bootstrap(events)
    n = len(events)
    if n < MIN_DAYS_A:
        verdict = "INSUFFICIENT"
    elif mean >= 0.50 and lo > 0:
        verdict = "PASS"
    elif hi < 0:
        verdict = "FAIL"
    else:
        verdict = "PARTIAL"
    return {"ok": True, "verdict": verdict, "n": n, "n_dates": len(dates),
            "mean_pp": round(mean, 3), "ci": [round(lo, 3), round(hi, 3)],
            "window": [dates[0] if dates else "", dates[-1] if dates else ""],
            "detail": (f"行业强度(前{int(TOP_PCT*100)}%)未来{RET_WINDOW}日超额 "
                       f"{mean:+.2f}pp（95%CI {lo:+.2f}~{hi:+.2f}，{n} 个交易日 / "
                       f"共 {len(dates)} 个有效日）")}


def _part_a_sqlite(start: str) -> Optional[dict]:
    """本机 21 年全市场库（优先；含 ST/科创/北交，零 egress）。"""
    if not os.path.exists(ZZ_DB):
        return None
    from app.industry_map import get_industry_map
    mapping = get_industry_map()
    if not mapping:
        return None
    conn = sqlite3.connect(ZZ_DB)
    try:
        conn.execute("CREATE TEMP TABLE m(code TEXT PRIMARY KEY, ind TEXT)")
        conn.executemany("INSERT OR REPLACE INTO m VALUES (?,?)",
                         [(str(c), str(i)) for c, i in mapping.items()])
        rows = conn.execute(
            "SELECT d.date, m.ind, AVG(d.pct_chg), COUNT(*) "
            "FROM daily d JOIN m ON m.code = substr(d.code, 1, 6) "
            "WHERE d.date >= ? AND d.pct_chg IS NOT NULL "
            "GROUP BY d.date, m.ind", (start,)).fetchall()
    except Exception as e:
        print(f"  [A] 本机库查询失败（回退 PG）: {str(e)[:80]}")
        return None
    finally:
        conn.close()
    by_date: Dict[str, Dict[str, float]] = defaultdict(dict)
    cnt: Dict[Tuple[str, str], int] = {}
    for d, ind, chg, n in rows:
        d = str(d)[:10]
        by_date[d][str(ind)] = float(chg or 0.0)
        cnt[(d, str(ind))] = int(n)
    out = _eval_a(by_date, cnt)
    out["source"] = "zzshare_daily（本机 21 年全市场）"
    return out


def _part_a_pg(start: str) -> dict:
    """PG 回退（Actions/Render 无本机库时用）：`backtest_prices` + `stock_industry`。

    用一条窗口函数 SQL 做「逐股日收益 → 行业均值」，只回 3.5 万行（≈1MB），
    不把 70 万行日线拉回本地（egress 纪律）。
    """
    from app.database import db
    try:
        rows = db.fetch("""
            SELECT date, ind, AVG(chg) AS chg, COUNT(*) AS n FROM (
                SELECT p.date AS date, s.main_industry AS ind,
                       (p.close / NULLIF(LAG(p.close) OVER
                          (PARTITION BY p.code ORDER BY p.date), 0) - 1) * 100 AS chg
                FROM backtest_prices p
                JOIN stock_industry s ON s.code = RIGHT(p.code, 6)
                WHERE p.date >= %s AND p.close > 0
            ) t WHERE chg IS NOT NULL
            GROUP BY date, ind
        """, (start,)) or []
    except Exception as e:
        return {"ok": False, "verdict": "INSUFFICIENT", "n": 0,
                "detail": f"A 段不可用（本机库缺失且 PG 回退失败: {str(e)[:60]}）",
                "source": "none"}
    by_date: Dict[str, Dict[str, float]] = defaultdict(dict)
    cnt: Dict[Tuple[str, str], int] = {}
    for r in rows:
        d = str(r.get("date"))[:10]
        by_date[d][str(r.get("ind"))] = float(r.get("chg") or 0.0)
        cnt[(d, str(r.get("ind")))] = int(r.get("n") or 0)
    out = _eval_a(by_date, cnt)
    out["source"] = "backtest_prices（PG 池 989 只）"
    return out


def part_a(start: str) -> dict:
    r = _part_a_sqlite(start)
    if r is not None:
        return r
    return _part_a_pg(start)


# ── B. 快照 A/B（PG）──────────────────────────────────────────────────────────
def _pool_bars(start: str) -> Dict[str, List[Tuple[str, float]]]:
    """按 code 分块读（Supabase 大查询会 SSL 断连 —— 项目既有范式）。"""
    from app.database import db
    out: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    try:
        codes = [str(r["code"]) for r in
                 (db.fetch("SELECT DISTINCT code FROM backtest_prices") or [])]
    except Exception as e:
        print(f"  [B] 代码表读取失败: {str(e)[:60]}")
        return out
    for i in range(0, len(codes), 120):
        chunk = codes[i:i + 120]
        ph = ",".join(["%s"] * len(chunk))
        rows = []
        for _attempt in range(2):
            try:
                rows = db.fetch(
                    f"SELECT code, date, close FROM backtest_prices "
                    f"WHERE code IN ({ph}) AND date >= %s ORDER BY code, date",
                    (*chunk, start)) or []
                break
            except Exception as e:
                print(f"  [B] 分块读失败（重试）: {str(e)[:60]}")
                try:
                    db._reset_pg_conn()
                except Exception:
                    pass
                rows = []
        for r in rows:
            c = r.get("close")
            if c and float(c) > 0:
                out[str(r["code"])].append((str(r["date"])[:10], float(c)))
    return out


def part_b() -> dict:
    from app.database import db
    snaps = db.fetch(
        "SELECT rank_date, COUNT(*) AS n FROM ranking_history "
        "GROUP BY rank_date ORDER BY rank_date ASC") or []
    days = [str(r["rank_date"])[:10] for r in snaps if int(r.get("n") or 0) >= 30]
    if len(days) < MIN_DAYS_B:
        return {"ok": True, "verdict": "INSUFFICIENT", "n": len(days),
                "detail": f"快照日 {len(days)} < {MIN_DAYS_B}，等样本"}
    from datetime import datetime, timedelta
    start = (datetime.strptime(days[0], "%Y-%m-%d") - timedelta(days=45)).strftime("%Y-%m-%d")
    bars = _pool_bars(start)
    calendar = sorted({d for seq in bars.values() for d, _ in seq})
    cidx = {d: i for i, d in enumerate(calendar)}
    mapping = None
    try:
        from app.industry_map import get_industry_map
        mapping = get_industry_map()
    except Exception:
        mapping = {}

    rows_all = db.fetch(
        "SELECT rank_date, code, price FROM ranking_history WHERE rank_date >= %s "
        "ORDER BY rank_date, rank_pos", (start,)) or []
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for r in rows_all:
        by_day[str(r["rank_date"])[:10]].append(
            {"code": str(r["code"]), "price": r.get("price")})

    # ── 一次性结构（★ 2026-10-19 修：原实现只在当天键上累加 ind_chg，
    #    却拿 win_dates 的**其它 4 天**去查 ⇒ 永远为空 ⇒ ret5/strong 恒空、样本 0）──
    ind_chg: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    market_by_date: Dict[str, List[float]] = defaultdict(list)
    idx_by_code: Dict[str, Dict[str, int]] = {}
    for code, seq in bars.items():
        ind = (mapping or {}).get(_code6(code))
        pos_map = {}
        prev = None
        for k, (dt, cl) in enumerate(seq):
            pos_map[dt] = k
            if prev:
                pct = (cl / prev - 1) * 100
                market_by_date[dt].append(pct)
                if ind:
                    ind_chg[(ind, dt)].append(pct)
            prev = cl
        idx_by_code[code] = pos_map

    def _fwd(code: str, d: str, horizon: int = 5) -> Optional[float]:
        seq = bars.get(code) or []
        pos = (idx_by_code.get(code) or {}).get(d)
        if pos is None or pos + horizon >= len(seq):
            return None
        return (seq[pos + horizon][1] / seq[pos][1] - 1) * 100

    deltas, kept_n, total_n = [], 0, 0
    for d in days:
        if d not in cidx:
            continue
        i = cidx[d]
        win_dates = calendar[max(0, i - RET_WINDOW + 1):i + 1]
        if len(win_dates) < RET_WINDOW:
            continue

        market_chg = market_by_date.get(d) or []
        if len(market_chg) < 300:
            continue
        up_ratio = sum(1 for x in market_chg if x > 0) / len(market_chg)

        ret5: Dict[str, float] = {}
        member: Dict[str, int] = {}
        for ind in {k[0] for k in ind_chg}:
            seq = []
            ok = True
            for wd in win_dates:
                vals = ind_chg.get((ind, wd)) or []
                if not vals:
                    ok = False
                    break
                seq.append(sum(vals) / len(vals))
            if ok:
                ret5[ind] = _cum(seq)
                member[ind] = len(ind_chg.get((ind, d)) or [])
        strong = set(_rank_strong(ret5, member))
        # 普涨守卫（**与冻结判据一致**：连续 BULL_DAYS 日上涨占比 ≥ BULL_RATIO ⇒ 过滤失效）
        guard_dates = calendar[max(0, i - BULL_DAYS + 1):i + 1]
        guard_ratios = []
        for gd in guard_dates:
            vals = market_by_date.get(gd) or []
            guard_ratios.append(sum(1 for x in vals if x > 0) / len(vals) if vals else 0.0)
        if (len(guard_ratios) == BULL_DAYS
                and all(r >= BULL_RATIO for r in guard_ratios)):
            strong = set()

        # 基准：全池当日→T+5 等权
        horizon = 5
        fwd_all = []
        for code in bars:
            r = _fwd(code, d, horizon)
            if r is not None:
                fwd_all.append(r)
        if len(fwd_all) < 300:
            continue
        base = sum(fwd_all) / len(fwd_all)

        day_rows = by_day.get(d) or []
        if not day_rows:
            continue
        hit, miss = [], []
        for x in day_rows:
            ret = _fwd(x["code"], d, horizon)
            if ret is None:
                continue
            (hit if (not strong or (mapping.get(_code6(x["code"])) in strong))
             else miss).append(ret)
        if not hit or not miss:
            if os.environ.get("SL_DEBUG"):
                print(f"  [B] {d} skip: market={len(market_chg)} strong={len(strong)} "
                      f"hit={len(hit)} miss={len(miss)} rated={len(ret5)}")
            continue
        ex_hit = sum(hit) / len(hit) - base
        ex_all = (sum(hit) + sum(miss)) / (len(hit) + len(miss)) - base
        deltas.append(ex_hit - ex_all)
        kept_n += len(hit)
        total_n += len(hit) + len(miss)

    mean, lo, hi = _bootstrap(deltas)
    n = len(deltas)
    if n < MIN_DAYS_B:
        verdict = "INSUFFICIENT"
    elif mean >= 1.0 and lo > 0:
        verdict = "PASS"
    elif hi < 0:
        verdict = "FAIL"
    else:
        verdict = "PARTIAL"
    return {"ok": True, "verdict": verdict, "n": n,
            "mean_pp": round(mean, 3), "ci": [round(lo, 3), round(hi, 3)],
            "kept": kept_n, "total": total_n,
            "detail": (f"过滤版−原版 T+5 超额 {mean:+.2f}pt"
                       f"（95%CI {lo:+.2f}~{hi:+.2f}，{n} 个快照日；"
                       f"留存 {kept_n}/{total_n}）")}


def main() -> int:
    ap = argparse.ArgumentParser(description="板块联动筛选 · 预登记检验")
    ap.add_argument("--start", default="2018-01-01",
                    help="A 段起始日（全量 2005-01-01 更慢但样本更足）")
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论")
    args = ap.parse_args()

    a = part_a(args.start)
    try:
        b = part_b()
    except Exception as e:
        b = {"ok": False, "verdict": "INSUFFICIENT", "n": 0,
             "detail": f"B 段运行失败: {str(e)[:80]}"}

    print("=== A. 行业强度预测力（本机 21 年库）===")
    print(f"  {a.get('detail')}")
    print(f"  判定：{a.get('verdict')}（样本闸 {MIN_DAYS_A}）")
    print("\n=== B. 快照 A/B（过滤版 Top50 vs 原版 Top50）===")
    print(f"  {b.get('detail')}")
    print(f"  判定：{b.get('verdict')}（样本闸 {MIN_DAYS_B}）")

    vs = [a.get("verdict"), b.get("verdict")]
    if "FAIL" in vs:
        verdict = "FAIL"
    elif all(v == "PASS" for v in vs):
        verdict = "PASS"
    elif all(v == "INSUFFICIENT" for v in vs):
        verdict = "INSUFFICIENT"
    else:
        verdict = "PARTIAL"
    print(f"\n★ 综合判定：{verdict}")
    print("  （GO 需 A、B 都 PASS 且用户拍板；KILL 则 SECTOR_LINKAGE_MODE 定案 off）")

    if args.json:
        print(json.dumps({"script": "sector_linkage_edge_check", "verdict": verdict,
                          "n": b.get("n") or 0, "need": MIN_DAYS_B,
                          "unit": "到期快照日",
                          "detail": f"A:{a.get('verdict')} {a.get('detail')}；"
                                    f"B:{b.get('verdict')} {b.get('detail')}"},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
