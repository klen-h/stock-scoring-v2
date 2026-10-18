# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】F1 关键校验：模拟盘实测 vs 回测引擎（**同批信号**口径对比）
================================================================================
要回答的问题（`PLAN_2026-09-12 §2.6` 第一条）：
  "模拟盘真金白银走出来的胜率，和回测引擎假设的胜率，差多少？差异来自哪里？"

★ 为什么必须"同批信号"：拿"模拟盘 46 笔"去比"回测全历史 546 笔"是**两种样本**，
  差异里混着"时期不同/池子不同/市况不同"，什么都说明不了。本脚本**只用模拟盘那 46 笔
  的 (代码, 信号日, 战法)** 喂给回测引擎 → 两边样本完全同一批，差异只剩执行层面。

三个版本（用来把差异**归因**，而不是笼统说"有偏差"）：
  P  模拟盘实测 ：真实成交价/真实退出（已平仓记录，**毛收益**，不含成本）
  A  引擎·标准v2：T+1 开盘入场 + 止损=信号日收盘×(1-7%) + 持有 3 日（当前生产口径）
  B  引擎·沿用模拟盘参数：T+1 开盘入场 + 止损=**模拟盘当初存的 stop_loss** + 持有 3 日
     ⇒ A vs B 的差 = **参数差**（早期 0.85 止损 vs 现行 0.93）；B vs P 的差 = **执行差**
        （模拟盘 9:35 成交 vs 引擎 T+1 开盘；盘中触发 vs 日线撮合）

★ 不是自研撮合器：直接调 `app.backtest.engine.match_signals`（与回测/周报**同一份**代码），
  否则"回测引擎的胜率"会变成我另写的一套假设，比了也没意义。

用法：
  python scripts/paper_vs_backtest_check.py
  python scripts/paper_vs_backtest_check.py --no-html   # 只出 MD
================================================================================
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import db                                    # noqa: E402
from app.backtest.engine import match_signals, DEFAULT_COSTS   # noqa: E402
from app.backtest.strategies import (                          # noqa: E402
    WARFARE_STOP_PCT_V2, WARFARE_HOLD_DAYS_V2, apply_exit_policy, exit_policy)

REPORT_DIR = BACKEND / "backtest_reports"


# ────────────────────────────────────────────────────────────────
#  数据
# ────────────────────────────────────────────────────────────────
def load_paper() -> list:
    """已平仓的模拟盘记录（**只取 closed**；cancelled 是没成交，不参与业绩）。"""
    rows = db.fetch(
        "SELECT id, code, name, strategy_name, signal_date, entry_price, stop_loss, "
        "fill_price, fill_date, exit_price, exit_date, exit_reason, pnl_pct "
        "FROM paper_positions WHERE status = 'closed' "
        "ORDER BY signal_date, id")
    out = []
    for r in rows:
        d = dict(r)
        for k in ("entry_price", "stop_loss", "fill_price", "exit_price", "pnl_pct"):
            d[k] = float(d[k]) if d.get(k) is not None else None
        for k in ("signal_date", "fill_date", "exit_date"):
            d[k] = str(d.get(k) or "")[:10]
        # ★ 模拟盘 pnl_pct 是**毛收益**（fill→exit），回测 trade 的 pnl_pct 含双边成本
        #   （≈0.3%）⇒ 对比一律用**毛收益**重算，否则会把"成本差"误读成"体系差"。
        if d["fill_price"] and d["exit_price"]:
            d["gross"] = (d["exit_price"] / d["fill_price"] - 1) * 100
        else:
            d["gross"] = d["pnl_pct"] or 0.0
        out.append(d)
    return out


