#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「反弹 vs 反转」组合信号**预登记**检验（PLAN_RESERVE_SIGNALS §6.3 = P1-1）
================================================================================
背景（为什么必须这么写）：
  PLAN §5 的教训：24 格矩阵（6 战法×4 态）19 负 5 正、行业动量无预测力、纳指隔夜 IC 塌陷
  ⇒ **不要发明新的形态反转信号**。反转识别只能做两件事：(a) 用已验证的 E2/事件框架；
  (b) **新想法一律走「预登记 + 进入点检验 + block bootstrap」**。
  2026-09-23 的 `reversal_edge_check.py`（指数冲高回落）已验证**形态本身不携带方向信息**；
  本脚本回答**下一问**：**「跌过一波」之后，加一个宏观 risk-on 确认（金银比回落），
  能不能把「反弹」升级成「反转」？**（PLAN §1 的目标：把叙事变成可检验命题）

────────────────────────────────────────────────────────────────────────────────
【预登记判据（2026-10-10 写死，跑之前就定；改判据须在文件末尾新开一节并注明日期）】
  标的：`sh000300`（沪深300）—— ★ 数据决定：本地数据包 klines 里**只有它**这一条宽基指数
        （`sh000852`/`sh000001`/`sh000905` 实测 0 根），口径与 `reversal_edge_check` 同源。
  三条腿（阈值均为**预登记**，非事后调参）：
    L1「跌过」    ：收盘 ≤ 20 日高点 × 0.95          （回撤 ≥ 5%）
    L2「risk-on」：金银比 5 日变化 ≤ −1.0%           （黄金相对白银走弱 = 避险退潮）
    L3「宽度修复」：前 5 日 up_ratio 均值 ≤ 0.35 **且** 当日 up_ratio ≥ 0.50（由弱转强）
  组：
    A  = L1 & L2            ← **主判定**（宏观确认能否改善反弹的延续性）
    A' = L1 & L2 & L3       ← 副（**仅描述**，因宽度口径有偏，见下「口径局限」）
    B  = L1 only            ← **最相关对照**（同样跌过一波，但没有 risk-on 确认）
    C  = 全样本             ← 市场基准（控制样本期趋势）
  入场点：信号日**收盘**（与 `reversal_edge_check` 同口径）。
  持有期：T+1 / T+5 / T+20；**主判定只看 T+5**（其余为交叉验证，避免多重比较挑格子）。
  统计（预登记）：
    · **block bootstrap**：信号日之间间隔 ≥ 10 个交易日即切块（处理重叠样本），
      对 A、B 两臂**各自的块**有放回重采样，B = 10000 次，固定 `seed=20261010`（可复现）
      ⇒ 报告 diff 的 95% 分位区间与**单侧 P(diff* ≤ 0)**。
    · **复利净值**：信号日**不重叠**采样（持满 T+5 再接受下一个信号）连乘 − 1，
      基准 = 同期指数 buy&hold ⇒ 报告超额。
    · **分年分解**：逐年 n / 均值 / 胜率（看是否靠单一年份）。
  判定（预登记阈值）：
    GO        ：T+5 的 diff(A−B) ≥ **+0.50pct** 且 单侧 P ≤ **0.05**
    DOWNGRADE ：diff > 0 但未达 GO（方向对、证据不足 ⇒ 记录，不采用）
    KILL      ：diff ≤ 0 或 P ≥ 0.20（⇒ 关闭该组合，写进「已证伪」清单）
  样本门槛：信号日 n ≥ **30**，否则 INSUFFICIENT（**样本是时钟，开发加速不了**）。

