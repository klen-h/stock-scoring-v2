#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】flow5 倒U曲线 × 价格位置 分层回测（评分倒U改进 P2）
================================================================================
背景（评分倒U改进_极端流入个案001368_20260914.md §P2）：
  倒U曲线（09-13 上线）把 >+20% 的极端主力净流入一律打 0 分——001368（低位
  极端流入强势股，RSI 80 / 主力 markup 段）被误伤，暴露了曲线没有价格位置
  维度。本脚本验证："极端流入"的负收益是否集中在【高位】（price_pos>0.75），
  【低位】极端流入是否其实不差——若是，倒U应加位置维度。

判定标准（预先定死，防止事后挑格子——见上述文档 §P2）：
  主判定：flow5>+10 且 price_pos<0.5 格，5 日去超额均值 > +0.5% 且 n≥100
    → 支持"低位极端流入不惩罚"，实现 FLOW5_POS_WEIGHTED 位置加权曲线；
  副判定：flow5>+5 且 price_pos<0.5 格同标准（放宽版）；
  两者都不满足 → 维持现状（P1 标签区分足矣），结论归档。

方法：
  - 复用 mainforce_factor_backtest.build_samples（与 flow5_curve_backtest 同源：
    样本含 fwd5/fwd10、x_fwd5/x_fwd10（去超额）、price_pos、flow5_amt）
  - flow5 分桶 {<0, 0~+5, +5~+10, +10~+20, >+20} × price_pos 分桶 {<0.5, 0.5~0.75, >0.75}
  - 每格：n / fwd 均值 / 去超额均值 / 去超额胜率
