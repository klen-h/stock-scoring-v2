#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】闸门 `ready==3`（当前**唯一买入入口**）的**联合期望值**检验
           —— P1「先落库、后验证」（2026-09-29）
================================================================================
【为什么有本脚本】
  `ready==3` = A 主力吸筹 + B 不追高 + C 市况允许，是 `_merged_buy_list` 的**唯一入口**。
  三者**各自**有依据（A 有回测 +1.1pt / B 为阈值设定 / C 有 19.5 年四态验证），
  但**组合起来**的胜率与期望收益**从未检验** —— 等于用一个未经联合验证的规则开仓。
  （项目自己的方法论铁律："因子显著 ≠ 可交易"；各部件合格 ≠ 组合合格。）
  出处：《专业度缺口与优先级建议_20260929》缺口 2。
  `app/mainforce/gate_history.py` 头注释早已预留消费方名 `scripts/gate_ready_backtest.py`
  ⇒ 本脚本即该预留实现。

【样本是「时钟」不是「开发」】样本只能靠日批逐日累积（3/3 在防御市天然为 0）
  ⇒ **先落库、后验证**：脚本先就位，样本自己长；越早启动越好。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死；改判据须新开一节并注明日期）━━━━━━━━━━━━━━━━━━
【事件定义】快照日 T = `gate_snapshot_history` 中某 candidate 的 `ready == 3`。
  ⚠️ 快照是**日批（收盘后）**落库的 ⇒ 用户最早 **T+1** 才能据它决策 ⇒
     入场 = **T+1 开盘**（可交易口径，与项目其它回测的 T+1 开盘撮合同源）。
【目标】T+1 开盘买 → **T+5 收盘**卖（口径同 `drawdown_rebound_check.py` 的
  "T+1 开盘买 → T+H 收盘卖"）。
【对照基准】**中证1000 `sh000852`** —— 缺口 1 定下的风格基准（持仓/信号偏中小盘），
  同起止日、同口径（指数 T+1 开盘 → T+5 收盘）⇒ 超额 = 个股收益 − 基准收益。
【对照档位】`ready==2`（同一快照、只差一个条件的**邻近档**）。
  ⚠️ **0/1 档不可得**（已核实，非脚本缺陷）：`routers/scoring._gate_watch_live`
     在写 candidates 前已 `if ready < 2: continue` ⇒ 候选清单只有 2/3 档；
     全市场 0/1 只进 `counts`（聚合数，无个股清单）⇒ **无法构造低档对照组**。
     故"联合期望"只能相对**基准**与**邻近档**判定，不能证明"3 档单调优于 1 档"。

【独立单位 = **快照日**（不是个股！）】★ 本脚本最关键的口径选择：
  · 同一快照日的不同股票**共享同一段市场行情**（共同市场因子）⇒ 按个股去重仍会
    **严重高估**显著性（首版按股聚簇实测 P=0.0000，明显虚高）；
  · 故以「**快照日**」为独立单位 —— 一个快照日 = 一次**决策批次的组合**，
    簇值 = 当日全部候选票的**等权**收益/超额 ⇒ 重采样单位与决策单位一致；
  · 簇内仍做**同股去重**（同一只票在 5 个交易日内只计首次，防同一持仓期重复计数）。
  ⇒ 代价诚实说出来：样本以**交易日**计 ⇒ 30 簇 ≈ 30 个交易日 ≈ 6 周。
    这正是「样本是时钟、开发加速不了」的量化形式。

【判据（三条同时满足 ⇒ PASS）】
  (a) 超额 ≥ **+0.80pp**（T+5，对中证1000，**快照日簇等权**）
  (b) **快照日级** bootstrap P(超额均值 ≤ 0) < **0.05**（对簇有放回重采样）
  (c) **分年**：正超额年份 ≥ **60%**
【附加（不作判据，但必须打印）】
  · 增量价值：ready==3 超额 − ready==2 超额 ≥ **+0.50pp**
    （否则"唯一入口"相对邻近档无增量 ⇒ 应重新考虑入口设计）
  · 可执行性：**不重叠贪心**单账户净值 vs 同期买入持有中证1000
    （「小盘偏好」栽过的地方：相对基准有超额 ≠ 可执行）
【INSUFFICIENT（缺证据 ≠ 反证）】ready==3 簇数 < **30** ⇒ 只报进度、**不下任何结论**。
  （同 `event_live_review.py` 纪律：样本不足时输出 insufficient。）
