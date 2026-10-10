#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「午盘前跌 >1% → 午后拉升/次日」**预登记**检验（PLAN §6.5 = P2-1）
================================================================================
背景：P1-1 的结论里最缺的拼图就是**日内** —— 日线看不到"早盘杀跌 → 午后收回"的 V 型，
  于是"这到底只是日内插针、还是能转化为可交易的次日机会"无法回答。
  本脚本是 `intraday_path`（自建分时归档）的**第一个消费方**，回答这个问题。
  ★ 时钟属性：`intraday_path` 从 **2026-10-09** 起累积 ⇒ 初期必然 INSUFFICIENT
    （"样本是时钟，开发加速不了"）；接进月度日批复核 ⇒ 到点自动出结论，不必人工跑。

【研究对象】
  数据：`intraday_path`（腾讯分时归档，**指数级**）
  标的：**主判定 = `sh000300`（沪深300）**；另对 sh000001/sz399001/sz399006/sh000852
        **交叉验证方向是否一致**（不一致 ⇒ 结论不可信）
  派生量（全部来自分时路径；**前收 = 上一归档日的 15:00 价**）：
    · 上午跌幅   m  = (11:30 价 / 前收 − 1) × 100          ← "午盘前"
    · 午后收益   a  = (15:00 价 / 11:30 价 − 1) × 100       ← **机制描述**：午后是否真的拉升
    · 可执行 T+1 e1 = (次一归档日 15:00 / 当日 15:00 − 1) × 100
        ← **收盘买入**（不抢跑当日午后的反弹，符合项目红线"慢一步、盘后确认次日执行"）
    · 可执行 T+5 e5 = (第 5 个前向归档日 15:00 / 当日 15:00 − 1) × 100
  信号：m ≤ **−1.0%**（"午盘前跌 >1%"）
  对照：m ∈ (−1.0%, 0]（"上午小跌"）；另报 m > 0（"上午上涨"）作背景

【预登记判据（2026-10-10 写死；改判据须在文件末尾新开一节并注明日期）】
  · 主判定（**进入点口径**）：信号组 e1 均值 − 对照组（上午小跌）e1 均值 ≥ **+0.30pct**
    且 **block bootstrap**（各自按**相邻 5 个信号日**切块，B=10000，seed 固定）单侧 **P ≤ 0.05**
  · **机制描述（不单独下结论）**：信号组 `a`（午后收益）均值 > 0 ⇒ 证实"午后确实有回升"；
    若 a>0 但 e1 无优势 ⇒ 说明**回升在收盘前已走完、次日接不到**（对策略是最关键的一句）。
  · 副判定（仅交叉验证）：`e5` 同口径；**5 个指数方向一致性**；分月分解；复利净值（信号日不重叠）
  · 样本门槛：归档 **≥30 个交易日** 且 信号日 **≥20** 个，否则 INSUFFICIENT
  · PASS/GO      ：主判定通过；PARTIAL/降级：差 > 0 但未达标
  · FAIL/KILL    ：差 ≤ 0 **且** P ≥ 0.20（★ 采用 P1-2 反省后的**双条件**口径，避免单条件误杀）

【口径局限（下结论前必读）】
  1. **指数级**：个股分时是另一件事（5000 只×240 点/日，成本高得多）；指数也不可直接交易
     ⇒ 本结论回答"该形态有没有信息"，落地要看 ETF/个股。
  2. 「前收」取**上一归档日**末价（非官方前收）⇒ 归档缺日会同时污染 m 与前向收益；
     脚本会打印**连续性自检**（归档日数 / 缺日警告），缺日多时结论要打折看。
  3. 未扣成本/滑点；且**同一天只有一个观测**（指数）⇒ 有效样本 = 天数，不是点数。
  4. 归档自 2026-10-09 起 ⇒ 样本期极短；**INSUFFICIENT 是常态**，不是故障。
  5. 只读；`intraday_path` 数据量小（5 标的 × 242 点/日 ≈ 36KB/日）。

用法：python scripts/intraday_midday_reversal_check.py [--json] [--code sh000300]
      [--drop 1.0] [--boot 10000]
================================================================================
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

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

