"""
================================================================================
【文件作用】消息面情绪打分（关键词规则，不引 NLP/LLM）
================================================================================

把 eastmoney_news 拉来的快讯转成个股「新闻分」（-10 ~ +10）：

  单条新闻分 = Σ(命中关键词权重)，clamp 到 [-2, +2]
    标题含否定词（终止/取消/搁置…）→ 极性反转（"终止减持"不算利空）
  个股新闻分 = Σ(单条分 × 时间衰减)，clamp 到 [-10, +10]
    时间衰减 w = max(0, 1 - 小时数/72)：新闻只影响 3 天，越新权重越大

设计边界（阶段 1）：
  - 只做规则打分：关键词误伤可预期、可调试；LLM 打分成本高且不可复现
  - 独立维度：本模块产出的分数【不进入】综合总分（需阶段 3 回测验证后再议）
================================================================================
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

# ── 关键词字典（权重 2 = 强信号，1 = 弱信号）──
# 打分时在「标题 + 摘要」全文中做子串匹配；命中权重累加后统一 clamp。
NEG_STRONG = [   # 强负面 -2
    "立案", "调查", "退市风险", "巨亏", "下调评级", "停牌核查",
    "违规担保", "财务造假", "终止上市", "强制平仓",
]
NEG = [          # 负面 -1（注：“终止”不在此表——它是否定词，语义由反转规则处理）
    "减持", "业绩预减", "亏损", "下滑", "处罚", "违规", "商誉减值",
    "质押", "诉讼", "仲裁", "警示函", "监管函", "问询函",
]
POS_STRONG = [   # 强正面 +2
    "重大合同", "中标", "回购", "业绩预增", "扭亏", "获批", "提价",
    "重大资产重组", "业绩大增", "战略合作",
]
POS = [          # 正面 +1
    "合作", "业绩增长", "预增", "增持", "再投资", "投产", "签约",
    "订单", "扩产", "分红",
]

# 否定词：标题含这些词时反转极性（如"终止减持"→ 偏正面、"取消回购"→ 偏负面）。
# 只收录高置信词，避免"不/未"这类高频字误伤。
NEGATORS = ["终止", "取消", "搁置", "告吹", "中止", "未能", "未果", "流产"]

DECAY_HOURS = 72      # 衰减窗口：72 小时后权重归零
SINGLE_CLAMP = 2.0    # 单条新闻分上限 ±2
TOTAL_CLAMP = 10.0    # 个股新闻分上限 ±10


def _beijing_now() -> datetime:
    """当前北京时间的 naive datetime（与快讯 showTime 的时区对齐）。"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def parse_news_time(s: str) -> Optional[datetime]:
    """解析快讯时间 "YYYY-MM-DD HH:MM:SS"（北京时间）。失败返回 None。"""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def decay_weight(news_time: str, now: datetime = None) -> float:
    """时间衰减权重：刚发布=1.0，72 小时后=0。时间解析失败给 0.5（中性保守）。"""
    t = parse_news_time(news_time)
    if t is None:
        return 0.5
    now = now or _beijing_now()
    hours = (now - t).total_seconds() / 3600
    if hours < 0:      # 时间在未来（时钟偏差），视为最新
        return 1.0
    return max(0.0, 1.0 - hours / DECAY_HOURS)


def score_news_item(item: dict) -> float:
    """
    单条新闻情绪分（-2 ~ +2）。
    全文（标题+摘要）匹配关键词，正负权重分别累加后相减，再：
      1) clamp 到 ±2；2) 标题含否定词 → 反转极性。
    """
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    title = item.get("title", "")
    # ★ 情境化："回购"权重按用途区分——
    #   标题含「员工持股/股权激励」→ 激励型回购：公司出钱买股再低价授予员工，
    #   对股东直接回报有限（拿全体股东的钱补贴少数人），权重降为 +1（中性偏正）；
    #   其余情形（含注销/减资，直接减股本增厚 EPS）→ 保持 +2（真正利好股东）。
    pos_w = 0
    for kw in POS_STRONG:
        if kw in text:
            if kw == "回购" and any(k in title for k in ("员工持股", "股权激励")):
                pos_w += 1
            else:
                pos_w += 2
    pos_w += sum(1 for kw in POS if kw in text)
    neg_w = sum(2 for kw in NEG_STRONG if kw in text) + sum(1 for kw in NEG if kw in text)
    score = pos_w - neg_w
    score = max(-SINGLE_CLAMP, min(SINGLE_CLAMP, score))
    # 否定反转：仅对标题判断（摘要太长易误触）。
    # 守卫：否定词本身是强负面词的一部分时不反转（如“终止上市”不能反转成正面）。
    title = item.get("title", "")
    if score != 0 and any(neg in title for neg in NEGATORS):
        all_kws = NEG_STRONG + NEG + POS_STRONG + POS
        # 守卫：否定词本身是某个命中关键词的一部分时不反转（如“终止上市”不能反转成正面）
        embedded = any(kw in title and kw.startswith(neg) and kw != neg
                       for neg in NEGATORS if neg in title for kw in all_kws)
        if not embedded:
            score = -score
    return score


def _level(score: float) -> tuple:
    """新闻分 → (等级码, 等级文案)。阈值与 plan 一致。"""
    if score <= -4:
        return -2, "强烈负面"
    if score <= -1.5:
        return -1, "负面"
    if score < 1.5:
        return 0, "中性"
    if score < 4:
        return 1, "正面"
    return 2, "强烈正面"


def score_stock_news(news_items: list, now: datetime = None) -> dict:
    """
    汇总一只股票的新闻列表 → 新闻分 + 等级 + 逐条明细。
    返回 {score, level, level_text, items: [{title, time, score}]}。
    """
    now = now or _beijing_now()
    total = 0.0
    details = []
    for it in news_items or []:
        s = score_news_item(it)
        w = decay_weight(it.get("time", ""), now)
        total += s * w
        if s != 0:   # 明细只保留有情绪倾向的（中性的占版面没意义）
            details.append({"title": it.get("title", ""),
                            "time": it.get("time", ""), "score": s})
    total = round(max(-TOTAL_CLAMP, min(TOTAL_CLAMP, total)), 2)
    level, level_text = _level(total)
    # 明细按时间倒序（最新在前），避免同一事件多篇报道混排让用户困惑
    details.sort(key=lambda d: parse_news_time(d.get("time", "")) or datetime.min, reverse=True)
    return {"score": total, "level": level, "level_text": level_text,
            "items": details}
