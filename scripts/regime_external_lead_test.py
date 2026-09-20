#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】regime「外部领先因子」增量价值验证（Phase 2 前置，先回测后改生产）
================================================================================
背景（2026-09-20）：
  `macro_lead_backtest.py` 用 **same（同日）口径** 算出两个外部变量的高 IC：
    · 纳指 NQ   1 日窗 IC = **+0.1609**（隔夜美股 → A 股次日，方向为正）
    · 美元指数  5 日窗 IC = **-0.1005**（美元走强 → A 股承压）
  ⚠️ **但该口径含时区前视**（美股夜盘发生在 A 股收盘之后 ⇒ 等于用"次日才知道的
    信息"预测），**不可交易**：按 `scripts/fake_lead_diagnosis.py` 的 lag1 口径实测，
    三者 |IC| 全部塌陷至 ≤0.04（NQ +0.1609→+0.0115、VX -0.1124→-0.0052、
    UDI -0.0972→-0.0230）。
  ✅ **本脚本的结论不受影响** —— 它的分组检验**本就用的 lag1**（`src = dates[i-1]`），
    测的是**条件差异**（同一 regime 内预警 vs 对照）而非"线性领先"，故依然成立 ✅
    · 美债 10Y/30Y 仅 0.066（弱，**不是领先而是同步**）
  而系统现在只把外部数据用于 `market_regime._external_panic()`（**当日脉冲**：
  日经单日<-2% / 油胀金跌 / 美债单日>+5bp），**没有**"领先因子"的用法。

本脚本要回答的唯一问题（**先回测，再决定要不要改生产**）：
  **在 regime 之外，这两个领先信号是否还有"增量"预警价值？**
  即：*同一个 regime 状态内*，被外部信号预警的日子，未来收益是否显著更差？
    · 若"是" ⇒ 值得把领先因子并入 regime 判据（Phase 3，带开关）
    · 若"否" ⇒ regime 已涵盖外部信息 ⇒ **不加**（负结果同样归档）

★ 判定标准**预先写死**（防事后挑格子）：
  在某个 regime 内，**预警组的未来 5 日收益比非预警组差 ≥1.0pt，且两组各 n≥50**
  ⇒ 「有增量价值」；否则「无增量」。

方法（零 Supabase，全本地）：
  1. regime 历史：`detect_market_regime(沪深300 bars)` —— **纯函数历史重放**
     （`market_regime_history` 表只有 22 天，不够；函数重放可得 700+ 天）
     ⚠️ 注意：重放得到的是**基础状态**（offensive/neutral/defensive），
     不含 `neutral_bearish`——因为 nb 依赖实时宽度缓存与实时宏观面板，无法回溯。
  2. 外部信号（两口径并算，互相印证）：
     · `same`  —— 同日对齐（保守）
     · `lag1`  —— **滞后 1 日**（纳指/美元 d-1 收盘 → A 股 d）＝真实可交易口径
  3. 预警定义（**分位自适应**，避免手调阈值）：纳指变化 < 20% 分位
     **或** 美元 5 日累计变化 > 80% 分位 ⇒ 预警。
  4. 分组统计 `regime × 预警` 的未来 5 日 / 10 日收益。

用法：
  python scripts/regime_external_lead_test.py
  python scripts/regime_external_lead_test.py --holding 5 --min-n 50
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db                                        # noqa: E402
from app.backtest.market_regime import detect_market_regime        # noqa: E402

SERIES_PATH = os.path.join(BACKEND, "data", "macro_series.json")
NQ_KEY = "sina:NQ"          # 纳斯达克100期货（领先 IC 最强）
USD_KEY = "100.UDI"         # 美元指数（5 日窗负 IC）
_MIN_DIFF = 1.0             # 预注册：预警组须比对照组差这么多 pt
_Q_LOW, _Q_HIGH = 20, 80    # 预警分位（自适应，不手调阈值）

# ── 逐因子单独评估的规格（2026-09-20 追加，`--factors` 启用）──────────────────
# 动机：本脚本默认的预警是「NQ 或 USD **两因子合并**」，因此无法回答
#   「单个因子自己是否具备条件价值」—— 尤其 VIX（Phase 1 里 |IC| 与美元相当，
#   却在 Phase 2 被排除）究竟是"没测过"还是"测了无效"。
# 规格：(序列key, 累计窗, 预警方向, 说明)
#   · 纳指 **跌** ⇒ 预警 ⇒ 取低分位；美元/VIX **涨** ⇒ 预警 ⇒ 取高分位
#   · 窗长取 Phase 1 各自的"最优窗"（纳指 1 日、美元 5 日、VIX 1 日），
#     VIX 另给 5 日累计作对照（防止"只是窗长选错"）。
SIGNALS = {
    "nq": ("sina:NQ", 1, "low", "纳指 1 日"),
    "nq5": ("sina:NQ", 5, "low", "纳指 5 日累计"),
    "usd": ("100.UDI", 5, "high", "美元 5 日累计"),
    "vx": ("sina:VX", 1, "high", "VIX 1 日"),
    "vx5": ("sina:VX", 5, "high", "VIX 5 日累计"),
}


