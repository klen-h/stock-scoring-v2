# -*- coding: utf-8 -*-
"""
================================================================================
「缩量调整 → 放量启动」形态 · 胜率验证工具（描述性研究，非预登记判据）
================================================================================

【形态定义（两段，全部可配）】
  ── 段 A「缩量调整」（启动日前 pullback_days 个交易日）──
    · 缩量：调整期均量 / 基准均量 ≤ vol_shrink（基准 = 调整期之前 vol_base_days 日均量）
    · 调整幅度：调整期累计涨跌 ∈ [pullback_min, pullback_max]（默认 -25% ~ +3%）
    · 不创新高：调整期最高价 ≤ 基准期最高价（below_high_tol=0）
  ── 段 B「启动日 T（迅猛上涨）」──
    · 涨幅：T 日涨幅 ≥ launch_pct（默认 +5%）
    · 放量：T 日量 ≥ vol_surge × 调整期均量（默认 2.0）
    · 突破：T 日收盘 > 调整期最高价（--no-breakout 可关）

【入场 / 前视】
   入场价 = T+1 开盘（--entry t1_open，默认；t1_close / t_close 可选）
   前视窗口 = fwd_days（默认 10；可逗号分隔多个）
   指标 = T+N 收益、区间最大涨幅（high/entry−1）、区间最大回撤（low/entry−1）、
          达标率（最大涨幅 ≥ target_pct）、胜率（T+N 收益 > 0）

【去重 / 样本纪律】
  · 冷却：同 code 相邻信号至少间隔 cooldown 交易日（默认 10，避免同一波重复计数）
  · 可执行性：剔 ST、剔停牌；T+1 一字板（开盘≈涨停）剔出并单独计数（买不进）
  · 聚类 bootstrap（按信号日聚类，seed 固定）：P(信号组均值 ≤ 基准)
  · 样本闸：独立信号日 < min_samples ⇒ 标注 INSUFFICIENT（数字照报，不下结论）

【数据源】单一源、绝不混源（两者原始收盘逐日一致，但复权/字段不同）
  · db      ：backtest_prices（989 只 / 2023-09 起 / 每日更新到昨日；无 is_st/涨跌停列）
  · zzshare ：data/zzshare_daily.db（全市场 / 2005 起 / 到 2026-09-24；含 is_st/high_limit/
               low_limit/is_paused/factor）
  · --source auto：单股默认 db（更新、含最近启动）；全市场默认 zzshare（样本大）

【已知简化（诚实披露）】
  · 收益用「原始收盘比」，除权/派息跳空未复权（zzshare 有 pct_chg 但两源统一口径更干净；
    单股无近期派息时可忽略；全市场扫描存在少量除权噪声）
  · 一字板剔除在 db 源为近似（open/prev_close ≥ +9.8% 视为买不进，未区分 20cm 板块）

【输出】
  · 控制台摘要 + Markdown 报告（backend/backtest_reports/）
  · 自包含 HTML（内联 SVG，零依赖）可视化：① 事件研究路径（T 对齐）② 收益分布
    ③ 分年度 n/胜率 ④ 单股价格 + 信号标注

用法：
  # 单股（默认 db 源，最近启动可被扫到）
  python scripts/pattern_launch_check.py --code 600777
  # 单股 + 长历史
  python scripts/pattern_launch_check.py --code 600777 --source zzshare
  # 全市场大样本（默认 zzshare；--limit 限制只数便于快速试跑）
  python scripts/pattern_launch_check.py --all --start 2024-01-01
  python scripts/pattern_launch_check.py --all --start 2024-01-01 --limit 300
  # 调参示例
  python scripts/pattern_launch_check.py --code 600777 --launch-pct 0.07 --vol-shrink 0.5 --fwd 5,10,20
  # 自测
  python scripts/pattern_launch_check.py --selftest
================================================================================
"""
import argparse
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
ZZDB = ROOT / "data" / "zzshare_daily.db"
REPORT_DIR = BACKEND / "backtest_reports"
sys.path.insert(0, str(BACKEND))     # 让 `from app.database import db` 可用


# ================================================================
# 配置（预登记参数快照；命令行可覆盖）
# ================================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="缩量调整→放量启动 形态胜率验证")
    src = p.add_argument_group("范围")
    src.add_argument("--code", help="单股代码（如 600777）")
    src.add_argument("--all", action="store_true", help="全市场扫描（大样本）")
    src.add_argument("--source", choices=["auto", "db", "zzshare"], default="auto")
    src.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（默认数据起点）")
    src.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（默认数据终点）")
    src.add_argument("--limit", type=int, default=None, help="仅扫前 N 只（快速试跑）")

    shape = p.add_argument_group("形态定义")
    shape.add_argument("--pullback-days", type=int, default=20)
    shape.add_argument("--vol-base-days", type=int, default=60)
    shape.add_argument("--vol-shrink", type=float, default=0.60)
    shape.add_argument("--pullback-min", type=float, default=-0.25)
    shape.add_argument("--pullback-max", type=float, default=0.03)
    shape.add_argument("--below-high-tol", type=float, default=0.0)
    shape.add_argument("--launch-pct", type=float, default=5.0)
    shape.add_argument("--vol-surge", type=float, default=2.0)
    shape.add_argument("--no-breakout", action="store_true")
    shape.add_argument("--cooldown", type=int, default=10)

    ev = p.add_argument_group("入场 / 前视 / 评估")
    ev.add_argument("--entry", choices=["t1_open", "t1_close", "t_close"], default="t1_open")
    ev.add_argument("--fwd", default="10", help="前视窗口，逗号分隔，如 5,10,20")
    ev.add_argument("--target", type=float, default=0.10)
    ev.add_argument("--cost", type=float, default=0.003,
                    help="双边交易成本（默认 0.003 = 佣金+印花税+滑点，与 shrink_rebound_live 同源）；"
                         "报告同时给毛/净两套口径")
    ev.add_argument("--exclude-limit-close", action="store_true",
                    help="更严可执行性：信号日 T 涨停收盘 ⇒ 剔除（默认关）")
    ev.add_argument("--baseline", choices=["self", "market"], default="self",
                    help="基准：self=同股无条件 T+N；market=全市场无条件 T+N")
    ev.add_argument("--baseline-eligibility", choices=["same", "all"], default="same",
                    help="基准取样资格：same=与信号同一门槛(≥pullback+base 根，默认，推荐)；"
                         "all=全部 bar（含新股上市初期 ⇒ 会把超额算歪，仅用于对照）")
    ev.add_argument("--seed", type=int, default=20261014)
    ev.add_argument("--min-samples", type=int, default=20)
    ev.add_argument("--bootstrap", type=int, default=4000)

    out = p.add_argument_group("输出")
    out.add_argument("--no-html", action="store_true")
    out.add_argument("--max-paths", type=int, default=400,
                     help="事件研究图最多画多少条样本路径（蓄水池抽样；防全量时 HTML 膨胀）")
    out.add_argument("--selftest", action="store_true")
    return p.parse_args(argv)


