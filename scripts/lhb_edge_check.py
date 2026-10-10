#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】龙虎榜（LHB）「候选信号」预测力**预登记**检验（PLAN §6.4 = P1-2）
================================================================================
背景：`mainforce/lhb.py` 已迁进日批，但一直是**纯归档**（fetch→save，无任何信号/打分）；
  文件头自述的用途（吸筹确认器 / 与 mainflow 交叉验证 / 撤退提醒）**一条都没实现**。
  2026-10-10 P1-2 把它接成**候选信号**（详情页标签 = 近 10 日上榜汇总），本脚本回答
  「上榜结构到底有没有预测力」——**先预登记、再看结果**。
  （前身：`scripts/lhb_factor_check.py`，只是个无判据、无聚类的"快检"，且每只票一次
    `backtest_prices` 查询、无 --json/无契约 ⇒ 无法接日批。本脚本是它的**预登记正式版**。）

【研究对象】
  数据：`lhb_history`（龙虎榜，code/date/net_buy/quote_change）＋ **本地数据包** klines
        （未来收益，**零 Supabase 流量**；包不可用 ⇒ 直接报 INSUFFICIENT，绝不回退全量读）
  信号（上榜日）：**净买强度** = 净买额 / (上榜日收盘 × 流通股本) × 100（%，占流通市值）
        分档：big_buy(>+1%) / small_buy(0~+1%] / small_sell[-1~0) / big_sell(<-1%)
  收益：上榜日**收盘** → T+N 收盘（%，未扣成本）
  主判定：`big_buy` vs `big_sell` 的 **T+5 差** ≥ **+1.00pct**
          且 **按上榜日聚类** bootstrap（B=10000，seed 固定）单侧 **P ≤ 0.05**
          ★ 为什么用「净买 vs 净卖」两端对照：两者都是"上过榜的票"⇒ 自动控制
            「龙虎榜选择效应」（上榜本身就不是随机样本）。

【★ 覆盖率门（本脚本最关键的一条预登记规则）】
  龙虎榜以小票/科创为主，而本地包只有 **2736 只**（沪深主板 + 创业板，**无科创板/北交所**）
  ⇒ 实测约 **46.6%** 的上榜记录能在包里取到未来收益。
  **预登记**：覆盖率 < **70%** ⇒ **禁止 KILL**（最高只给 DOWNGRADE）；
            覆盖率 < **40%** ⇒ 直接 INSUFFICIENT。
  理由：被排除的多为小票/科创，而龙虎榜效应**恰恰可能集中在被排除的那部分** ⇒
        在低覆盖样本上判"无预测力"= **假 KILL**（把真信号关掉，比漏掉更糟）。

【预登记判据汇总（2026-10-10 写死；改判据须在文件末尾新开一节并注明日期）】
  · 样本门槛：有效上榜事件 **≥300** 且 两臂各 **≥50**，否则 INSUFFICIENT
  · PASS/GO      ：T+5 差 ≥ +1.00pct 且 P ≤ 0.05（且覆盖率 ≥70%）
  · PARTIAL/降级 ：差 > 0 但未达 GO，**或**达 GO 但覆盖率 <70%（证据受限）
  · FAIL/KILL    ：差 ≤ 0 或 P ≥ 0.20（**且覆盖率 ≥70%**，否则最多 DOWNGRADE）
  · 副判定（仅交叉验证）：T+10 同口径；**按`up_reason`/涨跌幅**拆（涨停上榜 vs 非涨停）；
            **分年/分月**分解；复利净值（事件**不重叠**，持满 T+5 再接受下一事件）
  · 只做**展示/观察**结论：本脚本不写库、不改任何生产阈值；信号进决策链须另行评审。