def load_series():
    if not os.path.exists(SERIES_PATH):
        return {}
    with open(SERIES_PATH, "r", encoding="utf-8") as f:
        return (json.load(f) or {}).get("series") or {}


def load_bars(code: str = "sh000300") -> list:
    rows = db.fetch("SELECT date, open, high, low, close, volume "
                    "FROM backtest_prices WHERE code = %s "
                    "AND close IS NOT NULL ORDER BY date", (code,)) or []
    out = []
    for r in rows:
        try:
            out.append({"date": str(r["date"])[:10], "open": float(r["open"] or 0),
                        "high": float(r["high"] or 0), "low": float(r["low"] or 0),
                        "close": float(r["close"]), "volume": float(r["volume"] or 0)})
        except (TypeError, ValueError):
            continue
    return out


def pct_changes(rows: list) -> dict:
    """{date: 当日 % 变化}。"""
    out, prev = {}, None
    for r in rows:
        if prev and prev.get("close"):
            out[r["date"]] = (r["close"] / prev["close"] - 1) * 100
        prev = r
    return out


def rolling_sum(chg: dict, dates: list, win: int = 5) -> dict:
    """{date: 近 win 日累计变化}（用连续交易日）。"""
    out = {}
    seq = [d for d in dates if d in chg]
    for i, d in enumerate(seq):
        if i + 1 < win:
            continue
        out[d] = sum(chg[seq[j]] for j in range(i - win + 1, i + 1))
    return out


