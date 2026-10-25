# insight.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】「结构化观点」的唯一取用与校验口（L3，2026-10-11）
【要解决什么】
LLM 的观点输出此前是**一整块 markdown**：前端只能"读文本猜结构"（L2 用显式标记
`**结论**：` 缓解，但仍要求模型把标记写在第一行）；长度/条数/越界词也只能在 prompt 里
"求"它遵守，无法机械约束。
⇒ 本模块把口径改成**项目既有的成熟做法**（与 `daily_report` / `contradictions/report` /
`routers/stock` 的异动分析同源）：**LLM 出 JSON → 代码逐字段校验 → 由代码组装 markdown**。
于是「结论/依据」成为**数据**而不是"文本里的位置"，前端可以按角色渲染
（结论条 + 依据列表），而不是靠解析第一行。

【三级降级 —— **这是本模块能被安全上线的唯一理由**】
① **结构化**：LLM 给了合法 JSON ⇒

## 输入 / 输出

- LLM 的观点输出此前是**一整块 markdown**：前端只能"读文本猜结构"（L2 用显式标记

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/app/observer_review.py`
- `backend/app/routers/daily_report.py`
- `backend/app/routers/flash.py`
- `backend/app/stock_diagnosis.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_one_line`, `parse_insight`, `compose_markdown`, `section_first_paragraph`, `reasons_from_json`

## 已知坑

- 结构化结论唯一解析入口 parse_insight（JSON → 逐字段校验 → {conclusion, reasons[]}）；结论 >120 字丢弃不截断；越界词整段失败；解析失败 = 原文照旧渲染（structured=False）。

规模：160 行。
