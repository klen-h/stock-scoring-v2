# news_sentiment.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】消息面情绪打分（关键词规则，不引 NLP/LLM）

把 eastmoney_news 拉来的快讯转成个股「新闻分」（-10 ~ +10）：

单条新闻分 = Σ(命中关键词权重)，clamp 到 [-2, +2]
标题含否定词（终止/取消/搁置…）→ 极性反转（"终止减持"不算利空）
个股新闻分 = Σ(单条分 × 时间衰减)，clamp 到 [-10, +10]
时间衰减 w = max(0, 1 - 小时数/72)：新闻只影响 3 天，越新权重越大

设计边界（阶段 1）：
- 只做规则打分：关键词误伤可预期、可调试；LLM 打分成本高且不可复现
- 独立维度：本模块产出的分数【不进入】综合总分（需阶段 3 回测验证后再议）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/scoring.py`
- `backend/app/routers/stock.py`
- `backend/tmp_archive/tmp_import_check.py`
- `scripts/generate_backend_pack.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_beijing_now`, `parse_news_time`, `decay_weight`, `score_news_item`, `_level`, `score_stock_news`

## 已知坑

- 搜索源严格关联（标题含代码/公司名）；72h 相似去重（阈值 0.55）；回购情境化（激励型 +1、注销 +2）；消息分独立展示不进综合分（IC 定版 FAIL 2026-10-18）。

规模：144 行。