────────────────────────────────────────────────────────────────────────────────
【首跑记录（2026-10-10，样本期 2023-09-05 .. 2026-10-09 / 746 根）】
  group                      n |  T+1            |  T+5            |  T+20
  A  L1&L2 (signal)         29 | +0.05% / 55.2%  | +0.53% / 62.1%  | +2.57% / 62.1%
  A' L1&L2&L3 (sub)          1 | −0.18% / 0.0%   | +0.64% / 100%   | +3.26% / 100%
  B  L1 only (control)      87 | +0.27% / 60.5%  | +0.88% / 68.7%  | +1.68% / 62.2%
  C  all days              726 | +0.03% / 51.4%  | +0.15% / 51.2%  | +0.75% / 50.0%
  ⇒ **A n=29 < 30 ⇒ 判据要求报 INSUFFICIENT**（**不因"就差 1 例"就放宽预登记门槛**）。
  ⇒ 首跑读数（**未达判定门槛，不作结论**）：T+5 的 **A − B = −0.35pct**（加了金银比
     risk-on 确认反而**更差**）；而 B(L1 only) 相对全样本 C 明显为正（+0.88% vs +0.15%）
     ⇒ 与项目既有认知一致（**「跌过一波」本身自带反弹期望**），而"宏观确认"这个加法
     **暂无证据**。
  ⇒ 副腿 A'（再叠 L3 宽度修复）**只剩 1 例** ⇒ PLAN §6.3 的原始组合式（三腿齐备）**在 3 年
     样本里太稀有、不可检验**；L3 若要用，须**另行预登记更宽的定义**（不得事后调参）。
  ⇒ 攒样本：A 只差 1 例 ⇒ **下月自动复核极可能给出 PASS/FAIL**（这正是"接进日批"的意义）。

────────────────────────────────────────────────────────────────────────────────
【口径局限（下结论前必读）】
  1. **宽度腿是有偏子集**：本地包 klines 只有 **2736 只**（沪主板 60xx + 深主板 + 创业板 30x，
     **不含科创板 688 / 北交所**）⇒ up_ratio 与生产口径（全市场行情缓存）**不等价**，
     且包的 `codes` 是**当前**股票池 ⇒ 历史宽度含**幸存者偏差**（偏乐观）。
     ⇒ 故 L3 只作**副描述**，不进主判定；主判定只用 L1/L2（指数 + 宏观，无此偏差）。
  2. 样本期 = 数据包覆盖期（当前 2023-09-05 起，约 3 年、746 根）⇒ **未跨完整市况**；
     金银比腿来自 `macro_daily_history`（5.5 年），长于指数腿，实际取**交集**。
  3. **零 Supabase 全量读（egress 纪律）**：指数日线 + 宽度都读**本地数据包**（SQLite）；
     金银比读 `macro_daily_history`（~1.4 千行，几十 KB）。**包不可用时直接报
     INSUFFICIENT，绝不回退 Supabase 全量读**（回退一次就是几十 MB）。
  4. 与 `reversal_edge_check` 同样的边界：日线口径、相邻信号重叠、仅 3 年样本。
  5. **E2 为何不进本轮组合**（PLAN §6.3 曾把它列为候选）：E2 是**极稀有**事件，且项目
     `market_events` 表当前**仅 1 行**（历史 E2 需从数据包重建，是另一件事）；
     再叠加 L1+L2 后样本必趋近 0（本轮 A' 加一条 L3 就只剩 1 例，即此教训）
     ⇒ **先不加**（加了只会得到 n≈0 的 INSUFFICIENT），留待 P1-2 一并处理。

用法：python scripts/reversal_composite_edge_check.py [--json] [--code sh000300]
      [--drop 5] [--gsr 1.0] [--hold 1 5 20] [--boot 10000]
