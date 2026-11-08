#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】政策定调关键词账本 · 纯函数单测（offline，不连库/不调 LLM/零外呼）
================================================================================
覆盖 `policy_tone_watch.compute()` / `confirm()` 校验逻辑：
  · 命中按"条数"计（同一关键词在同一条快讯出现 N 次只算 1 条）
  · 拉丁词大小写不敏感（SFISF / PMI / CPI / PPI）
  · rows 覆盖**全部**冻结关键词（0 命中也要显式落库 = 预留词召回核对点）
  · 反向词表独立成 reverse 类
  · 样例最多 2 条、字段完整、文本截断
  · 交叉词并存（"平准基金"⊂"平准" 各自计入各自类别）
  · 纯函数性：同样输入两次结果相同、不修改入参、空语料不崩
  · tone_tag 校验（非法标签拒绝）
  · 默认日期口径（交易日，不是运行日——周末伪日期纪律）
  · 零外呼断言：模块源码不 import requests

运行：cd backend && python test_policy_tone_watch.py
================================================================================
"""
import copy
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app import policy_tone_watch as ptw  # noqa: E402

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


def _rowmap(res):
    return {(r["category"], r["keyword"]): r for r in res["rows"]}


ITEMS = [
    {"src": "flash", "time": "2026-11-06 06:35:14",
     "text": "【金十数据整理：昨日今晨重要新闻汇总（2026-11-06）】国内新闻："
             "1. 国务院报告：支持中央汇金公司发挥类平准基金作用，巩固完善资本市场稳市机制。"},
    {"src": "flash", "time": "2026-11-06 15:40:16",
     "text": "【中金公司已在“互换便利”项下买入股票】中金公司在第三批"
             "“证券、基金、保险公司互换便利”项下买入股票。互换便利 再互换便利。"},
    {"src": "tidy", "time": "2026-11-06 tidy#3",
     "text": "A股盘前｜央行报告提出继续实施好适度宽松的货币政策，SFISF 与 pmI 数据向好"},
]


def main():
    res = ptw.compute(ITEMS)
    m = _rowmap(res)

    print("\n=== 一、命中计数（按条数，不按出现次数）===")
    check("[1] 互换便利=1 条（一条快讯里出现 3 次只算 1 条）",
          m[("funds", "互换便利")]["hit_count"] == 1)
    check("[2] 中央汇金=1", m[("funds", "中央汇金")]["hit_count"] == 1)
    check("[3] 平准（funds）=1", m[("funds", "平准")]["hit_count"] == 1)
    check("[4] 平准基金（tone）=1（交叉词并存，各自计入）",
          m[("tone", "平准基金")]["hit_count"] == 1)
    check("[5] 稳市机制=1", m[("tone", "稳市机制")]["hit_count"] == 1)
    # ★ 2026-11-08.1（§3.1b）：tone 词表剔除"资本市场"（套话词）——
    #   同一条含"资本市场稳市机制"的语料只应命中"稳市机制"，不再被套话稀释
    check("[6] 资本市场已从词表剔除（套话不命中）",
          ("tone", "资本市场") not in m
          and m[("tone", "稳市机制")]["hit_count"] == 1)
    check("[7] 适度宽松=1（tidy 语料）", m[("tone", "适度宽松")]["hit_count"] == 1)

    print("\n=== 二、大小写不敏感 ===")
    check("[8] SFISF 小写命中", m[("funds", "SFISF")]["hit_count"] == 1)
    check("[9] pmI 混合大小写命中", m[("fundamental", "PMI")]["hit_count"] == 1)

    print("\n=== 三、全词表显式落库（含 0）===")
    expect_n = sum(len(v) for v in
                   {**ptw.KEYWORDS, **ptw.NEGATIVE_KEYWORDS}.values())
    check("[10] rows 覆盖全部冻结关键词（含 0）", len(res["rows"]) == expect_n,
          f"rows={len(res['rows'])} 期望={expect_n}")
    check("[11] 未发生的预留词显式为 0（中央经济工作会议）",
          m[("tone", "中央经济工作会议")]["hit_count"] == 0)
    check("[12] 社融=0 显式落库", m[("fundamental", "社融")]["hit_count"] == 0)

    print("\n=== 四、反向词表 ===")
    neg_items = ITEMS + [{"src": "flash", "time": "2026-11-06 20:00:00",
                          "text": "央行：下一步转向稳健的货币政策，注意动态微调节奏"}]
    res2 = ptw.compute(neg_items)
    m2 = _rowmap(res2)
    check("[13] 稳健的货币政策 → reverse 类命中 1",
          m2[("reverse", "稳健的货币政策")]["hit_count"] == 1)
    check("[14] 动态微调 → reverse 类命中 1（'微调节奏'子串在词内）",
          m2[("reverse", "动态微调")]["hit_count"] == 1, "动态微调⊂动态微调节奏")
    check("[15] reverse 不混入正向分类统计",
          all(c != "reverse" for c in ptw.KEYWORDS))

    print("\n=== 五、样例 ===")
    s = m[("funds", "互换便利")]["samples"]
    check("[16] 样例 ≤2 条", len(s) <= ptw.MAX_SAMPLES, str(len(s)))
    check("[17] 样例字段完整（src/time/head）",
          all(set(x) == {"src", "time", "head"} for x in s), str(s[:1]))
    check("[18] 样本文本截断 ≤ SAMPLE_HEAD",
          all(len(x["head"]) <= ptw.SAMPLE_HEAD for x in s))

    print("\n=== 六、纯函数性 ===")
    before = copy.deepcopy(ITEMS)
    a = ptw.compute(ITEMS)
    b = ptw.compute(ITEMS)
    check("[19] 同样输入两次结果相同（可复算）",
          a["rows"] == b["rows"] and a["by_category"] == b["by_category"])
    check("[20] 不修改入参", ITEMS == before)
    empty = ptw.compute([])
    check("[21] 空语料不崩且全 0",
          empty["n_items"] == 0
          and all(r["hit_count"] == 0 for r in empty["rows"]))
    check("[22] 自定义词表生效（不读写全局）",
          ptw.compute(ITEMS, keywords={"tone": ["自定义词"]},
                      negative={})["rows"][0]["keyword"] == "自定义词")

    print("\n=== 七、tone_tag 校验（纯函数，非法拒绝）===")
    for bad in ("", "随便写的", "买入", None):
        r = ptw.validate_tag(bad)
        check(f"[23] 非法标签「{bad}」拒绝", r is not None, str(r))
    for good in ("宽松延续", "宽松升级", "宽松回退", "未定", "无会议", "宽松延续 "):
        r = ptw.validate_tag(good)
        check(f"[23] 合法标签「{good}」通过（含尾空格 strip）", r is None, str(r))

    print("\n=== 八、默认日期口径 ===")
    d = ptw._default_day()
    check("[24] 默认日期是 YYYY-MM-DD",
          isinstance(d, str) and len(d) == 10 and d[4] == "-" and d[7] == "-",
          str(d))
    import datetime as _dt
    try:
        wd = _dt.date.fromisoformat(d).weekday()
        check("[25] 默认日期落在工作日（交易日历可用时）", wd < 5,
              f"{d} weekday={wd}")
    except Exception as e:
        check("[25] 默认日期可解析", False, str(e))

    print("\n=== 九、版本与零外呼 ===")
    import re as _re
    v = ptw.KEYWORD_VERSION
    check("[26] KEYWORD_VERSION 形如 YYYY-MM-DD.N",
          bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", v)), v)
    src = open(os.path.join(BACKEND_DIR, "app", "policy_tone_watch.py"),
               encoding="utf-8").read()
    check("[27] 零外呼：不 import requests",
          "import requests" not in src and "requests.get" not in src)
    # ★ 2026-11-07（§3.1a 修订后口径）：wechat 推送**仅限观察词层**且必须函数内懒导入；
    #   冻结账本函数（compute/snapshot）源码里不得出现任何推送调用。
    check("[28] wechat 为函数内懒导入（不在模块顶层）",
          "from app.flash import wechat" in src
          and _re.search(r"^from app\.flash import wechat", src, _re.M) is None)
    # ★ 2026-11-08（审查修正）：原切片 `def compute`..`def ensure_tables` **不含 `snapshot`**
    #   —— 后者定义在 ensure_tables 之后，而断言名却写"compute/snapshot 路径"，名不副实，
    #   挡不住将来有人往 snapshot 里加推送。改切到 `def load` 之前，
    #   覆盖 compute / ensure_tables / _default_day / collect_items / snapshot /
    #   validate_tag / confirm 全部只读与写入路径（观察词推送在 load 之后，不误伤）。
    _ledger_src = src[src.find("def compute"):src.find("def load")]
    check("[28b] 冻结账本函数（compute/snapshot/confirm 路径）零推送",
          "push_markdown_batched" not in _ledger_src
          and "wechat" not in _ledger_src)

    print("\n=== 十、审查回归（2026-11-08）——三处静默失效修复 ===")
    # [29] P1：watch_check_push 必须**先推送、后记账**。
    #   原实现先写 last_pushed_at 再推 ⇒ 企微一抖动记账已落，而
    #   `last_pushed_at[:10]==day` 会把该词当天后续全跳过 ⇒ **静默漏推**。
    #   此处用源码**顺序断言**，保持本文件的离线特性（不连库、不 mock）。
    _fn = src[src.find("def watch_check_push"):]
    _cut = _fn.find("\n# ═")
    if _cut > 0:
        _fn = _fn[:_cut]
    _i_push = _fn.find("push_markdown_batched(")
    _i_acct = _fn.find("last_pushed_at=%s")
    check("[29] watch_check_push 先推送后记账（顺序断言）",
          0 <= _i_push < _i_acct, f"push@{_i_push} acct@{_i_acct}")
    check("[29b] 推送未确认送达时不记账（有 error 分支）",
          "推送未确认送达" in _fn)

    # [30] P2：FedWatch 多方向并存 —— 纯函数 `latest_by_direction`
    #   原实现只留全局最新一条：最新句若是「维持利率不变」，加息假设的
    #   latest_pct 会变 None ⇒ 即便早已跌破阈值也不触发 ⇒ 静默漏报。
    from app.thesis_hints import latest_by_direction as _lbd
    _ms = [
        {"direction": "维持利率不变", "pct": 30.0, "date": "2026-11-07"},
        {"direction": "加息", "pct": 72.8, "date": "2026-11-06"},
        {"direction": "加息", "pct": 65.0, "date": "2026-11-05"},
    ]
    _snapshot = copy.deepcopy(_ms)
    _d = _lbd(_ms)
    check("[30] 按方向取最新（「维持不变」不遮挡「加息」）",
          _d.get("加息", {}).get("pct") == 72.8
          and _d.get("维持利率不变", {}).get("pct") == 30.0,
          str({k: v["pct"] for k, v in _d.items()}))
    check("[31] latest_by_direction 不修改入参 / 空输入不崩",
          _ms == _snapshot and _lbd([]) == {} and _lbd(None) == {})

    print("\n=== 十、三底时序纯函数（经济底月粒度，2026-11-08）===")
    check("[29] YYYY-MM 合法", ptw._valid_period("2027-01") == "2027-01")
    check("[30] 非法 period（日期/缺位/12月外）拒绝",
          all(ptw._valid_period(x) is None for x in
              ("2027-1", "2027-13", "2027/01", "abc", "", None, "2027-00")))
    check("[31] 月差：2008 复盘（市场底 10-28 → 经济底 2009-01）= 3 个月",
          ptw._month_diff("2009-01", "2008-10-28") == 3)
    check("[32] 月差：同月 = 0、跨年负数",
          ptw._month_diff("2027-01", "2027-01-15") == 0
          and ptw._month_diff("2026-11", "2027-01-15") == -2)
    check("[33] 月差：脏输入 → None（不抛）",
          ptw._month_diff("xx", "2027-01-15") is None)

    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