# ── 预登记常量 ──
MAIN_CODE = "sh000300"
XCODES = ("sh000001", "sz399001", "sz399006", "sh000300", "sh000852")
DROP = -1.0               # 信号：上午跌幅 ≤ −1.0%
GO_DIFF = 0.30            # 主判定：e1 差 ≥ +0.30pct
GO_P = 0.05
KILL_P = 0.20             # KILL 需「差≤0 且 P≥0.20」双条件
BLOCK = 5                 # 块长（相邻 5 个信号日）
BOOT_N = 10000
BOOT_SEED = 20261010
MIN_DAYS = 30             # 样本门槛：归档交易日
MIN_SIGNAL = 20           # 样本门槛：信号日


def _price_at(path: List[list], hhmm: str) -> Optional[float]:
    """取 ≤ hhmm 的最后一个价（容忍时间戳细节差异）。"""
    out = None
    for p in path or []:
        if str(p[0]) <= hhmm:
            out = float(p[1])
        else:
            break
    return out


def build_days(code: str) -> Tuple[List[Dict], List[str]]:
    """→ ([{date, prev_close, am, close, a, m, e1, e5}], gaps)。"""
    from app.intraday_path import days_for, load
    days = days_for(code)
    daily: Dict[str, Dict] = {}
    for d in days:
        path = load(d, code)
        if not path:
            continue
        daily[d] = {"date": d, "am": _price_at(path, "1130"),
                    "close": _price_at(path, "1500"),
                    "open": _price_at(path, "0930"), "n": len(path)}
    seq = [daily[d] for d in days if d in daily]
    gaps = []
    for i in range(1, len(seq)):
        try:
            d0 = datetime.strptime(seq[i - 1]["date"], "%Y-%m-%d")
            d1 = datetime.strptime(seq[i]["date"], "%Y-%m-%d")
            if (d1 - d0).days > 5:
                gaps.append("%s→%s" % (seq[i - 1]["date"], seq[i]["date"]))
        except ValueError:
            continue
    rows = []
    for i, r in enumerate(seq):
        if not r.get("am") or not r.get("close"):
            continue
        prev = seq[i - 1]["close"] if i >= 1 else None
        rec = dict(r)
        rec["prev_close"] = prev
        rec["m"] = ((r["am"] / prev - 1) * 100.0) if prev else None
        rec["a"] = (r["close"] / r["am"] - 1) * 100.0
        rec["e1"] = ((seq[i + 1]["close"] / r["close"] - 1) * 100.0
                     if i + 1 < len(seq) and seq[i + 1].get("close") else None)
        rec["e5"] = ((seq[i + 5]["close"] / r["close"] - 1) * 100.0
                     if i + 5 < len(seq) and seq[i + 5].get("close") else None)
        rows.append(rec)
    return rows, gaps


def stat(vals: List[Optional[float]]) -> Optional[Dict]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(vals), "mean": sum(vals) / len(vals), "median": s[len(s) // 2],
            "win": sum(1 for v in vals if v > 0) / len(vals) * 100,
            "min": s[0], "max": s[-1]}


def _fmt(st: Optional[Dict]) -> str:
    if not st:
        return "%-24s" % "-"
    return "%+6.2f%% %5.1f%% n=%-3d" % (st["mean"], st["win"], st["n"])


def blocks_of(vals: List[float], size: int = BLOCK) -> List[List[float]]:
    """按**相邻 size 个信号日**切块（bootstrap 重采样单位）。"""
    out = []
    for i in range(0, len(vals), size):
        blk = vals[i:i + size]
        if blk:
            out.append(blk)
    return out


def block_boot(a: List[float], b: List[float], boot_n: int = BOOT_N,
               seed: int = BOOT_SEED) -> Dict:
    """两样本块 bootstrap（各自按相邻块重采样，块长 BLOCK）⇒ diff 的 CI 与单侧 P。"""
    rnd = random.Random(seed)
    ab, bb = blocks_of(a), blocks_of(b)
    if not ab or not bb:
        return {}
    diffs = []
    for _ in range(boot_n):
        pa = []
        while len(pa) < len(a):
            pa.extend(ab[rnd.randrange(len(ab))])
        pb = []
        while len(pb) < len(b):
            pb.extend(bb[rnd.randrange(len(bb))])
        pa, pb = pa[:len(a)], pb[:len(b)]
        diffs.append(sum(pa) / len(pa) - sum(pb) / len(pb))
    diffs.sort()
    n = len(diffs)
    return {"p": sum(1 for x in diffs if x <= 0) / float(n),
            "lo": diffs[int(0.025 * n)], "hi": diffs[int(0.975 * n)], "boot": n,
            "blocks": (len(ab), len(bb))}


