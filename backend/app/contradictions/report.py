"""
矛盾扫描报告生成（LLM 结构化解读）

当前 MVP：硬数据渲染 + 可选 LLM 摘要。
若无 LLM 配置，直接输出结构化 markdown（避免空报告）。
"""

import json
import os
from typing import List, Dict, Optional

from app.contradictions.store import _today
from app.contradictions.labels import (
    severity_cn, level_cn, type_cn, metric_cn, fmt_metric_value,
)


_SYSTEM = (
    "你是 A 股投研助手，专门识别市场中的「叙事 vs 行为」矛盾。"
    "你只能基于用户提供的结构化数据做分析，禁止编造数字。"
    "你是研究员而非交易顾问：绝不给出「买入/加仓/减仓/卖出/止损」等指令式建议，"
    "只给出观察要点、关键位与风险提示。"
    "输出合法 JSON，不要输出 Markdown 或围栏。"
)


def _fmt_money(v) -> str:
    try:
        return f"{float(v) / 1e8:.2f} 亿"
    except (TypeError, ValueError):
        return "-"


def _render_markdown(date: str, items: List[Dict], llm_summary: Optional[Dict] = None) -> str:
    """把矛盾列表渲染成 markdown 报告。"""
    lines = []
    add = lines.append

    add(f"# 矛盾扫描报告 {date}")
    add(f"> 生成：{_today()}")
    add("")

    if not items:
        add("今日未发现显著矛盾（或数据暂缺）。")
        return "\n".join(lines)

    # 顶层摘要
    severe = [i for i in items if i.get("severity") == "severe"]
    obvious = [i for i in items if i.get("severity") == "obvious"]
    minor = [i for i in items if i.get("severity") == "minor"]
    add(f"## 摘要")
    levels = "、".join(sorted({level_cn(i.get('level', 'L2')) for i in items}))
    add(f"- 共识别 **{len(items)}** 个矛盾（{levels}）")
    if severe:
        add(f"- 严重：{len(severe)} 个")
    if obvious:
        add(f"- 明显：{len(obvious)} 个")
    if minor:
        add(f"- 轻微：{len(minor)} 个")
    if llm_summary and llm_summary.get("overview"):
        add(f"- AI 综述：{llm_summary['overview']}")
    add("")

    # 关键矛盾卡片
    add("## 关键矛盾")
    for idx, item in enumerate(items, 1):
        level = level_cn(item.get("level", "L2"))
        severity = severity_cn(item.get("severity", "minor"))
        ctype = type_cn(item.get("type", ""))
        title = item.get("title", "")
        summary = item.get("summary", "")
        signal = item.get("signal", "")
        head = f"{level} · {severity}" + (f" · {ctype}" if ctype else "")
        add(f"### {idx}. [{head}] {title}")
        add(f"- {summary}")
        if signal:
            add(f"- **交易含义**：{signal}")
        evidence = item.get("evidence") or {}
        metrics = evidence.get("metrics") or {}
        if metrics:
            add(f"- **关键数据**：")
            # 键与值都中文化；金额键按亿元渲染，计数键保持原样
            for k, v in metrics.items():
                add(f"  - {metric_cn(k)}：{fmt_metric_value(v, key=k)}")
        add("")

    # LLM 解读区（若有）
    if llm_summary:
        notes = llm_summary.get("notes") or []
        watch = llm_summary.get("watchlist") or []
        risk = llm_summary.get("risk") or ""
        if notes:
            add("## AI 解读")
            for n in notes:
                add(f"- {n}")
            add("")
        if watch:
            add("## 明日观察")
            for i, w in enumerate(watch[:5], 1):
                add(f"{i}. {w}")
            add("")
        if risk:
            add(f"**风险提示**：{risk}")

    return "\n".join(lines)


def _llm_summary(items: List[Dict]) -> Optional[Dict]:
    """调用 LLM 生成矛盾综述。失败返回 None（不影响硬数据报告）。"""
    try:
        from app.flash import llm
        blocked = llm.llm_blocked_reason()
        if blocked:
            print(f"[contradiction_report] LLM 不可用: {blocked}")
            return None

        # 只把标题、摘要、交易含义传给 LLM，避免 evidence 太长（severity 转中文）
        prompt_items = [
            {
                "index": i + 1,
                "severity": severity_cn(item.get("severity")),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "signal": item.get("signal"),
            }
            for i, item in enumerate(items)
        ]
        user = (
            "以下是今日 A 股收盘后扫描出的「叙事 vs 行为」矛盾清单：\n\n"
            f"{json.dumps(prompt_items, ensure_ascii=False, indent=2)}\n\n"
            "请输出 JSON（不要 Markdown），字段：\n"
            "{\n"
            '  "overview": "一句话综述今日矛盾的整体含义",\n'
            '  "notes": ["分点解读 2-5 条"],\n'
            '  "watchlist": ["明日关键观察指标 3-5 条"],\n'
            '  "risk": "一句话风险提示"\n'
            "}\n"
            "不得编造数据；不得给出买卖指令。"
        )
        data = llm._call_json(_SYSTEM, user, temperature=0.3)
        if data and isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[contradiction_report] LLM 摘要失败: {e}")
    return None


def generate_report(date: Optional[str] = None, items: Optional[List[Dict]] = None) -> str:
    """生成某日矛盾扫描报告并返回 markdown 文本。"""
    target = date or _today()
    if items is None:
        from app.contradictions.scanner import scan_all
        items = scan_all(date=target)
    summary = _llm_summary(items)
    return _render_markdown(target, items, summary)


def run_report(date: Optional[str] = None) -> Dict:
    """生成报告并落库。"""
    from app.contradictions.store import save_report
    target = date or _today()
    md = generate_report(date=target)
    save_report(target, md)
    return {"date": target, "len": len(md)}
