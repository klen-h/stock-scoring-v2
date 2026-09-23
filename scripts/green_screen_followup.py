# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】检验「候选池大量飘绿 → 未来继续走弱」是否有统计依据（2026-09-23 用户提问）
================================================================================
用户问题：今天 top50 大量飘绿，直觉"这种情况未来几天还会继续走弱"—— 有没有根据？
榜单（top50）这时有什么意义？

★ 命题性质：这是**时间序列**命题（市场结构走弱 → 未来走弱），不是横截面选股。
  项目已有结论（2026-09-09「观望胜率反超买入」）是**横截面**的「1 日短期反转、
  5/10 日评分单调」，**不直接回答此问**。故专门检验。

口径（**预先定死，防事后挑格子**）：
  A. 主检验【全池宽度 → 沪深300】（样本 ≈3 年，结论可信）：
       每日 up_ratio = 当日上涨家数 / 有数据家数（close > 前一日 close）。
       按 up_ratio 的**最弱 20% 分位**分组：
         「深度飘绿」日（up_ratio ≤ P20） vs 「非飘绿」日（其余）
       看未来 T+1 / T+3 / T+5 沪深300 的收益均值与方向。
  B. 副检验【top50 飘绿 → 沪深300】（样本仅 ~25 个交易日，只作参考）：
       每日 top50（ranking_history）的当日涨跌 → green_ratio=下跌家数/50 →
       分组（≥0.6 深绿 vs <0.6）看未来沪深300。

判读（写死）：主检验两组未来收益**方向一致且幅度差明显** ⇒ 有预测力；≈0 或方向不一
⇒ 「飘绿→走弱」无统计依据（只是事后描述的错觉 / 或已被价格本身定价）。
局限：① backtest_prices 覆盖 ~800 只（沪深300 + ETF + 战法池），非全市场 ⇒ up_ratio
  是「强势候选池的宽度」，恰与「top50 飘绿」的语义一致；② 未来收益按**交易日**步进
  （跳过周末/节假日，项目纪律）。