def compound(rows: List[Dict], key: str = "e1", hold: int = 1):
    """信号日不重叠（持 hold 个归档日）连乘 − 1（%）。"""
    net, used, last = 1.0, 0, -1
    for i, r in enumerate(rows):
        if r.get("m") is None or r["m"] > DROP or r.get(key) is None:
            continue
        if i <= last:
            continue
        net *= (1.0 + r[key] / 100.0)
        used += 1
        last = i + hold
    return ((net - 1.0) * 100.0 if used else None), used


def analyze(code: str) -> Dict:
    rows, gaps = build_days(code)
    sig = [r for r in rows if r.get("m") is not None and r["m"] <= DROP]
    small = [r for r in rows if r.get("m") is not None and DROP < r["m"] <= 0]
    up = [r for r in rows if r.get("m") is not None and r["m"] > 0]
    return {"code": code, "rows": rows, "gaps": gaps, "sig": sig,
            "small": small, "up": up}


def main():
    # `--drop` 只用于**敏感度探查**（默认跑不要传，判据以文件顶部的预登记常量为准）；
    # ★ `global` 必须在**任何对 DROP 的引用之前**声明（含 `add_argument(default=DROP)`），
    #   否则 Python 报 "name 'DROP' is used prior to global declaration"。
    global DROP
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=MAIN_CODE)
    ap.add_argument("--drop", type=float, default=DROP)
    ap.add_argument("--boot", type=int, default=BOOT_N)
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()
    DROP = -abs(args.drop)

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "intraday_midday_reversal_check",
                              "verdict": verdict, "n": n, "need": MIN_DAYS,
                              "unit": "交易日", "detail": detail},
                             ensure_ascii=False))

    try:
        from app.intraday_path import coverage
        cov = coverage(args.code)
    except Exception as e:
        print("[数据] intraday_path 不可用: %s" % str(e)[:120])
        emit("INSUFFICIENT", 0, "intraday_path 不可用")
        return
    print("=" * 78)
    print("「午盘前跌 >%.1f%% → 午后拉升/次日」检验（预登记）" % abs(args.drop))
    print("=" * 78)
    print("归档覆盖 %s: %d 个交易日  %s .. %s"
          % (args.code, cov["days"], cov["first"] or "-", cov["last"] or "-"))
    if cov["days"] < 2:
        print("⇒ 归档不足 2 个交易日（表 2026-10-09 起累积）—— INSUFFICIENT，等样本。")
        emit("INSUFFICIENT", cov["days"],
             "归档仅 %d 个交易日（需 ≥%d；表自 2026-10-09 起累积）" % (cov["days"], MIN_DAYS))
        return

    res = analyze(args.code)
    rows, gaps = res["rows"], res["gaps"]
    print("可用于计算的交易日 %d（缺日警告: %s）"
          % (len(rows), "无" if not gaps else "、".join(gaps[:5])))
    print("信号 m ≤ %.1f%% ｜ 对照 m ∈ (%.1f%%, 0] ｜ 派生量 a=午后收益, e1=收盘→次一归档日, "
          "e5=收盘→第5个归档日" % (DROP, DROP))
    print()

    groups = [("sig 午盘跌>%.1f%%" % abs(args.drop), res["sig"]),
              ("ctl 上午小跌", res["small"]),
              ("up  上午上涨", res["up"])]
    print("%-16s %4s | %s | %s | %s" % ("group", "n", "m(上午跌幅)", "a(午后收益)", "e1(可执行 T+1)"))
    print("-" * 92)
    stats = {}
    for nm, rs in groups:
        line = "%-16s %4d |" % (nm, len(rs))
        for key in ("m", "a", "e1"):
            st = stat([r.get(key) for r in rs])
            stats[(nm, key)] = st
            line += " " + _fmt(st) + " |"
        print(line)
    print()

    print("=== 主判定（预登记：可执行 T+1，sig − ctl）===")
    a_vals = [r["e1"] for r in res["sig"] if r.get("e1") is not None]
    b_vals = [r["e1"] for r in res["small"] if r.get("e1") is not None]
    sa, sb = stat(a_vals), stat(b_vals)
    verdict, detail, diff = "INSUFFICIENT", "", None
    if len(rows) < MIN_DAYS or len(a_vals) < MIN_SIGNAL:
        detail = ("样本不足：归档 %d 交易日(需%d) / 信号日 %d(需%d)"
                  % (len(rows), MIN_DAYS, len(a_vals), MIN_SIGNAL))
        print("  => %s" % detail)
    elif not (sa and sb):
        detail = "对照组无样本"
    else:
        diff = sa["mean"] - sb["mean"]
        boot = block_boot(a_vals, b_vals, args.boot)
        print("  sig %s" % _fmt(sa))
        print("  ctl %s" % _fmt(sb))
        print("  diff(T+1 可执行) = %+.2f pct" % diff)
        if boot:
            print("  块 bootstrap(B=%d, 块长%d, 块数 sig/ctl=%s): 95%%CI [%+.2f, %+.2f]  "
                  "P(diff<=0)=%.4f"
                  % (boot["boot"], BLOCK, boot["blocks"], boot["lo"], boot["hi"], boot["p"]))
        p = boot.get("p", 1.0)
        if diff >= GO_DIFF and p <= GO_P:
            verdict, detail = "PASS", "GO: T+1 差 %+.2f pct, P=%.4f（进入点有效）" % (diff, p)
        elif diff > 0:
            verdict, detail = "PARTIAL", "DOWNGRADE: T+1 差 %+.2f pct（未达 +%.2f 或 P=%.4f）" % (
                diff, GO_DIFF, p)
        elif p >= KILL_P:
            verdict, detail = "FAIL", "KILL: T+1 差 %+.2f pct 且 P=%.4f ≥ %.2f（双条件）" % (
                diff, p, KILL_P)
        else:
            verdict, detail = "PARTIAL", ("不为 KILL：T+1 差 %+.2f pct 但 P=%.4f < %.2f"
                                          "（证据不足以判死）" % (diff, p, KILL_P))
    print("  ⇒ verdict = %s" % verdict)
    print("     %s" % detail)
    print()

    # ── 机制描述：午后是否真的拉升（不单独下结论）──
    print("=== 机制描述（午后收益 a；用来解释 e1 为什么有/没有优势）===")
    s_a = stats.get(("sig 午盘跌>%.1f%%" % abs(args.drop), "a"))
    if s_a:
        print("  信号日午后收益 a: 均值 %+.2f%%（胜率 %.1f%%，n=%d）⇒ %s"
              % (s_a["mean"], s_a["win"], s_a["n"],
                 "午后**确实在回升**" if s_a["mean"] > 0 else "午后**继续走弱**（V 型不成立）"))
    print("  ⚠️ 若 a>0 而主判定无优势 ⇒ 回升在**收盘前就已完成**，次日接不到（对策略最关键的判断）。")
    print()

    # ── 副判定 ──
    print("=== 副判定（仅交叉验证，不单独下结论）===")
    for key in ("e5",):
        aa = stat([r[key] for r in res["sig"]])
        bb2 = stat([r[key] for r in res["small"]])
        if aa and bb2:
            print("  %s 交叉: sig %+.2f%% vs ctl %+.2f%%  diff %+.2f pct"
                  % (key, aa["mean"], bb2["mean"], aa["mean"] - bb2["mean"]))
    print("  指数一致性（同一口径的 e1 差 sig−ctl）:")
    for c in XCODES:
        if c == args.code:
            continue
        try:
            r2 = analyze(c)
            xa = stat([r["e1"] for r in r2["sig"] if r.get("e1") is not None])
            xb = stat([r["e1"] for r in r2["small"] if r.get("e1") is not None])
            if xa and xb:
                print("    %-10s 归档%3d 日  sig n=%-3d 差 %+.2f pct"
                      % (c, len(r2["rows"]), xa["n"], xa["mean"] - xb["mean"]))
        except Exception as e:
            print("    %-10s 计算失败: %s" % (c, str(e)[:50]))
    print("  分月（e1，sig / ctl）:")
    ymap = defaultdict(lambda: {"sig": [], "ctl": []})
    for r in rows:
        if r.get("e1") is None:
            continue
        if r.get("m") is not None and r["m"] <= DROP:
            ymap[r["date"][:7]]["sig"].append(r["e1"])
        elif r.get("m") is not None and DROP < r["m"] <= 0:
            ymap[r["date"][:7]]["ctl"].append(r["e1"])
    for k in sorted(ymap):
        print("    %s  sig %s | ctl %s"
              % (k, _fmt(stat(ymap[k]["sig"])), _fmt(stat(ymap[k]["ctl"]))))
    net, used = compound(rows, "e1", 1)
    print("  复利净值（信号日不重叠，持 T+1）: %s（%d 次）"
          % (("%+.2f%%" % net) if net is not None else "无样本", used))
    print()

    emit(verdict, len(rows), detail)


if __name__ == "__main__":
    main()
