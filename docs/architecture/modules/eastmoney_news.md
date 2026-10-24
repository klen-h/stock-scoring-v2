# eastmoney_news.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】东方财富新闻数据源 + 内存缓存层（7×24 快讯 / 个股新闻）

后端的「消息面」数据源（阶段 1：东财快讯，不接财联社）。

数据来源（在线验证可用，2026-08）：
- 7×24 快讯：np-listapi.eastmoney.com/comm/web/getFastNewsList
每条快讯自带 stockList（关联标的，格式 "市场.代码"）：
"0.300308" = 深市A股 300308，"1.600519" = 沪市A股 600519，
"90.BK0xxx" = 板块，其余为港美股/债券/指数（忽略）。
- 个股搜索：search-api-web.eastmoney.com/search/jsonp（关键词=代码）
覆盖个股深度报道（业绩说明会/研报/公告解读，快讯里没有的内容）。
注意：① 必须用 http（https 返回错乱缓存响应）；② 响应是 JSONP 壳

## 输入 / 输出

- 注意：① 必须用 http（https 返回错乱缓存响应）；② 响应是 JSONP 壳

## 依赖谁（本项目内 import）

- `app.tencent`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/scoring.py`
- `backend/app/routers/stock.py`
- `scripts/generate_backend_pack.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_get_cached`, `_parse_a_stock_codes`, `_normalize`, `get_global_news`, `_strip_jsonp`, `_fetch_search_news`, `_dedup_similar`, `get_stock_news`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：273 行。
