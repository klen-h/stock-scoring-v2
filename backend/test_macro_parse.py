#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宏观价格/FOMC/金十日历 · 纯函数单测（offline，不连库/不发请求）
================================================================================
覆盖今天新增的四个模块里**最容易错、又完全没有测试**的解析逻辑：

  `fomc_statement`
    · `parse_content`  —— 必须剥掉 `ctrl-tooltip` 控件文字；highlight / line-through 分离
    · `parse_decision` —— **双"至"连用**（「上调25个基点，至3.75%至4%」）
    · `_month_of`      —— 「2026年10月」→「2026-10」，`contents` 顺序不统一时靠它定本期
    · `judge_stage`    —— 分期优先级（confirm > pivot > transition > now）+ 关键词只给候选

  `jin10_calendar`
    · `period_iso`     —— 统计月提取（退到 full_time_period / year+time_period）
    · `pick_value`     —— **revised 优先于 actual**（可复算的前提）

  `macro_inflation`
    · `evaluate_series` —— 连续回落 + 趋势指向的判定与不足数据的区分

  `macro_price`
    · `band_cpi` / `band_ppi` / `scissors` / `trend`

★ 为什么单列这个文件：这些函数**没有网络、没有库依赖**，本该最好测；
  但恰恰是今天踩坑最多的地方（`contents` 顺序把 9 月加息吞了、双"至"让 decision 变 None）。
  离线单测是唯一能防止它们再次回归的手段。

运行：cd backend && python test_macro_parse.py
================================================================================
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.fomc_statement import (  # noqa: E402
    _month_of, judge_stage, parse_content, parse_decision,
)
from app.jin10_calendar import period_iso, pick_value  # noqa: E402
from app.macro_inflation import evaluate_series  # noqa: E402
from app.macro_price import (  # noqa: E402
    SCISSORS_BIG, band_cpi, band_ppi, scissors, trend,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {label}{(' | ' + detail) if detail else ''}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}{(' | ' + detail) if detail else ''}")