用法：python scripts/flow5_pos_backtest.py [--hold 5 10] [--step 5] [--refresh]
"""

import argparse
import datetime as dt
import os
import sys

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

from mainforce_factor_backtest import build_samples  # noqa: E402

F5_BUCKETS = [(None, 0, "<0"), (0, 5, "0~+5"), (5, 10, "+5~+10"),
              (10, 20, "+10~+20"), (20, None, ">+20")]
PP_BUCKETS = [(None, 0.5, "<0.5"), (0.5, 0.75, "0.5~0.75"), (0.75, None, ">0.75")]


def _bucket(v, lo, hi):
    if v is None:
        return False
    if lo is not None and v <= lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def _cell_stats(rows, h):
    rets = [s[f"fwd{h}"] for s in rows]
    xrets = [s[f"x_fwd{h}"] for s in rows if s.get(f"x_fwd{h}") is not None]
    if not rets:
        return None
    win = sum(1 for r in xrets if r > 0) / len(xrets) * 100 if xrets else None
    return {"n": len(rets),
            "fwd": sum(rets) / len(rets),
            "x_fwd": sum(xrets) / len(xrets) if xrets else None,
            "x_win": win}


def matrix_section(samples, holds):
    both = [s for s in samples
            if s.get("flow5_amt") is not None and s.get("price_pos") is not None]
    lines = []
    add = lines.append
    add(f"## 一、样本覆盖\n")
    add(f"- 全样本 {len(samples)} ｜ flow5 非空 {sum(1 for s in samples if s.get('flow5_amt') is not None)}"
        f" ｜ flow5+price_pos 双非空 **{len(both)}**"
        f"（price_pos 覆盖率 {len(both) / max(1, sum(1 for s in samples if s.get('flow5_amt') is not None)):.0%}）\n")

    for h in holds:
        add(f"\n## 二、flow5 × price_pos 分层（持有 {h} 日）\n")
        add("| flow5 \\ price_pos | " + " | ".join(b[2] for b in PP_BUCKETS) + " |")
        add("|---|---|---|---|")
        for lo5, hi5, label5 in F5_BUCKETS:
            cells = []
            for lo_p, hi_p, _ in PP_BUCKETS:
                rows = [s for s in both
                        if _bucket(s["flow5_amt"], lo5, hi5)
                        and _bucket(s["price_pos"], lo_p, hi_p)]
                st = _cell_stats(rows, h)
                cells.append((f"n={st['n']} 均值{st['fwd']:.2f}% 去超额{st['x_fwd']:.2f}%"
                              f" 胜率{st['x_win']:.0f}%") if st and st["x_fwd"] is not None
                             else (f"n={st['n']}（去超额缺失）" if st else "—"))
            add(f"| **{label5}** | " + " | ".join(cells) + " |")

        # 关键对照：同一 flow5 桶在"高位 vs 低位"的去超额差（倒U不加位置维度的代价）
        add(f"\n**高位 − 低位去超额差（正数=高位更差，支持加位置维度）**\n")
        for lo5, hi5, label5 in F5_BUCKETS:
            lo_rows = [s for s in both if _bucket(s["flow5_amt"], lo5, hi5)
                       and _bucket(s["price_pos"], None, 0.5) and s.get(f"x_fwd{h}") is not None]
            hi_rows = [s for s in both if _bucket(s["flow5_amt"], lo5, hi5)
                       and _bucket(s["price_pos"], 0.75, None) and s.get(f"x_fwd{h}") is not None]
            if lo_rows and hi_rows:
                diff = (sum(s[f"x_fwd{h}"] for s in hi_rows) / len(hi_rows)
                        - sum(s[f"x_fwd{h}"] for s in lo_rows) / len(lo_rows))
                add(f"- {label5}: 高位 n={len(hi_rows)} 低位 n={len(lo_rows)} → 差 **{diff:+.2f}pt**")
    return "\n".join(lines), both


def verdict(both):
    lines = []
    add = lines.append
    add("\n## 三、判定（标准预先定死，见文档 §P2）\n")
    main_rows = [s for s in both if s["flow5_amt"] > 10 and s["price_pos"] < 0.5
                 and s.get("x_fwd5") is not None]
    sub_rows = [s for s in both if s["flow5_amt"] > 5 and s["price_pos"] < 0.5
                and s.get("x_fwd5") is not None]
    main_ok = False
    sub_ok = False
    if main_rows:
        xm = sum(s["x_fwd5"] for s in main_rows) / len(main_rows)
        add(f"- 主判定（flow5>+10 & pp<0.5）：n={len(main_rows)}，"
            f"5日去超额均值 **{xm:+.3f}%**（标准 >+0.5% 且 n≥100）"
            f"→ {'✅ 支持' if (xm > 0.5 and len(main_rows) >= 100) else '❌ 不支持'}")
        main_ok = xm > 0.5 and len(main_rows) >= 100
    else:
        add("- 主判定（flow5>+10 & pp<0.5）：**无样本** → ❌ 不支持")
    if sub_rows:
        xs = sum(s["x_fwd5"] for s in sub_rows) / len(sub_rows)
        add(f"- 副判定（flow5>+5 & pp<0.5）：n={len(sub_rows)}，"
            f"5日去超额均值 **{xs:+.3f}%**（标准 >+0.5% 且 n≥100）"
            f"→ {'✅ 支持' if (xs > 0.5 and len(sub_rows) >= 100) else '❌ 不支持'}")
        sub_ok = xs > 0.5 and len(sub_rows) >= 100
    else:
        add("- 副判定（flow5>+5 & pp<0.5）：**无样本** → ❌ 不支持")
    add("\n## 结论\n")
    if main_ok or sub_ok:
        add("- **支持加位置维度**：实现 `FLOW5_POS_WEIGHTED` 位置加权曲线"
            "（低位极端流入不惩罚/轻降），前后端同步 + parity 校验，带开关上线。")
    else:
        add("- **维持现状**：低位极端流入无显著正超额，倒U不加位置维度"
            "（P1 标签区分足矣），本结论归档。001368 类个股为体系主动放弃型。")
    return "\n".join(lines), (main_ok or sub_ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    samples, _sec_dates, _anom = build_samples(args.hold, args.step, refresh=args.refresh)
    matrix_lines, both = matrix_section(samples, args.hold)
    verdict_lines, supported = verdict(both)

    lines = []
    add = lines.append
    add("# flow5 倒U × 价格位置 分层回测（评分倒U改进 P2）\n")
    add(f"> 运行：flow5_pos_backtest.py ｜ 生成：{dt.datetime.now():%Y-%m-%d %H:%M}"
        f" ｜ 截面样本 {len(samples)} 条\n")
    add(matrix_lines)
    add(verdict_lines)

    out_dir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"flow5_pos_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] {out}")
    print(f"[verdict] 位置维度支持: {supported}")


if __name__ == "__main__":
    main()
