# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】登记「事件脉冲协议」卡（2026-11-28）
================================================================================
背景：用户复盘 11-27 ——
  "政策中午发文，午盘开盘猛拉，我当时不知所措，犹豫不决，错过上车好时机。"
  线上库实测：12:23:26 国常会通稿（hot=沸，系统采到了）→ 12:32 企微推「平准基金」
  → 13:00 开盘 → 13:33 期货涨停。**到开盘还有 28 分钟，人不是不知道，
  是没有卡可翻。**

    情景卡（thesis_scenarios）回答"会怎样"，本卡回答"怎么办"。

【幂等】`action_protocol.create` 会归档同名的旧 active 卡 ⇒ 重复跑只会留下最新一份。

【用法】
    # 本地（默认 SQLite backend/data/app.db）
    python scripts/seed_action_protocol.py

    # 线上（读 backend/.env 的 DATABASE_URL，指向 PG）
    python scripts/seed_action_protocol.py --check

--check 只打印当前 active 协议，不写入。
================================================================================
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: except_pass  stdout 非文本流时忽略，不影响登记主流程
    pass

PAYLOAD = {
    "name": "事件脉冲协议",
    "trigger": {
        "keywords": [
            "国常会", "国务院常务会议", "政治局会议",
            "央行", "中国人民银行", "证监会",
            "汇金", "平准基金", "稳市", "增量政策",
            "投融资综合改革", "中长期资金", "国家队",
        ],
        "min_hits": 2,
        "window": "09:15-15:05（含午间休市）",
        "note": ("政策通稿常在午间休市时段发布，窗口必须覆盖午间；"
                 "min_hits=2 防单提某机构就误触发（11-27 通稿命中 8 个）"),
    },
    "first_move": ("T+0 不交易。只做读数：量能斜率、广度（涨跌板块比）、"
                   "涨停集中度、防御板块（银行/白酒/电力）资金流向。"),
    "verdict": ("事件日长阳 ≠ 普通长阳。普通长阳次日回踩是获利盘行为（高概率）；"
                "政策锚长阳的回踩被增量政策预期压缩（但不是消除）。"
                "⇒ 结论仍是当日不追 —— 差别只在：回踩带限价单必须当晚挂好，"
                "防止右侧再踏空。"),
    "position_path": ("复用追认协议：3480-3553 缩量企稳 → 1/4；"
                      "三日不回带 + 放量破平台 → 1/8。"),
    "invalidate": ("通稿后两日量能持续萎缩 + 收盘回破 3476.65 → 脉冲失败，"
                   "转 S2 整固处理。"),
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="只查看，不写入")
    a = p.parse_args()

    from app import action_protocol as ap
    ap.ensure_tables()

    cur = ap.list_protocols(active_only=True)
    print("当前 active 协议：%d 张" % len(cur))
    for x in cur:
        print("  %s  %s（关键词 %d，min_hits=%s，建于 %s）"
              % (x["id"], x["name"], len(x["trigger"].get("keywords") or []),
                 x["trigger"].get("min_hits"), x.get("created_at")))

    if a.check:
        print("\n--check：不写入")
        return 0

    r = ap.create(PAYLOAD)
    print("\n登记结果：", r)
    if not r.get("ok"):
        return 1

    # 用 11-27 通稿做一次自检，确认这张卡真的能被当时的文本触发
    tonggao = ("【李强主持召开国务院常务会议 研究持续深化资本市场投融资综合改革工作】"
               "国务院总理李强11月27日主持召开国务院常务会议，研究持续深化资本市场"
               "投融资综合改革工作。持续完善资本市场稳市机制，实施好存量政策，谋划出台"
               "增量政策。加力发挥\"国家队\"稳市作用，进一步优化中央汇金公司的类"
               "\"平准基金\"功能。完善保险、企业年金、社保基金等中长期资金入市政策。")
    hits = ap.match_items([{"text": tonggao, "time": "2026-11-27 12:23:26"}])
    print("自检（11-27 通稿）：命中 %d 张 %s"
          % (len(hits), [h.get("_hit_keywords") for h in hits]))
    return 0 if hits else 2


if __name__ == "__main__":
    sys.exit(main())
