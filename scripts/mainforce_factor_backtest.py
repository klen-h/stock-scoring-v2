#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】主力思维因子全池截面回测（PLAN_MAINFORCE.md 阶段 1）
================================================================================

回答一个问题：**主力行为指标能否预测未来收益？**
散户因子（技术形态/估值）在回测里 IC≈0（见 factor_20260903_2030.md），
本脚本验证三组"主力视角"因子的预测力：

  A. 筹码结构（chips.py，纯 OHLCV+流通股本）
     winner_ratio 获利盘 / cost_bias 成本溢价 / concentration 筹码集中度 /
     price_pos 现价在筹码区间位置
  B. 主力资金流（flow.py，东财超大单/大单）
     flow5_amt 近5日主力净流入占成交额% / super5_amt 超大单口径 /
     flow_consec 连续净流入天数 / flow5_mkt 净流入占流通市值%
  C. 主力阶段（phases.py）
     吸筹/洗盘/拉升/出货/下跌/盘整 六标签的未来收益

  D. 组合信号「低位筹码密集 × 主力净流入」—— 主力思维的核心命题：
     筹码从散户手里换到主力手里的阶段买入，未来收益应显著为正。

方法（与 factor_analysis.py 同口径）：
  - 全池截面：backtest_prices 全部股票，每 5 个交易日一个截面，
    每截面全部可交易股票（不是 Top50 高分池 → 无"咬合面"问题）
  - 未来收益：截面日收盘 → T+5 / T+10 收盘（按个股自身交易日行号，定长）
  - IC：Spearman（因子值 vs 未来收益），原始 + 去市场超额（减截面全池均值）
  - 分桶：因子五分位 → 未来收益均值/胜率（单调性检验）

输出：backend/backtest_reports/mainforce_YYYYMMDD_HHMM.md

