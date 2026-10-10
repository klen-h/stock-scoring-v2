#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】`flow_consec`（连续主力净流入）预测力**预登记**检验（PLAN §6.4 = P1-2）
================================================================================
背景：`flow_consec` 长期是**孤儿字段** —— 后端算了（`overlay.py:98-107`）、既不落表也
  无任何消费方。2026-10-10 P1-2 把它接成标签（详情页/观察池，**只分档不报精确值**），
  本脚本回答「这个标签值不值得看」——**先预登记、再看结果**（24 格教训：不预设结论）。

【研究对象】
  数据：`mainflow_history`（东财资金流，**与生产同源**）：code / date / main_net / close
        现覆盖 ~870 只 × ~146 交易日（表自 2026-03-16 起）
  信号：`tier = min(连续主力净流入天数, 10)`，分档**与生产标签同一分档**：
        none(0~1 天) / weak(2~4) / mid(5~9) / strong(≥10)
  收益：`close → T+N close`（用同表自带 close，零额外网络请求）
  ★★ **超额口径（关键，否则一切结论都只是「抄底大盘」）**：
     个股收益 − **当日全样本等权均值** ⇒ 剔除市场/日历涨跌，只留横截面选股信息。

【预登记判据（2026-10-10 写死；改判据须在文件末尾新开一节并注明日期）】
  主判定：`strong` 组 vs `none` 组 的 **T+5 超额差** ≥ **+0.50pct**
          且 **按交易日聚类** bootstrap（B=10000，seed 固定）单侧 **P ≤ 0.05**
  副判定（仅交叉验证，不单独下结论）：T+10 同口径；**单调性**（none ≤ weak ≤ mid ≤ strong）；
          分年分解（看是否靠单一年份）
  复利净值：信号日**不重叠**采样（持满 T+5 再接受下一个信号日），当日 strong 组等权 ⇒ 连乘 − 1
  样本门槛：**≥60 个交易日** 且 **≥5000 个观测**，否则 INSUFFICIENT
  · PASS/GO      ：主判定通过 ⇒ 值得进入「升级为正式因子/是否进决策链」的评审
  · PARTIAL/降级 ：超额差 > 0 但未达门槛 ⇒ 标签保留、仅供观察，不进决策链
  · FAIL/KILL    ：超额差 ≤ 0 或 P ≥ 0.20 ⇒ 摘掉标签并写进「已证伪」清单

【口径局限（下结论前必读）】
  1. `mainflow_history` 只覆盖**评分池 ~870 只**（非全市场）⇒ 结论**不可外推**到小票/科创。
  2. `close` 取东财资金流表自带值（与行情源可能有极小差异；未复权）。
  3. 窗口仅 ~7 个月（表自 2026-03-16 起）⇒ **未跨完整市况**；且同一交易日 ~870 只横截面
     高度相关 ⇒ **必须按日聚类**，按观测独立检验会严重高估显著性。
  4. 分档用 `min(consec,10)`：与生产标签同源（生产精确值受日批读取窗口 `state.FLOW_WINDOW_DAYS`
     封顶，见该文件列注释）。
  5. 只读、零写库。一次运行读一次全表窗口（~5MB）⇒ 接**月度**日批（均摊可忽略）。
  6. 未计交易成本/滑点（标签是"观察用"，非可交易策略 ⇒ 只做相对比较）。

用法：python scripts/flow_consec_edge_check.py [--json] [--hold 5 10] [--boot 10000]

────────────────────────────────────────────────────────────────────────────────
【首跑记录（2026-10-10；113,090 行 / 841 只 / 134 交易日 / 108,885 观测，2026-03~09）】
  T+5 超额：none −0.02%(n=83565) / weak +0.12%(21555) / mid −0.28%(3466) / **strong −0.57%(299)**
  主判定：strong − none = **−0.55pct**，按日聚类 bootstrap P(diff≤0)=**0.8904**，95%CI [−1.42, +0.34]
  ⇒ **verdict = FAIL（KILL）**（判据：超额差 ≤ 0）；T+10 同向（−0.80pct）；单调性不成立。
  ⇒ 结论：**`flow_consec` 没有正向预测力**（高档反而略差）。但标签**保留**为**事实描述**
     （不改分/不排序/不进闸门），且把本结论写进前端 tooltip 防误用 —— 与 PLAN §6.4 的
     「先做消费方」一致；**这是对本文书「KILL ⇒ 摘标签」字面后果的一处有意偏离，理由**：
     该字段不进决策链（非信号），摘掉只是删事实；写明结论比删掉更防误用。
     ★ 若用户要求严格按字面执行 ⇒ 摘掉 3 处前端标签即可（后端字段无副作用）。
  ★★ **本轮判据的一处缺陷（如实记录，留给 v2，不在本轮修改）**：KILL 写成
     「差 ≤ 0 **或** P ≥ 0.20」——**单条件**（只看点估计符号）⇒ 纯噪声下也有约一半概率
     被判 KILL，过松。**v2 建议**：KILL = 「差 ≤ 0 **且** P ≥ 0.20」或「95%CI 上界 < 0」；
     已获正结论需「差 ≥ 阈值 **且** CI 下界 > 0」。