【口径局限（下结论前必读）】
  1. **覆盖率 ~46.6%**（见上）⇒ 结论只对"包内票（主板+创业板）"成立。
  2. **未扣成本**：龙虎榜次日常高开 ⇒ 收盘入场口径偏乐观（结论宜作**上界**读）。
  3. 窗口仅 ~5 个月（`lhb_history` 自 2026-05-06 起）、且**同一日上榜票高度相关**
     ⇒ 必须按**上榜日**聚类 bootstrap。
  4. `net_buy` 为榜单口径（未必等于席位合计）；`buy_total/sell_total` 实测多为 NULL（源缺失）。
  5. 所有上榜原因（首板/连板/机构专用…）混在一起 ⇒ 只作整体方向检验，不分型定论。
  6. **额外一次小读**：流通股本取自 `market_snapshot`（~1.1MB，实测覆盖 5239 只）——
     月度跑一次可忽略；本地包只提供行情、不提供股本。缺股本的上榜记录会被剔除。

用法：python scripts/lhb_edge_check.py [--json] [--hold 5 10] [--boot 10000]

────────────────────────────────────────────────────────────────────────────────
【首跑记录（2026-10-10；8,330 条上榜 / 2,416 只 / 106 个交易日，2026-05-06~10-09）】
  数据包价格覆盖 **46.6%**（1125/2416 只）⇒ 有效样本 3,994 条（可算强度 100%）。
  T+5 按强度分档：big_buy −0.01%(n=783) / small_buy +0.67%(1470) /
                  small_sell −1.94%(1268) / **big_sell −3.85%(473)**
  主判定：big_buy − big_sell = **+3.84pct**，按上榜日聚类 bootstrap（101 日）
          P(diff≤0)=**0.0000**，95%CI [+1.96, +5.67]；T+10 同向 +3.23pct。
  ⇒ **verdict = PARTIAL（降级）**：差值/显著性都达标，但**覆盖率 46.6% < 70%** ——
     **覆盖率门按设计生效**（被排除的多是小票/科创，龙虎榜效应可能恰恰在那里 ⇒
     不允许在低覆盖样本上判 PASS，也不允许 KILL）。
  ⇒ 副观察（仅记录）：涨停上榜 +1.29% vs 非涨停上榜 −2.55%（追榜 vs 低吸两极化）；
     **强度并非单调**（small_buy 反而高于 big_buy）⇒ 主判定的 buy/sell 两端对照更稳。
  ⇒ 复利净值（不重叠，17 个事件）：big_buy +34.36% / big_sell −85.88%（**未扣成本，作上界读**）。
  ⇒ 下一步（需用户决定）：要坐实/推翻，得**补被排除票的历史行情**（另一件事，有 egress 成本）；
     在此之前该结论只对「包内（沪深主板+创业板）上榜票」成立。
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
PRIMARY_HOLD = 5
HOLD2 = 10
BIG = 1.0                 # 净买强度分档阈值（占流通市值 %）
BOOT_N = 10000
BOOT_SEED = 20261010
GO_DIFF = 1.00            # big_buy − big_sell 的 T+5 差 ≥ +1.00pct
GO_P = 0.05
KILL_P = 0.20
COV_OK = 0.70             # 覆盖率 ≥70% 才允许 KILL
COV_MIN = 0.40            # <40% ⇒ INSUFFICIENT
MIN_EVENTS = 300
MIN_ARM = 50              # 两臂各自最小样本


def bucket_of(strength: Optional[float]) -> Optional[str]:
    if strength is None:
        return None
    if strength > BIG:
        return "big_buy"
    if strength > 0:
        return "small_buy"
    if strength > -BIG:
        return "small_sell"
    return "big_sell"


def load_events() -> List[Dict]:
    """读龙虎榜事件（**窄列**：不碰 seats_json 大字段）。"""
    from app.database import db
    rows = db.fetch("SELECT code, date, net_buy, quote_change, up_reason "
                    "FROM lhb_history ORDER BY date ASC") or []
    return [{"code": r["code"], "date": str(r["date"])[:10],
             "net_buy": float(r["net_buy"] or 0),
             "chg": float(r["quote_change"] or 0),
             "reason": (r.get("up_reason") or "")} for r in rows]


def pack_conn() -> Tuple[Optional[sqlite3.Connection], str]:
    """本地数据包连接（零 Supabase 流量）。"""
    try:
        from app import pack_source
        pack_source.status()
        p = pack_source.pack_file()
    except Exception as e:
        return None, "pack_source 不可用: %s" % str(e)[:80]
    if not os.path.exists(p):
        return None, "本地数据包不存在（请先 python scripts/sync_local.py --force）"
    conn = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    return conn, ""


