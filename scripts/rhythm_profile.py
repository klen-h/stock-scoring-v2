# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】Phase 0 节奏画像（主力节奏框架_开发计划_v1.0 §4）
================================================================================

回答三个问题（全部只读库，零行情回源）：
  P0.D data-check  数据可得性：命题 B（时间序列跟随）能用什么样本回测
  P0.A episode     episode 切分：净流入有没有"节拍"（zigzag + θ 敏感性）
  P0.B causality   内生性检验：流对价有没有独立信息（B0/B1/B2/B3）
  P0.C lag         滞后成本：等确认再跟还剩多少（进攻/防守两侧 + 对照组）

统计口径（开发计划 §4 固定）：
  F_t  mainflow_history 全池 SUM(main_net)（★库内单位=元，加载后立即转亿；
      内部全脚本一律亿口径——E 与 R 混入同一回归时，元口径两列尺度差 12 个
      数量级会让 X'X 病态、标准误算崩，亿口径条件数健康）
  R_t  沪深300 当日涨跌幅（小数）
  E_t  F_t 对 R_t 回归的残差（β 用 winsorize(1%/99%) 口径估、E 用原始 F 算
       ——β 不被极端值绑架、极端值保留在 E 里，-1009 亿单点双保险）
  C_t  ΣE_s，前 30 日燃烧期
  θ    θ_scale × σ(E)，zigzag 反转阈值
  d1   进入识别日 = 起点（谷/峰）被确认的交易日（C 反向穿越 θ 首日，前向无前视）

依赖：numpy（OLS/Newey-West/winsorize/ACF 全部手写，无 scipy/statsmodels）。

用法：
  python scripts/rhythm_profile.py                     # 全模块
  python scripts/rhythm_profile.py --module episode    # 单模块
  python scripts/rhythm_profile.py --theta-scale 1.5
  python scripts/rhythm_profile.py --json              # 结构化输出（报告用）

输出仅为 Gate 评审提供材料；正式 GO/DOWNGRADE/KILL 由人工落盘。
================================================================================
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from app.database import db

# ---------------- 固定口径常量（参数表校准来源，勿随手改） ----------------
BURN_DAYS = 30                        # C_t 燃烧期（§4.A）
WINSOR_LO, WINSOR_HI = 0.01, 0.99     # β 稳健口径 winsorize 分位
THETA_GRID = [0.5, 1.0, 1.5, 2.0]     # θ 敏感性网格（§4.A）
FWD_HORIZONS = [5, 10, 20]            # 事后收益窗口（§4.A/§4.C）
CHI2_95 = [3.841, 5.991, 7.815, 9.488, 11.070]   # chi2 5% 临界值 df=1..5
INDEX_CODE = "sh000300"


# ================================================================
#  数据加载
# ================================================================

def load_aligned():
    """(dates, F, R)：市场级净流入 × 沪深300 日收益，按日 inner join。"""
    flow_rows = db.fetch(
        "SELECT date, SUM(main_net) AS s FROM mainflow_history "
        "GROUP BY date ORDER BY date ASC") or []
    f_map = {str(r["date"]): float(r["s"] or 0) for r in flow_rows}

    idx_rows = db.fetch(
        f"SELECT date, close FROM backtest_prices WHERE code='{INDEX_CODE}' "
        "ORDER BY date ASC") or []
    closes = [(str(r["date"]), float(r["close"])) for r in idx_rows if r.get("close")]

    dates, fs, rs = [], [], []
    prev_close = None
    for d, c in closes:
        r = (c / prev_close - 1.0) if prev_close else None
        prev_close = c
        if d in f_map and r is not None:
            dates.append(d)
            fs.append(f_map[d] / 1e8)   # ★ 元 → 亿（全脚本内部统一亿口径）
            rs.append(r)
    return dates, np.array(fs, dtype=float), np.array(rs, dtype=float)


# ================================================================
#  统计工具（手写：OLS + Newey-West HAC / winsorize / ACF + Ljung-Box）
# ================================================================