def main():
    print("=" * 78)
    print("宏观解析 · 纯函数单测")
    print("=" * 78)

    # ────────────────────────────── parse_content
    print("\n[1] parse_content：剥 tooltip / 分离 highlight 与 line-through")
    html = ('<div>以<i class="highlight">12票赞成、0票反对</i>通过'
            '<span class="ctrl-tooltip" style="left:1px">取消高亮</span></div>'
            '<div><i class="line-through">9票赞成</i></div>')
    p = parse_content(html)
    check("plain 不含「取消高亮」", "取消高亮" not in p["plain"], p["plain"][:40])
    check("plain 含正文「12票赞成」", "12票赞成" in p["plain"])
    check("highlights == ['12票赞成、0票反对']",
          p["highlights"] == ["12票赞成、0票反对"], str(p["highlights"]))
    check("struck == ['9票赞成']", p["struck"] == ["9票赞成"], str(p["struck"]))

    check("空输入不崩", parse_content("")["plain"] == "")

    # ────────────────────────────── parse_decision
    print("\n[2] parse_decision：双「至」连用（今天踩过的坑）")
    d = parse_decision("委员会决定将联邦基金利率目标区间上调25个基点，至3.75%至4%。")
    check("decision == '上调25bp 至 3.75%–4%'",
          d["decision"] == "上调25bp 至 3.75%–4%", str(d["decision"]))

    d2 = parse_decision("决定将联邦基金利率的目标区间维持在3.75%至4%之间。")
    check("维持口径识别", d2["decision"] == "维持 3.75%–4%", str(d2["decision"]))

    d3 = parse_decision("以10票赞成、2票反对的结果通过以下声明。")
    check("票数 == '10票赞成/2票反对'",
          d3["vote"] == "10票赞成/2票反对", str(d3["vote"]))

    d4 = parse_decision("这段文字没有任何利率或票数信息。")
    check("无匹配时 decision 为 None（不猜）", d4["decision"] is None)
    check("无匹配时 vote 为 None", d4["vote"] is None)

    # ────────────────────────────── _month_of
    print("\n[3] _month_of：title → 月份（contents 顺序不统一时靠它定本期）")
    check("「2026年10月」→ 2026-10", _month_of("2026年10月") == "2026-10")
    check("「2026年 9月」→ 2026-09", _month_of("2026年 9月") == "2026-09")
    check("「2025年12月」→ 2025-12", _month_of("2025年12月") == "2025-12")
    check("非法输入 → None", _month_of("garbage") is None)
    check("空 → None", _month_of("") is None)
    check("月份越界 → None", _month_of("2026年13月") is None)

    # ────────────────────────────── judge_stage
    print("\n[4] judge_stage：分期优先级 + 只给候选")
    s = judge_stage("随着更多信息的积累，委员会将能够确定合适的政策立场。", [])
    check("transition 命中", s["stage_candidate"] == "stage_transition",
          str(s["stage_candidate"]))
    check("need_manual 恒 True", s["need_manual"] is True)
    check("confidence == keyword-only", s["confidence"] == "keyword-only")

    s2 = judge_stage("委员会认为政策处于良好位置，可以保持耐心。", [])
    check("pivot 命中", s2["stage_candidate"] == "stage_pivot", str(s2["stage_candidate"]))

    # 同时含 pivot 与 confirm ⇒ confirm 优先（更靠近转向）
    s3 = judge_stage("我们可以保持耐心，并开始讨论何时放松。", [])
    check("confirm 优先于 pivot", s3["stage_candidate"] == "stage_confirm",
          str(s3["stage_candidate"]))

    # ★ 删除项含"加息语义"⇒ transition。**注意原文写的是「上调25个基点」不是"加息"**
    #   —— 这正是单测第一次跑时抓出来的 bug（只找"加息"两字永远匹配不到）。
    s4 = judge_stage("委员会将维持当前利率。",
                     ["联邦基金利率目标区间上调25个基点"],
                     decision="维持 3.75%–4%")
    check("struck 含「上调N个基点」⇒ transition",
          s4["stage_candidate"] == "stage_transition", str(s4["stage_candidate"]))
    check("removed_hike_wording == True", s4.get("removed_hike_wording") is True)

    # ★★ 但**本期本身就是加息月**时不算（实测 2026-09 的 struck 里就有"上调25个基点"，
    #   而 9 月恰恰是加息月，判成"转向信号"是反的）。
    s4b = judge_stage("委员会决定上调25个基点，至3.75%至4%。",
                      ["联邦基金利率目标区间上调25个基点"],
                      decision="上调25bp 至 3.75%–4%")
    check("加息月不因 struck 判 transition",
          s4b["stage_candidate"] is None, str(s4b["stage_candidate"]))
    check("加息月 removed_hike_wording == False",
          s4b.get("removed_hike_wording") is False)

    # 中文字面"加息"也要能识别
    s4c = judge_stage("委员会将维持当前利率。", ["委员会将考虑加息"],
                      decision="维持 3.75%–4%")
    check("struck 含字面「加息」⇒ transition",
          s4c["stage_candidate"] == "stage_transition", str(s4c["stage_candidate"]))

    # 无任何关键词 ⇒ None，但**不是**"没变化"
    s5 = judge_stage("经济活动正以稳健的速度扩张。", [])
    check("无关键词 ⇒ None", s5["stage_candidate"] is None)
    check("无关键词仍 need_manual", s5["need_manual"] is True)

    # ────────────────────────────── period_iso / pick_value
    print("\n[5] jin10_calendar：统计月提取 + revised 优先")
    check("xx 提取", period_iso({"xx": "2025年 9月"}) == "2025-09-01")
    check("退到 year+time_period",
          period_iso({"year": "2025年", "time_period": "9月"}) == "2025-09-01")
    check("退到 full_time_period",
          period_iso({"full_time_period": "2025年 12月"}) == "2025-12-01")
    check("空 dict → None", period_iso({}) is None)

    check("revised 优先于 actual", pick_value({"revised": "4.5", "actual": "4.4"}) == 4.5)
    check("revised 为 None 时用 actual",
          pick_value({"revised": None, "actual": "4.4"}) == 4.4)
    check("revised 为 '-' 时用 actual",
          pick_value({"revised": "-", "actual": "4.4"}) == 4.4)
    check("非数值 → None", pick_value({"actual": "abc"}) is None)
    check("全空 → None", pick_value({}) is None)

    # ────────────────────────────── evaluate_series
    print("\n[6] macro_inflation.evaluate_series：连续回落 + 趋势指向")
    # 4 期连降且落到目标下方 ⇒ 达成
    r = evaluate_series([3.6, 3.3, 3.0, 2.8])
    check("连降 3 月且已达标 ⇒ ok", r["ok"] is True, r.get("detail", "")[:60])
    check("falls == 3", r["falls"] == 3, str(r.get("falls")))

    # 最近一期回升 ⇒ falls 归零（不能因为 3 月净降就判"在改善"）
    r2 = evaluate_series([3.3, 3.0, 3.0, 3.1])
    check("最近一期回升 ⇒ falls == 0", r2["falls"] == 0, str(r2.get("falls")))
    check("回升 ⇒ 未达成", r2["ok"] is False)

    # 连降但离目标很远（外推 2 月也到不了）⇒ 未达成
    r3 = evaluate_series([4.0, 3.9, 3.8, 3.7])
    check("连降但外推仍 >2.8 ⇒ 未达成", r3["ok"] is False, str(r3.get("proj")))

    # 数据不足
    r4 = evaluate_series([3.0, 3.1])
    check("不足 4 期 ⇒ insufficient", r4["status"] == "insufficient")
    check("不足时 ok 为 False 而非 None", r4["ok"] is False)

    # ────────────────────────────── macro_price
    print("\n[7] macro_price：档位 / 剪刀差 / 走势")
    check("CPI 0.9 → 低通胀", band_cpi(0.9) == "低通胀")
    check("CPI 0.0 → 低通胀（左闭）", band_cpi(0.0) == "低通胀")
    check("CPI -0.1 → 通缩", band_cpi(-0.1) == "通缩")
    check("CPI None → 无数据", band_cpi(None) == "无数据")
    check("PPI -2.1 → 工业品通缩", band_ppi(-2.1) == "工业品通缩")
    check("PPI 5.0 → 高位", band_ppi(5.0) == "高位")

    check("剪刀差 5.0-0.9 = 4.1", abs(scissors(5.0, 0.9) - 4.1) < 1e-9)
    check("任一缺失 → None", scissors(None, 0.9) is None)

    check("3 期连升 → 上升", trend([1.0, 1.2, 1.5]) == "上升")
    check("3 期连降 → 下降", trend([1.5, 1.2, 1.0]) == "下降")
    check("窄幅 → 走平", trend([1.00, 1.05, 1.02]) == "走平")
    check("单点 → 数据不足", trend([1.0]) == "数据不足")

    check("SCISSORS_BIG 是常量而非散落字面量", isinstance(SCISSORS_BIG, float))

    # ────────────────────────────── 汇总
    print("\n" + "=" * 78)
    print(f"通过 {_passed} / 失败 {_failed}")
    print("=" * 78)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
