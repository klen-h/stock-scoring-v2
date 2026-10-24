# pattern_launch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

「缩量调整 → 放量启动」形态 · 每日扫描 + 观察追踪（生产版）
2026-10-15 落地（用户需求：每日自动扫描符合走势形态的个股 + 醒目标注 + 持续观察 +
预留回测条件重设接口）。

★ 研究结论（`scripts/pattern_launch_check.py` / `pattern_launch_grid.py`，已过
基准口径修正 + 参数网格 + 成本/可执行性三关）：
· T+10 超额 +2.09pp（净 +1.80pp）、网格 12 组合全正且显著 ⇒ **稳健的正边际**；
· 但胜率 43~45%、中位收益为负、T+5 全负 ⇒ **正边际但不赚钱**；
· ⇒ 本功能定位 = **描述性观察工具（候选清单 + 后续追踪），不构成买卖建议**。

【数据源】`backtest_prices`（989 只回测池、每日更新到昨日）—— 唯一"每日更新"的日线源；
`zzshare_daily.db` 冻

## 输入 / 输出

- 基准口径修正 + 参数网格 + 成本/可执行性三关）：

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- `backend/app/sector_strength.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_tables`, `get_config`, `set_config`, `_params_hash`, `_now`, `load_bars`, `load_bars_bulk`, `all_codes`, `latest_trading_date`, `scan_stock`, `quality_score`, `_board`, `_industry`, `_name_industry`, `scan_latest`, `persist_scan`, `get_scan`, `watch_add`, `watch_update`, `watch_remove`, `update_watch_returns`, `get_watch`, `highlight_codes`

## 已知坑

- 扫描日不能用 MAX(date)（逐股回填当天可能只有 150 只）⇒ 取"近 5 日里覆盖 >=800 只"的最新一日；表 pattern_scan_daily/pattern_watch/pattern_config。

规模：518 行。
