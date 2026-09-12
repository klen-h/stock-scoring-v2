#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法推送白名单口径复核（离线跑，用于回答"该不该推、按什么推"）
================================================================================

背景（2026-09-12）：
  推送白名单 2026-09-05 起改为动态计算 —— 从 `strategy_results` 重放撮合
  （T+1 开盘成交、涨停一字剔除、主力闸门 + 退出策略 v2，与回测管线同口径）。
  原判据是「样本 ≥30 且 胜率 ≥55%」。实测该体系胜率天花板就在 50% 附近
  （主力闸门自证：胜率 48.1→50.3%，但均收益 +0.07→+0.44%、盈亏比 1.05→1.36），
  于是白名单算成**空集** → 战法信号推送静默。本脚本把口径摊开给决策用。

它做三件事：
  1. 列出每个战法的 n / 胜率 / 均收益 / 盈亏比 / 中位收益（同一套回放口径）
  2. 分别按「胜率判据」和「期望判据」算出白名单，直观对比会推什么
  3. 顺带核对静态兜底名单、阈值配置、样本日期范围

用法：
  python scripts/strategy_whitelist_review.py            # 本地跑（K线走本机数据包，见下）
  DATA_SOURCE=local python scripts/strategy_whitelist_review.py

⚠️ 成本：脚本默认把 DATA_SOURCE 设为 local —— K 线读本机 `backend/data/pack/backend-pack.db`
   （零 Supabase 行情流量），只回源 `strategy_results` / `mainforce_state` 两张表。
   跑之前确保本机有数据包（`python scripts/sync_local.py` 或等 Render 下过一份）。
================================================================================
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

os.environ.setdefault("DATA_SOURCE", "local")   # ★ K 线走本机包，不回源

from app.database import db                          # noqa: E402
from app.strategies import recommendation as rec     # noqa: E402


def _fmt(v, suffix="", nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}" if isinstance(v, (int, float)) else str(v)


def main():
    r = db.fetch_one("SELECT COUNT(*) AS n, MIN(scan_date) AS a, MAX(scan_date) AS b, "
                     "SUM(length(results_json)) AS b2 FROM strategy_results WHERE count > 0")
    print("=== 样本范围（strategy_results）===")
    print(f"  {r['n']} 天扫描结果 / {r['a']} ~ {r['b']} / "
          f"正文 {int(r['b2'] or 0) / 1048576:.1f} MB（未压缩）")

    print("\n=== 回放口径（与生产同一函数）===")
    out = rec._recompute_whitelist()
    stats = out.get("stats") or {}
    if not stats:
        print("  [警告] 无任何可评估信号（信号为空 / 全被主力闸门过滤 / K线不可用）")
        return 0

    order = sorted(stats.items(), key=lambda kv: -(kv[1].get("avg_ret") or -999))
    print(f"  {'战法':<26}{'中文':<8}{'n':>5}{'胜率':>8}{'均收益':>9}"
          f"{'盈亏比':>8}{'中位':>7}  全期 近轨 期望 胜率")
    for name, v in order:
        zh = rec.STRATEGY_ZH.get(name, "")
        pf = v.get("profit_factor")
        pf_s = "inf" if pf == 999.0 else _fmt(pf)
        pass_exp = ((v.get("n") or 0) >= rec.WHITELIST_MIN_SAMPLES
                    and (v.get("avg_ret") or 0) > rec.WHITELIST_MIN_AVG_RET
                    and (pf is None or pf >= rec.WHITELIST_MIN_PROFIT_FACTOR))
        pass_win = ((v.get("n") or 0) >= rec.WHITELIST_MIN_SAMPLES
                    and (v.get("win_rate") or 0) >= rec.WHITELIST_MIN_WIN_RATE)
        all_t = "OK" if v.get("pass_all_time") else "--"
        if v.get("pass_recent"):
            rec_t = "OK"
        elif v.get("recent_insufficient"):
            rec_t = "n少"
        else:
            rec_t = "--"
        print(f"  {name:<26}{zh:<8}{v.get('n'):>5}{_fmt(v.get('win_rate'),'%',1):>8}"
              f"{_fmt(v.get('avg_ret'),'%'):>9}{pf_s:>8}"
              f"{_fmt(v.get('median_ret'),'%'):>7}   "
              f"{all_t:>4} {rec_t:>4} {'OK' if pass_exp else '--':>4} "
              f"{'OK' if pass_win else '--':>4}")

    print(f"\n=== 两套判据的结果 ===")
    crit_txt = (f"均收益 >{rec.WHITELIST_MIN_AVG_RET}% 且 盈亏比 "
                f"≥{rec.WHITELIST_MIN_PROFIT_FACTOR}"
                if rec.WHITELIST_CRITERION == "expectancy"
                else f"胜率 ≥{rec.WHITELIST_MIN_WIN_RATE}%")
    print(f"  当前判据 = {rec.WHITELIST_CRITERION}"
          f"（{crit_txt}，样本 ≥{rec.WHITELIST_MIN_SAMPLES}）"
          f" → 白名单 {out.get('list')}")
    exp = [k for k, v in stats.items()
           if (v.get("n") or 0) >= rec.WHITELIST_MIN_SAMPLES
           and (v.get("avg_ret") or 0) > rec.WHITELIST_MIN_AVG_RET
           and (v.get("profit_factor") is None
                or v["profit_factor"] >= rec.WHITELIST_MIN_PROFIT_FACTOR)]
    print(f"  期望判据（均收益 >{rec.WHITELIST_MIN_AVG_RET}% 且 盈亏比 "
          f"≥{rec.WHITELIST_MIN_PROFIT_FACTOR}）→ 白名单 {exp}")

    # ★ 2026-09-13 §3b#6：双轨滚动窗口 + 半衰期监控（全历史 replay 会被牛市旧战绩撑住）
    print("\n=== 滚动窗口 + 半衰期监控（PLAN §3b#6）===")
    rolling_txt = ("启用" if out.get("rolling_active")
                   else f"未启用（交易日 < {rec.WHITELIST_ROLLING_DAYS}，降级为全期单轨）")
    print(f"  双轨滚动窗口 = {rolling_txt}"
          f"（开关 WHITELIST_ROLLING_ENABLED={rec.WHITELIST_ROLLING_ENABLED}）")
    print(f"  半衰期监控（后半段胜率 < 前半段 {rec.WHITELIST_HALF_LIFE_RATIO:.0%}）：")
    alerts = out.get("alerts") or []
    if alerts:
        for a in alerts:
            print(f"    [警告] {a}")
    else:
        print("    无告警")

    if not out.get("list"):
        print("  [警告] 当前白名单为空 → **战法信号不会推送企微**（功能静默）；"
              "要恢复推送可设 WHITELIST_CRITERION=expectancy")
    print(f"  静态兜底名单 = {rec.PUSH_STRATEGY_WHITELIST}"
          f"（仅动态计算异常时使用）")
    print(f"  静态背书 STRATEGY_STATS = {rec.STRATEGY_STATS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