================================================================================
"""

import os
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, "backend")
for _l in open("backend/.env", encoding="utf-8"):
    if _l.strip() and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.strip().split("=", 1)
        os.environ.setdefault(_k, _v)


def _find_pack():
    for p in ("backend/data/pack/backend-pack.db", "backend/pack/backend-pack.db",
              "data/pack/backend-pack.db", "backend/backend-pack.db"):
        if os.path.exists(p):
            return p
    return None


def _pct(v, n=2):
    return round(v * 100, n)


def load_pack(conn):
    """从 pack（本地 SQLite，零 Supabase egress）读：沪深300 日线 + 每日涨跌家数。"""
    # 沪深300（含 sh000300 或 000300 前缀）
    idx = {}
    for code in ("sh000300", "000300"):
        rows = conn.execute("SELECT date, close FROM klines WHERE code=? ORDER BY date",
                            (code,)).fetchall()
        if rows:
            idx = {r[0]: r[1] for r in rows}
            break
    # 每日涨跌家数（窗口函数 lag，一次算完）
    rows = conn.execute("""
        SELECT date,
               SUM(CASE WHEN close > prev THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN close < prev THEN 1 ELSE 0 END) AS down,
               COUNT(*) AS total
        FROM (
            SELECT code, date, close,
                   LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev
            FROM klines
        )
        WHERE prev IS NOT NULL
        GROUP BY date ORDER BY date
    """).fetchall()
    breadth = {r[0]: (r[1], r[2], r[3]) for r in rows}
    return idx, breadth


def fwd_ret(idx, dates, d, n):
    """交易日 d 之后第 n 个交易日的沪深300 收益（%）。缺则 None。"""
    idx_dates = [x for x in dates if x > d][:n]
    if len(idx_dates) < n:
        return None
    tgt = idx_dates[-1]
    if d not in idx or tgt not in idx:
        return None
    return (idx[tgt] - idx[d]) / idx[d]


def main():
    pack = _find_pack()
    if not pack:
        print("未找到本地 pack，退出")
        return
    conn = sqlite3.connect(pack)
    try:
        idx, breadth = load_pack(conn)
    finally:
        conn.close()
    if not idx or not breadth:
        print("pack 数据缺失，退出")
        return

    idx_dates = sorted(idx)
    out = []
    out.append(f"沪深300 样本: {len(idx_dates)} 个交易日 "
               f"({idx_dates[0]} ~ {idx_dates[-1]})")

    # ── 主检验：全池宽度 → 沪深300 ──
    dates = sorted(breadth)
    up_ratios = [breadth[d][0] / breadth[d][2] for d in dates]
    p20 = sorted(up_ratios)[int(len(up_ratios) * 0.20)]
    weak = [d for d in dates if breadth[d][0] / breadth[d][2] <= p20]
    rest = [d for d in dates if breadth[d][0] / breadth[d][2] > p20]
    out.append("")
    out.append(f"=== A. 主检验【候选池宽度 → 沪深300】===")
    out.append(f"深度飘绿阈值 = up_ratio ≤ {_pct(p20, 1)}%（最弱 20% 分位）")
    out.append(f"深度飘绿日 {len(weak)} 天 vs 其余 {len(rest)} 天")
    out.append(f"{'horizon':>8} | {'飘绿日均值':>10} {'n':>4} | {'其余日均值':>10} {'n':>4} | {'差(飘绿-其余)':>12}")
    for n in (1, 3, 5):
        w = [fwd_ret(idx, idx_dates, d, n) for d in weak]
        r = [fwd_ret(idx, idx_dates, d, n) for d in rest]
        w = [x for x in w if x is not None]
        r = [x for x in r if x is not None]
        if not w or not r:
            continue
        mw, mr = sum(w) / len(w), sum(r) / len(r)
        out.append(f"{'T+%d' % n:>8} | {_pct(mw):>9}% {len(w):>4} | "
                   f"{_pct(mr):>9}% {len(r):>4} | {_pct(mw - mr):>11}%")

    # ── 副检验：top50 飘绿 → 沪深300（样本少，只参考）──
    out.append("")
    out.append("=== B. 副检验【top50 飘绿 → 沪深300】（样本仅 ~25 交易日，只参考）===")
    try:
        from app.database import db
        rows = db.fetch("SELECT rank_date, code FROM ranking_history WHERE rank_pos <= 50 "
                        "ORDER BY rank_date, rank_pos")
    except Exception as e:
        out.append(f"ranking_history 读取失败（跳过副检验）: {e}")
        rows = []
    if rows:
        # 用 pack 里该股当日涨跌（close vs 前一日 close）—— 与主检验同口径
        conn = sqlite3.connect(pack)
        try:
            closes = {}
            need = sorted({r["code"] for r in rows})
            for code in need:
                for r in conn.execute("SELECT date, close FROM klines WHERE code=? ORDER BY date",
                                      (code,)):
                    closes[(code, r[0])] = r[1]
        finally:
            conn.close()
        by_day = {}
        for r in rows:
            c, d = r["code"], r["rank_date"]
            # 前一日：需交易日历 —— 用该股自己的日期序列找 d 的前一根
            by_day.setdefault(d, []).append(c)
        # 简化：用 breadth 里的日期序做前一日
        alldates = sorted({d for (c, d) in closes})
        green = {}
        for d, codes in by_day.items():
            prev = None
            for x in alldates:
                if x >= d:
                    break
                prev = x
            if prev is None:
                continue
            up = down = 0
            for c in codes:
                cur, pre = closes.get((c, d)), closes.get((c, prev))
                if cur is None or pre is None:
                    continue
                if cur > pre:
                    up += 1
                elif cur < pre:
                    down += 1
            tot = up + down
            if tot:
                green[d] = down / tot
        vals = sorted(green.values())
        med = vals[len(vals) // 2]
        deep = [d for d in green if green[d] >= med]
        other = [d for d in green if green[d] < med]
        out.append(f"top50 green_ratio 分布: 最低 {int(vals[0]*100)}% / "
                   f"中位 {int(med*100)}% / 最高 {int(vals[-1]*100)}%")
        out.append("最近 8 个交易日的 green_ratio（飘绿占比）:")
        for d in sorted(green)[-8:]:
            out.append(f"  {d}: {int(green[d]*100)}%")
        out.append(f"按中位数分组：较深绿（≥{int(med*100)}%）{len(deep)} 天 vs "
                   f"较浅 {len(other)} 天")
        out.append(f"{'horizon':>8} | {'深绿日均值':>10} {'n':>4} | {'浅绿日均值':>10} {'n':>4}")
        for n in (1, 3, 5):
            w = [fwd_ret(idx, idx_dates, d, n) for d in deep]
            r = [fwd_ret(idx, idx_dates, d, n) for d in other]
            w = [x for x in w if x is not None]
            r = [x for x in r if x is not None]
            if not w or not r:
                continue
            out.append(f"{'T+%d' % n:>8} | {_pct(sum(w)/len(w)):>9}% {len(w):>4} | "
                       f"{_pct(sum(r)/len(r)):>9}% {len(r):>4}")

    print("\n".join(out))


if __name__ == "__main__":
    main()