# ================================================================
# 数据加载（单一源，绝不混源）
# ================================================================
def _load_env():
    env = BACKEND / ".env"
    if env.exists():
        for line in open(env, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_bars_db(code, start=None, end=None):
    """backtest_prices → 升序 bars。pct 用收盘比补；无 is_st/涨跌停。"""
    from app.database import db
    sql = ("SELECT date, open, high, low, close, volume FROM backtest_prices "
           "WHERE code=%s")
    params = [code]
    if start:
        sql += " AND date>=%s"
        params.append(start)
    if end:
        sql += " AND date<=%s"
        params.append(end)
    sql += " ORDER BY date ASC"
    rows = db.fetch(sql, params) or []
    bars, prev = [], None
    for r in rows:
        cl = float(r["close"] or 0)
        op = float(r["open"] or 0)
        hi = float(r["high"] or 0)
        lo = float(r["low"] or 0)
        if cl <= 0 or hi <= 0 or lo <= 0 or op <= 0:
            prev = cl if cl > 0 else prev
            continue
        pct = (cl / prev - 1) * 100 if prev else None
        bars.append({"date": str(r["date"])[:10], "open": op, "high": hi, "low": lo,
                     "close": cl, "volume": float(r["volume"] or 0), "pct": pct,
                     "is_st": None, "high_limit": None, "low_limit": None,
                     "is_paused": None})
        prev = cl
    return bars


def load_bars_zz(code, start=None, end=None):
    """zzshare_daily.db → 升序 bars（含 is_st/涨跌停/停牌）。"""
    suffix = _suffix(code)
    q = ("SELECT date, open, high, low, close, volume, pct_chg, high_limit, low_limit, "
         "is_st, is_paused FROM daily WHERE code=?")
    params = [suffix]
    if start:
        q += " AND date>=?"
        params.append(start)
    if end:
        q += " AND date<=?"
        params.append(end)
    q += " ORDER BY date ASC"
    conn = sqlite3.connect(str(ZZDB))
    try:
        bars = []
        for r in conn.execute(q, params):
            bars.append({"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                         "close": r[4], "volume": r[5], "pct": r[6],
                         "high_limit": r[7], "low_limit": r[8],
                         "is_st": r[9], "is_paused": r[10]})
        return bars
    finally:
        conn.close()


def _suffix(code):
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code                      # 已带后缀（all_codes_zz 返回的即是）
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    return f"{code}.SH"


def all_codes_zz(start=None, end=None):
    conn = sqlite3.connect(str(ZZDB))
    try:
        q = "SELECT DISTINCT code FROM daily"
        params = []
        if start:
            q += " WHERE date>=?"
            params.append(start)
        return [r[0] for r in conn.execute(q, params)]
    finally:
        conn.close()


def all_codes_db():
    from app.database import db
    rows = db.fetch("SELECT DISTINCT code FROM backtest_prices ORDER BY code")
    return [r["code"] for r in (rows or [])]


# ================================================================
# 形态扫描（单股 → 信号列表）
# ================================================================
def scan_stock(bars, cfg):
    """返回该股信号列表，每个信号：{date, idx, vol_ratio, launch_pct, ...}"""
    n = len(bars)
    pd, vb = cfg["pullback_days"], cfg["vol_base_days"]
    need = pd + vb
    sigs = []
    last_sig_idx = -10 ** 9
    for t in range(need, n):
        # 冷却
        if t - last_sig_idx < cfg["cooldown"]:
            continue
        a = bars[t - pd:t]                 # 调整段（不含 T）
        b = bars[t - pd - vb:t - pd]       # 基准段
        vol_a = statistics.mean(x["volume"] for x in a)
        vol_b = statistics.mean(x["volume"] for x in b)
        if vol_b <= 0 or vol_a <= 0:
            continue
        # 段 A 缩量调整
        if vol_a / vol_b > cfg["vol_shrink"]:
            continue
        ret_a = bars[t - 1]["close"] / bars[t - pd - 1]["close"] - 1
        if not (cfg["pullback_min"] <= ret_a <= cfg["pullback_max"]):
            continue
        high_b = max(x["high"] for x in b)
        if max(x["high"] for x in a) > high_b * (1 + cfg["below_high_tol"]):
            continue
        # 段 B 启动日
        pct_t = bars[t]["pct"] if bars[t]["pct"] is not None else \
            (bars[t]["close"] / bars[t - 1]["close"] - 1) * 100
        if pct_t < cfg["launch_pct"]:
            continue
        if bars[t]["volume"] < cfg["vol_surge"] * vol_a:
            continue
        if not cfg["breakout"]:
            pass
        elif bars[t]["close"] <= max(x["high"] for x in a):
            continue
        # ST / 停牌
        if bars[t].get("is_st") == 1:
            continue
        if bars[t].get("is_paused") == 1:
            continue
        sigs.append({"date": bars[t]["date"], "idx": t,
                     "vol_ratio": vol_a / vol_b,
                     "pullback_ret": ret_a,
                     "launch_pct": pct_t,
                     "surge": bars[t]["volume"] / vol_a})
        last_sig_idx = t
    return sigs


# ================================================================
# 评估：前视指标 + 一字板剔除
# ================================================================
# ★ 剔除记账（口径透明：每个被丢掉的样本都要说得出理由）——2026-10-15 B 项
REJECT_STATS = {"no_t1": 0, "paused": 0, "limit_open": 0, "limit_close": 0,
                "no_fwd": 0, "ok": 0}


def evaluate(bars, sig, cfg):
    """返回 dict；若买不进（T+1 停牌/一字板）或无前视数据 → None（并记账）。"""
    t = sig["idx"]
    fwd = cfg["fwd_days"]
    if cfg["entry"] == "t1_open":
        if t + 1 >= len(bars):
            return None
        entry = bars[t + 1]["open"]
        i0 = t + 1
    elif cfg["entry"] == "t1_close":
        if t + 1 >= len(bars):
            return None
        entry = bars[t + 1]["close"]
        i0 = t + 1
    else:  # t_close
        entry = bars[t]["close"]
        i0 = t
    if entry <= 0:
        REJECT_STATS["no_t1"] += 1
        return None
    t1 = bars[t + 1] if t + 1 < len(bars) else None
    # ★ 可执行性①：T+1 停牌 ⇒ 开盘根本买不进
    if t1 is not None and t1.get("is_paused") == 1:
        REJECT_STATS["paused"] += 1
        return None
    # ★ 可执行性②：T+1 开盘即涨停（一字/秒板）⇒ 买不进。
    #   用**交易所涨停价**（zzshare `high_limit`）判 ⇒ 精确覆盖 10cm/20cm/30cm，
    #   与项目先例 shrink_rebound_live 同口径（open ≥ high_limit×0.995）。
    if t1 is not None:
        if t1.get("high_limit"):
            if t1["open"] >= t1["high_limit"] * 0.995:
                REJECT_STATS["limit_open"] += 1
                return None
        elif t1.get("pct") is not None and t1["open"] / bars[t]["close"] - 1 >= 0.098:
            REJECT_STATS["limit_open"] += 1
            return None
    # ★ 可执行性③（可选，默认关）：信号日 T 涨停收盘 ⇒ 次日多半高开抢筹，
    #   属"看得见吃不到"，更保守的口径可剔除。
    if cfg.get("exclude_limit_close"):
        hl = bars[t].get("high_limit")
        if hl and bars[t]["close"] >= hl * 0.995:
            REJECT_STATS["limit_close"] += 1
            return None
    end_idx = i0 + fwd
    if end_idx >= len(bars):
        REJECT_STATS["no_fwd"] += 1
        return None
    seg = bars[i0:end_idx + 1]
    ret = bars[end_idx]["close"] / entry - 1
    max_up = max(x["high"] for x in seg) / entry - 1
    max_dd = min(x["low"] for x in seg) / entry - 1
    REJECT_STATS["ok"] += 1
    cost = cfg.get("cost", 0.0) or 0.0
    # ★ 落地口径：净收益 = 毛收益 − 双边成本（与 shrink_rebound_live 的 COST=0.003 同源）
    return {"date": sig["date"], "entry": entry, "ret": ret, "ret_net": ret - cost,
            "cost": cost, "max_up": max_up, "max_dd": max_dd,
            "hit": max_up >= cfg["target"]}


# ================================================================
# 基准（无条件 T+N 收益）
# ================================================================
def unconditional_returns(bars, fwd, entry_mode, limit=None, min_idx=0):
    """每根 bar 的 T+N 收益（T+1 开盘入场，最多 limit 根，降采样）。

    ★★ 2026-10-15 修正两处（都会静默把"超额"算歪）：
      ① **`min_idx`**：基准取样必须与信号**同一资格**（≥ pullback+base 根历史）——
         否则基准里混进**新股上市连板期**（2018-2026 大量 IPO 连板，10 日 +50% 级），
         而信号天然排除新股（需 80 根历史）⇒ 基准被抬高、超额被压低。
         实测：修前全市场基准 +2.50%/10日（离谱，A股均值量级应在 0.x%）。
      ② **出场日对齐**：信号是 `T+1 开盘 → T+1+fwd 收盘`（持有 fwd 日），基准原先写成
         `T+1 开盘 → T+fwd 收盘`（少持一日）⇒ 少持一日在上涨市会低估基准。
    """
    out = []
    step = max(1, len(bars) // 2000) if limit is None else 1
    hi = len(bars) - fwd - 1                 # 需 bars[i+1+fwd] 存在（与信号口径一致）
    idxs = range(min_idx, max(min_idx, hi), step)
    if limit is not None:
        idxs = list(idxs)
        random.Random(0).shuffle(idxs)
        idxs = idxs[:limit]
    for i in idxs:
        entry = bars[i + 1]["open"] if entry_mode == "t1_open" else bars[i]["close"]
        if entry <= 0:
            continue
        out.append(bars[i + 1 + fwd]["close"] / entry - 1)
    return out


# ================================================================
# 统计汇总
# ================================================================
def summarize(rets, sig_dates, cfg):
    if not rets:
        return None
    n = len(rets)
    win = sum(1 for r in rets if r > 0) / n
    avg = statistics.mean(rets)
    med = statistics.median(rets)
    std = statistics.pstdev(rets)
    return {"n": n, "clusters": len(set(sig_dates)), "win": win, "avg": avg,
            "median": med, "std": std}


def clustered_pvalue(sig_rets, sig_dates, baseline_rets, cfg):
    """按信号日聚类 bootstrap：P(信号组均值 ≤ 基准均值)。"""
    rng = random.Random(cfg["seed"])
    m_sig = statistics.mean(sig_rets)
    m_base = statistics.mean(baseline_rets) if baseline_rets else 0
    obs = m_sig - m_base
    # 信号组按簇聚类重采样
    clusters = defaultdict(list)
    for d, r in zip(sig_dates, sig_rets):
        clusters[d].append(r)
    keys = list(clusters)
    csum = sum(sum(v) for v in clusters.values())
    cnt = sum(len(v) for v in clusters.values())
    worse = 0
    B = cfg["bootstrap"]
    for _ in range(B):
        s = sum(sum(clusters[rng.choice(keys)]) for _ in range(len(keys)))
        if s / cnt <= m_base:      # 重采样均值 ≤ 基准
            worse += 1
    return worse / B, obs


# ================================================================
# SVG 可视化（零依赖，自包含 HTML）
# ================================================================
def _polyline(points, color, width=1.0, dash=None):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}"{d}/>'


def _svg_axes(w, h, xmin, xmax, ymin, ymax, pad=30):
    sx = (w - 2 * pad) / max(1e-9, xmax - xmin)
    sy = (h - 2 * pad) / max(1e-9, ymax - ymin)
    return sx, sy, pad


def chart_event_study(paths, median, x0, x1):
    """事件研究图：T 对齐归一化路径 + 中位数。paths: [(xs, ys) ...] 已归一化。"""
    w, h = 760, 340
    xmin, xmax = x0, x1
    if not paths and not median:
        return f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="background:#0b1220"><text x="30" y="40" fill="#64748b" font-size="12">'
        f'样本不足，无事件研究路径</text></svg>'
    ys_all = [y for _, ys in paths for y in ys] + list(median) + [0.0]
    ymin = min(ys_all) - 0.02
    ymax = max(ys_all) + 0.02
    sx, sy, pad = _svg_axes(w, h, xmin, xmax, ymin, ymax)
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
           f'style="background:#0b1220;font:10px sans-serif">']
    # 零线
    svg.append(f'<line x1="{pad}" y1="{h-pad-(0-ymin)*sy}" x2="{w-pad}" y2="{h-pad-(0-ymin)*sy}" '
               f'stroke="#334155" stroke-width="1"/>')
    for xs, ys in paths:
        pts = [(pad + (x - xmin) * sx, h - pad - (y - ymin) * sy) for x, y in zip(xs, ys)]
        svg.append(_polyline(pts, "#475569", 0.7))
    mpts = [(pad + (x - xmin) * sx, h - pad - (y - ymin) * sy) for x, y in zip(range(x0, x1 + 1), median)]
    svg.append(_polyline(mpts, "#22d3ee", 2.2))
    svg.append(f'<text x="{pad}" y="{h-6}" fill="#64748b">T 前 {x0} 日 → T 后 {x1} 日（中位数=青，单样本=灰）</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_hist(sig, base, label):
    w, h = 760, 260
    allv = (sig + base) or [0]
    lo, hi = min(allv), max(allv)
    lo = min(lo, -0.05)
    hi = max(hi, 0.05)
    nb = 30
    bw = (hi - lo) / nb

    def bins(vals):
        b = [0] * nb
        for v in vals:
            i = int((v - lo) / bw)
            i = max(0, min(nb - 1, i))
            b[i] += 1
        return b

    bs, bb = bins(sig), bins(base)
    mx = max(max(bs) if bs else 0, max(bb) if bb else 0, 1)
    pad = 30
    sx = (w - 2 * pad) / nb
    sy = (h - 2 * pad) / mx
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
           f'style="background:#0b1220;font:10px sans-serif">']
    for i in range(nb):
        x = pad + i * sx
        if bb[i]:
            svg.append(f'<rect x="{x:.1f}" y="{h-pad-bb[i]*sy:.1f}" width="{sx*0.45:.1f}" '
                       f'height="{bb[i]*sy:.1f}" fill="#334155"/>')
        if bs[i]:
            svg.append(f'<rect x="{(x+sx*0.5):.1f}" y="{h-pad-bs[i]*sy:.1f}" width="{sx*0.45:.1f}" '
                       f'height="{bs[i]*sy:.1f}" fill="#22d3ee"/>')
    svg.append(f'<text x="{pad}" y="{h-6}" fill="#64748b">收益分布（青=信号，灰=基准）· 横轴 T+{label} 收益</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_by_year(rows):
    w, h = 760, 240
    if not rows:
        return ""
    years = [r[0] for r in rows]
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
           f'style="background:#0b1220;font:10px sans-serif">']
    pad = 30
    bw = (w - 2 * pad) / len(years)
    mxn = max(r[1] for r in rows)
    for i, (y, n, winr, _avg) in enumerate(rows):
        x = pad + i * bw
        svg.append(f'<rect x="{x+4:.1f}" y="{h-pad-(n/mxn)*(h-2*pad):.1f}" width="{bw-8:.1f}" '
                   f'height="{(n/mxn)*(h-2*pad):.1f}" fill="#1e293b"/>')
        svg.append(f'<text x="{x+bw/2:.1f}" y="{h-pad+12}" fill="#94a3b8" text-anchor="middle">{y}</text>')
        svg.append(f'<text x="{x+bw/2:.1f}" y="{h-pad-(n/mxn)*(h-2*pad)-4:.1f}" fill="#22d3ee" '
                   f'text-anchor="middle">{n}</text>')
        svg.append(f'<text x="{x+bw/2:.1f}" y="{h-pad-(n/mxn)*(h-2*pad)-16:.1f}" fill="#fbbf24" '
                   f'text-anchor="middle">{winr*100:.0f}%</text>')
    svg.append(f'<text x="{pad}" y="{h-6}" fill="#64748b">分年度：样本数（青）与胜率（黄）</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_price(bars, sig_dates, code):
    w, h = 760, 300
    pad = 30
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    ymin, ymax = min(closes), max(closes)
    vmax = max(vols) or 1
    sx = (w - 2 * pad) / max(1, len(closes) - 1)
    sy = (h - 2 * pad - 60) / max(1e-9, ymax - ymin)
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
           f'style="background:#0b1220;font:10px sans-serif">']
    pts = [(pad + i * sx, h - pad - 60 - (c - ymin) * sy) for i, c in enumerate(closes)]
    svg.append(_polyline(pts, "#22d3ee", 1.2))
    # 成交量
    for i, v in enumerate(vols):
        x = pad + i * sx
        svg.append(f'<rect x="{x:.1f}" y="{h-pad-(v/vmax)*50:.1f}" width="{sx*0.8:.1f}" '
                   f'height="{(v/vmax)*50:.1f}" fill="#334155"/>')
    # 信号标注
    for d in sig_dates:
        for i, b in enumerate(bars):
            if b["date"] == d:
                x = pad + i * sx
                y = h - pad - 60 - (b["close"] - ymin) * sy
                svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#fbbf24"/>')
                break
    svg.append(f'<text x="{pad}" y="{h-6}" fill="#64748b">{code} 收盘价（上）+成交量（下），黄点=信号日</text>')
    svg.append("</svg>")
    return "".join(svg)


