# limit_stats.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】涨跌停家数的**唯一口径**（单票判定 + 家数统计）
【为什么必须收拢（2026-10-09 事故）】
盘中误推「跌停 123 只 / 黑天鹅熔断」，而当日**收盘真值仅 11~13 只**、且是
96 涨停的**上涨日**。根因是同一个"跌停家数"在系统里有多份实现、三套阈值：
· `flash/intraday_alerts` 用 `change_pct <= -9.7` 固定阈值 ⇒ 创业板/科创板（±20%）
跌 10%~13% 被当成跌停（早盘普跌 826涨/4044跌 时把计数放大到 123）——**事故主因**；
· `coach/rules` 用 `-19.9 / -9.9` 两档 ⇒ 漏**北交所 30cm**、漏 **ST 5%**；
· `backtest/market_regime`（→ E2 事件信号）用 `>=9.9 / <=-9.9` 一刀切 ⇒ 20cm 被高估。
⇒ 本

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.engine`
- `app.routers.market`

## 被谁依赖

- `backend/app/routers/market.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_limit_pct`, `_break_tol`, `is_limit_up`, `is_limit_down`, `counts_from_quotes`, `counts_best`

## 已知坑

- 涨跌停家数唯一口径：rt_k 精确优先（is_intraday 且 trading>=3500 才可用）→ 内存真值限价（涨停 0.2% 缓冲、跌停严格）→ 板幅+0.3pt 容差；新股首 5 日/退整期 limit_*=0 ⇒ 不计数；只读缓存绝不抓取。

规模：159 行。
