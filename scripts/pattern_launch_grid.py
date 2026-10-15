# -*- coding: utf-8 -*-
"""
================================================================================
「缩量调整→放量启动」形态 · **参数敏感性网格**（防过拟合核查）
================================================================================
目的：主工具全量给出口径修正后的 **超额 +2.09pp / P=0.000**（默认参数）。
      本脚本回答唯一问题：**这个正超额是稳定的，还是某个阈值的巧合？**

【为什么不能简单地跑 36 次主工具】
  单次全量（6058 只）≈ 12 分钟 ⇒ 3×4×3=36 个组合 = 7 小时。
  ⇒ 改为 **一次加载、一次窗口扫描、同时判定所有组合**：
     窗口类不变量（调整期均量比 / 调整幅度 / 高点关系 / 启动涨幅 / 放量倍数）
     与 launch_pct、vol_shrink **正交**，只需算一遍；每个 t 只多 2 次比较。
  实测总耗时 ≈ 单次全量的 1.1~1.5 倍（≈15 分钟），而非 36 倍。

【★ 一致性保证】`fast_scan()` 与主工具 `pattern_launch_check.scan_stock()` 的条件是
  纯合取 ⇒ 判定与检查顺序无关 ⇒ 结果必须**逐条相同**。`--verify` 用断言在真实数据上
  校验两者（默认组合，抽样 N 只）——不通过就退出，绝不带病出网格。

【网格定义（可改，改前留痕）】
  launch_pct ∈ {3, 5, 7} ｜ vol_shrink ∈ {0.4, 0.5, 0.6, 0.7} ｜ fwd ∈ {5, 10, 20}
  固定：pullback_days=20、vol_base_days=60、vol_surge=2.0、cooldown=10、
        pullback∈[-25%,+3%]、不创新高、突破、entry=t1_open、target=10%
  基准：**同资格**（≥pullback+base 根，2026-10-15 修正后的口径）

【输出】MD（每 fwd 一张 超额/胜率/样本 矩阵 + 稳定性汇总）+ 自包含 HTML 热力图

用法：
  python scripts/pattern_launch_grid.py --verify                 # 一致性断言（先跑这个）
  python scripts/pattern_launch_grid.py --limit 1500             # 小样本试跑
  python scripts/pattern_launch_grid.py                          # 全市场（≈15 分钟）
================================================================================
"""
import argparse
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pattern_launch_check as plc          # noqa: E402  （复用加载/评估/统计，口径零漂移）

LAUNCH_PCTS = (3.0, 5.0, 7.0)
VOL_SHRINKS = (0.4, 0.5, 0.6, 0.7)
FWDS = (5, 10, 20)
PULLBACK_DAYS = 20
VOL_BASE_DAYS = 60
VOL_SURGE = 2.0
COOLDOWN = 10
PULLBACK_MIN, PULLBACK_MAX = -0.25, 0.03
BELOW_HIGH_TOL = 0.0
TARGET = 0.10
ENTRY = "t1_open"
BOOT = 1000
COST = 0.003          # 双边成本（与 shrink_rebound_live 同源）；main 里可由 --cost 覆盖
GO_LINE = 0.01        # 判定线：落地（净）超额 ≥ +1.00pp


def base_cfg(fwd):
    return {"pullback_days": PULLBACK_DAYS, "vol_base_days": VOL_BASE_DAYS,
            "vol_shrink": 0.6, "pullback_min": PULLBACK_MIN, "pullback_max": PULLBACK_MAX,
            "below_high_tol": BELOW_HIGH_TOL, "launch_pct": 5.0, "vol_surge": VOL_SURGE,
            "breakout": True, "cooldown": COOLDOWN, "entry": ENTRY, "fwd_days": fwd,
            "target": TARGET, "seed": 20261014, "bootstrap": BOOT, "cost": COST}