用法：python scripts/mainforce_factor_backtest.py [--hold 5 10] [--step 5]
================================================================================
"""

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        print(f"[warn] 未找到 {ENV_PATH}，将使用系统环境变量")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
sys.path.insert(0, BACKEND_DIR)

from app.database import db                                   # noqa: E402
from app.mainforce.chips import chip_series                   # noqa: E402
from app.mainforce.phases import phase_series                 # noqa: E402
from app.mainforce.flow import (                              # noqa: E402
    load_flow_map, get_float_shares, get_float_shares_from_snapshot)

import time as _time


def q(sql, params=None, retries=4):
    """带重试的查询（Supabase pooler 空闲断连 SSL，重试即恢复）。"""
    last = None
    for i in range(retries):
        try:
            return db.fetch(sql, params)
        except Exception as e:
            last = e
            try:
                db._reset_pg_conn()
            except Exception:
                pass
            _time.sleep(1.5 * (i + 1))
    raise last

FS_FILE = os.path.join(BACKEND_DIR, "data", "float_shares.json")


# ──────────────────────────────────────────────────────────────
#  工具（口径与 factor_analysis.py 一致）
# ──────────────────────────────────────────────────────────────
def _rank(values):
    n = len(values)
    order = sorted(range(n), key=lambda i: (values[i], i))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys)
             if a is not None and b is not None]
    if len(pairs) < 30:
        return None
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    rx, ry = _rank(xs2), _rank(ys2)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return {"rho": round(num / (dx * dy), 4), "n": len(pairs)}


def load_ohlc_all():
    """{code: [{date, open, high, low, close, volume}]} 升序。
    分块加载（整表 39 万行一次查会被连接池掐断）。"""
    codes = [r["code"] for r in q(
        "SELECT DISTINCT code FROM backtest_prices ORDER BY code")]
    by_code = {}
    CH = 120
    for i in range(0, len(codes), CH):
        chunk = codes[i:i + CH]
        rows = q(
            "SELECT code, date, open, high, low, close, volume FROM backtest_prices "
            "WHERE code = ANY(%s) ORDER BY code, date ASC", (chunk,))
        for r in rows:
            by_code.setdefault(r["code"], []).append({
                "date": str(r["date"]), "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
            })
    return by_code


def load_float_shares():
    """优先 market_snapshot 反推（不联网、无风控）→ 文件缓存 → 东财在线兜底。"""
    try:
        fs = get_float_shares_from_snapshot()
        if fs:
            with open(FS_FILE, "w", encoding="utf-8") as f:
                json.dump(fs, f)
            return fs
    except Exception as e:
        print(f"[float_shares] 快照反推失败：{e}")
    if os.path.exists(FS_FILE):
        try:
            with open(FS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data:
                print(f"[float_shares] 读缓存 {len(data)} 只（{FS_FILE}）")
                return data
        except Exception:
            pass
    print("[float_shares] 联网拉取全市场流通股本...")
    fs = get_float_shares()
    if fs:
        os.makedirs(os.path.dirname(FS_FILE), exist_ok=True)
        with open(FS_FILE, "w", encoding="utf-8") as f:
            json.dump(fs, f)
        print(f"[float_shares] 拉取 {len(fs)} 只 → {FS_FILE}")
    return fs


def bucket_table(samples, key, n_buckets=5, fwd="fwd5"):
    """因子五分位 → 未来收益。samples: [{key字段, fwd}]。"""
    vals = sorted([s[key] for s in samples if s.get(key) is not None
                   and s.get(fwd) is not None])
    if len(vals) < n_buckets * 30:
        return None
    qs = [vals[int(len(vals) * i / n_buckets)] for i in range(1, n_buckets)]
    rows = []
    for b in range(n_buckets):
        lo = -1e18 if b == 0 else qs[b - 1]
        hi = 1e18 if b == n_buckets - 1 else qs[b]
        grp = [s for s in samples
               if s.get(key) is not None and s.get(fwd) is not None
               and (lo <= s[key] < hi if b < n_buckets - 1 else s[key] >= lo)]
        if not grp:
            continue
        rets = [s[fwd] for s in grp]
        rows.append({
            "bucket": b + 1,
            "lo": round(lo, 2) if b > 0 else None,
            "hi": round(hi, 2) if b < n_buckets - 1 else None,
            "n": len(grp),
            "win": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "mean": round(sum(rets) / len(rets), 3),
        })
    return rows


def fmt_bucket(rows):
    if not rows:
        return "样本不足"
    head = "| 分位 | 区间 | n | 胜率% | 均收益% |"
    sep = "|---|---|---|---|---|"
    lines = []
    for r in rows:
        lo = f"{r['lo']}" if r["lo"] is not None else "-inf"
        hi = f"{r['hi']}" if r["hi"] is not None else "+inf"
        lines.append(f"| Q{r['bucket']} | [{lo}, {hi}) | {r['n']} | {r['win']} | {r['mean']} |")
    return "\n".join([head, sep] + lines)


# ──────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────
def build_samples(holds, step):
    prices = load_ohlc_all()
    print(f"[data] backtest_prices 股票数 {len(prices)}")
    flow_map = load_flow_map()
    print(f"[data] mainflow_history 覆盖 {len(flow_map)} 只")
    fs_map = load_float_shares()

    # 截面日：以资金流覆盖窗为准，前推 warmup 70 根，尾部留 max(hold) 前瞻
    max_hold = max(holds)
    common_dates = sorted({str(r["date"]) for rows in flow_map.values() for r in rows})
    if not common_dates:
        raise SystemExit("mainflow_history 为空，请先回填（python -m app.mainforce.flow）")
    sec_dates = common_dates[70:-max_hold] if len(common_dates) > 70 + max_hold else []
    sec_dates = sec_dates[::step]
    print(f"[sections] 截面日 {len(sec_dates)} 个：{sec_dates[0]} ~ {sec_dates[-1]}")
    sec_set = set(sec_dates)

    samples = []
    for ci, (code, bars) in enumerate(sorted(prices.items()), 1):
        if code not in flow_map:
            continue
        fs = fs_map.get(code)
        chip = chip_series(bars, float_shares=fs, dates_out=sec_dates)
        phase = phase_series(bars, dates_out=sec_dates)
        flow = [r for r in flow_map[code] if r["date"] in sec_set]
        flow_by_date = {r["date"]: k for k, r in enumerate(flow_map[code])
                        if r["date"] in sec_set}
        dates_idx = {b["date"]: i for i, b in enumerate(bars)}

        for r in flow:
            d = r["date"]
            if d not in chip or d not in phase:
                continue
            i = dates_idx.get(d)
            if i is None or i + max_hold >= len(bars):
                continue
            # 前瞻收益（定长行号口径）
            fwd = {}
            ok = True
            for h in holds:
                if i + h >= len(bars):
                    ok = False
                    break
                fwd[f"fwd{h}"] = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
            if not ok:
                continue

            # 资金流特征：以截面日为端点的近 5 日窗口
            k = flow_by_date[d]
            w = flow_map[code][max(0, k - 4):k + 1]
            flow5_amt = sum(x["main_pct"] or 0 for x in w)
            super5_amt = sum(x["super_pct"] or 0 for x in w)
            consec = 0
            for x in reversed(flow_map[code][:k + 1]):
                if (x["main_net"] or 0) > 0:
                    consec += 1
                else:
                    break
            # 净流入占流通市值%（float shares 缺失时为 None）
            flow5_mkt = None
            if fs:
                mkt = (r["close"] or 0) * fs
                if mkt > 0:
                    flow5_mkt = sum(x["main_net"] or 0 for x in w) / mkt * 100

            cm, pm = chip[d], phase[d]
            s = {
                "code": code, "date": d,
                "winner_ratio": cm["winner_ratio"],
                "cost_bias": cm["cost_bias"],
                "concentration": cm["concentration"],
                "price_pos": cm["price_pos"],
                "flow5_amt": flow5_amt,
                "super5_amt": super5_amt,
                "flow_consec": float(consec),
                "flow5_mkt": flow5_mkt,
                "phase": pm["phase"],
                "vol_ratio": pm["vol_ratio"],
                "ret_20": pm["ret_20"],
                **fwd,
            }
            # 主力组合信号：低位筹码密集 × 主力净流入
            s["combo_accum"] = (cm["price_pos"] < 0.35 and cm["concentration"] < 0.25
                                and flow5_amt > 0)
            s["combo_distrib"] = (cm["price_pos"] > 0.75 and cm["winner_ratio"] > 0.7
                                  and flow5_amt < 0)
            samples.append(s)
        if ci % 100 == 0:
            print(f"[chip+phase] {ci}/{len(prices)}")

    # 去市场超额（截面全池均值）
    by_date = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)
    for d, grp in by_date.items():
        for h in holds:
            mkt = sum(g[f"fwd{h}"] for g in grp) / len(grp)
            for g in grp:
                g[f"x_fwd{h}"] = g[f"fwd{h}"] - mkt
    print(f"[samples] 截面样本 {len(samples)} 条（{len(by_date)} 日）")
    return samples, sec_dates


FACTORS = [
    ("winner_ratio", "获利盘比例", "低=主力吸筹区"),
    ("cost_bias", "成本溢价%", "低=现价贴近全市场成本"),
    ("concentration", "筹码集中度", "低=筹码集中(控盘)"),
    ("price_pos", "筹码区间位置", "低=区间底部"),
    ("flow5_amt", "5日主力净流入占额%", "高=主力吸筹"),
    ("super5_amt", "5日超大单占额%", "高=机构行为"),
    ("flow_consec", "连续净流入天数", "高=持续吸筹"),
    ("flow5_mkt", "5日净流入占流通市值%", "高=强度大"),
]


def analyze(samples, holds):
    lines = []
    add = lines.append

    add("# 主力思维因子全池截面回测（PLAN_MAINFORCE 阶段 1）\n")
    add(f"> 运行：mainforce_factor_backtest.py ｜ 生成：{dt.datetime.now():%Y-%m-%d %H:%M}\n")
    add(f"> 截面样本 {len(samples)} 条（全池，非 Top50 高分池）\n")

    # 1. IC
    add("\n## 1. 因子 IC（Spearman，原始 / 去市场超额）\n")
    for h in holds:
        add(f"\n### 持有 {h} 日\n")
        add("| 因子 | 含义 | 原始 IC | 去超额 IC | n |")
        add("|---|---|---|---|---|")
        for key, label, note in FACTORS:
            ic_raw = spearman([s[key] for s in samples if s.get(key) is not None],
                              [s[f"fwd{h}"] for s in samples if s.get(key) is not None])
            ic_x = spearman([s[key] for s in samples if s.get(key) is not None],
                            [s[f"x_fwd{h}"] for s in samples if s.get(key) is not None])
            n = ic_raw["n"] if ic_raw else 0
            add(f"| {key} | {label}（{note}） | {ic_raw['rho'] if ic_raw else '-'} | "
                f"{ic_x['rho'] if ic_x else '-'} | {n} |")

    # 1.5 IC 分日稳定性（截面日逐日 IC —— 检验是否单一时段驱动）
    add("\n## 1.5 IC 分日稳定性（持有 5 日，去超额）\n")
    stability_keys = ["winner_ratio", "price_pos", "cost_bias",
                      "flow5_amt", "super5_amt"]
    add("| 截面日 | " + " | ".join(stability_keys) + " | n |")
    add("|---|" + "---|" * (len(stability_keys) + 1))
    by_date = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)
    for d in sorted(by_date):
        grp = by_date[d]
        vals = []
        for key in stability_keys:
            ic = spearman([s[key] for s in grp if s.get(key) is not None],
                          [s["x_fwd5"] for s in grp if s.get(key) is not None])
            vals.append(f"{ic['rho']:+.3f}" if ic else "-")
        add(f"| {d} | " + " | ".join(vals) + f" | {len(grp)} |")

    # 2. 分桶（fwd5）
    h0 = holds[0]
    add(f"\n## 2. 因子五分位 → 未来 {h0} 日收益（单调性检验）\n")
    for key, label, _ in FACTORS:
        add(f"\n### {key}（{label}）\n")
        add(fmt_bucket(bucket_table(samples, key, fwd=f"fwd{h0}")))
        add("")

    # 3. 阶段收益
    from app.mainforce.phases import PHASE_CN
    add("\n## 3. 主力阶段 → 未来收益\n")
    for h in holds:
        add(f"\n### 持有 {h} 日\n")
        add("| 阶段 | n | 胜率% | 均收益% | 去超额% |")
        add("|---|---|---|---|---|")
        by_phase = defaultdict(list)
        for s in samples:
            if s.get(f"fwd{h}") is not None:
                by_phase[s["phase"]].append(s)
        order = ["accumulation", "shakeout", "markup", "distribution",
                 "decline", "sideways"]
        for ph in order:
            grp = by_phase.get(ph)
            if not grp:
                continue
            rets = [s[f"fwd{h}"] for s in grp]
            xr = [s[f"x_fwd{h}"] for s in grp]
            add(f"| {ph}（{PHASE_CN[ph]}） | {len(grp)} | "
                f"{sum(1 for r in rets if r > 0) / len(rets) * 100:.1f} | "
                f"{sum(rets) / len(rets):.3f} | {sum(xr) / len(xr):.3f} |")

    # 4. 组合信号
    add(f"\n## 4. 主力组合信号\n")
    for name, cn, desc in [
        ("combo_accum", "低位筹码密集×主力净流入",
         "price_pos<0.35 & concentration<0.25 & 5日主力净流入>0"),
        ("combo_distrib", "高位高获利×主力流出",
         "price_pos>0.75 & winner_ratio>0.7 & 5日主力净流入<0"),
    ]:
        add(f"\n### {cn}（{desc}）\n")
        for h in holds:
            hit = [s for s in samples if s.get(name) and s.get(f"fwd{h}") is not None]
            rest = [s for s in samples if not s.get(name) and s.get(f"fwd{h}") is not None]
            if hit:
                rh = sum(s[f"fwd{h}"] for s in hit) / len(hit)
                rr = sum(s[f"fwd{h}"] for s in rest) / len(rest) if rest else 0
                wh = sum(1 for s in hit if s[f"fwd{h}"] > 0) / len(hit) * 100
                add(f"- 持有{h}日：命中 {len(hit)} 条 | 胜率 {wh:.1f}% | 均收益 {rh:.3f}%"
                    f" ｜ 其余 {len(rest)} 条均 {rr:.3f}% → 差 {rh - rr:+.3f}pt")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--step", type=int, default=5)
    args = ap.parse_args()

    samples, sec_dates = build_samples(args.hold, args.step)
    report = analyze(samples, args.hold)
    out_dir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir,
                       f"mainforce_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