================================================================================
"""
import argparse
import json
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")

# ── 预登记常量（改这里 = 改判据，须在文件末尾新开一节注明日期与理由）──
DEFAULT_CODE = "sh000300"
DROP_PCT = 5.0            # L1：距 20 日高点回撤 ≥ 5%
GSR_DROP_PCT = -1.0       # L2：金银比 5 日变化 ≤ −1.0%
WEAK_UP_RATIO = 0.35      # L3：前 5 日 up_ratio 均值 ≤ 0.35（弱）
REPAIR_UP_RATIO = 0.50    # L3：当日 up_ratio ≥ 0.50（转强）
CLUSTER_GAP = 10          # block bootstrap：信号日间隔 ≥ 10 交易日切块
BOOT_N = 10000            # bootstrap 次数
BOOT_SEED = 20261010      # 固定种子（结论可复现）
NEED_SIGNAL_DAYS = 30     # 样本门槛
GO_DIFF = 0.50            # GO：T+5 diff ≥ +0.50pct
GO_P = 0.05               # GO：单侧 P ≤ 0.05
KILL_P = 0.20             # KILL：P ≥ 0.20
PRIMARY_HOLD = 5          # 主判定持有期


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)


# ────────────────────────────── 数据读取（零 egress 优先） ──────────────────────────────
def pack_path() -> Tuple[Optional[str], str]:
    """本地数据包路径。返回 (path, 说明)。**不触发 Supabase 读取**。

    `pack_source.status()` 会走 `_ensure_ready()`：pack 模式下缺包/陈旧会从 GitHub Pages
    重下（零 Supabase 流量）；local/db 模式下只看本地文件。
    """
    try:
        from app import pack_source
        pack_source.status()                    # 触发 ensure_ready（必要时下包）
        p = pack_source.pack_file()
    except Exception as e:
        return None, "pack_source 不可用: %s" % str(e)[:80]
    if not os.path.exists(p):
        return None, "本地数据包不存在（请先 python scripts/sync_local.py --force）"
    return p, ""


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_index_bars(conn: sqlite3.Connection, code: str) -> List[Dict]:
    rows = conn.execute(
        "SELECT date, close FROM klines WHERE code = ? AND close > 0 "
        "ORDER BY date ASC", (code,)).fetchall()
    return [{"date": str(r["date"])[:10], "close": float(r["close"])} for r in rows]


def load_breadth(conn: sqlite3.Connection) -> Dict[str, float]:
    """按日 up_ratio = 涨家数 /(涨家数+跌家数)，**仅用包内个股**（剔指数/ETF 代码）。

    ⚠️ 口径局限（见文件头）：包只有沪深主板 + 创业板 2736 只，**不含科创板/北交所**，
    且股票池是当前的 ⇒ 含幸存者偏差。故仅作 L3 副腿，不进主判定。
    """
    rows = conn.execute(
        "SELECT code, date, close FROM klines "
        "WHERE close > 0 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%' "
        "ORDER BY code, date ASC").fetchall()
    prev = {}
    up: Dict[str, int] = {}
    down: Dict[str, int] = {}
    for r in rows:
        c = r["code"]
        d = str(r["date"])[:10]
        px = float(r["close"])
        p0 = prev.get(c)
        if p0 is not None:
            if px > p0:
                up[d] = up.get(d, 0) + 1
            elif px < p0:
                down[d] = down.get(d, 0) + 1
        prev[c] = px
    out = {}
    for d in set(up) | set(down):
        a, b = up.get(d, 0), down.get(d, 0)
        if a + b > 0:
            out[d] = a / float(a + b)
    return out


def load_gsr() -> Dict[str, float]:
    """金银比（COMEX金/COMEX银）日频 —— 源自 macro_daily_history（~1.4 千行，极小）。"""
    from app import macro_daily
    gold = {str(r["date"])[:10]: float(r["close"]) for r in (macro_daily.load("gold_com") or [])}
    silver = {str(r["date"])[:10]: float(r["close"]) for r in (macro_daily.load("silver") or [])}
    return {d: gold[d] / silver[d] for d in sorted(set(gold) & set(silver)) if silver[d] > 0}


def _last_le(series: Dict[str, float], dates: List[str], i: int, max_back: int = 12):
    """取 ≤ dates[i] 的最近一个有效值；早于 `max_back` 个交易日则视为缺失。"""
    for k in range(i, max(-1, i - max_back - 1), -1):
        v = series.get(dates[k])
        if v is not None:
            return v
    return None


# ────────────────────────────── 打标与统计 ──────────────────────────────
def build_days(bars: List[Dict], breadth: Dict[str, float], gsr: Dict[str, float],
               drop_pct: float, gsr_drop: float) -> List[Dict]:
    dates = [b["date"] for b in bars]
    out = []
    gsr_dates = sorted(gsr)
    for i in range(len(bars)):
        if i < 20:
            continue
        hi20 = max(b["close"] for b in bars[i - 19:i + 1])
        dd = (bars[i]["close"] / hi20 - 1.0) * 100.0        # 距 20 日高点（负值）
        # 金银比 5 日变化（按指数交易日回看，容忍市场休市错位）
        g_now = _last_le(gsr, dates, i)
        g_5 = _last_le(gsr, dates, i - 5)
        gsr_chg = (g_now / g_5 - 1.0) * 100.0 if (g_now and g_5) else None
        # 宽度（当日 / 前 5 日均值，缺值跳过该日均值计算）
        ur_now = _last_le(breadth, dates, i, max_back=3)
        prev = [breadth.get(dates[k]) for k in range(max(0, i - 5), i)]
        prev = [x for x in prev if x is not None]
        ur_prev5 = sum(prev) / len(prev) if prev else None
        l1 = dd <= -abs(drop_pct)
        l2 = (gsr_chg is not None) and (gsr_chg <= gsr_drop)
        l3 = (ur_now is not None and ur_prev5 is not None
              and ur_prev5 <= WEAK_UP_RATIO and ur_now >= REPAIR_UP_RATIO)
        out.append({"i": i, "date": bars[i]["date"], "dd": round(dd, 2),
                    "gsr_chg": None if gsr_chg is None else round(gsr_chg, 2),
                    "up_ratio": None if ur_now is None else round(ur_now, 3),
                    "ur_prev5": None if ur_prev5 is None else round(ur_prev5, 3),
                    "L1": l1, "L2": l2, "L3": l3})
    return out


def fwd_ret(bars: List[Dict], i: int, n: int) -> Optional[float]:
    if i + n >= len(bars):
        return None
    c0, c1 = bars[i]["close"], bars[i + n]["close"]
    return (c1 / c0 - 1) * 100 if c0 else None


def stat(vals: List[Optional[float]]) -> Optional[Dict]:
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if not n:
        return None
    s = sorted(vals)
    return {"n": n, "mean": sum(vals) / n, "median": s[n // 2],
            "win": sum(1 for v in vals if v > 0) / n * 100,
            "min": s[0], "max": s[-1]}


def clusters(idxs: List[int], gap: int = CLUSTER_GAP) -> List[List[int]]:
    """按信号日间隔切块（≥ gap 个交易日开新块）—— bootstrap 的重采样单位。"""
    out, cur = [], []
    for i in sorted(idxs):
        if cur and i - cur[-1] >= gap:
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def block_bootstrap_diff(a_vals: List[float], a_blocks: List[List[float]],
                         b_vals: List[float], b_blocks: List[List[float]],
                         boot_n: int = BOOT_N, seed: int = BOOT_SEED) -> Dict:
    """两臂**各自按块**有放回重采样 ⇒ diff 的 95% 区间 + 单侧 P(diff* ≤ 0)。"""
    import random
    rnd = random.Random(seed)

    def _resample_mean(blocks, flat):
        if not blocks:
            return None
        tot = 0.0
        acc = []
        while len(acc) < len(flat):
            acc.extend(blocks[rnd.randrange(len(blocks))])
        vals = acc[:len(flat)]
        tot = sum(vals)
        return tot / len(vals)

    if not a_blocks or not b_blocks:
        return {}
    diffs = []
    for _ in range(boot_n):
        ma = _resample_mean(a_blocks, a_vals)
        mb = _resample_mean(b_blocks, b_vals)
        if ma is None or mb is None:
            return {}
        diffs.append(ma - mb)
    diffs.sort()
    n = len(diffs)
    p_le0 = sum(1 for d in diffs if d <= 0) / float(n)      # 单侧：越接近 0 越显著
    return {"p": p_le0, "lo": diffs[int(0.025 * n)], "hi": diffs[int(0.975 * n)],
            "mean": sum(diffs) / n, "boot": n}


def compound_nonoverlap(bars: List[Dict], idxs: List[int], hold: int) -> Tuple[Optional[float], List[str]]:
    """不重叠采样（持满 hold 再接受下一信号）连乘 − 1（%）。"""
    net, used, last_exit = 1.0, [], -1
    for i in sorted(idxs):
        if i <= last_exit:
            continue
        r = fwd_ret(bars, i, hold)
        if r is None:
            break
        net *= (1.0 + r / 100.0)
        used.append(bars[i]["date"])
        last_exit = i + hold
    if not used:
        return None, []
    return (net - 1.0) * 100, used


def by_year(bars: List[Dict], days: List[Dict], hold: int) -> List[Tuple[str, Dict]]:
    ymap: Dict[str, List] = {}
    for d in days:
        ymap.setdefault(d["date"][:4], []).append(fwd_ret(bars, d["i"], hold))
    return [(y, stat(v)) for y, v in sorted(ymap.items())]


def _fmt(st: Optional[Dict]) -> str:
    if not st:
        return "%-6s" % "-"
    return "%+6.2f%% %5.1f%% n=%-4d" % (st["mean"], st["win"], st["n"])


# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=DEFAULT_CODE)
    ap.add_argument("--drop", type=float, default=DROP_PCT)
    ap.add_argument("--gsr", type=float, default=GSR_DROP_PCT)
    ap.add_argument("--hold", type=int, nargs="+", default=[1, 5, 20])
    ap.add_argument("--boot", type=int, default=BOOT_N)
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "reversal_composite_edge_check",
                              "verdict": verdict, "n": n, "need": NEED_SIGNAL_DAYS,
                              "unit": "信号日", "detail": detail},
                             ensure_ascii=False))

    path, err = pack_path()
    if not path:
        print("[数据] %s" % err)
        print("[数据] ⇒ 报 INSUFFICIENT（**不回退 Supabase 全量读**，见文件头纪律 ③）")
        emit("INSUFFICIENT", 0, err)
        return

    conn = _connect(path)
    try:
        bars = load_index_bars(conn, args.code)
        if len(bars) < 60:
            print("[数据] %s 日线不足（%d 根）⇒ 跳过" % (args.code, len(bars)))
            emit("INSUFFICIENT", 0, "%s 日线仅 %d 根" % (args.code, len(bars)))
            return
        breadth = load_breadth(conn)
    finally:
        conn.close()
    gsr = load_gsr()
    if not gsr:
        print("[数据] 金银比为空（macro_daily_history 的 gold_com/silver 缺失）")
        emit("INSUFFICIENT", 0, "金银比数据缺失")
        return

    days = build_days(bars, breadth, gsr, args.drop, args.gsr)
    A = [d for d in days if d["L1"] and d["L2"]]
    A2 = [d for d in days if d["L1"] and d["L2"] and d["L3"]]
    B = [d for d in days if d["L1"] and not d["L2"]]
    L1 = [d for d in days if d["L1"]]

    print("=" * 78)
    print("「反弹 vs 反转」组合信号检验（预登记）")
    print("=" * 78)
    print("标的 %s  bars=%d  %s .. %s" % (args.code, len(bars),
                                          bars[0]["date"], bars[-1]["date"]))
    print("金银比 %s .. %s  | 宽度(包内) %d 个交易日" % (
        min(gsr), max(gsr), len(breadth)))
    print("腿: L1 回撤>=%.1f%% | L2 金银比5日<=%.2f%% | L3 前5日UR<=%.2f 且 当日UR>=%.2f"
          % (args.drop, args.gsr, WEAK_UP_RATIO, REPAIR_UP_RATIO))
    print("样本: A(L1&L2)=%d  A'(+L3)=%d  B(L1 only)=%d  全样本=%d"
          % (len(A), len(A2), len(B), len(days)))
    print()

    groups = [("A  L1&L2 (signal)", A), ("A' L1&L2&L3 (sub)", A2),
              ("B  L1 only (control)", B), ("C  all days", days)]
    header = "%-22s %5s |" % ("group", "n")
    for h in args.hold:
        header += "  T+%-2s mean  win%%        |" % h
    print(header)
    print("-" * len(header))
    stats = {}
    for name, rows in groups:
        line = "%-22s %5d |" % (name, len(rows))
        for h in args.hold:
            st = stat([fwd_ret(bars, d["i"], h) for d in rows])
            stats[(name, h)] = st
            line += " " + _fmt(st) + " |"
        print(line)
    print()

    # ── 主判定：A − B（T+5）──
    print("=== 主判定（预登记：A − B，T+%d）===" % PRIMARY_HOLD)
    a_st = stats.get(("A  L1&L2 (signal)", PRIMARY_HOLD))
    b_st = stats.get(("B  L1 only (control)", PRIMARY_HOLD))
    boot = {}
    verdict, detail = "INSUFFICIENT", "信号日 n=%d < %d" % (len(A), NEED_SIGNAL_DAYS)
    diff = None
    if a_st and b_st and len(A) >= NEED_SIGNAL_DAYS:
        a_vals = [fwd_ret(bars, d["i"], PRIMARY_HOLD) for d in A]
        b_vals = [fwd_ret(bars, d["i"], PRIMARY_HOLD) for d in B]
        a_vals = [v for v in a_vals if v is not None]
        b_vals = [v for v in b_vals if v is not None]
        diff = a_st["mean"] - b_st["mean"]
        # 按块 bootstrap（两臂各自切块）
        a_blocks = [[v for v in (fwd_ret(bars, i, PRIMARY_HOLD) for i in blk)
                     if v is not None] for blk in clusters([d["i"] for d in A])]
        b_blocks = [[v for v in (fwd_ret(bars, i, PRIMARY_HOLD) for i in blk)
                     if v is not None] for blk in clusters([d["i"] for d in B])]
        a_blocks = [x for x in a_blocks if x]
        b_blocks = [x for x in b_blocks if x]
        boot = block_bootstrap_diff(a_vals, a_blocks, b_vals, b_blocks, args.boot)
        print("  A mean %+.2f%%  win %.1f%%  (n=%d)" % (a_st["mean"], a_st["win"], a_st["n"]))
        print("  B mean %+.2f%%  win %.1f%%  (n=%d)" % (b_st["mean"], b_st["win"], b_st["n"]))
        print("  diff(A-B) = %+.2f pct   win diff = %+.1f pct" % (diff, a_st["win"] - b_st["win"]))
        if boot:
            print("  block bootstrap(B=%d, 块=%d/%d): 95%%CI [%+.2f, %+.2f]  P(diff<=0)=%.4f"
                  % (boot["boot"], len(a_blocks), len(b_blocks), boot["lo"], boot["hi"], boot["p"]))
        if diff >= GO_DIFF and boot.get("p", 1.0) <= GO_P:
            verdict = "PASS"
            detail = "GO: T+%d diff %+.2f pct, P=%.4f (n=%d)" % (PRIMARY_HOLD, diff, boot["p"], len(A))
        elif diff > 0:
            verdict = "PARTIAL"
            detail = "DOWNGRADE: diff %+.2f pct 但未达 GO（P=%.4f 或 <+%.2f）" % (
                diff, boot.get("p", 1.0), GO_DIFF)
        else:
            verdict = "FAIL"
            detail = "KILL: T+%d diff %+.2f pct <= 0（组合无效，进已证伪清单）" % (PRIMARY_HOLD, diff)
    else:
        print("  样本不足（A n=%d < %d）=> 只报数不判定" % (len(A), NEED_SIGNAL_DAYS))
    print("  ⇒ verdict = %s" % verdict)
    print("     %s" % detail)
    print()

    # ── 复利净值（不重叠）──
    print("=== 复利净值（信号日不重叠，持 T+%d）===" % PRIMARY_HOLD)
    for name, rows in (("A  L1&L2", A), ("A' +L3", A2), ("B  L1 only", B)):
        net, used = compound_nonoverlap(bars, [d["i"] for d in rows], PRIMARY_HOLD)
        if net is None:
            print("  %-12s 无可用样本" % name)
            continue
        print("  %-12s 净值 %+7.2f%%   用了 %2d 次信号（在场 %2d 个交易日，首次 %s）"
              % (name, net, len(used), len(used) * PRIMARY_HOLD, used[0]))
    bh = (bars[-1]["close"] / bars[0]["close"] - 1.0) * 100.0
    print("  基准 %-9s 净值 %+7.2f%%  （%s .. %s buy&hold）"
          % (args.code, bh, bars[0]["date"], bars[-1]["date"]))
    print("  ⚠️ 各臂**在场天数不同** ⇒ 净值不可直接互比（基准是全程在场）；")
    print("     判定只认上方的 diff + block bootstrap（同口径、不依赖净值）。")
    print()

    # ── 分年分解 ──
    print("=== 分年分解（T+%d，A 臂）===" % PRIMARY_HOLD)
    for y, st in by_year(bars, A, PRIMARY_HOLD):
        print("  %s  %s" % (y, _fmt(st)))
    print()
    if A2:
        print("=== 副腿 A'（L1&L2&L3）样本明细（仅描述，不进判定）===")
        for d in A2[:20]:
            print("  %s  dd=%+.2f%%  gsr5=%+.2f%%  UR=%s(前5日 %s)"
                  % (d["date"], d["dd"], d["gsr_chg"], d["up_ratio"], d["ur_prev5"]))
        if len(A2) > 20:
            print("  ...（共 %d 条）" % len(A2))
        print()

    emit(verdict, len(A), detail)


if __name__ == "__main__":
    main()
