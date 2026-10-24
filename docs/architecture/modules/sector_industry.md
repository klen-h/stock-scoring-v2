# sector_industry.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】板块数据基础设施：① 个股→行业映射 ② 板块每日快照

两部分共用同一套"东财数据源校验"逻辑（板块代码必须是 BK 前缀，否则视为降级新浪）。

① 映射（build_map）：个股属于哪个行业，含嵌套层级链
② 每日快照（take_snapshot）：每天记录全部板块的涨跌幅/涨跌家数/资金流，
积累板块历史序列，供板块分化、板块动量类因子使用。
成本极低——get_sectors + get_sector_flow 各一次请求就覆盖全部板块。

为什么需要：评分引擎要加"板块分化"因子，前提是知道每只股票属于哪个板块，
才能算"个股相对所属板块的强弱"。东财只提供"板块 → 成分股"的正向查询，
没有"个股 → 所属板块"的反查接口，所以这里遍历全部板块反向

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.eastmoney`
- `app.flash.rules`
- `app.industry_map`
- `app.tencent`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/mainforce/confluence.py`
- `backend/app/routers/sector.py`
- `backend/app/strategies/paper_trading.py`
- `backend/app/workbench_scan.py`
- `backend/tmp_archive/tmp_backfill.py`
- `backend/tmp_archive/tmp_cleanup.py`
- `backend/tmp_archive/tmp_full_run.py`
- `backend/tmp_archive/tmp_gap.py`
- `backend/tmp_archive/tmp_reclassify.py`
- `scripts/build_industry_map.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_now_iso`, `_sector_members`, `_fetch_sina_sectors`, `_fetch_sina_members`, `build_map_sina`, `_normalize_industry`, `cleanup_industry_residual`, `reclassify_others`, `build_map_full`, `backfill_missing`, `_main_site_available`, `build_map`, `_ensure_amount_table`, `_industry_map`, `industry_amount_share`, `save_amount_share`, `get_stock_industry`, `get_stocks_by_industry`, `get_all_codes`, `get_stats`, `_parse_row`, `take_snapshot`, `get_sector_history`, `get_date_snapshot`, `dispersion`

## 已知坑

- 行业聚合辅助。

规模：930 行。
