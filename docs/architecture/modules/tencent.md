# tencent.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】腾讯行情数据源 + 内存缓存层

整个后端的"数据来源"。它做的事：
1. 从腾讯财经公开接口（qt.gtimg.cn）抓取 A股实时行情、K线数据
2. 把全量 A股行情缓存到内存，供 /api/score/batch 等批量接口使用
3. 对外暴露函数（get_stock / get_kline / search_stocks 等）供路由调用

数据源说明：
- 腾讯的接口返回的是一段文本（不是标准 JSON），形如：
v_sz000001="51~平安银行~000001~10.5~10.3~..."
每个字段用 ~ 分隔，第 N 个字段对应固定含义（价格、成交量、PE 等）。
所以代码里有大量 data[3]、data[37] 这种"按下标取字段"的操作。

## 输入 / 输出

- - 腾讯的接口返回的是一段文本（不是标准 JSON），形如：

## 依赖谁（本项目内 import）

- `app.flash`
- `app.flash.rules`
- `app.scoring.kline_cache`

## 被谁依赖

- `backend/app/coach/position_sizing.py`
- `backend/app/contradictions/scanner.py`
- `backend/app/daily_report.py`
- `backend/app/eastmoney_news.py`
- `backend/app/exit_advice.py`
- `backend/app/flash/intraday_alerts.py`
- `backend/app/flash/llm.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash_brief.py`
- `backend/app/main.py`
- `backend/app/market_amount.py`
- `backend/app/portfolio_radar.py`
- `backend/app/risk_events.py`
- `backend/app/routers/market.py`
- `backend/app/routers/scoring.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_is_disabled`, `_is_st_stock`, `_build_stock_pool`, `_parse_ts`, `_is_trading_hours`, `_is_stale_kline`, `_load_kline_cache`, `_save_kline_cache`, `_fetch_tencent`, `refresh_all_stocks`, `_record_speed`, `speed_pct`, `_price_date_now`, `save_market_snapshot`, `restore_market_snapshot`, `sync_snapshot_if_newer`, `get_stock`, `get_stocks_batch`, `get_index`, `get_kline`, `search_stocks`

## 已知坑

- 快照字段下标以 MEMORY 实测为准勿按文档抄：[3]现价 [4]昨收 [32]涨跌幅 [47]涨停价 [48]跌停价；_cache 显示时点用 data_ts+from_snapshot，绝不用 last_update（=填充时刻）；_tencent_cache_key 只认裸 6 位码；web.ifzq.gtimg.cn 是滑动窗口配额（WAF），退避按"两次 501 间隔"加码。

规模：834 行。
