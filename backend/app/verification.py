# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「数据可信度」单一事实源 —— 哪些结论已验证、哪些没有
================================================================================
为什么需要（2026-09-27 用户旅程盘点发现）：
  项目界面上，「该不该买」（闸门，19.5 年验证过）与「买什么」（评分/战法，
  **未验证甚至已证伪**）**看起来同样权威** —— 用户无从分辨哪条结论有实证支撑。
  而这恰恰是最该告诉用户的事（项目本身就以"敢说自己的信号无效"为原则）。

本模块**只做一件事**：把散落在各轮研究里的**验证结论**集中成一张表，
供前端标注（`GET /api/verification`）。**不引入任何新的判据或策略**。

`status` 取值（三档 + 一档中间态）：
  · `verified`    已通过完整验证（有预登记判据 + 显著性 + 可执行性）
  · `partial`     部分成立（有实证但存在已知缺陷/边界）
  · `unverified`  尚未验证（数据不足，或只有一致性对齐而无回测）
  · `falsified`   已被证伪（有实证表明无效）

⚠️ **纪律：本表只允许"依据已有研究的既成结论"填写。**
   新增/修改任何一条前，必须先有对应脚本产出可复现证据，并在 `evidence` 写明出处。
================================================================================
"""
from __future__ import annotations

from typing import Dict, List

# ── 验证结论表（每条 = 一个用户可见的"结论来源"）──
ITEMS: List[Dict] = [
    {
        "key": "regime_gate",
        "label": "市场状态闸门（该不该买 / 买多少）",
        "status": "verified",
        "summary": "19.5 年（4736 交易日）四态分布 + 尾部风险画像已复核",
        "evidence": "scripts/regime_position_check.py、scripts/regime_distribution.py",
        "note": ("防御期均值不低（+2.50% vs 全样本 +2.87%）但**尾部显著更厚**"
                 "（P5 −17.80% vs −13.45%、大跌率 1.44×）⇒ 0% 仓位有风险依据。"
                 "⚠️ 已知缺陷：仓位映射**非单调**（三口径一致），说明该表基于直觉而非最优拟合。"),
    },
    {
        "key": "e2_event",
        "label": "E2 政策脉冲事件信号",
        "status": "verified",
        "summary": "唯一通过完整验证的信号：预登记 PASS + 可执行 + 扣成本",
        "evidence": "scripts/event_edge_check.py、event_realindex_check.py、event_hold_cost_check.py",
        "note": ("中证1000(512100) 扣成本后 T+20 **+2.46pp**（P=0.0012）。"
                 "⚠️ 极稀有（2024:10 / 2025:1 / 2026:0 次）⇒ 定位是"
                 "「防御期提前解除」，不是常规买入信号；当前**不进决策链**。"),
    },
    {
        "key": "score",
        "label": "综合评分（买什么）",
        "status": "unverified",
        "summary": "独立性检验（P0）因样本不足**未完成**",
        "evidence": "scripts/p0_score_independence.py（信号日 ~26 天，需 ≥120 天）",
        "note": ("评分 Top50 在两个市场状态下均为负期望，但样本仅 ~13 个有效信号日 ⇒"
                 "**统计上无结论**，不是「评分无效」，是「数据还不够」。"
                 "⚠️ 阈值（≥65 买入）是规则设定，未见回测最优性验证。"),
    },
    {
        "key": "strategies",
        "label": "6 个战法（买什么）",
        "status": "falsified",
        "summary": "bootstrap 全市场检验：24 格**全不显著**，全局负期望",
        "evidence": "scripts/bootstrap_alpha.py、backtest_rescan_by_regime.py",
        "note": ("2026-09-27 证伪 ⇒ **动态推送白名单当前为空**"
                 "（`recommendation.get_push_whitelist()` 返回 []）。"
                 "这正是「买入清单」一栏经常为空的原因。"),
    },
    {
        "key": "exit_alert",
        "label": "撤退提醒（什么时候卖）",
        "status": "unverified",
        "summary": "阈值与前端/教练**对齐**，但**未见回测**",
        "evidence": "app/strategies/exit_alert.py、app/coach/rules.py（−8% 同源对齐说明）",
        "note": ("−8% 止损、RSI 70 回落 10、放量 2×+跌 3% 等阈值属**规则设定**"
                 "（来源「跟随主力操作指南」），历史胜率未检验。"
                 "⚠️ 与闸门不同：闸门至少做过分布/尾部核算，本项尚未。"),
    },
    {
        "key": "industry_momentum",
        "label": "行业动量分层",
        "status": "falsified",
        "summary": "横截面超额多空 +0.01pp，P=0.38 ⇒ 无预测力",
        "evidence": "scripts/industry_momentum_check.py",
        "note": "已归档；反向印证 E2 期是「全面普涨」而非行业轮动 ⇒ 买宽基即可。",
    },
    {
        "key": "smallcap_pref",
        "label": "小盘偏好信号",
        "status": "falsified",
        "summary": "相对多空 +1.85pp（P=0.0000，显著）**但不可执行**",
        "evidence": "scripts/smallcap_pref_check.py",
        "note": ("小盘在**每一层**都跑赢大盘 ⇒ 可执行轮动（+2.80%）不如一直持小盘（+2.98%）"
                 "⇒ 归档。**方法论铁律：因子显著 ≠ 可交易**。"),
    },
]

BY_KEY: Dict[str, Dict] = {x["key"]: x for x in ITEMS}

_STATUS_CN = {"verified": "已验证", "partial": "部分成立",
              "unverified": "未验证", "falsified": "已证伪"}


def build() -> Dict:
    """返回全部验证结论（供前端标注）。无参数、无 IO、恒定输出。"""
    items = []
    for x in ITEMS:
        y = dict(x)
        y["status_cn"] = _STATUS_CN.get(x["status"], x["status"])
        items.append(y)
    counts: Dict[str, int] = {}
    for x in ITEMS:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {
        "items": items,
        "summary": {"total": len(items),
                    **{k: counts.get(k, 0) for k in
                       ("verified", "partial", "unverified", "falsified")}},
        "legend": {"verified": "已通过完整验证（预登记判据 + 显著性 + 可执行性）",
                   "partial": "部分成立（有实证但有已知缺陷）",
                   "unverified": "尚未验证（数据不足，或仅口径对齐而无回测）",
                   "falsified": "已被证伪（有实证表明无效）"},
        "note": "本表只记录已有研究的既成结论，不构成买卖建议。",
    }


def status_of(key: str) -> str:
    return (BY_KEY.get(key) or {}).get("status", "unverified")