================================================================================
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict
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

# ── 预登记常量 ──
CONSEC_CAP = 10           # 分档上限（与生产标签同源）
BUCKETS = ("none", "weak", "mid", "strong")
PRIMARY_HOLD = 5
HOLD2 = 10
BOOT_N = 10000
BOOT_SEED = 20261010
GO_DIFF = 0.50            # 主判定：strong − none 的 T+5 超额差 ≥ +0.50pct
GO_P = 0.05
KILL_P = 0.20
MIN_DAYS = 60             # 样本门槛：交易日
MIN_OBS = 5000            # 样本门槛：观测数


def tier_bucket(consec) -> str:
    """连续净流入 → 档（**与生产 `flow.consec_tier` 同分档**）。"""
    try:
        n = min(int(consec or 0), CONSEC_CAP)
    except (TypeError, ValueError):
        return "none"
    if n >= 10:
        return "strong"
    if n >= 5:
        return "mid"
    if n >= 2:
        return "weak"
    return "none"


def _code_chunks() -> List[str]:
    """分块读的候选 code 表：**优先本地包**（零流量），退回 DB 的 DISTINCT。"""
    try:
        from app import pack_source
        pack_source.status()
        codes = pack_source.get_codes()
        if codes:
            return codes
    except Exception:
        pass
    from app.database import db
    rows = db.fetch("SELECT DISTINCT code FROM mainflow_history") or []
    return [r["code"] for r in rows]


def load_rows() -> List[Dict]:
    """读 mainflow_history（code/date/main_net/close）。**只读、窄列、按 code 分块 + 重试**。

    ★ 为什么必须分块（2026-10-10 首跑实测踩到）：一次性 `SELECT ... FROM mainflow_history`
      （~12 万行）会被 Supabase 断开 —— `SSL connection has been closed unexpectedly`
      （本项目已知问题，见 `state.refresh_all` 的注释），整个脚本直接拿不到数据。
      按 120 个 code 分块后每块很小，且带**重试 + 连接重置**（沿用 `state._load_bars_all`
      与 `lhb_factor_check.q()` 的既有范式）。总读取量不变（仍是全表窗口 ~5MB/次，月度跑）。
    """
    import time
    from app.database import db
    codes = _code_chunks()
    out: List[Dict] = []
    for i in range(0, len(codes), 120):
        chunk = codes[i:i + 120]
        last = None
        for attempt in range(3):
            try:
                rows = db.fetch(
                    "SELECT code, date, main_net, close FROM mainflow_history "
                    "WHERE code = ANY(%s) AND close > 0 ORDER BY code, date ASC",
                    (chunk,))
                out.extend(rows or [])
                last = None
                break
            except Exception as e:            # 连接级异常 ⇒ 重置后重试该块
                last = e
                try:
                    db._reset_pg_conn()
                except Exception:
                    pass
                time.sleep(1.5 * (attempt + 1))
        if last is not None:
            raise last
    return out


def build_obs(rows: List[Dict], holds: List[int]) -> List[Dict]:
    """逐 code 算连续净流入 + 未来收益（含超额）。"""
    by_code = defaultdict(list)
    for r in rows:
        c = float(r.get("close") or 0)
        if c > 0:
            by_code[r["code"]].append((str(r["date"])[:10],
                                       float(r.get("main_net") or 0), c))
    obs = []
    for code, seq in by_code.items():
        run, n = 0, len(seq)
        for i, (d, mn, c) in enumerate(seq):
            run = run + 1 if mn > 0 else 0
            rec = {"date": d, "code": code, "bucket": tier_bucket(run), "consec": run}
            any_ok = False
            for h in holds:
                rec["fwd%d" % h] = (((seq[i + h][2] / c) - 1.0) * 100.0
                                    if i + h < n else None)
                any_ok = any_ok or rec["fwd%d" % h] is not None
            if any_ok:
                obs.append(rec)
    # 超额 = 个股收益 − 当日全样本等权均值
    for h in holds:
        sums, cnts = defaultdict(float), defaultdict(int)
        for o in obs:
            v = o["fwd%d" % h]
            if v is not None:
                sums[o["date"]] += v
                cnts[o["date"]] += 1
        means = {d: sums[d] / cnts[d] for d in sums if cnts[d]}
        for o in obs:
            v = o["fwd%d" % h]
            o["ex%d" % h] = (v - means.get(o["date"], 0.0)) if v is not None else None
    return obs


