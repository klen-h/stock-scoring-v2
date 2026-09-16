#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】补算 market_regime_history 的缺失日（2026-09-16）
================================================================================

背景（缺日根因，见 scheduler.regime_cache_loop 2026-09-16 修复注释）：
  REGIME_CACHE_WINDOW 起点 15:40 早于 backtest_prices 当日回填（16:10+）→
  refresh_regime_cache 命中「同日幂等跳过」并返回**昨日的 state**（非空）→
  loop 守卫误判「数据就绪」而 mark_done → 当日行永远不会由 loop 写入；而
  09-11 之前日批又没有 task_market_regime → market_regime_history 大面积缺日
  （08-20/21、08-25/26/27、08-31、09-01、09-03、09-09）。缺日会让按市况分组
  的研究把样本丢进 unknown 黑洞。

★ 口径决策（重要）：
  · 原始态（offensive / neutral / defensive）
      → 由 sh000300 价格序列确定性算出（MA/ADX/ATR 与 hysteresis 均只用过去
        窗口、无前视）→ **可精确复原**。本脚本**默认只写这一层**。
  · neutral_bearish 细化 → **默认不做**，三条理由（缺一不可）：
      ① _nb_condition_raw 的判据「上涨家数宽度」「外围恐慌」依赖**当前时点**
         的实时数据（内存行情/外部接口），历史日不可复原；只有判据「沪深300
         近 2 个交易日累计下跌」是价格的确定性函数。
      ② nb 进/出需「连续 2 日确认」，依赖历史行的 bearish_refine_raw；而该列
         2026-09-13 才新增 → 09-13 之前的 8 行 raw **全为 NULL**，链条断裂、
         不可重建（实测：对已有行复算只有 6/11 一致，差异全在 neutral↔nb）。
      ③ 09-13 之前的 nb 行本身就是**单日口径**（两日确认当时尚未上线），与现行
         口径不同，不能作为 prev 依据。
    ⇒ 标 nb 需要不可复原的证据；标 neutral 是价格的确定性结论。由于 nb 与
      neutral **权重相同、战法准入相同**（差异仅在状态标签/仓位警示），保守
      标 neutral 不会误导任何决策。
      如确需细化口径：加 --refine（走 _apply_bearish_refine）。但对 09-13 前的
      历史行 prev_raw=NULL 会让「保持 nb」分支误触发，结果不可信，仅供对照。

用法：
  python scripts/backfill_regime_history.py                   # dry-run：自检 + 预览
  python scripts/backfill_regime_history.py --apply           # 写库（原始态）
  python scripts/backfill_regime_history.py --refine --apply  # 对照用（不推荐）
  python scripts/backfill_regime_history.py --since 2026-08-20
================================================================================
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

from app.backtest import market_regime as mr     # noqa: E402
from app.database import db                      # noqa: E402


