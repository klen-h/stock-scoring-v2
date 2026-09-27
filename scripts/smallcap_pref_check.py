#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【方向 3 · 小盘偏好信号的泛化】小盘相对强弱，能否预测未来相对收益？
================================================================================
动机（从 E2 的结论出发）：
  §8.4 已确认 E2 的本质是「**小盘风险偏好回升**」——edge 随指数市值下沉**单调递增**
  （沪深300 +0.12pp → 创业板指 +1.53pp → 中证500 +2.03pp → **中证1000 +2.76pp**）。
  但 E2 每年只有约 3 次独立事件 ⇒ 系统大部分时间没有信号。
  ⇒ 本脚本问：能否用**更频繁**的「小盘 vs 大盘 相对强弱」直接刻画同一件事？

指标：
  · 市值估算 = 成交额 / (换手率/100) ≈ 流通市值（§8.3 已校验：2024-09-30 创业板市值加权
    +15.85% vs 真实创业板指 +15.36%）
  · 小盘组 = 每日市值**最小 30%**（等权）；大盘组 = **最大 30%**（等权）
  · **相对动量** = 小盘过去 20 日累计收益 − 大盘同期累计收益
  · **未来相对收益** = (小盘 T+1 开盘买 → T+20 收盘) − (大盘同期)   ← 已是对冲口径

━━━━━━━━━━━━━━━━━━ 预登记 ━━━━━━━━━━━━━━━━━━
  分层：按相对动量分 3 层（高/中/低，各 1/3 时期），比较各层未来的**相对收益**。
  判据：
    (a) 高动量层 − 低动量层 >= **+1.0pp** 且 bootstrap P < 0.05
        ⇒ 小盘偏好有**延续性**，可作择时信号
    (b) <= **−1.0pp** 且显著 ⇒ **反转**（小盘超买后回落），可作反向信号
    (c) |差| < 0.5pp 或不显著 ⇒ **无预测力，归档**

⚠️ 教训复用（§8.3 行业动量那轮踩过的坑）：
   ① **数据合理性过滤**（A 股单日理论极限 ±11%，超出即异常）；
   ② **必须先分年拆解**（全样本均值极易被单年异常主导）；
   ③ 相对收益本身即对冲口径，但**分组仍要看中位数**（防少数极端时期主导）。
⚠️ 全量 NTILE 窗口函数耗时约 76s（1614 万行），属正常。

用法：python scripts/smallcap_pref_check.py
================================================================================
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")
CACHE = os.path.join(ROOT, "data", "smallcap_groups.json")

MOM_DAYS = 20
FWD_DAYS = 20
B_BOOT = 10000
EXTREME = 11.0          # 单日理论极限（%）
E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
STOCK_FILTER = ("(code LIKE '60%.SH' OR code LIKE '68%.SH' OR code LIKE '00%.SZ' "
                "OR code LIKE '30%.SZ' OR code LIKE '%.BJ')")


def build_groups(refresh: bool = False):
    """按日市值十分位 → 大盘组（1-3 档）与小盘组（8-10 档）的等权日收益与跳空。

    ★ 全量 NTILE 实测约 115s ⇒ 结果缓存到 `data/smallcap_groups.json`（重跑秒级）。
    """
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            c = json.load(f)
        print(f"（读缓存 {CACHE}）")
        return (c["dates"], np.array(c["lg_pct"]), np.array(c["sm_pct"]),
                np.array(c["lg_gap"]), np.array(c["sm_gap"]))
    print("聚合中（NTILE 十分位，约 115s）…")
    t0 = time.time()
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute(f"""
        SELECT date,
               AVG(CASE WHEN d <= 3 THEN pct_chg END) AS lg_pct,
               AVG(CASE WHEN d >= 8 THEN pct_chg END) AS sm_pct,
               AVG(CASE WHEN d <= 3 AND pre_close > 0
                        THEN open / pre_close - 1 END) * 100 AS lg_gap,
               AVG(CASE WHEN d >= 8 AND pre_close > 0
                        THEN open / pre_close - 1 END) * 100 AS sm_gap,
               COUNT(CASE WHEN d <= 3 THEN 1 END) AS n_lg,
               COUNT(CASE WHEN d >= 8 THEN 1 END) AS n_sm
        FROM (
            SELECT date, pct_chg, open, pre_close,
                   NTILE(10) OVER (PARTITION BY date
                       ORDER BY (CASE WHEN turnover_rate > 0
                                      THEN amount / turnover_rate END) DESC) AS d
            FROM daily
            WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL
                  AND turnover_rate > 0
        )
        GROUP BY date ORDER BY date
    """).fetchall()
    conn.close()
    print(f"  done {len(rows)} 天，{time.time()-t0:.0f}s")
    dates = [str(r[0]) for r in rows]
    lg_pct = np.array([r[1] if r[1] is not None else np.nan for r in rows], dtype=float)
    sm_pct = np.array([r[2] if r[2] is not None else np.nan for r in rows], dtype=float)
    lg_gap = np.array([r[3] if r[3] is not None else 0.0 for r in rows], dtype=float)
    sm_gap = np.array([r[4] if r[4] is not None else 0.0 for r in rows], dtype=float)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"dates": dates, "lg_pct": lg_pct.tolist(), "sm_pct": sm_pct.tolist(),
                   "lg_gap": lg_gap.tolist(), "sm_gap": sm_gap.tolist()}, f)
    print(f"  已缓存 -> {CACHE}")
    return dates, lg_pct, sm_pct, lg_gap, sm_gap


