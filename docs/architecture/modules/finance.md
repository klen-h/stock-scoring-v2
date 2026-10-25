# finance.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】A股财务数据（东财 F10 主要财务指标）

给评分引擎的「成长」和「质量」因子提供数据源。这两个因子此前完全缺失——
routers/stock.py 里明说"没有独立的财务数据源，只用了实时行情里的 PE/PB/市值"。

数据源：datacenter-web.eastmoney.com
★ 与 push2.eastmoney.com（实时行情）是不同域名。实测后者被反爬封禁时，
本接口依然可用 —— 财务数据这块不受行情域名封禁影响。

为什么不用 AKShare：只要营收增速/利润增速/ROE/负债率等几个字段，
AKShare 会引入 pandas + 几十个间接依赖，对这点需求性价比太低。

★ 三个必须处理的坑（都是实测踩出来的）：

1. 结果混有非 A 股：不过滤是 13085 条，加 SECURITY_TYPE_CODE="058001001"
后 6236 条。不做过滤会把港股/B股数据写进 A 股因子。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/scoring.py`
- `backend/app/routers/stock.py`
- `backend/app/scoring/live_ranking.py`
- `backend/tmp_archive/tmp_align_test.py`
- `backend/tmp_archive/tmp_batch_test.py`
- `backend/tmp_archive/tmp_fin_test.py`
- `backend/tmp_archive/tmp_gen_input.py`
- `backend/tmp_archive/tmp_kline_test.py`
- `scripts/shadow_decay_ranking.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_now_iso`, `_num`, `_date`, `_fetch_page`, `fetch_report`, `latest_report_date`, `_prev_report`, `_save_rows`, `refresh`, `has_report`, `get_finance`, `get_finance_asof`, `clear_finance_cache`, `get_finance_batch`, `get_history`, `stats`

## 已知坑

- get_finance_batch 按 code 粒度缓存、TTL 24h（财报季度更新）；refresh 后必须 clear_finance_cache。

规模：369 行。