def build_html(cfg, code_tag, stats, sigs_detail, paths, median, x0, x1, sig_rets,
               base_rets, fwd, by_year, price_svg):
    title = f"{code_tag} · 缩量调整→放量启动 形态胜率"
    s = stats
    cards = ""
    if s:
        cards = (f"<div class='cards'>"
                 f"<div><b>{s['n']}</b><span>样本数</span></div>"
                 f"<div><b>{s['clusters']}</b><span>独立信号日</span></div>"
                 f"<div><b>{s['win']*100:.1f}%</b><span>胜率(T+{fwd})</span></div>"
                 f"<div><b>{s['avg']*100:+.2f}%</b><span>均收益</span></div>"
                 f"<div><b>{s['median']*100:+.2f}%</b><span>中位数</span></div>"
                 f"</div>")
    if not stats:
        panels = '<div class="note">样本数不足，无统计图表。</div>'
    else:
        panels = f"<h2>事件研究（T 对齐）</h2>{chart_event_study(paths, median, x0, x1)}" \
                 f"<h2>收益分布 vs 基准</h2>{chart_hist(sig_rets, base_rets, fwd)}" \
                 f"<h2>分年度</h2>{chart_by_year(by_year)}"
    if price_svg:
        panels += f"<h2>价格与信号</h2>{price_svg}"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{background:#0a0f1c;color:#cbd5e1;font:14px/1.6 -apple-system,'Segoe UI',sans-serif;margin:0;padding:24px}}
h1{{font-size:20px;color:#e2e8f0}}h2{{font-size:14px;color:#94a3b8;margin:28px 0 8px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.cards div{{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px 18px;min-width:110px}}
.cards b{{display:block;font-size:22px;color:#22d3ee}}
.cards span{{font-size:11px;color:#64748b}}
svg{{border:1px solid #1e293b;border-radius:8px;display:block;margin-bottom:10px}}
.note{{color:#64748b;font-size:11px;max-width:820px}}
</style></head><body>
<h1>{title}</h1>
<div class="note">参数：调整 {cfg['pullback_days']}日 · 缩量≤{cfg['vol_shrink']} · 启动涨≥{cfg['launch_pct']}% · 放量≥{cfg['vol_surge']}× · 前视 T+{fwd} · 入场 {cfg['entry']} · 源 {cfg['source_used']}</div>
{cards}
{panels}
</body></html>"""


# ================================================================
# 报告
# ================================================================
def write_reports(cfg, code_tag, stats, pval, obs, sigs_detail, by_year,
                  base_stats, html, fwd, stats_net=None, pval_net=None, obs_net=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    tag = (code_tag or "ALL").replace(".", "_")
    md_path = REPORT_DIR / f"pattern_launch_{tag}_{stamp}.md"
    lines = []
    lines.append(f"# 缩量调整→放量启动 形态胜率（{code_tag or '全市场'}）\n")
    lines.append(f"> 生成 {time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 数据源 `{cfg['source_used']}`\n")
    lines.append("## 参数快照\n")
    for k in ("pullback_days", "vol_base_days", "vol_shrink", "pullback_min", "pullback_max",
              "below_high_tol", "launch_pct", "vol_surge", "breakout", "cooldown",
              "entry", "fwd_days", "target", "baseline", "baseline_eligibility",
              "seed", "min_samples"):
        lines.append(f"- `{k}` = {cfg.get(k)}")
    lines.append("")
    if stats is None:
        lines.append("**样本数不足，未形成统计。**\n")
    else:
        lines.append("## 统计结果\n")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| 样本数 | {stats['n']} |")
        lines.append(f"| 独立信号日（簇） | {stats['clusters']} |")
        lines.append(f"| 胜率（T+{fwd} 收益>0） | {stats['win']*100:.1f}% |")
        lines.append(f"| 平均收益 | {stats['avg']*100:+.2f}% |")
        lines.append(f"| 中位收益 | {stats['median']*100:+.2f}% |")
        lines.append(f"| 标准差 | {stats['std']*100:.2f}% |")
        if base_stats:
            lines.append(f"| 基准（无条件）均收益 | {base_stats['avg']*100:+.2f}% |")
            lines.append(f"| **毛超额**（信号−基准） | {obs*100:+.2f}pp |")
            lines.append(f"| 聚类 bootstrap P(均值≤基准) | {pval:.3f} |")
        if stats_net and base_stats:
            lines.append(f"| ★ 净均收益（扣成本 {cfg.get('cost', 0)*100:.2f}%） | "
                         f"{stats_net['avg']*100:+.2f}% |")
            lines.append(f"| ★ 净胜率 | {stats_net['win']*100:.1f}% |")
            lines.append(f"| ★ **净超额**（落地） | **{obs_net*100:+.2f}pp** |")
            lines.append(f"| ★ 聚类 bootstrap P（净） | {pval_net:.3f} |")
            lines.append(f"| ★ 判定线（对齐 shrink_rebound_live：落地 ≥ +1.00pp） | "
                         f"{'**过关**' if obs_net >= 0.01 else '**不过关**'} |")
        if stats["clusters"] < cfg["min_samples"]:
            lines.append(f"\n⚠️ **独立信号日 {stats['clusters']} < {cfg['min_samples']} ⇒ INSUFFICIENT**（数字仅供参考）\n")
        lines.append("\n## 达标 / 回撤\n")
        hit = sum(1 for d in sigs_detail if d["hit"]) / max(1, len(sigs_detail))
        avg_up = statistics.mean(d["max_up"] for d in sigs_detail)
        avg_dd = statistics.mean(d["max_dd"] for d in sigs_detail)
        lines.append(f"- 达标率（区间最大涨幅 ≥ {cfg['target']*100:.0f}%）：**{hit*100:.1f}%**")
        lines.append(f"- 区间最大涨幅均值：**{avg_up*100:+.2f}%**；区间最大回撤均值：**{avg_dd*100:+.2f}%**")
        lines.append("\n## 分年度\n")
        lines.append("| 年度 | 样本数 | 胜率 | 均收益 |")
        lines.append("|---|---|---|---|")
        for y, n, wr, ar in by_year:
            lines.append(f"| {y} | {n} | {wr*100:.1f}% | {ar*100:+.2f}% |")
        lines.append("\n## 最近信号明细\n")
        lines.append("| 日期 | 缩量倍数 | 调整段收益 | 启动涨幅 | 放量倍数 | 毛收益 | 净收益 | 最大涨幅 | 最大回撤 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in sorted(sigs_detail, key=lambda x: x["date"], reverse=True)[:25]:
            lines.append(f"| {d['date']} | {d['vol_ratio']:.2f} | {d['pullback_ret']*100:+.1f}% | "
                         f"{d['launch_pct']:+.1f}% | {d['surge']:.1f}× | {d['ret']*100:+.2f}% | "
                         f"{d.get('ret_net', d['ret'])*100:+.2f}% | "
                         f"{d['max_up']*100:+.1f}% | {d['max_dd']*100:+.1f}% |")
    # ★ 剔除记账（口径透明）
    r = REJECT_STATS
    tot = r["ok"] + r["limit_open"] + r["paused"] + r["no_fwd"] + r.get("limit_close", 0)
    lines.append("\n## 可执行性剔除记账\n")
    lines.append(f"- 候选信号 {tot} ｜ **有效 {r['ok']}**（{r['ok']/tot*100:.1f}%）")
    lines.append(f"- 剔除：T+1 开盘即涨停（买不进）**{r['limit_open']}** ｜ T+1 停牌 "
                 f"**{r['paused']}** ｜ 前视数据不足 **{r['no_fwd']}**"
                 + (f" ｜ 信号日涨停收盘 **{r.get('limit_close', 0)}**"
                    if cfg.get("exclude_limit_close") else ""))
    lines.append("\n## 已知简化\n")
    lines.append("- 收益用原始收盘比，除权跳空未复权；一字板剔除在 db 源为近似（+9.8% 阈值）。")
    lines.append("- 描述性研究，**不构成预登记判据**；样本闸不足时结论仅方向参考。\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    html_path = md_path.with_suffix(".html")
    if html:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    return md_path, html_path


# ================================================================
# 主流程
# ================================================================
def run_single(cfg, code, loader):
    bars = loader(code, cfg["start"], cfg["end"])
    if len(bars) < cfg["pullback_days"] + cfg["vol_base_days"] + 2:
        print(f"[{code}] 数据不足（{len(bars)} 根）")
        return
    sigs = scan_stock(bars, cfg)
    sigs_detail = []
    for s in sigs:
        ev = evaluate(bars, s, cfg)
        if ev:
            ev.update({"vol_ratio": s["vol_ratio"], "pullback_ret": s["pullback_ret"],
                       "launch_pct": s["launch_pct"], "surge": s["surge"]})
            sigs_detail.append(ev)
    return bars, sigs, sigs_detail        # ★ 同时返回原始 sigs，避免调用方重复扫描


def main(argv=None):
    args = parse_args(argv)
    cfg = vars(args)
    cfg["fwd_days"] = [int(x) for x in args.fwd.split(",")]
    cfg["breakout"] = not args.no_breakout
    cfg["source_used"] = args.source

    if args.selftest:
        return selftest()

    _load_env()

    # 数据源选择
    if args.all:
        if args.source == "db":
            codes = all_codes_db()
            loader = load_bars_db
        else:
            codes = all_codes_zz(args.start, args.end)
            loader = load_bars_zz
        cfg["source_used"] = "db" if args.source == "db" else "zzshare"
        if args.limit:
            codes = codes[:args.limit]
        print(f"[scan] 全市场 {len(codes)} 只 · 源 {cfg['source_used']} · 起 {args.start or '全历史'}")
    elif args.code:
        codes = [args.code]
        if args.source == "zzshare":
            loader = load_bars_zz
        elif args.source == "db":
            loader = load_bars_db
        else:  # auto：单股优先 db（新鲜、含最近启动）
            db_bars = load_bars_db(args.code, args.start, args.end)
            if db_bars and len(db_bars) >= 250:
                loader = load_bars_db
                cfg["source_used"] = "db"
            else:
                loader = load_bars_zz
                cfg["source_used"] = "zzshare"
        print(f"[scan] {args.code} · 源 {cfg['source_used']}")
    else:
        print("请指定 --code 或 --all")
        return 1

    # 全市场模式下，self 基准（逐股）不聚合 ⇒ 默认切成 market（全市场无条件 T+N）
    if args.all and args.baseline == "self":
        cfg["baseline"] = "market"

    fwd = cfg["fwd_days"][0]          # 主窗口 = 第一个
    cfg["fwd_days"] = fwd

    all_sigs = []
    all_paths = []
    all_rets = []
    all_rets_net = []                     # ★ 落地口径（扣双边成本）
    all_dates = []
    base_rets_all = []
    bars_single = None
    rng_p = random.Random(cfg["seed"])     # 图表路径抽样（蓄水池）
    path_seen = 0

    t_start = time.time()
    for i_code, code in enumerate(codes, 1):
        r = run_single(cfg, code, loader)
        if not r:
            if i_code % 500 == 0:      # ★ 进度（全量要 20 分钟，没有进度无法判断死活）
                print(f"  [progress] {i_code}/{len(codes)} ｜ 信号 {len(all_rets)} ｜ "
                      f"{time.time() - t_start:.0f}s", flush=True)
            continue
        bars, sigs_raw, sigs = r
        if code == args.code:
            bars_single = bars
        if sigs:
            all_sigs.extend(sigs)
            for s in sigs:
                all_rets.append(s["ret"])
                all_rets_net.append(s.get("ret_net", s["ret"]))
                all_dates.append(s["date"])
            # 事件研究路径（归一化到 T 日）—— ★ 蓄水池抽样：全量信号上万条时
            #   逐条画 SVG 会让 HTML 膨胀到几十 MB 且极慢 ⇒ 只保留 max_paths 条
            for s in sigs_raw:
                t = s["idx"]
                x0 = -cfg["pullback_days"]
                seg = bars[t + x0:t + fwd + 1]
                if len(seg) < (fwd - x0):
                    continue
                base = bars[t]["close"]
                xs = list(range(x0, fwd + 1))[:len(seg)]
                ys = [b["close"] / base for b in seg][:len(xs)]
                path_seen += 1
                if len(all_paths) < cfg["max_paths"]:
                    all_paths.append((xs, ys))
                else:
                    j = rng_p.randrange(path_seen)
                    if j < cfg["max_paths"]:
                        all_paths[j] = (xs, ys)
        # 基准：self（单股）或 market（全市场，抽样）—— ★ min_idx 与信号同一资格
        _min_idx = (cfg["pullback_days"] + cfg["vol_base_days"]
                    if cfg["baseline_eligibility"] == "same" else 0)
        if cfg["baseline"] == "self" and not args.all:
            base_rets_all.extend(unconditional_returns(
                bars, fwd, cfg["entry"], limit=1500, min_idx=_min_idx))
        elif cfg["baseline"] == "market":
            base_rets_all.extend(unconditional_returns(
                bars, fwd, cfg["entry"], limit=4, min_idx=_min_idx))
        if i_code % 500 == 0:          # ★ 进度（flush 保证日志能实时看到）
            print(f"  [progress] {i_code}/{len(codes)} ｜ 信号 {len(all_rets)} ｜ "
                  f"{time.time() - t_start:.0f}s", flush=True)

    print(f"[done] 信号样本 {len(all_rets)} 个（{len(set(all_dates))} 个独立信号日）")
    if all_dates:
        recent = sorted(set(all_dates))[-6:]
        print(f"  最近信号日: {', '.join(recent)}")

    stats = summarize(all_rets, all_dates, cfg)
    pval = obs = None
    base_stats = summarize(base_rets_all, list(range(len(base_rets_all))), cfg)
    if stats and base_rets_all:
        pval, obs = clustered_pvalue(all_rets, all_dates, base_rets_all, cfg)
    # ★ 落地口径（净 = 毛 − 成本）：同样给超额与 bootstrap P
    stats_net = summarize(all_rets_net, all_dates, cfg)
    pval_net = obs_net = None
    if stats_net and base_rets_all:
        pval_net, obs_net = clustered_pvalue(all_rets_net, all_dates, base_rets_all, cfg)

    # 中位数路径
    median = []
    if all_paths:
        for i in range(-cfg["pullback_days"], fwd + 1):
            vals = [ys[xi] for xs, ys in all_paths for xi, x in enumerate(xs) if x == i]
            median.append(statistics.median(vals) if vals else 1.0)

    # 分年度
    by_year = []
    if all_dates:
        by = defaultdict(list)
        for d, r in zip(all_dates, all_rets):
            by[d[:4]].append(r)
        for y in sorted(by):
            by_year.append((y, len(by[y]), sum(1 for r in by[y] if r > 0) / len(by[y]),
                            statistics.mean(by[y])))

    # 控制台摘要
    if stats:
        print(f"  样本 {stats['n']} ｜ 独立日 {stats['clusters']} ｜ 胜率 {stats['win']*100:.1f}% "
              f"｜ 均 {stats['avg']*100:+.2f}% ｜ 中位 {stats['median']*100:+.2f}%")
        if pval is not None:
            print(f"  基准均 {base_stats['avg']*100:+.2f}% ｜ 毛超额 {obs*100:+.2f}pp "
                  f"｜ P(≤基准)={pval:.3f}")
        if stats_net and obs_net is not None:
            print(f"  ★落地（扣成本 {cfg['cost']*100:.2f}%）：净均 {stats_net['avg']*100:+.2f}% "
                  f"｜ 净胜率 {stats_net['win']*100:.1f}% ｜ **净超额 {obs_net*100:+.2f}pp** "
                  f"｜ P={pval_net:.3f} ｜ 判定线 +1.00pp ⇒ "
                  f"{'过关' if obs_net >= 0.01 else '不过关'}")
        if stats["clusters"] < cfg["min_samples"]:
            print(f"  ⚠ INSUFFICIENT：独立信号日 {stats['clusters']} < {cfg['min_samples']}")
    r = REJECT_STATS
    print(f"  剔除记账：一字板(T+1开盘涨停)={r['limit_open']} 停牌={r['paused']} "
          f"无前视={r['no_fwd']}"
          + (f" 涨停收盘={r['limit_close']}" if cfg.get("exclude_limit_close") else "")
          + f" ｜ 有效样本={r['ok']}")

    price_svg = chart_price(bars_single, all_dates, args.code) if bars_single and args.code else ""
    html = None
    if not args.no_html:
        html = build_html(cfg, args.code or "ALL", stats, all_sigs, all_paths,
                          median, -cfg["pullback_days"], fwd, all_rets, base_rets_all,
                          fwd, by_year, price_svg)
    md_path, html_path = write_reports(cfg, args.code or "ALL", stats, pval, obs,
                                       all_sigs, by_year, base_stats, html, fwd,
                                       stats_net, pval_net, obs_net)
    print(f"[report] {md_path}")
    if html_path:
        print(f"[report] {html_path}")
    return 0


# ================================================================
# 自测（合成数据验证扫描器逻辑）
# ================================================================
def selftest():
    cfg = dict(pullback_days=20, vol_base_days=60, vol_shrink=0.6, pullback_min=-0.25,
               pullback_max=0.03, below_high_tol=0.0, launch_pct=5.0, vol_surge=2.0,
               breakout=True, cooldown=10, fwd_days=10, entry="t1_open", target=0.10)

    def mk(seq):
        bars, d = [], 0
        for (cl, vol) in seq:
            bars.append({"date": f"2020-01-{d+1:02d}", "open": cl, "high": cl * 1.01,
                         "low": cl * 0.99, "close": cl, "volume": vol, "pct": None,
                         "is_st": 0, "high_limit": None, "low_limit": None, "is_paused": 0})
            d += 1
        for i in range(1, len(bars)):
            bars[i]["pct"] = (bars[i]["close"] / bars[i - 1]["close"] - 1) * 100
        return bars

    # 形态1：基准期上涨→调整期缩量回调（低于基准高点）→启动放量突破
    seq = []
    for i in range(60):
        seq.append((10.0 + 0.008 * i, 1000))   # 基准期（涨，量大）
    for i in range(20):
        seq.append((10.4 - 0.02 * i, 300))     # 调整期（缩量回调到 ~10.02）
    seq.append((11.0, 1200))                   # 启动日 +~9.8% 放量4x 突破
    seq += [(11.3 + 0.05 * i, 800) for i in range(15)]   # 前视（继续上行）
    bars = mk(seq)
    sigs = scan_stock(bars, cfg)
    assert len(sigs) == 1, f"应命中 1 个信号，实际 {len(sigs)}"
    assert sigs[0]["idx"] == 80, "信号日应在 idx=80"
    ev = evaluate(bars, sigs[0], cfg)
    assert ev and ev["ret"] > 0, "前视收益应>0"

    # 形态2：无缩量（均量不降）⇒ 不命中
    seq = []
    for _ in range(80):
        seq.append((10.0, 1000))
    seq.append((11.0, 1200))
    seq += [(11.3, 800)] * 15
    sigs2 = scan_stock(mk(seq), cfg)
    assert len(sigs2) == 0, f"无缩量应不命中，实际 {len(sigs2)}"

    # 形态3：放量但不突破调整期高点 ⇒ 不命中
    seq = []
    for _ in range(60):
        seq.append((10.0, 1000))
    for _ in range(20):
        seq.append((9.8, 300))
    seq.append((9.9, 1200))              # 涨但不突破 10.0 高点
    seq += [(9.9, 800)] * 15
    sigs3 = scan_stock(mk(seq), cfg)
    assert len(sigs3) == 0, f"未突破应不命中，实际 {len(sigs3)}"

    print("SELFTEST PASS：扫描器 3 组用例全过（命中/无缩量/未突破）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
