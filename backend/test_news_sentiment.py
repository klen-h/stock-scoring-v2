"""
消息面情绪打分单元测试（news_sentiment.py）。
运行：python test_news_sentiment.py
"""
from datetime import datetime
from app import news_sentiment as ns

NOW = datetime(2026, 8, 24, 12, 0, 0)   # 固定"当前时间"，保证衰减可复现


def item(title, summary="", when="2026-08-24 12:00:00"):
    return {"title": title, "summary": summary, "time": when}


def test_keyword_hits():
    # 强负面：立案调查 = -2
    assert ns.score_news_item(item("公司被证监会立案调查")) == -2
    # 多关键词累加：减持(-1) + 诉讼(-1) = -2
    assert ns.score_news_item(item("股东宣布减持计划，另涉重大诉讼")) == -2
    # 强正面：中标 = +2
    assert ns.score_news_item(item("公司中标重大项目")) == 2
    # 正负对冲：回购(+2) vs 下滑(-1) = +1
    assert ns.score_news_item(item("公司宣布回购股份，业绩有所下滑")) == 1
    # 无关键词 = 0
    assert ns.score_news_item(item("公司召开年度股东大会")) == 0


def test_single_clamp():
    # 多个负面词命中也封顶 -2
    s = ns.score_news_item(item("立案调查叠加违规担保，财务造假被揭，亏损巨大"))
    assert s == -2


def test_negation_flip():
    # "终止减持"：减持(-1) 被标题否定词反转 → +1
    assert ns.score_news_item(item("股东终止减持计划")) == 1
    # "取消回购"：回购(+2) 反转 → -2
    assert ns.score_news_item(item("公司取消回购方案")) == -2


def test_negation_guard():
    # "终止上市"是强负面词本身，不能被"终止"反转成正面
    assert ns.score_news_item(item("公司股票面临终止上市风险")) == -2


def test_decay():
    # 刚发布 = 1.0
    assert ns.decay_weight("2026-08-24 12:00:00", NOW) == 1.0
    # 36 小时前 = 0.5
    assert abs(ns.decay_weight("2026-08-23 00:00:00", NOW) - 0.5) < 1e-9
    # 72 小时前 = 0
    assert ns.decay_weight("2026-08-21 12:00:00", NOW) == 0.0
    # 更早 = 0（不为负）
    assert ns.decay_weight("2026-08-01 00:00:00", NOW) == 0.0
    # 时间格式坏 → 保守 0.5
    assert ns.decay_weight("bad-time", NOW) == 0.5
    # 未来时间（时钟偏差）→ 1.0
    assert ns.decay_weight("2026-08-25 00:00:00", NOW) == 1.0


def test_total_and_levels():
    # 两条强负面刚发布：-2 + -2 = -4 → 强烈负面
    r = ns.score_stock_news([item("立案调查"), item("财务造假实锤")], NOW)
    assert r["score"] == -4 and r["level"] == -2 and r["level_text"] == "强烈负面"
    # 单条弱负面（-1）在中性区（阈值 -1.5）；两条弱负面叠加 → 负面档
    r = ns.score_stock_news([item("股东宣布减持计划")], NOW)
    assert r["level"] == 0 and r["level_text"] == "中性"
    r = ns.score_stock_news([item("股东宣布减持计划"), item("收到监管问询函")], NOW)
    assert r["score"] == -2 and r["level"] == -1 and r["level_text"] == "负面"
    # 空列表 → 中性 0
    r = ns.score_stock_news([], NOW)
    assert r["score"] == 0 and r["level"] == 0 and r["level_text"] == "中性"
    # 强正面 → 正面/强烈正面
    r = ns.score_stock_news([item("中标重大项目"), item("签署战略合作并获大额订单")], NOW)
    assert r["level"] in (1, 2) and r["score"] >= 1.5
    # 封顶 ±10：大量负面也不超
    r = ns.score_stock_news([item("立案调查叠加违规担保")] * 10, NOW)
    assert r["score"] == -10


def test_total_decay_applied():
    # 48 小时前的强负面：-2 × (1-48/72) = -2 × 1/3 ≈ -0.67 → 中性档
    old = item("立案调查", when="2026-08-22 12:00:00")
    r = ns.score_stock_news([old], NOW)
    assert abs(r["score"] - (-2 / 3)) < 0.01
    assert r["level"] == 0


def test_items_only_sentiment():
    # 明细只保留有情绪倾向的条目
    r = ns.score_stock_news([item("立案调查"), item("公司召开股东大会")], NOW)
    assert len(r["items"]) == 1 and r["items"][0]["score"] == -2


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print("=" * 40)
    print("ALL PASSED" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
