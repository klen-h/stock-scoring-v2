# macro.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】宏观数据源 + 规则引擎（全球+国内面板 → 标签 → 今日方向分）

数据来源：新浪财经公开行情接口（hq.sinajs.cn），一次请求抓全部品种。
全球：布伦特/WTI原油、COMEX金银铜、美债2Y/10Y/30Y、纳指期货、日经、恒生科技、
美元指数、离岸人民币、VIX
国内：富时A50期货、中国10年期国债、在岸人民币、螺纹钢/铁矿石主连（黑色系）
（以上国内代码均经实抓验证）

规则引擎：把面板数据按「配置表」逐条评估，输出结构化标签 + 分组加权方向分。
设计原则（详见规则表）：
- 每条规则 = 一个可说清传导逻辑的假设
- 阈值优先用变化率（自我归一），少数用水平值（VIX 等有真实分界线）
- 三态输出（偏多/中性/偏空），留中性带防噪声
- 规则是数据不是代码：可调、可版本化、可归因

## 输入 / 输出

- 规则引擎：把面板数据按「配置表」逐条评估，输出结构化标签 + 分组加权方向分。
- - 三态输出（偏多/中性/偏空），留中性带防噪声
- - 规则输出叫「参考标签」，回测校准后才配叫「信号」

## 依赖谁（本项目内 import）

- `app`
- `app.eastmoney`
- `app.flash`
- `app.flash.margin_sentiment`
- `app.flash.rules`
- `app.routers.market`

## 被谁依赖

- `backend/app/backtest/market_regime.py`
- `backend/app/coach/rules.py`
- `backend/app/daily_report.py`
- `backend/app/flash/llm.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash/service.py`
- `backend/app/portfolio_radar.py`
- `backend/app/routers/macro.py`
- `backend/app/signals/tracker.py`
- `backend/app/trader_brief.py`
- `scripts/verify_northbound_fix.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_f`, `_s`, `_fetch_sina`, `_parse`, `_third_friday`, `_front_month_codes`, `_basis_codes`, `get_index_futures_basis`, `get_macro_panel`, `_resolve_metric`, `_hit`, `evaluate_rules`, `_pnum`, `_overnight_base`, `get_overnight_change`, `_safe_overnight`, `_safe_sentiment`, `get_macro_snapshot`

## 已知坑

- 宏观面板 + direction 早盘锁定（08:55-13:00 一次当日固定）；P0-1 阈值按分位重标定（PLAN_RESERVE_SIGNALS §6.1）。

规模：926 行。