def q(values: list, p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def _stat(v):
    if not v:
        return None
    wins = [x for x in v if x > 0]
    return {"n": len(v), "avg": sum(v) / len(v),
            "win": len(wins) / len(v) * 100}


def _f(s):
    return "n=0" if not s else (f"n={s['n']:<4} 均{s['avg']:>+6.2f}% "
                                f"胜率{s['win']:>5.1f}%")


def verdict(warn_stat, calm_stat, min_n, min_diff):
    """**预先写死**的判定。"""
    if not warn_stat or not calm_stat:
        return "样本不足", "任一组为空"
    if warn_stat["n"] < min_n or calm_stat["n"] < min_n:
        return "样本不足", f"组内 n<{min_n}（{warn_stat['n']}/{calm_stat['n']}）"
    diff = calm_stat["avg"] - warn_stat["avg"]        # 正 = 预警组确实更差
    if diff >= min_diff:
        return "**有增量价值**", f"预警组比对照组差 {diff:.2f}pt（≥{min_diff} 门槛）"
    return "无增量（regime 已涵盖）", f"仅差 {diff:.2f}pt（<{min_diff} 门槛）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holding", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--min-n", type=int, default=50)
    ap.add_argument("--index", default="sh000300")
    ap.add_argument("--factors", nargs="+", default=None,
                    help=f"逐因子单独评估（可选 {'/'.join(SIGNALS)}）；"
                         "不给则只跑原两因子合并口径（默认行为不变）")
    ap.add_argument("--include", nargs="+", default=None,
                    help="把这些因子**并入**合并预警，与基线（NQ低|USD高）对照 —— "
                         "回答『追加因子（如 vx）是增强还是稀释』")
    args = ap.parse_args()

    series = load_series()
    bars = load_bars(args.index)
    if not series or not bars:
        print("✗ 缺数据：macro_series.json 或 沪深300 日线")
        return 1
    nq_rows = (series.get(NQ_KEY) or {}).get("rows") or []
    usd_rows = (series.get(USD_KEY) or {}).get("rows") or []
    if not nq_rows or not usd_rows:
        print(f"✗ 缺外部序列（需要 {NQ_KEY} 与 {USD_KEY}）")
        return 1

    # 1) regime 历史重放（纯函数）
    states = detect_market_regime(bars)
    regime_map = {s.date: s.state for s in states}

    # 2) 外部信号
    nq_chg = pct_changes([{"date": r[0], "close": r[2]} for r in nq_rows])
    usd_chg = pct_changes([{"date": r[0], "close": r[2]} for r in usd_rows])
    usd_chg5 = rolling_sum(usd_chg, [r[0] for r in usd_rows], win=5)

    dates = [b["date"] for b in bars]
    idx = {d: i for i, d in enumerate(dates)}

    print("=" * 96)
    print(f"regime 外部领先因子 · 增量价值验证    指数={args.index}（{len(bars)} 根）"
          f"  regime 重放 {len(states)} 天")
    print(f"外部信号：{NQ_KEY}（纳指，1 日窗 IC 曾=+0.16）｜"
          f"{USD_KEY}（美元指数，5 日窗 IC 曾=-0.10）")
    print(f"预注册判定：组内『预警 vs 对照』未来收益差 ≥ {_MIN_DIFF}pt 且两组 n≥{args.min_n}"
          f" ⇒ 有增量价值")
    print("=" * 96)

    for align in ("same", "lag1"):
        # 3) 逐日表
        recs = []
        for d in dates:
            i = idx[d]
            src = d if align == "same" else (dates[i - 1] if i >= 1 else None)
            if src is None:
                continue
            rec = {"date": d, "regime": regime_map.get(d),
                   "nq": nq_chg.get(src), "usd5": usd_chg5.get(src)}
            if not rec["regime"]:
                continue
            recs.append(rec)
        # 4) 分位阈值（用全体样本，避免分组后自证）
        nq_vals = [r["nq"] for r in recs if r["nq"] is not None]
        usd_vals = [r["usd5"] for r in recs if r["usd5"] is not None]
        nq_cut, usd_cut = q(nq_vals, _Q_LOW), q(usd_vals, _Q_HIGH)
        print(f"\n{'=' * 96}\n对齐口径 = "
              f"{'同日（保守）' if align == 'same' else '滞后 1 日（可交易口径）'}"
              f"   纳指阈值<{nq_cut:+.2f}%  美元5日阈值>{usd_cut:+.2f}%"
              f"   （{_Q_LOW}/{_Q_HIGH} 分位）")
        print("=" * 96)
        for r in recs:
            r["warn"] = ((r["nq"] is not None and r["nq"] < nq_cut)
                         or (r["usd5"] is not None and r["usd5"] > usd_cut))
        for h in args.holding:
            groups = defaultdict(list)
            for r in recs:
                i = idx[r["date"]]
                if i + h >= len(bars):
                    continue
                fwd = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
                groups[(r["regime"], r["warn"])].append(fwd)
            print(f"\n  持有 {h} 日 【regime × 外部预警 → 收益】")
            print(f"    {'regime':<18}{'对照（未预警）':<40}{'预警':<40}判定")
            for st in ("offensive", "neutral", "defensive"):
                calm = _stat(groups.get((st, False), []))
                warn = _stat(groups.get((st, True), []))
                if not calm and not warn:
                    continue
                tag, why = verdict(warn, calm, args.min_n, _MIN_DIFF)
                print(f"    {st:<18}{_f(calm):<40}{_f(warn):<40}{tag}（{why}）")

    # ── 逐因子单独评估（`--factors`）────────────────────────────────────────
    # ★ 只在**显式指定**时运行 ⇒ 上面「两因子合并」的输出与历史结论**完全不变**。
    # ★ 固定用 **lag1 口径**：same 含时区前视（见 `fake_lead_diagnosis.py`），
    #   条件价值必须在可交易口径下测才有意义。
    if args.factors:
        print("\n" + "=" * 96)
        print("【逐因子单独评估】每个因子**单独**定义预警（不与其它因子混合）")
        print(f"  判定门槛同预注册：『预警 vs 对照』未来收益差 ≥{_MIN_DIFF}pt 且两组 n≥{args.min_n}")
        print("  ★ 口径 = **lag1（可交易）** —— same 含时区前视，不适合判条件价值")
        print("=" * 96)
        for key in args.factors:
            spec = SIGNALS.get(key)
            if not spec:
                print(f"\n  ### {key}: 未定义（可选 {'/'.join(SIGNALS)}）")
                continue
            sid, win, side, desc = spec
            rows = (series.get(sid) or {}).get("rows") or []
            if len(rows) < args.min_n:
                print(f"\n  ### {key}: 序列样本不足（{len(rows)}）")
                continue
            chg = pct_changes([{"date": r[0], "close": r[2]} for r in rows])
            sig = (rolling_sum(chg, [r[0] for r in rows], win=win)
                   if win > 1 else chg)
            recs2 = []
            for i, d in enumerate(dates):
                if i < 1:
                    continue
                st = regime_map.get(d)
                v = sig.get(dates[i - 1])          # ← lag1：上一 A 股交易日
                if st and v is not None:
                    recs2.append({"date": d, "regime": st, "v": v})
            if len(recs2) < args.min_n:
                print(f"\n  ### {key}: 可用样本不足（{len(recs2)}）")
                continue
            cut = q([r["v"] for r in recs2], _Q_LOW if side == "low" else _Q_HIGH)
            for r in recs2:
                r["warn"] = (r["v"] < cut) if side == "low" else (r["v"] > cut)
            print(f"\n  ### {key} = {sid}（{desc}，预警取"
                  f"{'低' if side == 'low' else '高'}分位）  阈值={cut:+.2f}  n={len(recs2)}")
            for h in args.holding:
                groups = defaultdict(list)
                for r in recs2:
                    i = idx[r["date"]]
                    if i + h >= len(bars):
                        continue
                    groups[(r["regime"], r["warn"])].append(
                        (bars[i + h]["close"] / bars[i]["close"] - 1) * 100)
                print(f"    持有 {h} 日")
                for st in ("offensive", "neutral", "defensive"):
                    calm = _stat(groups.get((st, False), []))
                    warn = _stat(groups.get((st, True), []))
                    if not calm and not warn:
                        continue
                    tag, why = verdict(warn, calm, args.min_n, _MIN_DIFF)
                    print(f"      {st:<16}{_f(calm):<42}{_f(warn):<42}{tag}（{why}）")

    # ── 合并扩容对照（`--include`）──────────────────────────────────────────
    # ★ 动机（2026-09-20）：实测发现 **VX 从未被评估过** —— 本脚本原先把预警硬编码为
    #   「NQ 低 或 USD 高」两项，VX 根本没进过实验，所以"只有 NQ+UDI 有增量价值"
    #   这句话当时并无实验依据。逐因子测出：单因子在 defensive 内**全部样本不足**
    #   （n=28~37 < 50），但 VX 与 NQ 仅中度重叠（Jaccard≈0.52）且**有 12 天独有**
    #   ⇒ 必须实测"并入后是增强还是稀释"（净增样本若明显更差，说明是稀释）。
    if args.include:
        print("\n" + "=" * 96)
        print("【合并扩容对照】基线（NQ 低 | USD 高） vs 追加因子后的合并")
        print(f"  ★ 只看 lag1（可交易）；门槛同上（差 ≥{_MIN_DIFF}pt 且两组 n≥{args.min_n}）")
        print("=" * 96)
        vals = {}
        for key in ("nq", "usd") + tuple(args.include):
            spec = SIGNALS.get(key)
            if not spec:
                continue
            sid, win, side, _desc = spec
            rows = (series.get(sid) or {}).get("rows") or []
            chg = pct_changes([{"date": r[0], "close": r[2]} for r in rows])
            vals[key] = (rolling_sum(chg, [r[0] for r in rows], win=win)
                         if win > 1 else chg, side)
        recs3 = []
        for i, d in enumerate(dates):
            if i < 1:
                continue
            st = regime_map.get(d)
            if not st:
                continue
            item = {"date": d, "regime": st}
            for k, (sig, _s) in vals.items():
                item[k] = sig.get(dates[i - 1])
            recs3.append(item)
        cuts = {}
        for k, (sig, side) in vals.items():
            xs = [r[k] for r in recs3 if r.get(k) is not None]
            if xs:
                cuts[k] = q(xs, _Q_LOW if side == "low" else _Q_HIGH)
        base_keys = ("nq", "usd")
        combos = [("基线 nq|usd", base_keys)] + [
            (f"+ {'+'.join(args.include)}", base_keys + tuple(
                k for k in args.include if k in vals))]
        for h in args.holding:
            print(f"\n  持有 {h} 日")
            for label, keys in combos:
                for r in recs3:
                    r["warn"] = False
                    for k in keys:
                        v = r.get(k)
                        if v is None or k not in cuts:
                            continue
                        side = vals[k][1]
                        if (v < cuts[k]) if side == "low" else (v > cuts[k]):
                            r["warn"] = True
                            break
                groups = defaultdict(list)
                for r in recs3:
                    i = idx[r["date"]]
                    if i + h >= len(bars):
                        continue
                    groups[(r["regime"], r["warn"])].append(
                        (bars[i + h]["close"] / bars[i]["close"] - 1) * 100)
                calm = _stat(groups.get(("defensive", False), []))
                warn = _stat(groups.get(("defensive", True), []))
                tag, why = verdict(warn, calm, args.min_n, _MIN_DIFF)
                print(f"    {label:<16}{_f(calm):<42}{_f(warn):<42}{tag}（{why}）")

    print("\n" + "=" * 96)
    print("注：regime 为重放的基础状态（不含 neutral_bearish —— 它依赖实时宽度/宏观面板，不可回溯）；")
    print("    预警用全体样本的 20/80 分位自适应阈值；两口径结论一致才算稳。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
