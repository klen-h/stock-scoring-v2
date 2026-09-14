# -*- coding: utf-8 -*-
"""
Coach LLM 翻译层（W2）—— 评审①「事前无权」的唯一落地方式。

分工（这是本模块存在的全部意义）：
  规则引擎 `rules.py` 负责**判定 + 数字**（事实）；本模块只负责**措辞**。
  最终推送给用户的文案 = 规则原文（含数字，代码生成） + 本模块的解说（禁含数字）。

★ 结构性保证（不靠"事后检测冲突"）：LLM 输出**不得出现任何数字**——连复述
  规则里的数字都禁止。数字只能来自代码注入，因此"LLM 篡改数字/与规则冲突"
  在结构上不可能发生，而不是"发生了再丢弃"。

降级 fail-open（绝不因翻译层故障挡住纪律提醒）：开关关 / 情绪熔断 / LLM 不可用 /
  输出含数字 / 任何异常 → 一律返回 ""，调用方直接用规则原文（原文永远可用）。

情绪熔断（简报 §3.4）：当日浮亏超阈值（`emotion_fuse` 已落库）→ LLM **闭嘴**，
  只输出规则原文、不做安抚性解读——防止亏损时被"安慰话术"劝住止损。
"""
import os
import re
from typing import Dict, List, Optional

from app.flash import rules as flash_rules

# 开关（默认 on；设 COACH_EXPLAINER=off 回滚为"只推规则原文"）
EXPLAINER_ENABLED = (os.environ.get("COACH_EXPLAINER", "on").strip() != "off")

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_MAX_PER_PUSH = 3        # 一次推送最多解说几条（防刷屏）

_SYSTEM = (
    "你是交易纪律教练的『翻译层』。规则引擎**已经判定事实并给出全部数字**，"
    "你的唯一职责：把这条提醒翻译成一句有人味、能帮用户压住『再等等看』冲动的话。\n"
    "硬性纪律（违反即作废）：\n"
    "① **绝对不许出现任何数字**（不许复述、不许估算、不许举例）；\n"
    "② 不许给出买卖建议，不许预测涨跌；\n"
    "③ 只针对这一条提醒，一句话，不超过 50 字；\n"
    "④ 只解释『为什么该照做』，不做安慰式解读。"
)

_fuse_cache: Dict = {"date": None, "active": False}


def _has_digits(s: str) -> bool:
    return bool(_NUM_RE.search(s or ""))


def fuse_active() -> bool:
    """情绪熔断：当日已触发 `emotion_fuse`（浮亏超阈值）→ LLM 闭嘴。

    每天缓存一次（`emotion_fuse` 一旦触发当天不会撤销）。
    """
    today = flash_rules.beijing_now().strftime("%Y-%m-%d")
    if _fuse_cache["date"] == today:
        return _fuse_cache["active"]
    active = False
    try:
        from app.database import db
        row = db.fetch_one(
            "SELECT 1 FROM coach_alerts WHERE alert_date=%s AND rule_id='emotion_fuse' LIMIT 1",
            (today,))
        active = bool(row)
    except Exception:
        active = False          # fail-open：查不到 → 不熔断
    _fuse_cache.update({"date": today, "active": active})
    return active


def explain(label: str, message: str) -> str:
    """把一条规则结果渲染成一句人话；返回 "" 表示不解释（调用方用规则原文）。"""
    if not EXPLAINER_ENABLED:
        return ""
    if fuse_active():
        return ""               # 情绪熔断：闭嘴，只输出规则原文
    try:
        from app.flash import llm
        if llm.llm_blocked_reason():
            return ""
        user = (f"规则：{label}\n规则引擎给出的事实：{message}\n\n"
                "请用一句人话解释为什么要照做（**不许出现任何数字**）：")
        out = (llm.call_llm(_SYSTEM, user, temperature=0.3, retries=1) or "").strip()
        if not out:
            return ""
        # ★ 结构性校验：输出含任何数字（含复述规则数字）→ 丢弃，回退规则原文。
        if _has_digits(out):
            print(f"[coach] explainer 输出含数字，已丢弃（回退规则原文）：{out[:40]}")
            return ""
        return out
    except Exception as e:
        print(f"[coach] explainer 失败（用规则原文）: {e}")
        return ""


def explain_batch(alerts: List[dict]) -> str:
    """给一批新触发的硬警报生成「教练解说」段（每条一行；数字全部由规则原文承载）。"""
    if not alerts or not EXPLAINER_ENABLED:
        return ""
    lines = []
    for a in alerts[:_MAX_PER_PUSH]:
        txt = explain(a.get("label") or "", a.get("message") or "")
        if not txt:
            continue
        tag = a.get("name") or a.get("code") or ""
        lines.append(f"· {tag}：{txt}" if tag else f"· {txt}")
    return "\n".join(lines)
