# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「结构化观点」的唯一取用与校验口（L3，2026-10-11）
================================================================================
【要解决什么】
  LLM 的观点输出此前是**一整块 markdown**：前端只能"读文本猜结构"（L2 用显式标记
  `**结论**：` 缓解，但仍要求模型把标记写在第一行）；长度/条数/越界词也只能在 prompt 里
  "求"它遵守，无法机械约束。
  ⇒ 本模块把口径改成**项目既有的成熟做法**（与 `daily_report` / `contradictions/report` /
  `routers/stock` 的异动分析同源）：**LLM 出 JSON → 代码逐字段校验 → 由代码组装 markdown**。
  于是「结论/依据」成为**数据**而不是"文本里的位置"，前端可以按角色渲染
  （结论条 + 依据列表），而不是靠解析第一行。

【三级降级 —— **这是本模块能被安全上线的唯一理由**】
  ① **结构化**：LLM 给了合法 JSON ⇒ 返回 `conclusion` + `reasons` + 组装好的 `markdown`；
  ② **L2 兜底**：JSON 解析失败（但文本可用）⇒ `markdown = 原文`（前端 L2 的
     `splitInsight` 还会尝试抽 `**结论**：` 首行）；
  ③ **L1 保底**：连标记都没有 ⇒ 原文当 markdown 渲染（就是改造前的样子）。
  **任何一级都不"猜"**：解析不出就不给结论条，页面照常显示全文。

【校验纪律（宁缺勿猜，别放宽）】
  · `conclusion`：必须**单行**（多行会被压成一行）；超长（> `CONC_MAX`）视为**没照做** ⇒
    丢弃结论 —— **不截断**（截断是改模型原话，与"只能复述"的纪律冲突）。
  · `reasons`：逐条 strip、去空、最多 `REASON_MAX` 条、单条超 `REASON_LEN` 丢弃该条；
    全丢光 ⇒ 只留结论。
  · 可选 `sanitize`（如 P1 的 `_sanitize` 越界词表）对**拼起来的全文**统一作用：命中 ⇒
    **整段判失败**（保持既有 C1 语义：宁可只剩事实，也不放带买卖话术的句子出去）。
================================================================================
"""
import json
import re
from typing import Callable, List, Optional

CONC_MAX = 120          # 结论上限（"一句话"写不到 120 字；超了 = 没照做 ⇒ 丢弃而非截断）
REASON_MAX = 6          # 依据条数上限
REASON_LEN = 400        # 单条依据长度上限（超了丢弃该条）

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def _one_line(v) -> str:
    """压成单行 + 去首尾空白（结论必须是"一句话"）。"""
    return " ".join(str(v or "").split())


def parse_insight(text: str, sanitize: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """LLM 原始输出 → `{conclusion, reasons, markdown, structured}`。

    · `structured=True` ⇒ `conclusion`/`reasons` 可用，且 `markdown` 是**按同一份数据组装**的
      （保证"结构化通道"与"markdown 通道"**内容一致** —— 不会一处改了另一处没改）；
    · `structured=False` ⇒ `markdown = 原文`，调用方按老契约使用（前端三级降级渲染）。
    """
    raw = (text or "").strip()
    data = None
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            m = _JSON_FENCE_RE.search(raw)          # 模型爱在 JSON 外面包 ```json 围栏
            if m:
                try:
                    data = json.loads(m.group(1))
                except Exception:
                    data = None

    conclusion, reasons = "", []
    if isinstance(data, dict):
        c = _one_line(data.get("conclusion"))
        conclusion = c if 0 < len(c) <= CONC_MAX else ""
        rs = data.get("reasons")
        if isinstance(rs, (list, tuple)):
            items = list(rs)
        elif isinstance(rs, str):                    # 偶尔给一整段字符串 ⇒ 按行/分号拆
            items = re.split(r"[\n;；]+", rs)
        else:
            items = []
        for it in items:
            t = _one_line(it)
            if not t or len(t) > REASON_LEN:
                continue
            reasons.append(t)
            if len(reasons) >= REASON_MAX:
                break

        # 越界词机械兜底：对拼起来的全文统一判（保持"命中即整段失败"的既有语义）
        if sanitize is not None and (conclusion or reasons):
            joined = " ".join([conclusion] + reasons)
            if sanitize(joined) is None:
                return {"conclusion": "", "reasons": [], "markdown": raw, "structured": False}

    if not conclusion and not reasons:
        return {"conclusion": "", "reasons": [], "markdown": raw, "structured": False}
    return {"conclusion": conclusion, "reasons": reasons,
            "markdown": compose_markdown(conclusion, reasons), "structured": True}


def compose_markdown(conclusion: str, reasons: List[str]) -> str:
    """结构化结论 → markdown（**老通道的契约**，内容与结构化字段同源）。

    ★ 首行故意写成 L2 的约定标记 `**结论**：…` ⇒ **即使消费方还没升级**（前端旧版、
      或直接读 markdown 的第三方），结论也天然置顶 ⇒ 新老两条通道看起来一致。
    """
    out = []
    if conclusion:
        out.append(f"**结论**：{conclusion}")
    if reasons:
        out.append("")
        out.extend(f"- {r}" for r in reasons)
    return "\n".join(out).strip()


def section_first_paragraph(md: str, title: str, max_len: int = CONC_MAX) -> str:
    """从 markdown 里取 `## <title>` 小节的**第一段**（到空行为止）→ 当「结论」用。

    ★ 只用于**我们自己代码写死的标题**（如日报的 `## 今日主线` —— 见
      `daily_report._render_llm_markdown` 的 `add("## 今日主线")`）。
      **这不是"猜模型把结论放哪"**：标题是我们机械生成的，内容才是模型写的；
      若将来标题文案改动，这里会**取不到 ⇒ 返回 `''`**（前端只是不显示结论条，不报错）。

    纪律（与 `parse_insight` 一致）：只有标题没内容 ⇒ `''`；首段超 `max_len` ⇒ `''`
    （**丢弃而不是截断** —— 宁可没有结论条，也不替模型裁句子）。
    """
    text = str(md or "")
    if not text or not title:
        return ""
    pat = re.compile(r"^#{1,6}\s*" + re.escape(title.strip()) + r"\s*$", re.M)
    m = pat.search(text)
    if not m:
        return ""
    rest = text[m.end():].split("\n")
    buf: List[str] = []
    for ln in rest:
        s = ln.strip()
        if not s:
            if buf:
                break                    # 第一段结束
            continue                     # 跳过标题后的空行
        if s.startswith("#"):
            break                        # 撞到下一节标题
        buf.append(s)
    para = " ".join(buf).strip()
    return para if 0 < len(para) <= max_len else ""


def reasons_from_json(v) -> List[str]:
    """落库的 `reasons`（JSON 数组字符串）→ `list`；坏数据返回 `[]`（**读侧绝不抛**）。

    落库选 TEXT 存 JSON 数组（与项目既有的 `seats_json` / `dimensions_json` 同风格）；
    旧行该列为 NULL ⇒ 返回 `[]` ⇒ 前端退化为"只有结论条"，不会报错。
    """
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    try:
        out = json.loads(v)
    except Exception:
        return []
    return [str(x) for x in out] if isinstance(out, list) else []
