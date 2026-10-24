# policy_bottom_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.contradictions.scanner`
- `app.database`
- `app.flash.rules`

## 被谁依赖

- `backend/test_policy_bottom_watch.py`
- `scripts/pbw_replay.py`

## 关键阈值 / 环境变量

- `PBW_AMOUNT_DAYS`、`PBW_AMOUNT_YI`、`PBW_BANK_IDX`、`PBW_BRENT`、`PBW_CATCHDOWN_DAYS`、`PBW_CATCHDOWN_LAG`、`PBW_MKT_IDX`、`PBW_PASS`、`PBW_POSITION_CAP`、`PBW_RATIO`、`PBW_RATIO_DAYS`、`PBW_US10Y`

## 顶层结构（def/class，前 25 个）

`_default_day`, `ensure_table`, `_num`, `_amount_series`, `_breadth_series`, `_chinext_weekly`, `_bank_catchdown`, `_parse_macro_date`, `_macro_snapshot`, `_manual_of`, `nation_hint`, `etf_hint`, `evaluate`, `_prev_checked`, `save`, `load`, `confirm_nation_team`, `render_markdown`, `run_daily`

## 已知坑

- 纯观察器零决策链；五条判据阈值 env 可调；第①条三层证据（公告提示不打勾 + ETF 份额连续 2 日 >=8% 自动打勾 + 人工覆盖优先）；缺失标 insufficient（缺失≠不满足）；2024-09-24/2025-04-08 无历史源不可回放。

规模：774 行。
