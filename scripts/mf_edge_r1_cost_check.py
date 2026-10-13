# -*- coding: utf-8 -*-
"""
================================================================================
【R1】主力节奏假设回填 · **成本实测**（PLAN_MAINFORCE_EDGE.md §6，规则已冻结）
================================================================================
【要回答的唯一问题】
  用**生产代码**（mainforce_overlay + trade_gate.evaluate/summarize）对 1 个历史
  交易日重算 phase+ready，**单日耗时 > 5 分钟 ⇒ 放弃回填、走前瞻**（规则冻结，
  防"回填到一半改主意"）。

【为什么这样算"生产口径"】
  · `mainforce_overlay(bars, flow_rows, float_shares, regime)` 是**纯函数** ——
    历史重算 = 把 bars/flow 序列**截断到判定日 d** 再调同一函数（无近似实现）；
  · ready：`trade_gate.evaluate(code, mf)` + `summarize`，mf 由重算 ov 组装
    （字段形状 = state._save/load_latest 同款）；**monkeypatch `trade_gate._regime`**
    注入 d 日的回填 regime（regime_edge_check 先例：回填与实时重叠段一致率 100%）；
  · 一致性预览：重算 phase/signal vs 当日**真实落库** mainforce_state 行
    （R2 的 ≥95% 一致率闸在 phase/signal 上的先导信号；ready 无落库历史 ⇒ 不比）。

【抽样】默认 150 只（成本线性外推到全池）；seed 固定 ⇒ 可复现。
================================================================================
"""
import argparse
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import db                                    # noqa: E402
from app.mainforce.overlay import mainforce_overlay            # noqa: E402
from app.mainforce.flow import (                               # noqa: E402
    get_float_shares_from_snapshot, load_flow_map,
)
from app.mainforce import trade_gate                           # noqa: E402
from app.mainforce.trade_gate import summarize as gate_sum     # noqa: E402
from app.mainforce.state import BARS_WINDOW                    # noqa: E402

BARS = 260          # 与 state.BARS_WINDOW 同款（chip warmup 60 / MA120 / phase ≥70）
FLOW_KEEP = 20      # 与 state.FLOW_WINDOW_DAYS 同款（overlay 只用近 5 日）


def trading_days() -> list:
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices ORDER BY date") or []
    return [str(r["date"])[:10] for r in rows]