【阈值来源（预注册，**经验初值、未校准**）】
  · +0.80pp ≈ A 条件既有回测依据 +1.1pt 的 ~70%（留联合稀释余量）
  · ±0.30pp ≈ 双边交易成本量级（低于此无可交易意义，用于 NO EDGE 带）
  ⚠️ 与项目其它预登记脚本同款标注：**阈值未回测校准**，PASS 也仅代表"值得进入下一轮"。

【快照健全性（本脚本同时充当"落库体检"）】
  输出会打印快照行数 / 日期区间 / 中间缺口 / 每日 ready 分布 ⇒ 直接回答
  P1 的第二问「日批是否每天写入」。
  ★ 已核实：`gate_snapshot` 在 `scripts/daily_batch.py` 的 `DEFAULT_ORDER` 里
    （`task_gate_snapshot` → `gate_history.snapshot()`，时序在 `mainforce_state` 之后）；
    且该表 **不在 `app/db_retention._TABLES`** ⇒ **不会被保留期清理**，样本可长期累积。

【样本现实（2026-09-29 首次运行实测，避免误读输出）】
  快照仅 **6 行**（09-21 ~ 09-29），**ready==3 全为 0**、regime 全 `defensive`
  ⇒ 首次运行**必然 INSUFFICIENT**（预期结果，不是故障）。
  同期 ready==2 有 864 个候选/6 个快照日 ⇒ 对照组当日即可读，但**仅 6 簇且单一
  regime、单一年份** ⇒ 只作"机制健康度"参考，**不可当结论**。

用法：
  python scripts/gate_ready_backtest.py
  python scripts/gate_ready_backtest.py --hold 5 10        # 多持有期（(a)(b)(c) 只用第一个）
  python scripts/gate_ready_backtest.py --bench sh000300   # ⚠️ 非预登记基准，仅作参考对照