def winsorize(x, lo=WINSOR_LO, hi=WINSOR_HI):
    q1, q2 = np.quantile(x, [lo, hi])
    return np.clip(x, q1, q2)


def ols_nw(y, X, nw_lag=None):
    """OLS + Newey-West HAC 标准误。X 含常数列。返回 (beta, se, t, r2)。"""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    e = y - X @ beta
    if nw_lag is None:
        nw_lag = max(1, int(4 * (n / 100.0) ** (2.0 / 9.0)))
    Xe = X * e[:, None]
    S = Xe.T @ Xe
    for lag in range(1, min(nw_lag, n - 1) + 1):
        G = Xe[lag:].T @ Xe[:-lag]
        w = 1.0 - lag / (nw_lag + 1.0)
        S = S + w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(V), 1e-18))
    t = beta / se
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((e ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    return beta, se, t, r2


def acf_ljungbox(x, max_lag=5):
    """ACF(1..max_lag) + Ljung-Box Q（5% 临界值硬编码）。"""
    n = len(x)
    xc = x - x.mean()
    denom = float((xc ** 2).sum())
    if denom <= 0 or n < max_lag + 2:
        return {"acfs": [None] * max_lag, "Q": None, "crit5": None}
    acfs, q_sum = [], 0.0
    for k in range(1, max_lag + 1):
        rho = float((xc[k:] * xc[:-k]).sum() / denom)
        acfs.append(rho)
        q_sum += rho * rho / (n - k)
    return {"acfs": acfs, "Q": n * (n + 2) * q_sum, "crit5": CHI2_95[max_lag - 1]}


def fwd_return(R, start_idx, horizon):
    """R[start] 收盘 → R[start+horizon] 收盘 的区间收益；越界返回 None。"""
    end = start_idx + horizon
    if end >= len(R):
        return None
    return float(np.prod(1.0 + R[start_idx + 1:end + 1]) - 1.0)


def seg_return(R, a, b):
    """R[a] 收盘 → R[b] 收盘。"""
    if b is None or a is None or b <= a or b >= len(R):
        return None
    return float(np.prod(1.0 + R[a + 1:b + 1]) - 1.0)


# ================================================================
#  超额流 E_t
# ================================================================

def excess_flow(F, R, robust=True):
    """E_t = F_t - α - β·R_t。robust=True：β 用 winsorize 后样本估（防单点绑架），
    E 用原始 F 算（极端值保留）。"""
    F_reg, R_reg = (winsorize(F), winsorize(R)) if robust else (F, R)
    X = np.column_stack([np.ones_like(R_reg), R_reg])
    beta, _, _, _ = ols_nw(F_reg, X, nw_lag=0)
    alpha, slope = float(beta[0]), float(beta[1])
    E = F - alpha - slope * R
    return E, alpha, slope


# ================================================================
#  zigzag episode 切分
# ================================================================

def zigzag(C, theta):
    """对累计序列 C 做 zigzag。

    返回 episodes：{dir: +1流入/-1流出, start: 谷/峰idx, end: 峰/谷idx,
    d1_enter: 起点被确认的 idx（= 进入识别日，C 反向穿越 θ 首日）,
    d1_exit: 终点被确认的 idx（= 离场识别日）, ongoing: 末段未确认}
    索引均相对传入的 C（调用方自行偏移 BURN_DAYS）。
    """
    n = len(C)
    if n < 3 or theta <= 0:
        return []
    hi = lo = 0
    for i in range(1, n):
        if C[i] > C[hi]:
            hi = i
        if C[i] < C[lo]:
            lo = i
        if C[hi] - C[lo] >= theta:
            break
    else:
        return []
    if hi > lo:      # 先升 → lo 是谷，当前向上找峰
        pivots = [(lo, -1, None)]          # (idx, 峰+1/谷-1, confirm_idx)
        cur_dir, cur_ext = +1, hi
    else:            # 先降 → hi 是峰，当前向下找谷
        pivots = [(hi, +1, None)]
        cur_dir, cur_ext = -1, lo
    for i in range(cur_ext + 1, n):
        if cur_dir == +1:
            if C[i] > C[cur_ext]:
                cur_ext = i
            elif C[cur_ext] - C[i] >= theta:
                pivots.append((cur_ext, +1, i))
                cur_dir, cur_ext = -1, i
        else:
            if C[i] < C[cur_ext]:
                cur_ext = i
            elif C[i] - C[cur_ext] >= theta:
                pivots.append((cur_ext, -1, i))
                cur_dir, cur_ext = +1, i

    episodes = []
    for j, p in enumerate(pivots):
        d1_enter = p[2]
        if j + 1 < len(pivots):
            nxt = pivots[j + 1]
            episodes.append({"dir": +1 if p[1] == -1 else -1,
                             "start": p[0], "end": nxt[0],
                             "d1_enter": d1_enter, "d1_exit": nxt[2],
                             "ongoing": False})
        else:
            episodes.append({"dir": +1 if p[1] == -1 else -1,
                             "start": p[0], "end": cur_ext,
                             "d1_enter": d1_enter, "d1_exit": None,
                             "ongoing": True})
    return episodes


def run_zigzag(E, theta):
    """C 累计 + 燃烧期 + zigzag，索引偏移回全序列。"""
    C = np.cumsum(E)
    eps = zigzag(C[BURN_DAYS:].tolist(), theta)
    for ep in eps:
        for k in ("start", "end", "d1_enter", "d1_exit"):
            if ep.get(k) is not None:
                ep[k] += BURN_DAYS
    return eps, C


# ================================================================
#  P0.D 数据可得性
# ================================================================

def module_data_check():
    out = {"module": "P0.D 数据可得性"}
    r = (db.fetch("SELECT MIN(date) mn, MAX(date) mx, COUNT(DISTINCT date) nd, "
                  "COUNT(DISTINCT code) nc FROM mainflow_history") or [{}])[0]
    out["mainflow_history"] = {"range": f"{r.get('mn')} ~ {r.get('mx')}",
                               "days": r.get("nd"), "codes": r.get("nc")}
    r = (db.fetch("SELECT MIN(date) mn, MAX(date) mx, COUNT(DISTINCT date) nd, "
                  "COUNT(DISTINCT code) nc FROM mainforce_state") or [{}])[0]
    out["mainforce_state"] = {"range": f"{r.get('mn')} ~ {r.get('mx')}",
                              "days": r.get("nd"), "codes": r.get("nc")}
    q = db.fetch("SELECT signal, COUNT(*) n, COUNT(DISTINCT date) nd FROM mainforce_state "
                 "WHERE signal IS NOT NULL GROUP BY signal")
    out["labels"] = {r["signal"]: {"rows": r["n"], "days": r["nd"]} for r in (q or [])}
    r = (db.fetch(f"SELECT MIN(date) mn, MAX(date) mx, COUNT(*) n FROM backtest_prices "
                  f"WHERE code='{INDEX_CODE}'") or [{}])[0]
    out["index"] = {"code": INDEX_CODE, "range": f"{r.get('mn')} ~ {r.get('mx')}",
                    "bars": r.get("n")}
    r = (db.fetch("SELECT COUNT(DISTINCT code) nc, MIN(date) mn, MAX(date) mx "
                  "FROM backtest_prices WHERE code != %s", (INDEX_CODE,)) or [{}])[0]
    out["stock_prices"] = {"codes": r.get("nc"), "range": f"{r.get('mn')} ~ {r.get('mx')}"}

    days_flow = out["mainflow_history"]["days"] or 0
    days_label = out["mainforce_state"]["days"] or 0
    out["conclusion"] = [
        f"命题 B（时间序列跟随）主样本 = mainflow_history {days_flow} 个交易日"
        f"（约 {days_flow / 21:.1f} 个月）——P0.A/B/C 立即可跑，n≈{days_flow} 功效有限。",
        f"mainforce_state 标签仅 {days_label} 天 → 宽度序列现有历史不可用；"
        "需重算器用 backtest_prices 三年个股回算筹码标签后（开发计划 §4.D 第二步）"
        "样本才能扩到三年。",
    ]
    return out


def print_data_check(out):
    print("=" * 72)
    print("【P0.D 数据可得性】")
    print("=" * 72)
    for k in ("mainflow_history", "mainforce_state", "labels", "index", "stock_prices"):
        print(f"  {k}: {out[k]}")
    for c in out["conclusion"]:
        print(f"  → {c}")
    print()


# ================================================================
#  P0.A episode 切分 + θ 敏感性
# ================================================================

def module_episode(dates, F, R, theta_scale=1.0):
    n = len(dates)
    if n <= BURN_DAYS + 10:
        return {"module": "P0.A episode", "error": f"样本不足（{n} 日）"}

    E_rob, a_rob, b_rob = excess_flow(F, R, robust=True)
    E_ols, a_ols, b_ols = excess_flow(F, R, robust=False)
    sigma_E = float(np.std(E_rob[BURN_DAYS:], ddof=1))
    theta = theta_scale * sigma_E

    episodes, C = run_zigzag(E_rob, theta)
    rows = []
    for ep in episodes:
        a, b = ep["start"], ep["end"]
        row = {"dir": "流入" if ep["dir"] == +1 else "流出",
               "start": dates[a], "end": dates[b],
               "days": b - a, "amp_yi": round(float(abs(C[b] - C[a])), 1),
               "idx_ret": seg_return(R, a, b), "ongoing": ep["ongoing"]}
        for h in FWD_HORIZONS:
            row[f"fwd{h}"] = fwd_return(R, b, h)
        rows.append(row)

    sens = []
    for ts in THETA_GRID:
        eps_t, _ = run_zigzag(E_rob, ts * sigma_E)
        inflow = [e for e in eps_t if e["dir"] == +1 and not e["ongoing"]]
        outflow = [e for e in eps_t if e["dir"] == -1 and not e["ongoing"]]

        def med(xs):
            xs = sorted(xs)
            return xs[len(xs) // 2] if xs else None

        sens.append({"theta_scale": ts, "theta_yi": round(ts * sigma_E, 1),
                     "episodes": len(eps_t), "inflow_n": len(inflow),
                     "outflow_n": len(outflow),
                     "inflow_med_days": med([e["end"] - e["start"] for e in inflow]),
                     "outflow_med_days": med([e["end"] - e["start"] for e in outflow])})

    inflow_done = [r for r in rows if r["dir"] == "流入" and not r["ongoing"]]
    lens = sorted(r["days"] for r in inflow_done)
    med_len = lens[len(lens) // 2] if lens else None
    return {
        "module": "P0.A episode", "n_days": n, "burn": BURN_DAYS,
        "effective_days": n - BURN_DAYS,
        "beta_robust": {"alpha": round(a_rob, 1),
                        "slope_yi_per_pct": round(b_rob * 0.01, 1)},
        "beta_ols": {"alpha": round(a_ols, 1),
                     "slope_yi_per_pct": round(b_ols * 0.01, 1)},
        "sigma_E_yi": round(sigma_E, 1), "theta_scale": theta_scale,
        "theta_yi": round(theta, 1), "episodes": rows, "sensitivity": sens,
        "gate_A_material": {
            "inflow_done_n": len(inflow_done), "inflow_med_days": med_len,
            "note": "Gate A: 流入 n≥5 且中位长度≥3 日且 θ 稳健 → PASS",
        },
    }


def print_episode(out):
    print("=" * 72)
    print("【P0.A episode 切分】")
    print("=" * 72)
    if "error" in out:
        print(f"  {out['error']}")
        return
    print(f"  样本 {out['n_days']} 日（燃烧 {out['burn']}，有效 {out['effective_days']}）"
          f"  β稳健 α={out['beta_robust']['alpha']}亿 β={out['beta_robust']['slope_yi_per_pct']}亿/1%"
          f"（OLS β={out['beta_ols']['slope_yi_per_pct']}）")
    print(f"  σ(E)={out['sigma_E_yi']}亿 → θ={out['theta_yi']}亿（×{out['theta_scale']}σ）")
    print(f"\n  {'方向':<4}{'起点':<12}{'终点':<12}{'日':>4}{'幅度亿':>8}"
          f"{'期间指数':>9}{'后5日':>9}{'后10日':>9}{'后20日':>9}")

    def fmt(v):
        return f"{v * 100:+.1f}%".rjust(9) if v is not None else "-".rjust(9)

    for r in out["episodes"]:
        print(f"  {r['dir']:<4}{r['start']:<12}{r['end']:<12}{r['days']:>4}"
              f"{r['amp_yi']:>8}{fmt(r['idx_ret'])}{fmt(r['fwd5'])}"
              f"{fmt(r['fwd10'])}{fmt(r['fwd20'])}" + ("  ←进行中" if r["ongoing"] else ""))
    print(f"\n  θ 敏感性：")
    print(f"  {'×σ':>5}{'θ亿':>8}{'总ep':>7}{'流入n':>7}{'流出n':>7}"
          f"{'流入中位日':>10}{'流出中位日':>10}")
    for s in out["sensitivity"]:
        print(f"  {s['theta_scale']:>5}{s['theta_yi']:>8}{s['episodes']:>7}"
              f"{s['inflow_n']:>7}{s['outflow_n']:>7}"
              f"{str(s['inflow_med_days']):>10}{str(s['outflow_med_days']):>10}")
    g = out["gate_A_material"]
    print(f"\n  Gate A 材料：已完成流入 episode {g['inflow_done_n']} 个，"
          f"中位长度 {g['inflow_med_days']} 日")
    print()


# ================================================================
#  P0.B 内生性检验
# ================================================================

def module_causality(F, R, E):
    n = len(R)
    out = {"module": "P0.B 内生性检验", "n": n}

    lb = acf_ljungbox(E, max_lag=5)
    out["B0"] = {
        "acfs": [round(a, 3) if a is not None else None for a in lb["acfs"]],
        "Q": round(lb["Q"], 1) if lb["Q"] is not None else None,
        "crit5": lb["crit5"],
        "verdict": ("E 非白噪声（有自相关/节拍）"
                    if lb["Q"] is not None and lb["Q"] > CHI2_95[4]
                    else "无法拒绝白噪声（episode 可能是随机切割）→ Gate A KILL 风险"),
    }

    X1 = np.column_stack([np.ones(n), winsorize(R)])
    beta1, _, t1, r2_1 = ols_nw(winsorize(F), X1)
    out["B1"] = {"slope_yi_per_pct": round(float(beta1[1]) * 0.01, 1),
                 "t": round(float(t1[1]), 2), "r2": round(r2_1, 3)}

    y = R[1:]
    X2 = np.column_stack([np.ones(n - 1), E[:-1], R[:-1]])
    beta2, _, t2, r2_2 = ols_nw(y, X2)
    out["B2"] = {"coef_E_lag1": round(float(beta2[1]), 8),
                 "t_E_lag1": round(float(t2[1]), 3),
                 "coef_R_lag1": round(float(beta2[2]), 4),
                 "t_R_lag1": round(float(t2[2]), 2),
                 "r2": round(r2_2, 4),
                 "per_100yi_bp": round(float(beta2[1]) * 100 * 10000, 2)}

    med = float(np.median(E[:-1]))
    hi = R[1:][E[:-1] > med]
    lo = R[1:][E[:-1] <= med]
    sp = math.sqrt(float(hi.var(ddof=1) / len(hi) + lo.var(ddof=1) / len(lo)))
    out["B2b"] = {"cut_yi": round(med, 1),
                  "hi_mean_pct": round(float(hi.mean()) * 100, 3),
                  "lo_mean_pct": round(float(lo.mean()) * 100, 3),
                  "diff_pct": round(float(hi.mean() - lo.mean()) * 100, 3),
                  "t_welch": round(float(hi.mean() - lo.mean()) / sp, 2) if sp > 0 else None}

    X3 = np.column_stack([np.ones(n - 1), R[:-1]])
    beta3, _, t3, _ = ols_nw(E[1:], X3)
    out["B3"] = {"coef_yi_per_pct": round(float(beta3[1]) * 0.01, 1),
                 "t": round(float(t3[1]), 2),
                 "verdict": ("流显著追涨（影子证据，独立信息存疑）"
                             if float(t3[1]) >= 2 and float(beta3[1]) > 0
                             else "未见流追涨的显著证据")}

    b2 = out["B2"]
    if b2["t_E_lag1"] >= 2 and b2["coef_E_lag1"] > 0:
        verdict = "PASS（流入领先次日收益：方向+显著）"
    elif b2["coef_E_lag1"] > 0 and b2["t_E_lag1"] >= 1:
        verdict = "弱 PASS（方向对、显著性不足——n 小，扩样后重估）"
    elif b2["coef_E_lag1"] > 0:
        verdict = "方向对但不显著（证据不足，勿上仓位）"
    elif b2["coef_E_lag1"] < 0 and b2["t_E_lag1"] <= -2:
        verdict = "反向显著（流入次日成反指）→ 命题 B 危险"
    else:
        verdict = "无信号"
    out["gate_B_material"] = {"verdict": verdict}
    return out


def print_causality(out):
    print("=" * 72)
    print(f"【P0.B 内生性检验】（Newey-West HAC，n={out['n']}）")
    print("=" * 72)
    b0 = out["B0"]
    print(f"  B0 白噪声: ACF(1..5)={b0['acfs']}  Q={b0['Q']}（5%临界 {b0['crit5']}）")
    print(f"     → {b0['verdict']}")
    b1 = out["B1"]
    print(f"  B1 基准弹性: 指数每+1% → 当日流 +{b1['slope_yi_per_pct']}亿"
          f"（t={b1['t']}，R²={b1['r2']}）")
    b2 = out["B2"]
    print(f"  B2 核心 R_t~E_(t-1)+R_(t-1): E 每+100亿→次日 {b2['per_100yi_bp']}bp"
          f"（t={b2['t_E_lag1']}，R²={b2['r2']}）")
    print(f"     对照 R_(t-1) 系数={b2['coef_R_lag1']}（t={b2['t_R_lag1']}）")
    b2b = out["B2b"]
    print(f"  B2b 分组: E前日>{b2b['cut_yi']}亿组 次日均值 {b2b['hi_mean_pct']}% vs "
          f"其余 {b2b['lo_mean_pct']}%（差 {b2b['diff_pct']}%，t={b2b['t_welch']}）")
    b3 = out["B3"]
    print(f"  B3 影子 E_t~R_(t-1): 指数每+1%→次日E {b3['coef_yi_per_pct']}亿"
          f"（t={b3['t']}）→ {b3['verdict']}")
    print(f"\n  Gate B 初判: {out['gate_B_material']['verdict']}")
    print()


# ================================================================
#  P0.C 滞后成本 + 对照组
# ================================================================

def _uncond(R, horizon):
    """全样本无条件 fwd 分布（有效起点）。"""
    vals = [fwd_return(R, t, horizon) for t in range(len(R) - horizon)]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    arr = np.array(vals)
    return {"mean": round(float(arr.mean()) * 100, 2),
            "median": round(float(np.median(arr)) * 100, 2)}


def module_lag(dates, F, R, E, theta_scale=1.0):
    n = len(R)
    sigma_E = float(np.std(E[BURN_DAYS:], ddof=1))
    theta = theta_scale * sigma_E
    episodes, C = run_zigzag(E, theta)

    # ---- 进攻侧：流入 episode 滞后成本（d1_enter 有值才有意义）----
    inflow = []
    for ep in episodes:
        if ep["dir"] != +1 or ep["d1_enter"] is None:
            continue
        d0, d1, de = ep["start"], ep["d1_enter"], ep["end"]
        lag_cost = seg_return(R, d0, d1)
        remain = seg_return(R, d1, de)
        total = (lag_cost + remain) if (lag_cost is not None and remain is not None) else None
        share = (lag_cost / total if total is not None and total > 0 else None)
        inflow.append({"start": dates[d0], "d1": dates[d1], "end": dates[de],
                       "lag_days": d1 - d0,
                       "lag_cost_pct": round(lag_cost * 100, 2) if lag_cost is not None else None,
                       "remain_pct": round(remain * 100, 2) if remain is not None else None,
                       "cost_share": round(share, 3) if share is not None else None,
                       "ongoing": ep["ongoing"]})

    shares = [r["cost_share"] for r in inflow if r["cost_share"] is not None]
    lags = [r["lag_days"] for r in inflow]
    med_share = sorted(shares)[len(shares) // 2] if shares else None
    med_lag = sorted(lags)[len(lags) // 2] if lags else None

    # ---- 对照组：流入识别日 d1 后 fwd vs 无条件 ----
    enter_days = [ep["d1_enter"] for ep in episodes
                  if ep["dir"] == +1 and ep["d1_enter"] is not None]
    attack_ctrl = {}
    for h in FWD_HORIZONS:
        vals = [fwd_return(R, t, h) for t in enter_days]
        vals = [v for v in vals if v is not None]
        unc = _uncond(R, h)
        attack_ctrl[h] = {
            "n": len(vals),
            "mean": round(float(np.mean(vals)) * 100, 2) if vals else None,
            "uncond_mean": unc["mean"] if unc else None,
            "uncond_median": unc["median"] if unc else None,
        }

    # ---- 防守侧：流出确认日（峰被确认 = d1_enter of 流出 episode）后 fwd ----
    exit_days = [ep["d1_enter"] for ep in episodes
                 if ep["dir"] == -1 and ep["d1_enter"] is not None]
    defense_ctrl = {}
    for h in FWD_HORIZONS:
        vals = [fwd_return(R, t, h) for t in exit_days]
        vals = [v for v in vals if v is not None]
        defense_ctrl[h] = {
            "n": len(vals),
            "mean": round(float(np.mean(vals)) * 100, 2) if vals else None,
            "median": round(float(np.median(vals)) * 100, 2) if vals else None,
            "uncond_mean": attack_ctrl[h]["uncond_mean"],
        }

    # ---- Gate C 初判 ----
    if med_share is None:
        gate_c = "样本不足，无法判定"
    elif med_share < 0.5:
        gate_c = f"PASS（中位滞后成本占比 {med_share:.0%} <50%）"
    elif med_share <= 0.7:
        gate_c = f"DOWNGRADE（占比 {med_share:.0%} 在 50~70% → 仅回避用途）"
    else:
        gate_c = f"FAIL（占比 {med_share:.0%} >70% → 确认后已无肉）"

    return {"module": "P0.C 滞后成本", "theta_yi": round(theta, 1),
            "inflow": inflow, "med_lag_days": med_lag, "med_cost_share": med_share,
            "attack_ctrl": attack_ctrl, "defense_ctrl": defense_ctrl,
            "gate_C_material": {"verdict": gate_c}}


def print_lag(out):
    print("=" * 72)
    print(f"【P0.C 滞后成本与可跟性】（θ={out['theta_yi']}亿）")
    print("=" * 72)
    print(f"  进攻侧（流入 episode，d1=谷确认日）：")
    print(f"  {'谷底日':<12}{'识别日d1':<12}{'峰日':<12}{'滞后日':>5}"
          f"{'滞后成本':>9}{'剩余':>9}{'占比':>7}")
    for r in out["inflow"]:
        lc = f"{r['lag_cost_pct']:+.1f}%" if r["lag_cost_pct"] is not None else "-"
        rm = f"{r['remain_pct']:+.1f}%" if r["remain_pct"] is not None else "-"
        sh = f"{r['cost_share']:.0%}" if r["cost_share"] is not None else "-"
        print(f"  {r['start']:<12}{r['d1']:<12}{r['end']:<12}{r['lag_days']:>5}"
              f"{lc:>9}{rm:>9}{sh:>7}" + ("  ←进行中" if r["ongoing"] else ""))
    print(f"  → 中位滞后 {out['med_lag_days']} 日，中位滞后成本占比 "
          f"{out['med_cost_share'] if out['med_cost_share'] is None else f'{out['med_cost_share']:.0%}'}")
    print(f"\n  对照组（识别日 d1 后 vs 无条件）：")
    print(f"  {'窗口':>6}{'n':>4}{'d1后均值':>10}{'无条件均值':>10}{'差bp':>8}")
    for h, v in out["attack_ctrl"].items():
        if v["mean"] is None or v["uncond_mean"] is None:
            continue
        print(f"  {h:>6}{v['n']:>4}{v['mean']:>9}%{v['uncond_mean']:>9}%"
              f"{(v['mean'] - v['uncond_mean']) * 100:>8.0f}")
    print(f"\n  防守侧（流出确认日 d1' 后 vs 无条件）—— 回避价值：")
    print(f"  {'窗口':>6}{'n':>4}{'d1后均值':>10}{'d1后中位':>10}{'无条件均值':>10}")
    for h, v in out["defense_ctrl"].items():
        if v["mean"] is None:
            continue
        print(f"  {h:>6}{v['n']:>4}{v['mean']:>9}%{v['median']:>9}%{v['uncond_mean']:>9}%")
    print(f"\n  Gate C 初判: {out['gate_C_material']['verdict']}")
    print()


# ================================================================
#  汇总
# ================================================================

def print_gate_summary(results):
    print("=" * 72)
    print("【Gate 评审材料汇总】（正式 GO/DOWNGRADE/KILL 人工落盘）")
    print("=" * 72)
    for r in results:
        m = r.get("module", "")
        if m.startswith("P0.A") and "gate_A_material" in r:
            g = r["gate_A_material"]
            print(f"  Gate A（节拍存在性）: 流入 n={g['inflow_done_n']}，"
                  f"中位长度 {g['inflow_med_days']} 日")
        if m.startswith("P0.B") and "gate_B_material" in r:
            print(f"  Gate B（信息独立性）: {r['gate_B_material']['verdict']}")
        if m.startswith("P0.C") and "gate_C_material" in r:
            print(f"  Gate C（滞后经济性）: {r['gate_C_material']['verdict']}")
    print()


def main():
    ap = argparse.ArgumentParser(description="Phase 0 节奏画像（开发计划 v1.0 §4）")
    ap.add_argument("--module", choices=["data-check", "episode", "causality", "lag", "all"],
                    default="all")
    ap.add_argument("--theta-scale", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    if args.module in ("data-check", "all"):
        results.append(module_data_check())
    if args.module in ("episode", "causality", "lag", "all"):
        dates, F, R = load_aligned()
        E, _, _ = excess_flow(F, R, robust=True)
        if args.module in ("episode", "all"):
            results.append(module_episode(dates, F, R, args.theta_scale))
        if args.module in ("causality", "all"):
            results.append(module_causality(F, R, E))
        if args.module in ("lag", "all"):
            results.append(module_lag(dates, F, R, E, args.theta_scale))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return

    printers = {"P0.D": print_data_check, "P0.A": print_episode,
                "P0.B": print_causality, "P0.C": print_lag}
    for r in results:
        for prefix, fn in printers.items():
            if r.get("module", "").startswith(prefix):
                fn(r)
                break
    if len(results) > 1:
        print_gate_summary(results)


if __name__ == "__main__":
    main()