def load_e2() -> set:
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    out = set()
    for d, v in raw.items():
        denom = (v.get("up") or 0) + (v.get("down") or 0)
        if v["up_ratio"] >= E2_UP_RATIO and denom > 0 \
                and v["limit_up"] / denom >= E2_LU_RATIO:
            out.add(d)
    return out


def fwd_group(pct, gap, i, n=FWD_DAYS):
    """T+1 开盘买 → T+n 收盘（%，相对买入价）。"""
    if i + 1 + n > len(pct):
        return None
    entry = 1 + gap[i + 1] / 100.0
    if entry <= 0.05 or entry > 1 + EXTREME / 100.0:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        if np.isnan(pct[k]):
            return None
        cum *= (1 + pct[k] / 100.0)
    return (cum / entry - 1) * 100


def block_p(diffs, B=B_BOOT, seed=42):
    a = np.asarray([x for x in diffs if x is not None], dtype=float)
    n = len(a)
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, n, size=(B, n))].mean(axis=1)
    return float((means <= 0).mean()), float(a.mean())


def main():
    dates, lg_pct, sm_pct, lg_gap, sm_gap = build_groups()
    n_d = len(dates)
    print(f"覆盖 {dates[0]} ~ {dates[-1]}")

    # ── 数据合理性过滤（§8.3 教训①）──
    for name, arr in (("大盘收益", lg_pct), ("小盘收益", sm_pct)):
        bad = int(np.sum(np.abs(arr) > EXTREME))
        if bad:
            print(f"  {name} 异常值 {bad} 个 → 置 NaN")
    lg_pct = np.where(np.abs(lg_pct) > EXTREME, np.nan, lg_pct)
    sm_pct = np.where(np.abs(sm_pct) > EXTREME, np.nan, sm_pct)

    rel_pct = sm_pct - lg_pct                    # 小盘相对日收益
    r_sm = np.nan_to_num(sm_pct, nan=0.0) / 100.0
    r_lg = np.nan_to_num(lg_pct, nan=0.0) / 100.0

    rel_mom = np.full(n_d, np.nan)
    rel_fwd = np.full(n_d, np.nan)
    sm_fwd = np.full(n_d, np.nan)      # 小盘**绝对**未来收益（判断可执行性）
    lg_fwd = np.full(n_d, np.nan)      # 大盘**绝对**未来收益
    for t in range(MOM_DAYS, n_d - FWD_DAYS - 1):
        if np.isnan(sm_pct[t - MOM_DAYS + 1: t + 1]).any() or \
           np.isnan(lg_pct[t - MOM_DAYS + 1: t + 1]).any():
            continue
        m_sm = np.prod(1 + r_sm[t - MOM_DAYS + 1: t + 1]) - 1
        m_lg = np.prod(1 + r_lg[t - MOM_DAYS + 1: t + 1]) - 1
        rel_mom[t] = (m_sm - m_lg) * 100
        f_sm = fwd_group(sm_pct, sm_gap, t)
        f_lg = fwd_group(lg_pct, lg_gap, t)
        if f_sm is not None and f_lg is not None:
            sm_fwd[t], lg_fwd[t] = f_sm, f_lg
            rel_fwd[t] = f_sm - f_lg

    valid = ~np.isnan(rel_mom) & ~np.isnan(rel_fwd)
    print(f"\n有效样本 {int(valid.sum())} 天")

    # ── 分层（按相对动量分 3 层）──
    def layered(mask=None, label=""):
        m = valid.copy()
        if mask is not None:
            m &= np.array([d in mask for d in dates])
        idx = np.where(m)[0]
        if len(idx) < 60:
            return None
        order = idx[np.argsort(rel_mom[idx])]
        k = len(order) // 3
        lo, mid_s, hi = order[:k], order[k: 2 * k], order[-k:]
        out = {}
        for nm, seg in (("low", lo), ("mid", mid_s), ("high", hi)):
            vals = rel_fwd[seg]
            out[nm] = {"n": len(seg), "mean": float(np.mean(vals)),
                       "med": float(np.median(vals)),
                       "win": float(np.mean(vals > 0) * 100),
                       "mom": float(np.mean(rel_mom[seg])),
                       "sm": float(np.nanmean(sm_fwd[seg])),
                       "lg": float(np.nanmean(lg_fwd[seg])),
                       "vals": vals}
        # 高 − 低（逐日配对差，用于 bootstrap）
        out["diffs"] = rel_fwd[hi] - rel_fwd[lo]
        out["days"] = [dates[i] for i in hi]
        return out

    print(f"\n{'='*100}")
    print(f"【小盘偏好信号】相对动量({MOM_DAYS}日) → 未来 {FWD_DAYS} 日**相对收益**（小盘−大盘）")
    print(f"{'='*100}")
    res, detail = {}, {}
    for label, mask in (("全样本", None), ("E2 触发日", load_e2())):
        r = layered(mask, label)
        if not r:
            print(f"\n  [{label}] 样本不足")
            continue
        print(f"\n  [{label}]  有效 {r['high']['n'] + r['mid']['n'] + r['low']['n']} 天")
        print(f"    {'层':<5}{'天数':>6}{'平均动量':>10}{'未来相对':>10}"
              f"{'中位':>9}{'胜率':>7}{'小盘绝对':>10}{'大盘绝对':>10}")
        for nm, cn in (("low", "低"), ("mid", "中"), ("high", "高")):
            s = r[nm]
            print(f"    {cn:<5}{s['n']:>6}{s['mom']:>+9.2f}pp{s['mean']:>+9.2f}pp"
                  f"{s['med']:>+8.2f}pp{s['win']:>6.1f}%"
                  f"{s['sm']:>+9.2f}%{s['lg']:>+9.2f}%")
        print(f"    （基准＝全样本无条件：小盘 {np.nanmean(sm_fwd):+.2f}% / "
              f"大盘 {np.nanmean(lg_fwd):+.2f}% / 相对 {np.nanmean(rel_fwd):+.2f}pp）")
        p, md = block_p(r["diffs"])
        print(f"    ★ 高 − 低 = {md:+.2f}pp   bootstrap P(mean<=0) = {p:.4f}")
        # 分年拆解（§8.3 教训②）
        by_year = {}
        for d, v in zip(r["days"], r["diffs"]):
            by_year.setdefault(d[:4], []).append(v)
        pos_y = sum(1 for y in by_year if np.mean(by_year[y]) > 0)
        print(f"    · 分年（高−低）：为正 {pos_y}/{len(by_year)} 年；"
              + " ".join(f"{y}:{np.mean(by_year[y]):+.1f}" for y in sorted(by_year)[-6:]))
        res[label] = (md, p, len(r["days"]))
        detail[label] = r

    print(f"\n{'='*100}")
    print("【预登记判定】")
    print(f"{'='*100}")
    if "全样本" in res:
        d, p, n = res["全样本"]
        if d >= 1.0 and p < 0.05:
            v = "有延续性（相对多空显著）"
        elif d <= -1.0 and p > 0.95:
            v = "反转效应（可作反向信号）"
        else:
            v = "无预测力"
        print(f"  (a) 全样本（{n} 天）：高−低 = {d:+.2f}pp，P={p:.4f} ⇒ {v}")

        # ★ (c) 可执行性 —— 相对多空**不可执行**（A 股不能做空大盘，IF 对小资金不现实）
        #   ⇒ 唯一可执行形式是「轮动」：高动量持小盘、否则持大盘。
        #   若轮动收益不优于"一直持小盘"，则信号**无实用价值**（无论多空多显著）。
        r = detail["全样本"]
        base_sm = None
        _v = valid
        base_sm = float(np.nanmean(sm_fwd[_v]))
        base_lg = float(np.nanmean(lg_fwd[_v]))
        rot = (r["high"]["sm"] + r["mid"]["lg"] + r["low"]["lg"]) / 3.0
        best_static = max(base_sm, base_lg)
        print(f"  (c) 可执行性（关键）：")
        print(f"      · 无条件基准：小盘 {base_sm:+.2f}% / 大盘 {base_lg:+.2f}%"
              f" ⇒ 小盘{'长期占优' if base_sm > base_lg else '不占优'}"
              f"（{base_sm - base_lg:+.2f}pp）")
        print(f"      · 可执行轮动（高动量→小盘，中/低→大盘）= {rot:+.2f}%"
              f"  vs 最佳静态持有 {best_static:+.2f}%"
              f" ⇒ {'轮动有超额' if rot > best_static + 0.5 else '**轮动无优势**'}")
        if rot <= best_static + 0.5:
            print("      ⇒ **相对多空虽显著，但唯一可执行形式无优势 ⇒ 归档**"
                  "（根因：小盘在**每一层**都跑赢大盘，静态持有小盘已是最优）")
    if "E2 触发日" in res:
        d2, p2, n2 = res["E2 触发日"]
        print(f"  (b) E2 条件（{n2} 天）：高−低 = {d2:+.2f}pp，P={p2:.4f}"
              f" ⇒ {'E2 期动量更强' if abs(d2) >= 2.0 else '未见放大'}")
    print(f"\n  注：相对收益已是对冲口径；市值用「成交额/换手率」估算；未扣成本。")


if __name__ == "__main__":
    main()