def regime_map() -> dict:
    """回填 regime（**与 regime_edge_check.regime_map 逐行同款**：确定性规则 × 沪深300）。
    ⚠️ `detect_market_regime(bars)` 返回**带 .date/.state 的对象列表**（每日一条），
    不是单值——第一次跑就栽在这（list 进 `regime in GATE_REGIMES` ⇒ unhashable）。"""
    from app.backtest.market_regime import detect_market_regime
    rows = db.fetch("SELECT date, open, high, low, close, volume FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    bars = [{"date": str(r["date"])[:10], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"] or 0}
            for r in rows]
    states = detect_market_regime(bars)
    return {s.date: s.state for s in states}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-09",
                    help="判定日（默认取有真实 mainforce_state 落库的最新日）")
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261011)
    args = ap.parse_args()
    d = args.date

    t0 = time.time()
    # ── 1. 真实落库行（= 当日生产宇宙 + 一致性对照）──
    real = {r["code"]: r for r in
            (db.fetch("SELECT code, name, phase, signal FROM mainforce_state "
                      "WHERE date = %s", (d,)) or [])}
    stock_codes = sorted(c for c in real if _is_stock(c))
    pool_n = len(real)                      # 生产宇宙（含 31 个 ETF，回填同样会算）
    if not stock_codes:
        print(f"[R1] {d} 无真实 mainforce_state 落库行 ⇒ 无法一致性对照，换 --date")
        return 2

    # ── 2. 历史 regime（注入 trade_gate._regime）──
    reg = regime_map()
    regime_d = reg.get(d, "")
    trade_gate._regime = lambda: regime_d   # ★ 注入（仅本进程；measure 用）

    # ── 3. 抽样（确定性）──
    rnd = random.Random(args.seed)
    sample = rnd.sample(stock_codes, min(args.sample, len(stock_codes)))

    # ── 4. 共享数据装载（计时）──
    t = time.time()
    bars_all = _load_bars(sample, d)
    t_bars = time.time() - t
    t = time.time()
    flow_map = load_flow_map(codes=sample)          # 整窗读一次（本机缓存）
    t_flow = time.time() - t
    t = time.time()
    fs_map = get_float_shares_from_snapshot()
    t_fs = time.time() - t

    # ── 5. 逐票重算（计时）──
    t_overlay = t_gate = 0.0
    n_ov = n_gate = n_skip = 0
    match_phase = match_signal = n_cmp = 0
    ready_dist = {}
    for code in sample:
        bars, name = bars_all.get(code, ([], ""))
        if len(bars) < 120:                 # overlay 最低要求（与生产同门槛）
            n_skip += 1
            continue
        rows = flow_map.get(code) or []
        flow_trunc = [r for r in rows if str(r.get("date"))[:10] <= d][-FLOW_KEEP:]
        t = time.time()
        try:
            ov = mainforce_overlay(bars, flow_rows=flow_trunc,
                                   float_shares=fs_map.get(code), regime=regime_d)
        except Exception as e:
            print(f"  [warn] {code} overlay 异常: {type(e).__name__}: {e}")
            n_skip += 1
            continue
        t_overlay += time.time() - t
        n_ov += 1
        if not ov:
            continue
        # mf 字段形状 = state._save / load_latest 同款
        mf = {"code": code, "name": name, "date": d, "phase": ov.get("phase"),
              "signal": ov.get("signal"), "mult": ov.get("mult", 1.0),
              "chip": ov.get("chip") or {}, "flow5_amt": ov.get("flow5_amt"),
              "flow5_amt_yuan": ov.get("flow5_amt_yuan"),
              "flow_consec": ov.get("flow_consec")}
        t = time.time()
        try:
            s = gate_sum(trade_gate.evaluate(code, mf=mf))
        except Exception as e:
            print(f"  [warn] {code} gate 异常: {type(e).__name__}: {e}")
            continue
        t_gate += time.time() - t
        n_gate += 1
        ready_dist[s.get("ready")] = ready_dist.get(s.get("ready"), 0) + 1
        # 一致性（phase/signal vs 真实落库）
        r = real.get(code)
        if r:
            n_cmp += 1
            match_phase += (r.get("phase") == ov.get("phase"))
            match_signal += (r.get("signal") == ov.get("signal"))

    # ── 6. 外推与裁决（冻结规则：>5 分钟/日 ⇒ 放弃回填）──
    n_eff = max(n_ov, 1)
    per_stock = (t_overlay + t_gate) / n_eff
    projected_s = per_stock * pool_n + t_bars + t_flow + t_fs
    days_250 = projected_s * 250 / 3600.0
    verdict = ("R2 回填可行（≤5 分钟/日）" if projected_s <= 300
               else "放弃回填 → 走前瞻（R3）")

    print("=" * 72)
    print(f"【R1】主力节奏回填成本实测（判定日 {d}，抽样 {len(sample)} 只，seed={args.seed}）")
    print(f"  生产宇宙（真实落库行）= {pool_n} ｜ 个股 = {len(stock_codes)}（其余为 ETF/指数）")
    print(f"  共享装载：bars {t_bars:.1f}s ｜ flow {t_flow:.1f}s ｜ float_shares {t_fs:.1f}s")
    print(f"  单票 overlay：{t_overlay / n_eff * 1000:.0f} ms（n={n_ov}，合计 {t_overlay:.1f}s）")
    print(f"  单票 gate   ：{t_gate / max(n_gate, 1) * 1000:.0f} ms（n={n_gate}，合计 {t_gate:.1f}s）")
    print(f"  跳过（K线<120 根/异常）：{n_skip}")
    print(f"  ready 分布（重算）：{dict(sorted((ready_dist or {}).items()))}")
    if n_cmp:
        print(f"  一致率预览（vs {d} 真实落库，n={n_cmp}）："
              f"phase {match_phase / n_cmp:.1%} ｜ signal {match_signal / n_cmp:.1%}")
        print(f"    （R2 闸 = ≥95%；本抽样为 {min(args.sample, len(stock_codes))} 只的先导信号）")
    print("-" * 72)
    print(f"  外推单日全池（{pool_n} 只）：**{projected_s:.0f} 秒 ≈ {projected_s / 60:.1f} 分钟**"
          f"（冻结线 = 300 秒）")
    print(f"  回填 250 个交易日总量 ≈ {days_250:.1f} 小时"
          f"（另有逐日落库写入，R2 实施时实测）")
    print(f"  ★ 裁决（PLAN_MAINFORCE_EDGE §6 冻结规则）：{verdict}")
    print(f"  总耗时 {time.time() - t0:.0f}s")
    return 0


def _is_stock(code: str) -> bool:
    return (bool(code) and len(code) == 6 and code.isdigit() and code[0] in "036")


def _load_bars(codes: list, d: str) -> dict:
    """抽样票的日线（≤d，BARS 根）。数据源优先本地包（零 egress），回退 backtest_prices。"""
    out = {}
    try:
        from app import pack_source
        if pack_source.enabled():
            for c in codes:
                kl = pack_source.get_klines(c) or []
                kl = [b for b in kl if str(b.get("date"))[:10] <= d][-BARS:]
                if kl:
                    nm, _ = pack_source.get_name_cap(c)
                    out[c] = (kl, nm)
            if out:
                return out
    except Exception:
        pass
    # 回退：按抽样票直查（egress 可控：150 只 × 260 行）
    cutoff = (d and (d[:10])) or ""
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        rows = db.fetch("SELECT code, name, date, open, high, low, close, volume "
                        "FROM backtest_prices WHERE code = ANY(%s) AND date <= %s "
                        "ORDER BY code, date", (chunk, cutoff)) or []
        for r in rows:
            c = r["code"]
            bars = out.setdefault(c, ([], r.get("name") or ""))[0]
            if len(bars) < BARS:
                bars.append({"date": str(r["date"])[:10], "open": r["open"],
                             "high": r["high"], "low": r["low"], "close": r["close"],
                             "volume": r["volume"]})
    return out


if __name__ == "__main__":
    sys.exit(main())
