# zzshare_daily.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】zzshare 每日数据积累（涨停生态 / 情绪套件快照，2026-09-21）
背景（`zzshare功能盘点与接入建议_20260920.md` §7 复测后的排期决策）：
zzshare 的补充功能分两类 —— **分析开发型**（情绪×战法交互回测 / 基本面 PIT 体检，
等 10 月体检收口后再做）与**数据积累型**（本模块）。
数据积累有时钟属性：**晚一天接入就永远少一天历史**，且每日落库本身就是
选型原则要求的「30 天影子验证」载体 ⇒ 先积累、后分析。

内容（每日一行 JSON，date 主键幂等）：
sentiment_trend(model=1)      情绪分时序列 —— ② 情绪×战法交互回测的核心原料
⚠️ model 是整数（README L432），字符串会 0 行
market_style(date)

## 输入 / 输出

- 数据积累有时钟属性：**晚一天接入就永远少一天历史**，且每日落库本身就是

## 依赖谁（本项目内 import）

- `app.database`
- `app.zzshare_client`

## 被谁依赖

- `backend/app/routers/market.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_slim_uplimit_hot`, `_slim_uplimit_reason`, `collect_daily`, `run_daily`, `load_range`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：187 行。