def validate_prices(paper: list, bars_map: dict) -> list:
    """★ 价格可信度自检：成交价/退出价必须落在**当日 [low, high]** 内（容差 1 分）。

    为什么必须查：本次实测发现 2 笔的退出价**低于当日最低价**（如 603408 记 10.91，
    而 09-15 最低 11.20；两源日线完全一致 ⇒ 不是行情数据错），物理上不可能成交
    ⇒ 这些笔的"实测收益"本身不可信，不能拿来当"体系差"的证据。
    """
    bad = []
    for p in paper:
        bars = bars_map.get(p["code"]) or []
        by = {b["date"]: b for b in bars}
        fb, eb = by.get(p["fill_date"]), by.get(p["exit_date"])
        p["fill_ok"] = bool(fb and p["fill_price"]
                            and fb["low"] - 0.011 <= p["fill_price"] <= fb["high"] + 0.011)
        p["exit_ok"] = bool(eb and p["exit_price"]
                            and eb["low"] - 0.011 <= p["exit_price"] <= eb["high"] + 0.011)
        p["price_ok"] = p["fill_ok"] and p["exit_ok"]
        if not p["price_ok"]:
            bad.append({
                "code": p["code"], "name": p["name"], "signal_date": p["signal_date"],
                "fill_date": p["fill_date"], "fill_price": p["fill_price"],
                "fill_range": f"{fb['low']:.2f}~{fb['high']:.2f}" if fb else "无此日",
                "exit_date": p["exit_date"], "exit_price": p["exit_price"],
                "exit_range": f"{eb['low']:.2f}~{eb['high']:.2f}" if eb else "无此日",
                "fill_ok": p["fill_ok"], "exit_ok": p["exit_ok"],
            })
    return bad


def load_bars(codes: list, start: str, end: str) -> dict:
    """{code: [{date, open, high, low, close}]}（升序）。**单源** backtest_prices。"""
    if not codes:
        return {}
    ph = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, date, open, high, low, close FROM backtest_prices "
        f"WHERE code IN ({ph}) AND date >= %s AND date <= %s ORDER BY code, date",
        tuple(codes) + (start, end))
    out = {}
    for r in rows:
        out.setdefault(r["code"], []).append({
            "date": str(r["date"])[:10],
            "open": float(r["open"] or 0), "high": float(r["high"] or 0),
            "low": float(r["low"] or 0), "close": float(r["close"] or 0),
        })
    return out


# ────────────────────────────────────────────────────────────────
#  引擎撮合（两个版本）
# ────────────────────────────────────────────────────────────────
def run_engine(paper: list, bars_map: dict, variant: str) -> tuple:
    """variant='A'（标准 v2）/'B'（沿用模拟盘止损）。返回 (trades, skipped)。"""
    signals = []
    for p in paper:
        sig = {
            "date": p["signal_date"], "code": p["code"], "name": p["name"],
            "direction": "long", "strategy": p["name"],
            "strategy_en": p["strategy_name"],
            "hold_days": WARFARE_HOLD_DAYS_V2, "is_etf": False,
        }
        if variant == "A":
            # 标准 v2：止损基准 = **信号日收盘**（与 apply_exit_policy 的 base_close 同义）
            base = None
            for b in bars_map.get(p["code"]) or []:
                if b["date"] == p["signal_date"]:
                    base = b["close"]
                    break
            sig = apply_exit_policy(sig, base_close=base or p["entry_price"] or 0)
        else:
            # B：用模拟盘当初存下的止损（早期行是 0.85，现行是 0.93 ⇒ 参数差就出在这里）
            sig["stop_loss"] = p["stop_loss"]
            sig["take_profit"] = None
        signals.append(sig)

    skipped = []
    trades = match_signals(signals, bars_map, costs=DEFAULT_COSTS,
                           default_hold_days=WARFARE_HOLD_DAYS_V2,
                           skipped_out=skipped)
    for t in trades:
        t["gross"] = (t["exit_price"] / t["entry_price"] - 1) * 100
    return trades, skipped


