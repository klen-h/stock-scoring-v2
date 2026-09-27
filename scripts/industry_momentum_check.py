#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【P2 · 行业动量分层】行业 20 日动量是否有预测力？E2 脉冲期是否更强？
================================================================================
动机（A 股的"结构性行情"）：
  大量年份是"指数熊 + 局部牛"（2013 创业板、2023 微盘、2024 科技）。
  此前全项目用**全市场统一视角** ⇒ 注定错过结构。本脚本回答两个问题：
    ① 行业 20 日动量在**横截面**上是否有预测力？（独立候选信号源）
    ② **E2 政策脉冲期**内，该预测力是否更强？（用于回答"E2 后买什么"）

数据：
  · 行业映射：`stock_industry`（Supabase，5932 只 / **49 个行业**）
  · 个股日线：`data/zzshare_daily.db`（21 年全市场）
  · 对齐：`stock_industry.code`（6 位）↔ `daily.code`（`000001.SZ`）用 `substr(code,1,6)`
  · 映射写入 zzshare 本地临时表 `_code_industry` 后 JOIN 聚合（跨库不能直接 JOIN）

━━━━━━━━━━━━━━━━━━ 预登记 ━━━━━━━━━━━━━━━━━━
  行业收益 = 行业内个股等权日收益；动量 = 过去 20 个交易日累计收益；
  未来收益 = **T+1 开盘买 → T+20 收盘卖**（与 §8 全链同口径）。
  分层：**逐日**按动量横截面分 3 层（top30% / mid40% / bottom30%），比较各层未来收益。

判据：
  (a) 全样本 top30% − bottom30% 的 T+20 收益差 >= **+1.0pp** 且 bootstrap P < 0.05
      ⇒ 行业动量有**正向**预测力（可作独立信号源候选）
      若 <= **−1.0pp** 且显著 ⇒ **反转效应**（同样有价值，方向相反）
  (b) E2 条件下同一差值 >= **+2.0pp**（含负向）⇒ 脉冲期动量作用更强
  (c) |差| < 0.5pp 或不显著 ⇒ **无预测力，归档**

⚠️ 已知局限（必须写进结论）：行业映射是**当前快照**（无历史）⇒ 历史归属按"现在"回填；
   板块归属变动缓慢，但改名/重组会引入噪声。
⚠️ 本检验是**因子横截面**检验（逐日分层），不是可执行策略（逐日换仓有成本）。