def stat(vals: List[Optional[float]]) -> Optional[Dict]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(vals), "mean": sum(vals) / len(vals),
            "median": s[len(s) // 2],
            "win": sum(1 for v in vals if v > 0) / len(vals) * 100}


def _fmt(st: Optional[Dict]) -> str:
    if not st:
        return "%-22s" % "-"
    return "%+6.2f%% %5.1f%% n=%-6d" % (st["mean"], st["win"], st["n"])


def date_key_stats(obs: List[Dict], field: str, bucket: str) -> Dict[str, tuple]:
    """{date: (sum, count)} —— bootstrap 的聚类单位（不用逐样本列表，快 2 个数量级）。"""
    out = defaultdict(lambda: [0.0, 0])
    for o in obs:
        if o["bucket"] != bucket:
            continue
        v = o.get(field)
        if v is None:
            continue
        a = out[o["date"]]
        a[0] += v
        a[1] += 1
    return {d: (a[0], a[1]) for d, a in out.items() if a[1]}


def cluster_boot(dk_a: Dict[str, tuple], dk_b: Dict[str, tuple],
                 boot_n: int = BOOT_N, seed: int = BOOT_SEED) -> Dict:
    """按交易日聚类的两样本 bootstrap ⇒ diff 的 95% 区间 + 单侧 P(diff ≤ 0)。"""
    rnd = random.Random(seed)
    dates = sorted(set(dk_a) | set(dk_b))
    m = len(dates)
    if m < 10:
        return {}
    A = [dk_a.get(d, (0.0, 0)) for d in dates]
    B = [dk_b.get(d, (0.0, 0)) for d in dates]
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


