# events/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

事件驱动信号源（event-driven signal）。

唯一通过预登记检验的事件：E2 政策脉冲（极端普涨 + 涨停骤增）。
详见 app/events/signal.py 头部说明。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.backtest.market_regime`
- `app.database`
- `app.events.signal`
- `app.flash`
- `app.flash.rules`

## 被谁依赖

- `backend/app/backtest/market_regime.py`
- `backend/app/daily_report.py`
- `backend/app/flash/scheduler.py`
- `backend/app/routers/market.py`
- `scripts/event_live_review.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_build`, `run_alert`, `_ensure_table`, `detect_events`, `get_event_signal`, `record_event_snapshot`, `event_summary_line`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：474 行。