用法：python scripts/industry_momentum_check.py
================================================================================
"""
import os
import sqlite3
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")

MOM_DAYS = 20
FWD_DAYS = 20
TOP_PCT = 0.30
B_BOOT = 10000
E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
# ★ 最小成分数：行业当日有效个股 < 该值时不参与分层。
#   动机：行业映射是**当前快照**回填历史 ⇒ 早期年份/小行业当日可能只有 1~2 只有数据，
#   其等权收益被单只极端涨跌主导 ⇒ 会产生虚假的"动量极高 / 未来极高"样本。
MIN_STOCKS = 5


def load_industry_map() -> dict:
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app.database import db
    rows = db.fetch("SELECT code, main_industry FROM stock_industry") or []
    return {str(r["code"]): str(r["main_industry"])
            for r in rows if r.get("code") and r.get("main_industry")}


def build_industry_matrix(imap):
    """写入临时映射表 → 按 (date, industry) 聚合 → 返回 (dates, inds, pct, gap, n)。"""
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("DROP TABLE IF EXISTS _code_industry")
    conn.execute("CREATE TABLE _code_industry (code TEXT PRIMARY KEY, ind TEXT)")
    conn.executemany("INSERT OR REPLACE INTO _code_industry (code, ind) VALUES (?, ?)",
                     list(imap.items()))
    conn.commit()
    rows = conn.execute("""
        SELECT d.date, m.ind,
               AVG(d.pct_chg) AS pct,
               AVG(CASE WHEN d.pre_close > 0 THEN d.open / d.pre_close - 1 END) * 100 AS gap,
               COUNT(*) AS n
        FROM daily d JOIN _code_industry m ON substr(d.code, 1, 6) = m.code
        WHERE d.is_paused = 0 AND d.pct_chg IS NOT NULL
        GROUP BY d.date, m.ind
        ORDER BY d.date
    """).fetchall()
    conn.close()

    dates = sorted({r[0] for r in rows})
    inds = sorted({r[1] for r in rows})
    di = {d: i for i, d in enumerate(dates)}
    ii = {x: i for i, x in enumerate(inds)}
    pct = np.full((len(dates), len(inds)), np.nan)
    gap = np.zeros((len(dates), len(inds)))
    cnt = np.zeros((len(dates), len(inds)))
    for d, ind, p, g, c in rows:
        i, j = di[d], ii[ind]
        if p is not None:
            pct[i, j] = p
        gap[i, j] = g or 0.0
        cnt[i, j] = c or 0
    return dates, inds, pct, gap, cnt


def load_e2_set() -> set:
    import json
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    out = set()
    for d, v in raw.items():
        denom = (v.get("up") or 0) + (v.get("down") or 0)
        if v["up_ratio"] >= E2_UP_RATIO and denom > 0 \
                and v["limit_up"] / denom >= E2_LU_RATIO:
            out.add(d)
    return out


def block_p(diffs, B=B_BOOT, seed=42):
    """按「日期」重采样均值差 → P(mean <= 0)。"""
    a = np.asarray([x for x in diffs if x is not None], dtype=float)
    n = len(a)
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, n, size=(B, n))].mean(axis=1)
    return float((means <= 0).mean()), float(means.mean())


def layered(mom, fwd, fwd_ex, dates, cnt, n_d, mask_days=None):
    """逐日按动量分 3 层 → 各层未来收益（绝对 + 超额）。"""
    top, mid, bot, diffs, ns = [], [], [], [], []
    ex_diffs, by_year = [], {}
    for t in range(n_d):
        if mask_days is not None and dates[t] not in mask_days:
            continue
        valid = ~np.isnan(mom[t]) & ~np.isnan(fwd[t]) & (cnt[t] >= MIN_STOCKS)
        k = int(valid.sum())
        if k < 10:
            continue
        idx = np.where(valid)[0]
        order = idx[np.argsort(-mom[t][idx])]
        m = max(1, int(k * TOP_PCT))
        top.extend(fwd[t][order[:m]].tolist())
        bot.extend(fwd[t][order[-m:]].tolist())
        mid.extend(fwd[t][order[m: k - m]].tolist())
        # ★ 用**中位数**衡量组收益：行业只有 ~40 个，均值极易被单个极端行业主导
        d_abs = float(np.median(fwd[t][order[:m]]) - np.median(fwd[t][order[-m:]]))
        diffs.append(d_abs)
        rt, rb = fwd_ex[t][order[:m]], fwd_ex[t][order[-m:]]
        if not np.isnan(rt).all() and not np.isnan(rb).all():
            ex_diffs.append(float(np.median(rt) - np.median(rb)))
        # 分年（用超额差，剔除年份 β）
        yr = dates[t][:4]
        by_year.setdefault(yr, []).append(d_abs)
        ns.append(k)
    return {"top": top, "mid": mid, "bot": bot, "diffs": diffs,
            "ns": ns, "ex_diffs": ex_diffs, "by_year": by_year}


def main():
    imap = load_industry_map()
    print(f"行业映射：{len(imap)} 只股票")
    dates, inds, pct, gap, cnt = build_industry_matrix(imap)
    print(f"行业日线矩阵：{len(dates)} 天 × {len(inds)} 个行业"
          f"（{dates[0]} ~ {dates[-1]}）")

    # ★★ 数据合理性过滤（关键，实测踩坑）：
    #   首轮跑出全样本多空 +16.93pp（年化 179%，不可能），分年拆解定位到
    #   **2026 年 +562pp** —— 个别行业的开盘跳空 `gap` 极端值使 `cum/entry` 爆炸
    #   （行业等权口径下 A 股单日理论极限约 ±11%，超出即数据异常）。
    EXTREME = 11.0
    _bad_p = int(np.sum(np.abs(pct) > EXTREME))
    _bad_g = int(np.sum(np.abs(gap) > EXTREME))
    pct = np.where(np.abs(pct) > EXTREME, np.nan, pct)
    gap = np.where(np.abs(gap) > EXTREME, np.nan, gap)
    print(f"  异常值过滤（|x|>{EXTREME}%）：涨跌幅 {_bad_p} 个、跳空 {_bad_g} 个")

    # 动量与未来收益（均以「行业等权日收益」为基础）
    n_d, n_i = pct.shape
    mom = np.full((n_d, n_i), np.nan)
    fwd = np.full((n_d, n_i), np.nan)
    r = np.nan_to_num(pct, nan=0.0) / 100.0
    for t in range(MOM_DAYS, n_d - FWD_DAYS - 1):
        # 动量：过去 MOM_DAYS 个交易日（含 t）
        win = r[t - MOM_DAYS + 1: t + 1]
        mom[t] = np.where(np.isnan(pct[t - MOM_DAYS + 1: t + 1]).any(axis=0),
                          np.nan, np.prod(1 + win, axis=0) - 1)
        # 未来：T+1 开盘买 → T+20 收盘
        entry = 1 + gap[t + 1] / 100.0
        cum = np.prod(1 + r[t + 1: t + 1 + FWD_DAYS], axis=0)
        # 跳空需落在 ±11% 内（行业等权口径的理论极限），否则视为异常样本
        ok_entry = (entry > 1 - EXTREME / 100.0) & (entry < 1 + EXTREME / 100.0)
        fwd[t] = np.where(ok_entry, (cum / entry - 1) * 100, np.nan)

    # ★ 超额口径（关键）：逐日减去**当日全行业均值** ⇒ 剔除 β，
    #   否则"动量 top 在牛市里涨得多"会被误读成 alpha（top 组天然高 β）。
    with np.errstate(invalid="ignore"):
        fwd_ex = fwd - np.nanmean(fwd, axis=1, keepdims=True)

    e2 = load_e2_set()


    print(f"\n{'='*96}")
    print(f"【行业动量分层】{MOM_DAYS} 日动量 → 未来 {FWD_DAYS} 日收益（T+1 开盘买）")
    print(f"{'='*96}")
    print(f"  MIN_STOCKS={MIN_STOCKS}（当日有效个股少于此值的行业不参与分层）")

    # ── 诊断：抽查若干交易日的 top/bot 行业与未来收益（判断数字是否可信）──
    print("\n【诊断】抽查（行业名 | 动量 → 未来20日收益）")
    _shown = 0
    for t in range(MOM_DAYS, n_d - FWD_DAYS - 1):
        if _shown >= 3:
            break
        if t % 1200 != 0:
            continue
        _v = ~np.isnan(mom[t]) & ~np.isnan(fwd[t]) & (cnt[t] >= MIN_STOCKS)
        _idx = np.where(_v)[0]
        if len(_idx) < 10:
            continue
        _o = _idx[np.argsort(-mom[t][_idx])]
        _m = max(1, int(len(_idx) * TOP_PCT))
        print(f"  {dates[t]}（有效行业 {len(_idx)}）：")
        print("    top: " + " ｜ ".join(
            f"{inds[j]} {mom[t][j]*100:+.1f}%→{fwd[t][j]:+.1f}%" for j in _o[:3]))
        print("    bot: " + " ｜ ".join(
            f"{inds[j]} {mom[t][j]*100:+.1f}%→{fwd[t][j]:+.1f}%" for j in _o[-3:]))
        _shown += 1
    out = {}
    for label, mask in (("全样本", None), ("E2 触发日", e2)):
        r = layered(mom, fwd, fwd_ex, dates, cnt, n_d, mask)
        top, mid, bot, diffs, ns, ex_diffs = (r["top"], r["mid"], r["bot"],
                                              r["diffs"], r["ns"], r["ex_diffs"])
        if not top:
            print(f"\n  [{label}] 样本不足")
            continue
        tm, bm = np.median(top), np.median(bot)
        mm = np.median(mid) if mid else np.nan
        tw = np.mean(np.array(top) > 0) * 100
        bw = np.mean(np.array(bot) > 0) * 100
        p, _ = block_p(diffs)
        p_ex, m_ex = (block_p(ex_diffs) if ex_diffs else (None, None))
        print(f"\n  [{label}]  覆盖 {len(diffs)} 个交易日（横截面中位 {int(np.median(ns))} 个行业）")
        print(f"    top30%  未来{FWD_DAYS}日 {tm:+.2f}%  胜率 {tw:.1f}%  (n={len(top)})")
        print(f"    mid40%  未来{FWD_DAYS}日 {mm:+.2f}%")
        print(f"    bot30%  未来{FWD_DAYS}日 {bm:+.2f}%  胜率 {bw:.1f}%  (n={len(bot)})")
        print(f"    · 绝对多空 top-bot = {tm - bm:+.2f}pp   P={p:.4f}")
        if ex_diffs:
            print(f"    ★ **超额**多空（逐日剔除全行业均值）= {m_ex:+.2f}pp   P={p_ex:.4f}"
                  f"   <- 判断是真 alpha 还是 beta")
        by = r["by_year"]
        pos_y = sum(1 for y in by if np.mean(by[y]) > 0)
        print(f"    · 分年：绝对差为正的年份 {pos_y}/{len(by)}；"
              + " ".join(f"{y}:{np.mean(by[y]):+.1f}" for y in sorted(by)[-6:]))
        out[label] = (tm - bm, p, len(diffs), m_ex, p_ex)

    print(f"\n{'='*96}")
    print("【预登记判定】")
    print(f"{'='*96}")
    if "全样本" in out:
        d, p, n, ex, pex = out["全样本"]
        print(f"  (a) 全样本（覆盖 {n} 日）：")
        print(f"      · 绝对多空 {d:+.2f}pp（P={p:.4f}）")
        if ex is not None:
            print(f"      · **超额多空 {ex:+.2f}pp（P={pex:.4f}）**  <- 判定以此为准（剔除 beta）")
        if ex is not None and ex >= 1.0 and pex < 0.05:
            v = "**正向 alpha 成立**（剔除 beta 后仍为正）⇒ 可作独立信号源候选"
        elif d >= 1.0 and p < 0.05:
            v = ("**仅绝对口径为正、超额不显著** ⇒ 大概率是 beta"
                 "（牛市里高动量行业涨得多），**非 alpha，归档**")
        elif ex is not None and ex <= -1.0 and pex > 0.95:
            v = "**反转效应**（动量低者反而更好；方向相反但同样有价值）"
        else:
            v = "**无预测力，归档**"
        print(f"      => {v}")
    if "E2 触发日" in out:
        d2, p2, n2, ex2, pex2 = out["E2 触发日"]
        print(f"  (b) E2 条件（覆盖 {n2} 日）：绝对 {d2:+.2f}pp"
              + (f"，超额 {ex2:+.2f}pp" if ex2 is not None else "")
              + " => " + ("脉冲期动量被放大" if (ex2 is not None and abs(ex2) >= 2.0)
                          else "**未见结构分化（E2 期是普涨而非行业轮动）**"))
    print(f"\n  注：行业映射为**当前快照**（无历史），历史归属按现在回填；"
          f"本检验为横截面因子检验（逐日分层，非可执行策略）。")


if __name__ == "__main__":
    main()
