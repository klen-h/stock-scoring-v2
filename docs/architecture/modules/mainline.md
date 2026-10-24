# mainline.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】行业主线/共振分析 —— 行业映射补齐（新浪全量）后的核心决策功能

数据源：
- ranking_history（每日评分 Top50 快照，rank_pos<=50）
- stock_industry（新浪全市场行业映射，Top50 命中率 84%）

核心产出：
1. 主线榜：连续多日在 Top50 扎堆的行业（占比 + 趋势 + 候选股）
2. 风格切换信号：行业占比相对窗口前半段突变（如 医药 6只→1.2只退出、
金融/有色接棒）——这是"静态榜单"看不到的资金切换信号

调度：scheduler.mainline_loop 每交易日 16:05 跑（ranking_history 已落库），
分析落库 industry_mainline 并推送企微日报。

## 输入 / 输出

- 调度：scheduler.mainline_loop 每交易日 16:05 跑（ranking_history 已落库），
- 分析落库 industry_mainline 并推送企微日报。

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`
- `app.flash.wechat`
- `app.industry_map`
- `app.scoring.ranking_history`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/sector.py`
- `backend/app/trader_brief.py`
- `backend/tmp_archive/tmp_mainline_test.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_load_bars_for_crowding`, `_crowding_flags`, `init_mainline_table`, `compute_mainline`, `get_mainline_summary`, `_latest_unknown`, `push_mainline_report`, `get_mainline_performance`, `_excess`

## 已知坑

- 评分 Top50 行业扎堆（≠板块涨跌幅）；与 industry_mainline 同源两表注意只渲染一处。

规模：467 行。