def _index_closes_asc():
    """sh000300 收盘序列（升序）：[(date, close), ...]。"""
    rows = db.fetch("SELECT date, close FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    return [(r["date"], r["close"]) for r in rows if (r.get("close") or 0) > 0]


def _raw_nb_asof(date, st, dates_asc, close_by_date):
    """截止 date 的 nb 原始条件（仅判据③可复原，见模块 docstring）。"""
    if st.state != mr.NEUTRAL or st.ma_trend != "down":
        return False
    try:
        i = dates_asc.index(date)
    except ValueError:
        return False
    if i >= 2 and close_by_date[dates_asc[i]] < close_by_date[dates_asc[i - 2]]:
        return True
    return False


def _decide(date, st, dates_asc, close_by_date, do_refine):
    """(写入 state, regime_score, raw_nb③)。do_refine=False → 只写原始态。"""
    rn = _raw_nb_asof(date, st, dates_asc, close_by_date)
    if do_refine:
        ref, sc = mr._apply_bearish_refine(st.state, st.ma_trend, st.regime_score, rn,
                                           as_of_date=date)
        return ref, sc, rn
    return st.state, st.regime_score, rn


def _deraw(state):
    """库中 state 去 nb 化 → 与复算原始态对齐（自检用）。"""
    return mr.NEUTRAL if state == mr.NEUTRAL_BEARISH else state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="真写库（默认仅 dry-run 预览）")
    ap.add_argument("--since", default="2026-08-20",
                    help="只处理该日及之后的缺失日（默认 2026-08-20）")
    ap.add_argument("--refine", action="store_true",
                    help="走 nb 两日确认细化（默认不做；09-13 前 prev_raw=NULL，结果不可信）")
    args = ap.parse_args()

    mr._ensure_history_table()
    states = mr.load_regime_history(force=True)
    if not states:
        print("沪深300 历史不足，无法判定")
        return 1
    by_date = {s.date: s for s in states}

    pairs = _index_closes_asc()
    if not pairs:
        print("backtest_prices 无 sh000300 数据")
        return 1
    dates_asc = [d for d, _ in pairs]
    close_by_date = dict(pairs)

    existing = {r["date"]: r for r in (db.fetch(
        "SELECT date, state, bearish_refine_raw FROM market_regime_history") or [])}

    print("=" * 78)
    print("market_regime_history 缺失日补算")
    print("=" * 78)
    print(f"价格序列 {dates_asc[0]} ~ {dates_asc[-1]}（{len(dates_asc)} 日）"
          f"  |  库中已有 {len(existing)} 行  |  判定序列 {len(by_date)} 日")
    print(f"模式：{'nb 细化（对照用，不推荐）' if args.refine else '只写原始态（默认·保守）'}")

    # ── 自检：库中已有行的 state 去 nb 化后，应与价格复算的原始态一致 ──
    checked = same = 0
    diffs = []
    for d in sorted(existing):
        st = by_date.get(d)
        if st is None:
            checked += 1
            diffs.append((d, existing[d]["state"], "无原始态", "-"))
            continue
        checked += 1
        if _deraw(existing[d]["state"]) == st.state:
            same += 1
        else:
            diffs.append((d, existing[d]["state"], st.state, "-"))
    print(f"\n[自检] 原始态可复原性：库中已有行去 nb 化 == 价格复算原始态 "
          f"{same}/{checked}")
    for d, db_state, calc, _x in diffs:
        print(f"        {d}  库={db_state:<16s} 复算原始态={calc}")
    if same == checked:
        print("        → 原始态 100% 可复原（neutral_bearish 差异已按去 nb 化抵消）")

    raw_null = [d for d, r in sorted(existing.items())
                if r.get("bearish_refine_raw") is None]
    print(f"\n[历史 raw] 库中 bearish_refine_raw 为 NULL 的行：{len(raw_null)} 行"
          f"{'（' + '、'.join(raw_null) + '）' if raw_null else ''}")
    print("        → 该列 2026-09-13 才新增；此前 nb 为单日口径，两日确认链不可重建")

    # ── 缺失日 ──
    missing = sorted(d for d in by_date if d not in existing and d >= args.since)
    print(f"\n[缺失日] {len(missing)} 天：{missing}")
    if not missing:
        print("无缺失，退出")
        return 0

    print(f"\n{'日期':<12}{'原始state':<11}{'ma_trend':<9}{'adx':>7}"
          f"{'raw_nb③':>10}{'将写入':>13}")
    print("-" * 78)
    for d in missing:
        st = by_date[d]
        ref, _sc, rn = _decide(d, st, dates_asc, close_by_date, args.refine)
        print(f"{d:<12}{st.state:<11}{st.ma_trend:<9}{st.adx:>7.2f}"
              f"{str(rn):>10}{ref:>13}")

    if not args.apply:
        print("\n（dry-run，未写库；确认无误后加 --apply 生效）")
        return 0

    n = 0
    for d in missing:
        st = by_date[d]
        ref, sc, rn = _decide(d, st, dates_asc, close_by_date, args.refine)
        try:
            db.upsert("market_regime_history", {
                "date": d,
                "state": ref,
                "regime_score": sc,
                "adx": st.adx,
                "ma_trend": st.ma_trend,
                "volatility_regime": st.volatility_regime,
                "bearish_refine_raw": rn,
                "weights_json": json.dumps(mr.get_regime_weights(ref),
                                           ensure_ascii=False),
            }, conflict_columns=["date"])
            n += 1
            print(f"  写入 {d} {ref} (raw_nb③={rn})")
        except Exception as e:
            print(f"  写入失败 {d}: {e}")
    total = db.fetch_one("SELECT COUNT(*) AS c FROM market_regime_history") or {}
    print(f"\n补算完成：写入 {n}/{len(missing)} 行；market_regime_history "
          f"现有 {total.get('c')} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