def compound(obs: List[Dict], cal_index: Dict[str, int], hold: int,
             bucket: str = "strong"):
    """信号日**不重叠**采样：当日该档等权收益 ⇒ 连乘 − 1（%）。"""
    by_date = defaultdict(list)
    for o in obs:
        if o["bucket"] == bucket:
            v = o.get("fwd%d" % hold)
            if v is not None:
                by_date[o["date"]].append(v)
    net, used, last_exit = 1.0, [], -1
    for d in sorted(by_date, key=lambda x: cal_index.get(x, 0)):
        i = cal_index.get(d, 0)
        if i <= last_exit:
            continue
        vals = by_date[d]
        net *= (1.0 + (sum(vals) / len(vals)) / 100.0)
        used.append((d, len(vals)))
        last_exit = i + hold
    return ((net - 1.0) * 100.0 if used else None), used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[PRIMARY_HOLD, HOLD2])
    ap.add_argument("--boot", type=int, default=BOOT_N)
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()
    holds = args.hold

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "flow_consec_edge_check", "verdict": verdict,
                              "n": n, "need": MIN_OBS, "unit": "观测",
                              "detail": detail}, ensure_ascii=False))

    try:
        rows = load_rows()
    except Exception as e:
        print("[数据] mainflow_history 读取失败: %s" % str(e)[:120])
        emit("INSUFFICIENT", 0, "mainflow_history 读取失败")
        return
    if not rows:
        print("[数据] mainflow_history 为空")
        emit("INSUFFICIENT", 0, "mainflow_history 为空")
        return

    obs = build_obs(rows, holds)
    dates = sorted({o["date"] for o in obs})
    cal_index = {d: i for i, d in enumerate(dates)}
    print("=" * 78)
    print("flow_consec 预测力检验（预登记）")
    print("=" * 78)
    print("数据 mainflow_history: %d 行 / %d 只 / %d 个交易日  %s .. %s"
          % (len(rows), len({r["code"] for r in rows}), len(dates),
             dates[0] if dates else "-", dates[-1] if dates else "-"))
    print("超额口径 = 个股收益 − 当日全样本等权均值（剔除市场涨跌）")
    print("样本 %d 个观测；分档 none(0~1)/weak(2~4)/mid(5~9)/strong(≥10)（与生产标签同源）"
          % len(obs))
    print()

    for h in holds:
        fld = "ex%d" % h
        print("── T+%d（超额）──" % h)
        print("%-8s %5s | %s" % ("bucket", "n", "mean/win/n"))
        print("-" * 52)
        for b in BUCKETS:
            vals = [o[fld] for o in obs if o["bucket"] == b]
            print("%-8s %5d | %s" % (b, len(vals), _fmt(stat(vals))))
        print()

    # ── 主判定（T+PRIMARY_HOLD：strong vs none）──
    ph = PRIMARY_HOLD
    fld = "ex%d" % ph
    a_vals = [o[fld] for o in obs if o["bucket"] == "strong"]
    b_vals = [o[fld] for o in obs if o["bucket"] == "none"]
    sa, sb = stat(a_vals), stat(b_vals)
    print("=== 主判定（预登记：strong − none，T+%d 超额）===" % ph)
    verdict, detail, diff = "INSUFFICIENT", "", None
    if len(dates) < MIN_DAYS or len(obs) < MIN_OBS:
        detail = "样本不足：%d 交易日(需%d) / %d 观测(需%d)" % (
            len(dates), MIN_DAYS, len(obs), MIN_OBS)
        print("  样本不足 => 只报数不判定（%s）" % detail)
    elif not sa or not sb:
        detail = "某档无样本"
    else:
        diff = sa["mean"] - sb["mean"]
        boot = cluster_boot(date_key_stats(obs, fld, "strong"),
                            date_key_stats(obs, fld, "none"), args.boot)
        print("  strong %s" % _fmt(sa))
        print("  none   %s" % _fmt(sb))
        print("  diff(strong-none) = %+.2f pct" % diff)
        if boot:
            print("  按日聚类 bootstrap(B=%d, 簇=%d 日): 95%%CI [%+.2f, %+.2f]  P(diff<=0)=%.4f"
                  % (boot["boot"], len(dates), boot["lo"], boot["hi"], boot["p"]))
        if diff >= GO_DIFF and boot.get("p", 1.0) <= GO_P:
            verdict = "PASS"
            detail = ("GO: T+%d 超额 diff %+.2f pct, P=%.4f（可进入是否进决策链的评审）"
                      % (ph, diff, boot["p"]))
        elif diff > 0:
            verdict = "PARTIAL"
            detail = "DOWNGRADE: diff %+.2f pct（P=%.4f）未达 GO ⇒ 标签保留、仅供观察" % (
                diff, boot.get("p", 1.0))
        else:
            verdict = "FAIL"
            detail = "KILL: T+%d 超额 diff %+.2f pct <= 0 ⇒ 摘标签、进已证伪清单" % (ph, diff)
    print("  ⇒ verdict = %s" % verdict)
    print("     %s" % detail)
    print()

    # ── 副判定：单调性 + T+10 + 分年 ──
    print("=== 副判定（仅交叉验证，不单独下结论）===")
    tier_mean = {}
    for b in BUCKETS:
        st = stat([o[fld] for o in obs if o["bucket"] == b])
        tier_mean[b] = st["mean"] if st else None
    seq = [(b, tier_mean[b]) for b in BUCKETS if tier_mean[b] is not None]
    mono = all(seq[i][1] <= seq[i + 1][1] + 1e-9 for i in range(len(seq) - 1))
    print("  单调性（none≤weak≤mid≤strong, T+%d）: %s  %s"
          % (ph, "是" if mono else "否",
             " ".join("%s=%+.2f" % (b, m) for b, m in seq)))
    if HOLD2 in holds:
        f2 = "ex%d" % HOLD2
        s2a, s2b = stat([o[f2] for o in obs if o["bucket"] == "strong"]), \
            stat([o[f2] for o in obs if o["bucket"] == "none"])
        if s2a and s2b:
            print("  T+%d 交叉: strong %+.2f%% vs none %+.2f%%  diff %+.2f pct"
                  % (HOLD2, s2a["mean"], s2b["mean"], s2a["mean"] - s2b["mean"]))
    print("  分年（T+%d 超额，strong / none）:" % ph)
    ymap = defaultdict(lambda: {"strong": [], "none": []})
    for o in obs:
        v = o.get(fld)
        if v is None:
            continue
        ymap[o["date"][:4]][o["bucket"]].append(v) if o["bucket"] in ("strong", "none") else None
    for y in sorted(ymap):
        ya, yb = stat(ymap[y]["strong"]), stat(ymap[y]["none"])
        print("    %s  strong %s | none %s" % (y, _fmt(ya), _fmt(yb)))
    print()

    # ── 复利净值（不重叠）──
    print("=== 复利净值（信号日不重叠，持 T+%d，当日 strong 组等权）===" % ph)
    for b in ("strong", "mid"):
        net, used = compound(obs, cal_index, ph, b)
        if net is None:
            print("  %-8s 无可用信号日" % b)
            continue
        print("  %-8s 净值 %+7.2f%%  用了 %2d 个信号日（均 %d 只，首次 %s）"
              % (b, net, len(used), int(sum(n for _d, n in used) / len(used)), used[0][0]))
    net_all, used_all = compound(obs, cal_index, ph, "none")
    print("  基准 none %s" % ((("净值 %+7.2f%%" % net_all) if net_all is not None else "无样本")))
    print("  ⚠️ 在场天数不同 ⇒ 各档净值不可直接互比；判定只认上方 diff + bootstrap。")
    print()

    emit(verdict, len(obs), detail)


if __name__ == "__main__":
    main()