def load_prices(conn: sqlite3.Connection, codes: List[str]) -> Dict[str, List[Tuple[str, float]]]:
    out = {}
    for c in codes:
        rows = conn.execute("SELECT date, close FROM klines WHERE code = ? AND close > 0 "
                            "ORDER BY date ASC", (c,)).fetchall()
        if rows:
            out[c] = [(str(r[0])[:10], float(r[1])) for r in rows]
    return out


def stat(vals: List[Optional[float]]) -> Optional[Dict]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(vals), "mean": sum(vals) / len(vals), "median": s[len(s) // 2],
            "win": sum(1 for v in vals if v > 0) / len(vals) * 100}


def _fmt(st: Optional[Dict]) -> str:
    if not st:
        return "%-22s" % "-"
    return "%+7.2f%% %5.1f%% n=%-5d" % (st["mean"], st["win"], st["n"])


def cluster_boot(a_by_date: Dict[str, List[float]], b_by_date: Dict[str, List[float]],
                 boot_n: int = BOOT_N, seed: int = BOOT_SEED) -> Dict:
    """按**上榜日**聚类的两样本 bootstrap ⇒ diff 的 95% 区间 + 单侧 P(diff ≤ 0)。"""
    rnd = random.Random(seed)
    dates = sorted(set(a_by_date) | set(b_by_date))
    m = len(dates)
    if m < 8:
        return {}
    A = [(sum(a_by_date.get(d, [])), len(a_by_date.get(d, []))) for d in dates]
    B = [(sum(b_by_date.get(d, [])), len(b_by_date.get(d, []))) for d in dates]
    diffs = []
    for _ in range(boot_n):
        sa = ca = sb = cb = 0.0
        for _ in range(m):
            k = rnd.randrange(m)
            s1, c1 = A[k]
            sa += s1
            ca += c1
            s2, c2 = B[k]
            sb += s2
            cb += c2
        if ca and cb:
            diffs.append(sa / ca - sb / cb)
    if not diffs:
        return {}
    diffs.sort()
    n = len(diffs)
    return {"p": sum(1 for x in diffs if x <= 0) / float(n),
            "lo": diffs[int(0.025 * n)], "hi": diffs[int(0.975 * n)], "boot": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[PRIMARY_HOLD, HOLD2])
    ap.add_argument("--boot", type=int, default=BOOT_N)
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()
    holds = args.hold

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "lhb_edge_check", "verdict": verdict, "n": n,
                              "need": MIN_EVENTS, "unit": "上榜事件",
                              "detail": detail}, ensure_ascii=False))

    try:
        events = load_events()
    except Exception as e:
        print("[数据] lhb_history 读取失败: %s" % str(e)[:120])
        emit("INSUFFICIENT", 0, "lhb_history 读取失败")
        return
    if not events:
        print("[数据] lhb_history 为空")
        emit("INSUFFICIENT", 0, "lhb_history 为空")
        return

    conn, err = pack_conn()
    if not conn:
        print("[数据] %s" % err)
        print("[数据] ⇒ 报 INSUFFICIENT（**绝不回退 Supabase 全量读**，见文件头纪律）")
        emit("INSUFFICIENT", 0, err)
        return

    codes = sorted({e["code"] for e in events})
    print("=" * 78)
    print("龙虎榜候选信号预测力检验（预登记）")
    print("=" * 78)
    print("lhb_history: %d 条上榜 / %d 只 / %s .. %s"
          % (len(events), len(codes), events[0]["date"], events[-1]["date"]))
    try:
        px = load_prices(conn, codes)
    finally:
        conn.close()
    hit = [c for c in codes if c in px]
    cov = len(hit) / float(max(1, len(codes)))
    print("数据包价格覆盖: %d/%d 只 = %.1f%%  （★ 覆盖率门：<%d%% 禁止 KILL，<%d%% 报不足）"
          % (len(hit), len(codes), cov * 100, int(COV_OK * 100), int(COV_MIN * 100)))

    # 净买强度（占流通市值 %）：流通股本由 market_snapshot 反推（月度一次的小读）
    try:
        from app.mainforce.flow import get_float_shares_from_snapshot
        fs_map = get_float_shares_from_snapshot() or {}
    except Exception as e:
        print("[数据] 流通股本读取失败（强度无法分档）: %s" % str(e)[:100])
        emit("INSUFFICIENT", 0, "流通股本缺失")
        return
    print("流通股本覆盖: %d 只" % len(fs_map))
    print()

    samples = []
    for e in events:
        bars = px.get(e["code"])
        if not bars:
            continue
        idx = {d: i for i, (d, _c) in enumerate(bars)}
        i = idx.get(e["date"])
        if i is None:
            continue
        rec = dict(e)
        rec["strength"] = None
        fs = fs_map.get(e["code"])
        c0 = bars[i][1]
        if fs and fs > 0 and c0 > 0:
            rec["strength"] = e["net_buy"] / (c0 * fs) * 100.0
        rec["bucket"] = bucket_of(rec["strength"])
        for h in holds:
            rec["fwd%d" % h] = (((bars[i + h][1] / c0) - 1.0) * 100.0
                                if i + h < len(bars) else None)
        if any(rec.get("fwd%d" % h) is not None for h in holds):
            samples.append(rec)

    n_str = sum(1 for s in samples if s["strength"] is not None)
    print("有效样本（包内有价 + 有未来收益）: %d 条；其中可算净买强度: %d 条 (%.0f%%)"
          % (len(samples), n_str, 100.0 * n_str / max(1, len(samples))))
    print()

    ph = PRIMARY_HOLD
    fld = "fwd%d" % ph
    arms = {}
    for b in ("big_buy", "small_buy", "small_sell", "big_sell"):
        arms[b] = [s for s in samples if s["bucket"] == b]
    print("── T+%d 按净买强度分档 ──" % ph)
    for b in ("big_buy", "small_buy", "small_sell", "big_sell"):
        print("  %-11s %s" % (b, _fmt(stat([s.get(fld) for s in arms[b]]))))
    print()

    a_list = [s.get(fld) for s in arms["big_buy"]]
    b_list = [s.get(fld) for s in arms["big_sell"]]
    sa, sb = stat(a_list), stat(b_list)
    print("=== 主判定（预登记：big_buy − big_sell，T+%d）===" % ph)
    verdict, detail, diff = "INSUFFICIENT", "", None
    if len(samples) < MIN_EVENTS or len(a_list) < MIN_ARM or len(b_list) < MIN_ARM:
        detail = ("样本不足：有效 %d(需%d) / big_buy %d / big_sell %d（各需%d）"
                  % (len(samples), MIN_EVENTS, len(a_list), len(b_list), MIN_ARM))
        print("  => %s" % detail)
    elif cov < COV_MIN:
        detail = "覆盖率 %.1f%% < %.0f%% ⇒ INSUFFICIENT（样本不代表龙虎榜总体）" % (
            cov * 100, COV_MIN * 100)
        print("  => %s" % detail)
        verdict = "INSUFFICIENT"
    else:
        diff = sa["mean"] - sb["mean"]
        a_by_date, b_by_date = defaultdict(list), defaultdict(list)
        for s in arms["big_buy"]:
            if s.get(fld) is not None:
                a_by_date[s["date"]].append(s[fld])
        for s in arms["big_sell"]:
            if s.get(fld) is not None:
                b_by_date[s["date"]].append(s[fld])
        boot = cluster_boot(a_by_date, b_by_date, args.boot)
        print("  big_buy  %s" % _fmt(sa))
        print("  big_sell %s" % _fmt(sb))
        print("  diff(big_buy-big_sell) = %+.2f pct" % diff)
        if boot:
            print("  按上榜日聚类 bootstrap(B=%d, 簇=%d 日): 95%%CI [%+.2f, %+.2f]  P(diff<=0)=%.4f"
                  % (boot["boot"], len(set(a_by_date) | set(b_by_date)),
                     boot["lo"], boot["hi"], boot["p"]))
        p = boot.get("p", 1.0)
        if diff >= GO_DIFF and p <= GO_P:
            if cov < COV_OK:
                verdict = "PARTIAL"
                detail = ("降级：T+%d 差 %+.2f pct 且 P=%.4f 达标，但覆盖率仅 %.1f%% < %.0f%%"
                          "（证据受限，不判 PASS）" % (ph, diff, p, cov * 100, COV_OK * 100))
            else:
                verdict = "PASS"
                detail = "GO: T+%d 差 %+.2f pct, P=%.4f（可进入是否接决策链的评审）" % (ph, diff, p)
        elif diff > 0:
            verdict = "PARTIAL"
            detail = "DOWNGRADE: 差 %+.2f pct（P=%.4f）未达 GO ⇒ 标签保留、仅供观察" % (diff, p)
        elif cov < COV_OK:
            verdict = "PARTIAL"
            detail = ("不为 KILL：差 %+.2f pct 但覆盖率仅 %.1f%%（被排除的多为小票/科创，"
                      "禁止低覆盖下判死）" % (diff, cov * 100))
        else:
            verdict = "FAIL"
            detail = "KILL: T+%d 差 %+.2f pct <= 0 且覆盖率达标（%.1f%%）⇒ 关掉该候选信号" % (
                ph, diff, cov * 100)
    print("  ⇒ verdict = %s" % verdict)
    print("     %s" % detail)
    print()

    # ── 副判定 ──
    print("=== 副判定（仅交叉验证，不单独下结论）===")
    if HOLD2 in holds:
        f2 = "fwd%d" % HOLD2
        s2a = stat([s.get(f2) for s in arms["big_buy"]])
        s2b = stat([s.get(f2) for s in arms["big_sell"]])
        if s2a and s2b:
            print("  T+%d 交叉: big_buy %+.2f%% vs big_sell %+.2f%%  diff %+.2f pct"
                  % (HOLD2, s2a["mean"], s2b["mean"], s2a["mean"] - s2b["mean"]))
    hot = [s for s in samples if s["chg"] >= 9.9]
    cold = [s for s in samples if s["chg"] < 9.9]
    for nm, grp in (("涨停上榜", hot), ("非涨停上榜", cold)):
        st = stat([s.get(fld) for s in grp])
        if st:
            print("  %-8s %s" % (nm, _fmt(st)))
    print("  分年（T+%d，big_buy / big_sell）:" % ph)
    ymap = defaultdict(lambda: {"big_buy": [], "big_sell": []})
    for s in samples:
        if s.get(fld) is not None and s["bucket"] in ("big_buy", "big_sell"):
            ymap[s["date"][:4]][s["bucket"]].append(s[fld])
    for y in sorted(ymap):
        print("    %s  big_buy %s | big_sell %s"
              % (y, _fmt(stat(ymap[y]["big_buy"])), _fmt(stat(ymap[y]["big_sell"]))))
    print()

    # ── 复利净值（事件不重叠）──
    print("=== 复利净值（事件不重叠，持 T+%d）===" % ph)
    for b in ("big_buy", "big_sell"):
        rows = sorted([s for s in arms[b] if s.get(fld) is not None], key=lambda x: x["date"])
        net, used, last_date = 1.0, 0, ""
        cal = sorted({s["date"] for s in samples})
        ci = {d: i for i, d in enumerate(cal)}
        last_exit = -1
        for s in rows:
            i = ci.get(s["date"], -1)
            if i < 0 or i <= last_exit:
                continue
            net *= (1.0 + s[fld] / 100.0)
            used += 1
            last_exit = i + ph
            last_date = last_date or s["date"]
        print("  %-9s 净值 %+8.2f%%  用了 %2d 个事件（首次 %s）"
              % (b, (net - 1.0) * 100.0, used, last_date or "-"))
    print("  ⚠️ 未扣成本（龙虎榜次日常高开）⇒ 作**上界**读；两臂事件数不同 ⇒ 净值不可直接比。")
    print()

    emit(verdict, len(samples), detail)


if __name__ == "__main__":
    main()
