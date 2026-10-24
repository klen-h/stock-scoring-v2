# news_history.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】消息面历史持久化（阶段 3 回测的数据基础）

每日盘后给「持仓股 + 评分 Top50」算一次新闻分并落库（news_history 表）。
积累 4~8 周后即可回测验证"消息分是否预测次日/5日收益"，决定是否进总分。

表结构（幂等，模块导入即建表，与 ranking_history 同款模式）：
(snap_date, code) 唯一 → 同一天可覆盖重写（快照补跑友好）。

对外函数：
record_news_snapshot(rows)     批量写入/覆盖某日快照
get_news_history(code, days)   读某只股票最近 N 天（升序，画走势图用）

## 输入 / 输出

- 每日盘后给「持仓股 + 评分 Top50」算一次新闻分并落库（news_history 表）。

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/stock.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`init_news_history_table`, `record_news_snapshot`, `get_news_history`

## 已知坑

- 消息分每日 15:20 快照持久化（news_history.snap_date）；消息分 IC 验证的数据底座。

规模：86 行。
