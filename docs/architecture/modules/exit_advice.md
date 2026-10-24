# exit_advice.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】统一出场建议（「什么时候卖」的**单一事实源**）
为什么需要（2026-09-27 用户旅程盘点发现）：
项目此前有**三套互不相通**的"什么时候卖"口径：
① `strategies/exit_alert.py`      止损 / 支撑 / RSI / 放量   → 只在评分榜 Top tab
与战法观察池出现（**选股页**）
② `portfolio_radar.py`            浮亏 / 浮盈 / 主力 / 闸门   → 工作台「持仓预案」
③ 前端 `usePortfolio.evaluateAlerts`  −8% / +30% / 移动止盈 / 评分 → 「我的持仓」页
⇒ 同一只持仓，在「我的持仓」说"减仓"、在工作台说"持有"，用户无法判断信谁。

本模块

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.strategies.exit_alert`
- `app.tencent`

## 被谁依赖

- `backend/app/routers/user.py`

## 关键阈值 / 环境变量

- `EXIT_ADVICE`

## 顶层结构（def/class，前 25 个）

`_enabled`, `_tech_alert`, `_trailing_from_kline`, `advise_for_item`, `_warming`, `build`

## 已知坑

- 退出建议观察器：表述遵守"观察不指令"红线。

规模：280 行。
