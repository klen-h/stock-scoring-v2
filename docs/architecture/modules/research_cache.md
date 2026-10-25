# research_cache.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】研究脚本的本地数据缓存（避免每次跑脚本都整表拉远程库）
背景（2026-09-12，egress 治理第三步）：
`scripts/mainforce_factor_backtest.py` 的 `load_ohlc_all()` + `load_flow_map()`、
`scripts/strategy_mainforce_filter_test.py` 的逐股 K 线、`scripts/factor_analysis.py`
的批量日线——每次运行都要把 backtest_prices（48.9 万行 / 82MB 表）与
mainflow_history（8.4 万行 / 21MB 表）整表过一遍网。pg_stat_statements 里
"返回 40 万行/次"的那几条就是这么来的（21 天累计 ~90MB/次 × 4 次）。
而这类数据是"历史冻结、只追加"的，本机跑研究完全没必要每次回源。

做法：落到本机 SQ

## 输入 / 输出

- "返回 40 万行/次"的那几条就是这么来的（21 天累计 ~90MB/次 × 4 次）。

## 依赖谁（本项目内 import）

- `app`
- `app.database`
- `app.mainforce.flow`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_conn`, `_meta_get`, `_meta_set`, `_fresh`, `_log`, `_pack_bars`, `ohlc_all`, `ohlc_for`, `upsert_bars`, `flow_map`, `stats`

## 已知坑

- ohlc_for/flow_map 带 24h 缓存与新鲜度守卫；bars 写回有字节上限，超限整体清空（形同虚设的坑已修：按日期增量补 _top_up_bars 常态约 1k 行）。

规模：322 行。