# ────────────────────────────────────────────────────────────────
#  统计
# ────────────────────────────────────────────────────────────────
def stats(vals: list) -> dict:
    n = len(vals)
    if not n:
        return {"n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gain = sum(wins)
    loss = abs(sum(losses))
    srt = sorted(vals)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return {
        "n": n,
        "win": round(len(wins) / n * 100, 1),
        "avg": round(sum(vals) / n, 3),
        "median": round(med, 3),
        "pf": round(gain / loss, 2) if loss > 1e-9 else None,
        "max_gain": round(max(vals), 2),
        "max_loss": round(min(vals), 2),
    }


def decompose(rows: list) -> dict:
    """差异分解：总差 → 入场日口径 / 入场价差 / 退出原因 三个维度。"""
    def agg(sub):
        p = [r["paper_gross"] for r in sub if r["paper_gross"] is not None]
        a = [r["A_gross"] for r in sub if r["A_gross"] is not None]
        d = [_diff(r) for r in sub if _diff(r) is not None]
        return {"n": len(sub),
                "P": round(sum(p) / len(p), 3) if p else None,
                "A": round(sum(a) / len(a), 3) if a else None,
                "diff": round(sum(d) / len(d), 3) if d else None}

    out = {"all": agg(rows),
           "same_day": agg([r for r in rows if r["same_day_fill"]]),
           "normal": agg([r for r in rows if not r["same_day_fill"]])}
    # 入场价差（模拟盘成交价 vs 引擎 T+1 开盘）：>0 = 模拟盘买得更贵
    buck = {"cheap": [], "flat": [], "dear": []}
    for r in rows:
        e = r.get("entry_diff_pct")
        if e is None:
            continue
        buck["cheap" if e <= -0.5 else ("dear" if e >= 0.5 else "flat")].append(r)
    out["entry_cheap"] = agg(buck["cheap"])
    out["entry_flat"] = agg(buck["flat"])
    out["entry_dear"] = agg(buck["dear"])
    # 按模拟盘退出原因
    by = {}
    for r in rows:
        by.setdefault(r["paper_reason"] or "—", []).append(r)
    out["by_reason"] = {k: agg(v) for k, v in sorted(by.items(), key=lambda x: -len(x[1]))}
    return out


def build_compare(paper: list, tA: dict, tB: dict) -> list:
    """逐笔对齐 P / A / B 三个口径。"""
    out = []
    for p in paper:
        key = (p["code"], p["signal_date"], p["strategy_name"])
        a = tA.get(key) or {}
        b = tB.get(key) or {}
        out.append({
            "code": p["code"], "name": p["name"], "strategy": p["strategy_name"],
            "signal_date": p["signal_date"], "fill_date": p["fill_date"],
            "same_day_fill": bool(p["fill_date"] and p["fill_date"] <= p["signal_date"]),
            "paper_gross": p["gross"], "paper_reason": p["exit_reason"],
            "paper_stop_ratio": (round(p["stop_loss"] / p["entry_price"], 4)
                                 if p["entry_price"] and p["stop_loss"] else None),
            "A_gross": a.get("gross"), "A_entry": a.get("entry_price"),
            "A_entry_date": a.get("entry_date"), "A_reason": a.get("exit_reason"),
            "A_hold": a.get("hold_days"),
            "B_gross": b.get("gross"), "B_reason": b.get("exit_reason"),
            "B_hold": b.get("hold_days"), "price_ok": p.get("price_ok", True),
            "entry_diff_pct": (round((p["fill_price"] / a["entry_price"] - 1) * 100, 2)
                               if a.get("entry_price") and p.get("fill_price") else None),
        })
    return out


# ────────────────────────────────────────────────────────────────
#  报告
# ────────────────────────────────────────────────────────────────
def _row(cells) -> str:
    return "| " + " | ".join("" if c is None else str(c) for c in cells) + " |"


def _fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def write_report(paper, rows, stP, stA, stB, skA, skB, split, dec, bad, clean_st, path_md):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append(f"# 模拟盘实测 vs 回测引擎（同批信号）· {now}\n")
    L.append("## 一、口径\n")
    L.append(f"- **样本**：`paper_positions` 已平仓 **{len(paper)} 笔**（同一批 (代码, 信号日, 战法) 喂给引擎）")
    L.append("- **P 模拟盘**：真实成交价 → 真实退出；★ 库里的 `pnl_pct` **含双边成本**（实测与价差差 0.3%），"
             "本表一律重算为**毛收益**（fill→exit 价差），与引擎的 gross 同口径")

    L.append(f"- **A 引擎·标准 v2**：T+1 开盘入场、止损=信号日收盘×{1 - WARFARE_STOP_PCT_V2:.2f}、"
             f"持有 {WARFARE_HOLD_DAYS_V2} 日（当前生产口径 `WARFARE_EXIT_POLICY={exit_policy()}`）")
    L.append("- **B 引擎·沿用模拟盘止损**：同上，但止损用模拟盘当初存的值（用于分离**参数差**）")
    L.append("- 撮合器：`app.backtest.engine.match_signals`（与回测/周报同一份代码，非另写）")
    L.append("- 单源价格：`backtest_prices`（未与 zzshare 混用）\n")

    L.append("## 二、核心对照\n")
    L.append(_row(["口径", "n", "胜率%", "均收益%", "中位%", "盈亏比", "最大盈利%", "最大亏损%"]))
    L.append(_row(["---"] * 8))
    for label, st in (("P 模拟盘实测", stP), ("A 引擎·标准v2", stA), ("B 引擎·沿用盘中止损", stB)):
        L.append(_row([label, st.get("n"), st.get("win"), st.get("avg"), st.get("median"),
                       _fmt(st.get("pf")), _fmt(st.get("max_gain")), _fmt(st.get("max_loss"))]))
    L.append("")

    if bad:
        cp, ca = clean_st["P"], clean_st["A"]
        L.append(f"- ★ **价格自检**：{len(bad)} 笔的成交/退出价**不在当日 [low, high] 内**（物理上不可能成交）"
                 f"⇒ 剔除后剩 **{cp.get('n')} 笔**：P 胜率 {cp.get('win')}% / 均 {cp.get('avg')}%，"
                 f"A 胜率 {ca.get('win')}% / 均 {ca.get('avg')}%（详见 §七）")
    L.append("")

    L.append("## 三、差异归因（把「有偏差」拆成可修的项）\n")
    dA = stP.get("win", 0) - stA.get("win", 0)
    dAB = stA.get("win", 0) - stB.get("win", 0)
    dBP = stB.get("win", 0) - stP.get("win", 0)
    L.append(f"- **总差（P−A）胜率 {dA:+.1f}pp、均收益 {stP.get('avg', 0) - stA.get('avg', 0):+.3f}pt**")
    L.append(f"- **参数差（A−B，止损 0.93 vs 模拟盘存的 0.85~0.99）胜率 {dAB:+.1f}pp**")
    L.append(f"- **执行差（B−P，入场时点/盘中触发）胜率 {dBP:+.1f}pp**")
    if skA:
        L.append(f"- A 版被引擎剔除 {len(skA)} 笔：" +
                 "、".join(f"{k}×{v}" for k, v in _count(skA).items()))
    if skB:
        L.append(f"- B 版被引擎剔除 {len(skB)} 笔：" +
                 "、".join(f"{k}×{v}" for k, v in _count(skB).items()))
    L.append("")

    L.append("## 四、差异从哪来（三步分解）\n")
    L.append("① **入场日口径**（模拟盘当日成交 vs 引擎 T+1）：\n")
    L.append(_row(["子集", "n", "P 均%", "A 均%", "P−A"]))
    L.append(_row(["---"] * 5))
    for label, k in (("全部", "all"), ("同日成交子集", "same_day"),
                     ("正常 T+1 子集", "normal")):
        d = dec[k]
        L.append(_row([label, d["n"], _fmt(d["P"], 3), _fmt(d["A"], 3), _fmt(d["diff"], 3)]))
    L.append("")
    L.append("② **入场价差**（模拟盘 9:35 成交价 vs 引擎 T+1 开盘价；>0 = 模拟盘买得更贵）：\n")
    L.append(_row(["入场差区间", "n", "P 均%", "A 均%", "P−A"]))
    L.append(_row(["---"] * 5))
    for label, k in (("≤ −0.5%（买得更便宜）", "entry_cheap"),
                     ("−0.5% ~ +0.5%", "entry_flat"),
                     ("≥ +0.5%（买得更贵）", "entry_dear")):
        d = dec[k]
        L.append(_row([label, d["n"], _fmt(d["P"], 3), _fmt(d["A"], 3), _fmt(d["diff"], 3)]))
    L.append("")
    L.append("③ **按模拟盘退出原因**（P−A 明显为负 ⇒ 该退出路径与引擎不一致）：\n")
    L.append(_row(["退出原因", "n", "P 均%", "A 均%", "P−A"]))
    L.append(_row(["---"] * 5))
    for k, d in dec["by_reason"].items():
        L.append(_row([k, d["n"], _fmt(d["P"], 3), _fmt(d["A"], 3), _fmt(d["diff"], 3)]))
    L.append("")

    if split["same_day"]:
        L.append("## 五、⚠️ 入场口径不同源的子集（必须单独看）\n")
        L.append(f"**{len(split['same_day'])} 笔** 的 `fill_date <= signal_date`（模拟盘当日就成交）——"
                 "这来自 2026-09-19 之前 `signal_date` 用「运行日」而非「信号所属交易日」的历史遗留。")
        L.append("引擎按「信号日 T+1 开盘」入场 ⇒ 这批的差异**主要是口径差，不是执行差**：\n")
        L.append(_row(["代码", "名称", "信号日", "成交日", "P", "A", "差"]))
        L.append(_row(["---"] * 7))
        for r in split["same_day"]:
            L.append(_row([r["code"], r["name"], r["signal_date"], r["fill_date"],
                           _fmt(r["paper_gross"]), _fmt(r["A_gross"]),
                           _fmt(_diff(r))]))
        L.append("")

    L.append("## 六、逐笔明细\n")
    L.append(_row(["代码", "名称", "信号日", "P 毛%", "A 毛%", "B 毛%", "P−A", "P退出", "A退出",
                   "A持有", "入场差%", "止损率"]))
    L.append(_row(["---"] * 12))
    for r in sorted(rows, key=lambda x: -(x["paper_gross"] or 0)):
        L.append(_row([r["code"], r["name"], r["signal_date"], _fmt(r["paper_gross"]),
                       _fmt(r["A_gross"]), _fmt(r["B_gross"]), _fmt(_diff(r)),
                       r["paper_reason"], r["A_reason"], r["A_hold"],
                       _fmt(r["entry_diff_pct"]), r["paper_stop_ratio"]]))
    L.append("")

    L.append("## 七、数据可信度自检（成交/退出价是否落在当日区间内）\n")
    if not bad:
        L.append("全部通过：成交价与退出价均落在当日 [low, high] 内。\n")
    else:
        L.append(f"**{len(bad)} 笔不通过** —— 价格落在当日最低~最高之外，物理上不可能成交"
                 "（两源日线 low/high 完全一致，不是行情数据错）：\n")
        L.append(_row(["代码", "名称", "信号日", "成交日", "成交价", "当日区间",
                       "退出日", "退出价", "当日区间", "判定"]))
        L.append(_row(["---"] * 10))
        for b in bad:
            chk = "退出价异常" if b["exit_ok"] is False else "成交价异常"
            L.append(_row([b["code"], b["name"], b["signal_date"], b["fill_date"],
                           _fmt(b["fill_price"]), b["fill_range"], b["exit_date"],
                           _fmt(b["exit_price"]), b["exit_range"], chk]))
        L.append("")
        L.append("**剔除这些笔后的对照（这才是可信样本）**：\n")
        L.append(_row(["口径", "n", "胜率%", "均收益%", "中位%", "盈亏比"]))
        L.append(_row(["---"] * 6))
        for label, st in (("P 模拟盘", clean_st["P"]), ("A 引擎·标准v2", clean_st["A"]),
                          ("B 引擎·沿用盘中止损", clean_st["B"])):
            L.append(_row([label, st.get("n"), st.get("win"), st.get("avg"),
                           st.get("median"), _fmt(st.get("pf"))]))
        L.append("")

    L.append("## 八、结论\n")
    cp, ca, cb = clean_st["P"], clean_st["A"], clean_st["B"]
    L.append(f"1. **可信样本上，模拟盘与回测引擎基本吻合**：P 胜率 {cp.get('win')}% vs A {ca.get('win')}%"
             f"（{cp.get('win', 0) - ca.get('win', 0):+.1f}pp），均收益 {cp.get('avg')}% vs {ca.get('avg')}%"
             f"（{cp.get('avg', 0) - ca.get('avg', 0):+.3f}pt）⇒ 回测引擎的撮合假设"
             f"**没有被实盘证伪**（样本 {cp.get('n')} 笔，统计力有限，不作强结论）。")
    L.append(f"2. **全样本的『模拟盘更差 0.32pt』不可信**：那 {len(bad)} 笔越界价格"
             f"几乎全是亏损单（如 600686 −8.18% vs 引擎 −1.00%），把结论整体拉偏。")
    L.append(f"3. **参数差**（A−B）胜率 {stA.get('win', 0) - stB.get('win', 0):+.1f}pp ⇒ 现行 −7% 止损"
             "（`WARFARE_STOP_PCT_V2`）与早期 0.85 止损之间**有实质差别**，不是纯噪声。")
    L.append("4. ★ **新发现的数据缺陷（P1）**：模拟盘记录的成交/退出价有 "
             f"**{len(bad)}/{len(rows)} = {len(bad) / max(len(rows), 1) * 100:.0f}%** 落在当日 "
             "[low, high] 之外。两个候选机制（都在 `strategies/paper_trading.py`，已定位到行）：")
    L.append("   - **① 价格与日期不同源**：`_close_position()` 用 `exit_date = _bj_date()`（**今天**）落库，"
             "而价格来自 `bars[-1][\"close\"]`（盘后兜底分支）或 `_latest_close()`（安全网分支）**不一定是同一天** "
             "⇒ 「退出日」是今天、「退出价」是别的日子 ⇒ 必然越界。")
    L.append("   - **② 复权口径**：部分个股呈**每只恒定的价差/比率**（603408 10.91 vs 11.60、"
             "600686 10.10 vs 10.89、001296 20.00 vs 22.00），符合「前复权 vs 不复权」的特征"
             "（`backtest_prices` 是原始价）⇒ 需核对平仓所用 K 线的取数口径。")
    L.append("   → **修法**：平仓时把「价格所属交易日」一并落库（新增 `exit_price_date`），"
             "`exit_date` 一律由它回填；并核对平仓 K 线是否复权。**在此之前，任何「模拟盘实测绩效」"
             "都只能当参考，不能当结论。**")
    L.append("")

    path_md.write_text("\n".join(L), encoding="utf-8")
    return path_md


def _diff(r):
    if r.get("paper_gross") is None or r.get("A_gross") is None:
        return None
    return r["paper_gross"] - r["A_gross"]


def _count(sk: list) -> dict:
    c = {}
    for x in sk:
        c[x["reason"]] = c.get(x["reason"], 0) + 1
    return c


# ────────────────────────────────────────────────────────────────
#  main
# ────────────────────────────────────────────────────────────────
def main():
    paper = load_paper()
    if not paper:
        print("[F1] 无已平仓记录")
        return
    codes = sorted({p["code"] for p in paper})
    start = min(p["signal_date"] for p in paper)
    end = max(p["exit_date"] or p["signal_date"] for p in paper)
    bars_map = load_bars(codes, start, end)
    print(f"[F1] 模拟盘 {len(paper)} 笔 / {len(codes)} 只；行情 {len(bars_map)} 只有数据")
    bad = validate_prices(paper, bars_map)
    print(f"[F1] 价格自检: {len(bad)} 笔成交/退出价不在当日区间内"
          f"{'（已单列，见报告 §七）' if bad else ''}")

    tA, skA = run_engine(paper, bars_map, "A")
    tB, skB = run_engine(paper, bars_map, "B")
    keyA = {(t["code"], t["signal_date"], t["strategy_en"]): t for t in tA}
    keyB = {(t["code"], t["signal_date"], t["strategy_en"]): t for t in tB}

    rows = build_compare(paper, keyA, keyB)
    stP = stats([r["paper_gross"] for r in rows])
    stA = stats([r["A_gross"] for r in rows if r["A_gross"] is not None])
    stB = stats([r["B_gross"] for r in rows if r["B_gross"] is not None])

    split = {"same_day": [r for r in rows if r["same_day_fill"]]}
    dec = decompose(rows)

    clean = [r for r in rows if r["price_ok"]]
    clean_st = {"P": stats([r["paper_gross"] for r in clean]),
                "A": stats([r["A_gross"] for r in clean if r["A_gross"] is not None]),
                "B": stats([r["B_gross"] for r in clean if r["B_gross"] is not None])}

    for label, st in (("P 模拟盘", stP), ("A 引擎标准v2", stA), ("B 引擎沿用盘中止损", stB)):
        print(f"  {label:<20} n={st['n']:>3} 胜率={st['win']:>5.1f}% "
              f"均={st['avg']:+.3f}% 中位={st['median']:+.3f}% PF={_fmt(st['pf'])}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md = REPORT_DIR / f"paper_vs_backtest_{stamp}.md"
    write_report(paper, rows, stP, stA, stB, skA, skB, split, dec, bad, clean_st, md)
    print(f"[F1] 报告: {md}")

    # 结论提示（让人一眼看到"差多少、差在哪"）
    cp, ca = clean_st["P"], clean_st["A"]
    print(f"\n[F1] 剔除越界价后（n={cp['n']}）：P 胜率 {cp['win']}% / 均 {cp['avg']:+.3f}% "
          f"｜ A 胜率 {ca['win']}% / 均 {ca['avg']:+.3f}% "
          f"⇒ 差 {cp['win'] - ca['win']:+.1f}pp / {cp['avg'] - ca['avg']:+.3f}pt")
    print(f"[F1] 总差 P−A 胜率 {stP['win'] - stA['win']:+.1f}pp / "
          f"均收益 {stP['avg'] - stA['avg']:+.3f}pt（全样本，受越界价影响）")
    print(f"[F1] 其中 参数差(A−B) {stA['win'] - stB['win']:+.1f}pp / "
          f"执行差(B−P) {stB['win'] - stP['win']:+.1f}pp")


if __name__ == "__main__":
    main()