# ================================================================
# 快速扫描：一次窗口计算 → 所有 (launch_pct, vol_shrink) 组合同时判定
# ================================================================
def fast_scan(bars, launch_pcts, vol_shrinks):
    """返回 {(launch_pct, vol_shrink): [signal, ...]}，signal 同主工具结构。"""
    n = len(bars)
    pd, vb = PULLBACK_DAYS, VOL_BASE_DAYS
    need = pd + vb
    combos = [(lp, vs) for lp in launch_pcts for vs in vol_shrinks]
    last = {k: -10 ** 9 for k in combos}
    out = {k: [] for k in combos}
    for t in range(need, n):
        # ── 窗口不变量（与组合正交 ⇒ 只算一次）──
        a = bars[t - pd:t]
        b = bars[t - pd - vb:t - pd]
        vol_a = sum(x["volume"] for x in a) / pd
        vol_b = sum(x["volume"] for x in b) / vb
        if vol_b <= 0 or vol_a <= 0:
            continue
        ratio = vol_a / vol_b
        if ratio > max(vol_shrinks):        # 所有组合都要求 ratio ≤ 某阈值
            continue
        ret_a = bars[t - 1]["close"] / bars[t - pd - 1]["close"] - 1
        if not (PULLBACK_MIN <= ret_a <= PULLBACK_MAX):
            continue
        high_b = max(x["high"] for x in b)
        high_a = max(x["high"] for x in a)
        if high_a > high_b * (1 + BELOW_HIGH_TOL):
            continue
        bar = bars[t]
        if bar.get("is_st") == 1 or bar.get("is_paused") == 1:
            continue
        if bar["close"] <= high_a:          # breakout（固定开启）
            continue
        pct_t = bar["pct"] if bar["pct"] is not None else \
            (bar["close"] / bars[t - 1]["close"] - 1) * 100
        if pct_t < min(launch_pcts):
            continue
        surge = bar["volume"] / vol_a
        if surge < VOL_SURGE:
            continue
        # ── 组合判定（每 t 只多 2 次比较）──
        for lp in launch_pcts:
            if pct_t < lp:
                continue                    # launch_pcts 已升序 ⇒ 可直接 break
            for vs in vol_shrinks:
                if ratio > vs:
                    continue
                key = (lp, vs)
                if t - last[key] < COOLDOWN:
                    continue
                last[key] = t
                out[key].append({"date": bar["date"], "idx": t, "vol_ratio": ratio,
                                 "pullback_ret": ret_a, "launch_pct": pct_t,
                                 "surge": surge})
    return out


def verify(n_codes=25):
    """断言 fast_scan 与主工具 scan_stock 在真实数据上逐条一致。"""
    codes = plc.all_codes_zz("2018-01-01")[:n_codes]
    bad = 0
    checked = 0
    for code in codes:
        bars = plc.load_bars_zz(code, "2018-01-01", None)
        if len(bars) < PULLBACK_DAYS + VOL_BASE_DAYS + 5:
            continue
        fast = fast_scan(bars, LAUNCH_PCTS, VOL_SHRINKS)
        for lp in LAUNCH_PCTS:
            for vs in VOL_SHRINKS:
                cfg = base_cfg(10)
                cfg["launch_pct"], cfg["vol_shrink"] = lp, vs
                ref = [s["date"] for s in plc.scan_stock(bars, cfg)]
                got = [s["date"] for s in fast[(lp, vs)]]
                checked += 1
                if ref != got:
                    bad += 1
                    print(f"  [MISMATCH] {code} lp={lp} vs={vs} "
                          f"ref={len(ref)} got={len(got)}")
                    if bad >= 3:
                        print("VERIFY FAIL")
                        return 1
    print(f"VERIFY PASS：{checked} 个 (股票×组合) 逐条一致"
          if not bad else "VERIFY FAIL")
    return 0 if not bad else 1


