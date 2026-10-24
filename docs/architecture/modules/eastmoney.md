# eastmoney.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】东方财富数据源 + 内存缓存层（板块 / 资金流）

整个后端的「第二数据源」，与 tencent.py 并列。专门负责板块和资金流数据：
- 行业板块 / 概念板块（列表、资金流排名）
- 个股主力资金流向
- 北向资金（沪深港通当日净流入）

数据来源：东方财富 push2.eastmoney.com 公开 JSON 接口。
这组接口非官方，但已稳定运行多年，也是 AkShare / Tushare 的底层源。
字段码（f12/f62/f184 等）东方财富偶有调整，所以下面解析全程防御式：
任何字段缺失/类型异常都兜底为 0 或空，绝不让单行异常拖垮整批数据。

对外函数（路由层调用，均带缓存 + 降级）：
get_sectors(kind)            行业/概念板块列表
get_sector_flow(kind)        板块资金流排名（按主力净流入降序）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.flash.rules`
- `app.zzshare_client`

## 被谁依赖

- `backend/app/contradictions/scanner.py`
- `backend/app/daily_report.py`
- `backend/app/flash/llm.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash_brief.py`
- `backend/app/macro.py`
- `backend/app/risk_events.py`
- `backend/app/routers/capital.py`
- `backend/app/routers/market.py`
- `backend/app/routers/sector.py`
- `backend/app/sector_industry.py`
- `backend/tmp_archive/tmp_sector_probe.py`
- `scripts/probe_northbound.py`
- `scripts/verify_northbound_fix.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_to_float`, `_to_int`, `_get_cached`, `_fetch_clist`, `_fetch_clist_paged`, `get_sectors`, `_parse_flow`, `get_sector_flow`, `get_stock_flow`, `get_northbound`, `get_lift_stage`, `_zzshare_sector_list`, `_sina_get`, `_sina_sector_list`, `_sina_sector_flow`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：686 行。