================================================================================
"""

import argparse
import os
import random
import sys
from collections import defaultdict
from datetime import date as _date
from datetime import timedelta
from typing import Dict, List, Optional

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


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

# ── 预登记判据（先于结果写死；勿在看过结果后修改）─────────────────────────
MIN_CLUSTERS = 30          # ready==3 最小**独立簇**数（= 快照日数；低于此 ⇒ INSUFFICIENT）
MIN_EXCESS = 0.80          # 判据 (a)：T+5 超额门槛（pp）
ALPHA = 0.05               # 判据 (b)：簇级 bootstrap P(超额均值 ≤ 0)
MIN_POS_YEAR = 0.60        # 判据 (c)：正超额年份比例门槛
MIN_VS_CTRL = 0.50         # 附加：相对 ready==2 的增量门槛（pp）
COST_BAND = 0.30           # NO EDGE 带（≈双边交易成本量级）
HOLD_DEFAULT = 5           # T+5（判据只用第一个持有期）
ENTRY_READY = 3            # 目标档位
CTRL_READY = 2             # 邻近对照档位
DEDUP_GAP = 5              # 同一只票在 < 5 个交易日内只计首次（防同一持仓期重复计数）
B_BOOT = 2000              # bootstrap 次数
SEED = 42
BENCH_DEFAULT = "sh000852"  # 预登记基准 = 中证1000（缺口 1 口径）
_PRICE_CHUNK = 200         # `code IN (...)` 分片（egress 纪律：单次查询别太大）


# ══════════════════════════════════════════════════════════════════════════
#  一、读快照（唯一入口 = gate_history.load_range，防口径漂移）
# ══════════════════════════════════════════════════════════════════════════

def load_snapshots() -> List[Dict]:
    """全部闸门快照（升序）。返回 [{date, regime, counts, candidates, strategy_date}]。"""
    from app.database import db
    from app.mainforce import gate_history
    b = db.fetch_one("SELECT MIN(date) AS a, MAX(date) AS b FROM gate_snapshot_history") or {}
    if not b.get("a"):
        return []
    return gate_history.load_range(str(b["a"])[:10], str(b["b"])[:10])


def snapshot_health(snaps: List[Dict]) -> None:
    """快照健全性（P1 第二问：日批是否每天写入）。打印行数/缺口/每日档位分布。"""
    print("=" * 118)
    print("【快照健全性】gate_snapshot_history（P1 第二问：日批是否每天写入）")
    print("=" * 118)
    if not snaps:
        print("  ⚠️ 无快照 —— 请检查日批 `task_gate_snapshot` 是否执行")
        return
    print(f"  行数 {len(snaps)}｜日期 {snaps[0]['date']} ~ {snaps[-1]['date']}")
    gaps = []
    for a, b in zip(snaps, snaps[1:]):
        try:
            da, dbb = _date.fromisoformat(a["date"]), _date.fromisoformat(b["date"])
            if (dbb - da).days > 4:          # >4 自然日 ⇒ 中间有缺失交易日
                gaps.append(f"{a['date']}→{b['date']}({(dbb - da).days}天)")
        except (ValueError, TypeError):
            continue
    print(f"  疑似缺口（间隔 >4 自然日）：{'、'.join(gaps) if gaps else '无 ✓'}")
    print(f"  {'日期':<12}{'regime':<13}{'全市场 ready 分布 0/1/2/3':<26}{'候选':>6}{'其中3':>7}")
    for s in snaps:
        c = s.get("counts") or {}
        cands = s.get("candidates") or []
        r3 = sum(1 for x in cands if int(x.get("ready") or 0) == ENTRY_READY)
        dist = f"{c.get(0, 0)}/{c.get(1, 0)}/{c.get(2, 0)}/{c.get(3, 0)}"
        print(f"  {s['date']:<12}{str(s.get('regime') or ''):<13}{dist:<26}"
              f"{len(cands):>6}{r3:>7}")
    print()


def collect_events(snaps: List[Dict], ready: int) -> List[Dict]:
    """收集指定档位的事件 [{code, name, date}]。"""
    out = []
    for s in snaps:
        for x in s.get("candidates") or []:
            if int(x.get("ready") or 0) != ready:
                continue
            if x.get("code"):
                out.append({"code": str(x["code"]), "name": x.get("name") or "",
                            "date": s["date"]})
    return out


# ══════════════════════════════════════════════════════════════════════════
#  二、行情（一次批量查询；缺失跳过、**绝不填 0**）
# ══════════════════════════════════════════════════════════════════════════

def load_bars(codes: List[str], d0: str, d1: str) -> Dict[str, Dict[str, Dict]]:
    """{code: {date: {open, close}}}（仅取所需日期窗，控制 egress）。"""
    from app.database import db
    out: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for k in range(0, len(codes), _PRICE_CHUNK):
        chunk = codes[k:k + _PRICE_CHUNK]
        ph = ",".join(["%s"] * len(chunk))
        rows = db.fetch(
            f"SELECT code, date, open, close FROM backtest_prices "
            f"WHERE code IN ({ph}) AND date >= %s AND date <= %s",
            tuple(chunk) + (d0, d1))
        for r in rows or []:
            o, c = float(r.get("open") or 0), float(r.get("close") or 0)
            if o > 0 and c > 0:
                out[str(r["code"])][str(r["date"])[:10]] = {"open": o, "close": c}
    return out


def load_cal(code: str, d0: str, d1: str) -> List[str]:
    """交易日历（用基准指数的日期序列 —— 市场时钟；个股缺该日柱 ⇒ 跳过该事件）。"""
    from app.database import db
    rows = db.fetch("SELECT date FROM backtest_prices WHERE code = %s "
                    "AND date >= %s AND date <= %s ORDER BY date ASC", (code, d0, d1))
    return [str(r["date"])[:10] for r in rows or []]


def ret_open_to_close(bars: Dict[str, Dict], d_entry: str, d_exit: str) -> Optional[float]:
    """T+1 开盘 → T+5 收盘 收益 %（任一端缺柱 ⇒ None = 缺失，不填 0）。"""
    a, b = bars.get(d_entry), bars.get(d_exit)
    if not a or not b or a["open"] <= 0:
        return None
    return (b["close"] / a["open"] - 1) * 100


# ══════════════════════════════════════════════════════════════════════════
#  三、聚簇（独立单位 = 快照日）与统计
# ══════════════════════════════════════════════════════════════════════════

def dedup_by_code(events: List[Dict], cal: List[str]) -> List[Dict]:
    """同股在 DEDUP_GAP 个交易日内只计首次（防同一持仓期重复计数）。"""
    idx = {d: k for k, d in enumerate(cal)}
    by_code: Dict[str, List[Dict]] = defaultdict(list)
    for e in events:
        by_code[e["code"]].append(e)
    kept = []
    for _code, evs in by_code.items():
        evs.sort(key=lambda x: x["date"])
        last_k = None
        for e in evs:
            k = idx.get(e["date"], 999)
            if last_k is None or k - last_k >= DEDUP_GAP:
                kept.append(e)
                last_k = k
    return kept


def build_clusters(events: List[Dict], cal: List[str], prices: Dict[str, Dict[str, Dict]],
                   bench: Dict[str, Dict], bench_cal: List[str], hold: int) -> Dict:
    """按**快照日**聚簇：每簇 = 当日候选等权组合的收益/超额。

    见文件头「独立单位 = 快照日」——同日股票共享市场行情，按股聚簇会虚高显著性。
    """
    bidx = {d: k for k, d in enumerate(bench_cal)}
    kept = dedup_by_code(events, cal)
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for e in kept:
        by_date[e["date"]].append(e)

    clusters, void = [], 0
    for d in sorted(by_date):
        k = bidx.get(d)
        if k is None or k + hold >= len(bench_cal):
            void += 1                       # 窗口未走完（未来数据尚未产生）
            continue
        d_entry, d_exit = bench_cal[k + 1], bench_cal[k + hold]
        b = ret_open_to_close(bench, d_entry, d_exit)
        rows = []
        for e in by_date[d]:
            r = ret_open_to_close(prices.get(e["code"]) or {}, d_entry, d_exit)
            if r is None or b is None:
                continue                    # 缺柱 ⇒ 跳过（不填 0）
            rows.append({"code": e["code"], "name": e["name"], "date": d,
                         "entry": d_entry, "exit": d_exit, "ret": r, "bench": b,
                         "exc": r - b})
        if not rows:
            void += 1
            continue
        clusters.append({
            "date": d, "n": len(rows), "rows": rows,
            "abs": sum(x["ret"] for x in rows) / len(rows),
            "exc": sum(x["exc"] for x in rows) / len(rows),
        })

    exc_vals = [c["exc"] for c in clusters]
    abs_vals = [c["abs"] for c in clusters]
    stock_exc = [x["exc"] for c in clusters for x in c["rows"]]
    by_year: Dict[str, List[float]] = defaultdict(list)
    for c in clusters:
        by_year[c["date"][:4]].append(c["exc"])
    years = sorted(by_year)
    return {
        "days_total": len(by_date), "clusters": len(clusters), "void_days": void,
        "events_raw": len(events), "events_dedup": len(kept),
        "stocks": sum(c["n"] for c in clusters),
        "abs": _mean(abs_vals), "exc": _mean(exc_vals),
        "stock_win": (sum(1 for v in stock_exc if v > 0) / len(stock_exc) * 100)
                     if stock_exc else None,
        "cluster_win": (sum(1 for v in exc_vals if v > 0) / len(exc_vals) * 100)
                       if exc_vals else None,
        "p": boot_cluster_p(exc_vals),
        "by_year": {y: (_mean(by_year[y]), len(by_year[y])) for y in years},
        "years": years,
        "pos_years": [y for y in years if (_mean(by_year[y]) or 0) > 0],
        "valid": clusters,
    }


def boot_cluster_p(exc_vals: List[float], B: int = B_BOOT,
                   seed: int = SEED) -> Optional[float]:
    """快照日级有放回重采样 ⇒ P(簇均值 ≤ 0)。

    ⚠️ **簇数 < 3 直接返回 None**：k=1 时重采样恒等于原值 ⇒ 必然给出 0.0000 或 1.0000，
       看起来"极显著"实则无信息（首版实测踩到：1 簇却打印 P=0.0000）。缺失 ≠ 显著。
    """
    if len(exc_vals) < 3:
        return None
    rng = random.Random(seed)
    k, hit = len(exc_vals), 0
    for _ in range(B):
        v = sum(exc_vals[rng.randrange(k)] for _ in range(k)) / k
        if v <= 0:
            hit += 1
    return hit / B


def executable_nav(clusters: List[Dict]) -> Dict:
    """不重叠贪心单账户净值 vs 同期买入持有基准（可执行性，附加项）。"""
    picks, last_exit = [], ""
    for c in sorted(clusters, key=lambda x: x["date"]):
        c_exit = c["rows"][0]["exit"]        # 同日各票共用同一出场日（窗口由基准日历定）
        if c_exit <= last_exit:              # 持有区间与已选重叠 ⇒ 跳过（单账户）
            continue
        picks.append(c)
        last_exit = c_exit
    if not picks:
        return {"n": 0}
    nav = bnav = 1.0
    for c in picks:
        nav *= (1 + c["abs"] / 100)
        bnav *= (1 + c["rows"][0]["bench"] / 100)
    return {"n": len(picks), "nav": nav, "bench_nav": bnav,
            "start": picks[0]["rows"][0]["entry"], "end": last_exit}


def _mean(v: List[float]) -> Optional[float]:
    return sum(v) / len(v) if v else None


def _fmt(v: Optional[float], suffix: str = "%", nd: int = 2) -> str:
    return "  -" if v is None else f"{v:+.{nd}f}{suffix}"


# ══════════════════════════════════════════════════════════════════════════
#  四、主流程
# ══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[HOLD_DEFAULT],
                    help="持有交易日数；(a)(b)(c) 判据只用第一个")
    ap.add_argument("--bench", default=BENCH_DEFAULT,
                    help=f"对照基准（预登记 {BENCH_DEFAULT}；改它即为非预登记口径）")
    args = ap.parse_args()
    hold = args.hold[0]
    prereg = (args.bench == BENCH_DEFAULT)

    snaps = load_snapshots()
    snapshot_health(snaps)
    if not snaps:
        return
    cal = [s["date"] for s in snaps]
    d0, d1 = cal[0], cal[-1]
    end = (_date.fromisoformat(d1) + timedelta(days=40)).isoformat()

    print("=" * 118)
    print(f"【预登记检验】ready=={ENTRY_READY}（唯一买入入口）vs 基准 {args.bench}"
          f"{'' if prereg else '  ⚠️ 非预登记基准，仅参考'}")
    print(f"  入场 = 快照日 T 的 **T+1 开盘** → 出场 = **T+{hold} 收盘**；"
          f"超额 = 个股收益 − 基准同期收益；独立单位 = **快照日**（簇）")
    print(f"  判据：(a) 超额 ≥ +{MIN_EXCESS}pp  (b) 簇级 P < {ALPHA}  "
          f"(c) 正超额年份 ≥ {MIN_POS_YEAR:.0%}   ｜ 最小独立簇 {MIN_CLUSTERS}")
    print("=" * 118)

    bench_bars = load_bars([args.bench], d0, end).get(args.bench) or {}
    bench_cal = load_cal(args.bench, d0, end)
    if not bench_cal:
        print(f"  ⚠️ 基准 {args.bench} 无数据 —— 中证1000/中证500 由 `backfill_daily` "
              f"指数段写入，等 P0 回填后重跑；临时参考可加 `--bench sh000300`"
              f"（**非预登记口径**）。")
        return
    print(f"  基准 {args.bench}：可用交易日 {len(bench_cal)} 根"
          f"（{bench_cal[0]} ~ {bench_cal[-1]}）"
          + (f" ⚠️ 不足 T+{hold} 窗口的事件会被跳过（等日批推进，属正常）"
             if len(bench_cal) <= hold + 1 else ""))

    ev3 = collect_events(snaps, ENTRY_READY)
    ev2 = collect_events(snaps, CTRL_READY)
    codes = sorted({e["code"] for e in ev3 + ev2})
    prices = load_bars(codes, d0, end) if codes else {}
    print(f"  候选事件：ready=={ENTRY_READY} {len(ev3)} 个 / ready=={CTRL_READY} {len(ev2)} 个｜"
          f"涉及个股 {len(codes)} 只（价格库覆盖 {len(prices)} 只）")
    print()

    res = {}
    for tag, evs in ((f"ready=={ENTRY_READY}", ev3), (f"ready=={CTRL_READY}", ev2)):
        st = build_clusters(evs, cal, prices, bench_bars, bench_cal, hold)
        res[tag] = st
        print(f"  ── {tag} ──")
        print(f"     候选事件 {st['events_raw']} → 同股去重后 {st['events_dedup']} → "
              f"**独立簇（快照日）{st['clusters']}**（另有 {st['void_days']} 日因"
              f"窗口未走完/无柱跳过，**不填 0**）｜涉及个股 {st['stocks']} 只次")
        if st["clusters"]:
            p_txt = "  -" if st["p"] is None else f"{st['p']:.4f}"
            sw = st.get("stock_win")
            sw_txt = "  -" if sw is None else f"{sw:.1f}%"
            cw = st.get("cluster_win")
            cw_txt = "  -" if cw is None else f"{cw:.1f}%"
            print(f"     T+{hold}：绝对 {_fmt(st['abs'])}  对基准 {_fmt(st['exc'], 'pp')}"
                  f"  ｜ 簇胜率 {cw_txt}  个股胜率 {sw_txt}  ｜ 簇级 P(≤0) {p_txt}")
            ys = "、".join(f"{y}:{_fmt(v, 'pp', 1)}×{n}" for y, (v, n) in st["by_year"].items())
            print(f"     分年超额：{ys or '—'}")
            if st["clusters"] < 3:
                print(f"     ⚠️ 仅 {st['clusters']} 个簇 ⇒ 胜率/P 值**无统计意义**，"
                      f"只看方向不看显著性")
            nav = executable_nav(st["valid"])
            if nav.get("n"):
                print(f"     可执行（不重叠贪心 {nav['n']} 笔）：策略净值 {nav['nav']:.3f} "
                      f"vs 买入持有 {nav['bench_nav']:.3f}"
                      f"（{(nav['nav'] - 1) * 100:+.1f}% vs {(nav['bench_nav'] - 1) * 100:+.1f}%）"
                      f" ⇒ {'有超额' if nav['nav'] > nav['bench_nav'] else '**不优于买入持有**'}")
        else:
            print("     无有效样本")
        print()

    # ── 判定（预登记）──
    s3, s2 = res.get(f"ready=={ENTRY_READY}", {}), res.get(f"ready=={CTRL_READY}", {})
    print("=" * 118)
    print("【判定（预登记）】")
    n_cl = s3.get("clusters") or 0
    if n_cl < MIN_CLUSTERS:
        print(f"  **INSUFFICIENT**：ready=={ENTRY_READY} 独立簇（快照日）{n_cl} / "
              f"{MIN_CLUSTERS} ⇒ 只报进度、**不下结论**（缺证据 ≠ 反证）。")
        print(f"  进度：还差 **{MIN_CLUSTERS - n_cl}** 个快照日。样本靠日批累积"
              f"（该表**不受保留期清理**，可长期累积），建议每月重跑本脚本。")
        print(f"  ⚠️ 注意：{ENTRY_READY}/3 需 regime 非 defensive 才可能成立；"
              f"当前 6 个快照全为 defensive ⇒ 零样本属**机制预期**，不是故障。")
        return
    exc = s3.get("exc")
    p = s3.get("p")
    years = s3.get("years") or []
    pos_ratio = (len(s3.get("pos_years") or []) / len(years)) if years else 0
    a_ok = exc is not None and exc >= MIN_EXCESS
    b_ok = p is not None and p < ALPHA
    c_ok = pos_ratio >= MIN_POS_YEAR
    print(f"  (a) 超额 {_fmt(exc, 'pp')} ≥ {MIN_EXCESS}pp ? {'✓' if a_ok else '✗'}")
    print(f"  (b) 簇级 P {('  -' if p is None else f'{p:.4f}')} < {ALPHA} ? {'✓' if b_ok else '✗'}")
    print(f"  (c) 正超额年份 {len(s3.get('pos_years') or [])}/{len(years)}"
          f"（{pos_ratio:.0%}）≥ {MIN_POS_YEAR:.0%} ? {'✓' if c_ok else '✗'}"
          f"{'  ⚠️ 年份数 <3，分年不足以判' if len(years) < 3 else ''}")
    if a_ok and b_ok and c_ok:
        print(f"  ⇒ **PASS**：ready=={ENTRY_READY} 有联合期望值 ⇒ 进入下一轮"
              f"（样本外复核 / 是否给该档位加权或推送 —— 那是**另一次预登记**的事）。")
    elif exc is not None and abs(exc) < COST_BAND:
        print(f"  ⇒ **NO EDGE**：超额在 ±{COST_BAND}pp（交易成本量级）内 ⇒ "
              f"入口相对基准无增量 ⇒ 应重新考虑「以 ready==3 为唯一入口」的设计。")
    else:
        print("  ⇒ **WEAK / 归档**：不满足三条且超出 NO EDGE 带 ⇒ 记录归档，"
              "**不调权重、不推送**。")
    if s2.get("clusters"):
        inc = (s3.get("exc") or 0) - (s2.get("exc") or 0)
        print(f"  [附加] 相对 ready=={CTRL_READY} 增量 {_fmt(inc, 'pp')}"
              f"（门槛 +{MIN_VS_CTRL}pp）⇒ {'有增量' if inc >= MIN_VS_CTRL else '**无增量价值**'}")
    print("  ⚠️ 阈值 +0.80pp / ±0.30pp 为**预注册经验初值、未回测校准**（同项目其它预登记脚本）。")
    print("=" * 118)


if __name__ == "__main__":
    main()
