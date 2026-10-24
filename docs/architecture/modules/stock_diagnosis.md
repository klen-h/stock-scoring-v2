# stock_diagnosis.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】个股「一页纸诊断」—— 把既有**结构化事实**聚合成可读结论（P1，2026-10-11）
【为什么有它】
详情页上躺着十几个标签（评分/主力阶段/连续净流入/闸门就绪/龙虎榜风险/财报断层…），
但它们是**散的**：用户要自己拼结论。外部开源项目（PanWatch 的"多分析师→决策书"、
daily_stock_analysis 的"决策仪表盘"）证明这种"合成呈现"很被需要。
本项目已有全部原料 ⇒ 本模块只做**聚合与表述**。

【三条硬约束（本模块存在的意义，别放宽）】
C1 **LLM 只做聚合与表述，不产生新信号**：prompt 里只喂本模块取到的**编号事实**，
明令不得引入外部知识/不得预测价格/不得给买卖建议；输出**逐句可回溯到事实编号**。
C2 **无 LLM 也完整可用**：`facts` 永远返回（页面靠自己就能读）；`narrative` 只是补充。
LLM 未配置

## 输入 / 输出

- 明令不得引入外部知识/不得预测价格/不得给买卖建议；输出**逐句可回溯到事实编号**。
- C2 **无 LLM 也完整可用**：`facts` 永远返回（页面靠自己就能读）；`narrative` 只是补充。

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.llm`
- `app.insight`
- `app.mainforce`
- `app.mainforce.l3_history`
- `app.mainforce.lhb`
- `app.mainforce.state`

## 被谁依赖

- `backend/app/routers/scoring.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_f`, `build_facts`, `_render_facts`, `_sanitize`, `diagnose`, `clean_question`, `ask`

## 已知坑

- 个股一页纸诊断：路径必须两段 /score/diag/{symbol}（单段会被通配吞掉）；事实先渲染、每条带 source+as_of、缺失如实披露；AI 解读按需点按钮（12h 缓存）。

规模：446 行。