def svg_heatmap(grid, fwd, launch_pcts, vol_shrinks):
    """超额热力图（行=launch_pct，列=vol_shrink）。"""
    cw, ch, pad = 92, 46, 78
    w = pad + cw * len(vol_shrinks) + 20
    h = pad + ch * len(launch_pcts) + 46
    vals = [grid[(lp, vs)]["excess_net"] for lp in launch_pcts for vs in vol_shrinks
            if grid.get((lp, vs))]
    mx = max(1e-9, max(abs(v) for v in vals)) if vals else 1e-9
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
           f'style="background:#0b1220;font:11px sans-serif">']
    for ci, vs in enumerate(vol_shrinks):
        svg.append(f'<text x="{pad + ci*cw + cw/2}" y="{pad-12}" fill="#94a3b8" '
                   f'text-anchor="middle">vol_shrink={vs}</text>')
    for ri, lp in enumerate(launch_pcts):
        svg.append(f'<text x="{pad-10}" y="{pad + ri*ch + ch/2 + 4}" fill="#94a3b8" '
                   f'text-anchor="end">涨≥{lp}%</text>')
        for ci, vs in enumerate(vol_shrinks):
            c = grid.get((lp, vs))
            x, y = pad + ci * cw, pad + ri * ch
            if not c:
                svg.append(f'<rect x="{x+2}" y="{y+2}" width="{cw-4}" height="{ch-4}" '
                           f'fill="#1e293b"/>')
                continue
            e = c["excess_net"]        # ★ 落地（净）超额为准
            op = min(0.85, 0.12 + abs(e) / mx * 0.75)
            color = f"rgba(34,211,238,{op:.2f})" if e >= 0 else f"rgba(248,113,113,{op:.2f})"
            svg.append(f'<rect x="{x+2}" y="{y+2}" width="{cw-4}" height="{ch-4}" '
                       f'fill="{color}" rx="4"/>')
            svg.append(f'<text x="{x+cw/2}" y="{y+ch/2-2}" fill="#e2e8f0" '
                       f'text-anchor="middle">{e*100:+.2f}pp</text>')
            svg.append(f'<text x="{x+cw/2}" y="{y+ch/2+13}" fill="#cbd5e1" '
                       f'text-anchor="middle" font-size="10">n={c["n"]} '
                       f'胜{c["win"]*100:.0f}%</text>')
    svg.append("</svg>")
    return "".join(svg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="形态参数敏感性网格")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--source", choices=["zzshare", "db"], default="zzshare")
    ap.add_argument("--cost", type=float, default=0.003,
                    help="双边交易成本（默认 0.003；落地净收益 = 毛 − cost）")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--verify-n", type=int, default=25)
    args = ap.parse_args(argv)

    if not args.verify:
        plc._load_env()
    if args.verify:
        return verify(args.verify_n)
    global COST
    COST = args.cost

    codes = (plc.all_codes_zz(args.start, None) if args.source == "zzshare"
             else plc.all_codes_db())
    if args.limit:
        codes = codes[:args.limit]
    loader = plc.load_bars_zz if args.source == "zzshare" else plc.load_bars_db
    print(f"[grid] {len(codes)} 只 ｜ 源 {args.source} ｜ "
          f"{len(LAUNCH_PCTS)}×{len(VOL_SHRINKS)}×{len(FWDS)} = "
          f"{len(LAUNCH_PCTS)*len(VOL_SHRINKS)*len(FWDS)} 个组合")

    combos = [(lp, vs) for lp in LAUNCH_PCTS for vs in VOL_SHRINKS]
    sigs = {k: [] for k in combos}                    # 信号（含 fwd 前视由后续评估）
    rets = {(k, f): [] for k in combos for f in FWDS}
    rets_net = {(k, f): [] for k in combos for f in FWDS}    # ★ 落地（扣成本）
    dates = {(k, f): [] for k in combos for f in FWDS}
    base = {f: [] for f in FWDS}
    need = PULLBACK_DAYS + VOL_BASE_DAYS

    t0 = time.time()
    for i, code in enumerate(codes, 1):
        bars = loader(code, args.start, None)
        if len(bars) < need + max(FWDS) + 2:
            continue
        fs = fast_scan(bars, LAUNCH_PCTS, VOL_SHRINKS)
        # 基准（每 fwd 一份；同资格，与主工具同口径）
        for f in FWDS:
            base[f].extend(plc.unconditional_returns(
                bars, f, ENTRY, limit=4, min_idx=need))
        for k, sl in fs.items():
            for s in sl:
                sigs[k].append(s)
            for s in sl:
                for f in FWDS:
                    cfg = base_cfg(f)
                    cfg["launch_pct"], cfg["vol_shrink"] = k
                    ev = plc.evaluate(bars, s, cfg)
                    if ev:
                        rets[(k, f)].append(ev["ret"])
                        rets_net[(k, f)].append(ev.get("ret_net", ev["ret"]))
                        dates[(k, f)].append(ev["date"])
        if i % 500 == 0:
            print(f"  [progress] {i}/{len(codes)} ｜ {time.time()-t0:.0f}s", flush=True)

    # ── 汇总 ──
    grid = {}
    detail = {}
    for k in combos:
        for f in FWDS:
            r, rn, d, b = rets[(k, f)], rets_net[(k, f)], dates[(k, f)], base[f]
            if not r or not b:
                continue
            st = plc.summarize(r, d, {"seed": 20261014})
            bs = plc.summarize(b, list(range(len(b))), {})
            pv, obs = plc.clustered_pvalue(r, d, b, base_cfg(f))
            obs_net = statistics.mean(rn) - bs["avg"]        # ★ 落地超额（净）
            cell = {"n": st["n"], "clusters": st["clusters"], "win": st["win"],
                    "avg": st["avg"], "median": st["median"], "base": bs["avg"],
                    "excess": obs, "p": pv, "excess_net": obs_net,
                    "avg_net": statistics.mean(rn)}
            detail[(k, f)] = cell
            if f == 10:
                grid[k] = cell

    # 稳定性汇总（以 fwd=10 为主；★ 以**落地净超额**为准）
    exc = [c["excess_net"] for c in grid.values()]
    exc_g = [c["excess"] for c in grid.values()]
    pos = sum(1 for c in grid.values() if c["excess_net"] > 0)
    sig = sum(1 for c in grid.values() if c["p"] < 0.05 and c["excess_net"] > 0)
    go = sum(1 for c in grid.values() if c["excess_net"] >= GO_LINE)
    r = plc.REJECT_STATS
    print(f"\n[落地口径 cost={COST*100:.2f}%][fwd=10] 组合数 {len(exc)} ｜ "
          f"净超额>0 {pos}/{len(exc)} ｜ 显著正 {sig}/{len(exc)} ｜ "
          f"≥+1.00pp 过关 {go}/{len(exc)}")
    if exc:
        print(f"  净超额 min/中位/max = {min(exc)*100:+.2f} / "
              f"{statistics.median(exc)*100:+.2f} / {max(exc)*100:+.2f} pp"
              f"  （毛 {min(exc_g)*100:+.2f} ~ {max(exc_g)*100:+.2f}）")
    print(f"  可执行性剔除记账：候选 {r['limit_open'] + r['paused'] + r['no_fwd'] + r['ok']} ｜ "
          f"T+1 一字板买不进 {r['limit_open']} ｜ 停牌 {r['paused']} ｜ "
          f"无前视 {r['no_fwd']} ｜ 有效 {r['ok']}")

    # ── 输出 ──
    os.makedirs(plc.REPORT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    md = plc.REPORT_DIR / f"pattern_launch_grid_{stamp}.md"
    L = [f"# 形态参数敏感性网格（{len(codes)} 只 · {args.source} · 起 {args.start}）\n",
         f"> 生成 {time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 固定：pullback={PULLBACK_DAYS}/"
         f"base={VOL_BASE_DAYS}/surge={VOL_SURGE}/cooldown={COOLDOWN}/entry={ENTRY}/"
         f"target={TARGET*100:.0f}% ｜ 基准=同资格\n"]
    for f in FWDS:
        L.append(f"\n## fwd = T+{f}\n")
        L.append("| launch_pct \\ vol_shrink | " +
                 " | ".join(str(v) for v in VOL_SHRINKS) + " |")
        L.append("|---|" + "---|" * len(VOL_SHRINKS))
        for lp in LAUNCH_PCTS:
            cells = []
            for vs in VOL_SHRINKS:
                c = detail.get((lp, vs, f)) or detail.get(((lp, vs), f))
                cells.append(f"**净{c['excess_net']*100:+.2f}pp** / 毛{c['excess']*100:+.2f}pp"
                             f" (n={c['n']}, 胜{c['win']*100:.0f}%, P={c['p']:.3f})"
                             f"{' ✅' if c['excess_net'] >= GO_LINE else ''}" if c else "—")
            L.append(f"| **{lp}%** | " + " | ".join(cells) + " |")
    L.append(f"\n## 稳定性汇总（fwd=10 · 落地口径 cost={COST*100:.2f}%）\n")
    L.append(f"- 组合数 **{len(exc)}** ｜ 净超额>0 **{pos}/{len(exc)}** ｜ "
             f"显著正（P<0.05）**{sig}/{len(exc)}** ｜ "
             f"**≥ +1.00pp 过关（对齐 shrink_rebound_live 判定线）{go}/{len(exc)}**")
    if exc:
        L.append(f"- **净**超额 min / 中位 / max = **{min(exc)*100:+.2f} / "
                 f"{statistics.median(exc)*100:+.2f} / {max(exc)*100:+.2f} pp**"
                 f"（毛 {min(exc_g)*100:+.2f} ~ {max(exc_g)*100:+.2f}）")
    r = plc.REJECT_STATS
    L.append(f"\n## 可执行性剔除记账\n")
    L.append(f"- 候选 {r['limit_open'] + r['paused'] + r['no_fwd'] + r['ok']} ｜ "
             f"T+1 一字板买不进 **{r['limit_open']}** ｜ 停牌 **{r['paused']}** ｜ "
             f"无前视 **{r['no_fwd']}** ｜ **有效 {r['ok']}**")
    L.append("\n## 读法\n")
    L.append("- 若**多数格子**正超额且量级相近 ⇒ 不是阈值巧合（稳健）；"
             "若只有个别格子为正、邻格翻负 ⇒ 阈值敏感性高，**不可当信号用**。")
    L.append(f"- 口径：**净超额 = 毛超额 − {COST*100:.2f}%（双边成本）**；"
             f"判定线 +1.00pp 对齐 `shrink_rebound_live`。")
    L.append("- 仍为描述性研究：不构成预登记判据，亦未计冲击成本/容量约束。\n")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    html = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>形态参数网格</title>",
            "<style>body{background:#0a0f1c;color:#cbd5e1;font:14px/1.6 sans-serif;padding:24px}"
            "h1{font-size:20px;color:#e2e8f0}h2{font-size:15px;color:#94a3b8;margin:26px 0 8px}"
            "svg{border:1px solid #1e293b;border-radius:8px;display:block}"
            ".note{color:#64748b;font-size:11px}</style></head><body>",
            f"<h1>缩量调整→放量启动 · 参数网格（{len(codes)} 只，落地口径 扣成本 "
            f"{COST*100:.2f}%）</h1>",
            "<div class='note'>格子=**净超额**（已扣双边成本；青为正/红为负），下方 n 与胜率；"
            "行=启动涨幅阈值，列=缩量阈值。图内为 T+10。</div>",
            "<h2>T+10 净超额热力图</h2>",
            svg_heatmap(grid, 10, LAUNCH_PCTS, VOL_SHRINKS),
            "</body></html>"]
    hp = md.with_suffix(".html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write("".join(html))
    print(f"[report] {md}\n[report] {hp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
