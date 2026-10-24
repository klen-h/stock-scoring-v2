# reserve_monthly.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】央行储备月度面板：外汇储备 + 黄金储备（慢变量，**非交易信号**）

定位（见 `PLAN_RESERVE_SIGNALS.md` §6.2）：
“减美债、增黄金”是**月频结构变量**，不是日频择时信号。本模块只负责把它落成
**可追溯的月度事实**，供面板展示与长期研究引用；**不进决策链、不产生买卖信号**。

数据源：akshare `macro_china_foreign_exchange_gold()`（外管局/央行月度口径）。

⚠️ 单位**照抄源，不换算**（换算一次就容易与官方口径对不上——本项目踩过单位坑）：
· fx_reserve_100m_usd  外汇储备（**亿美元**，如 34002.51）
· gold_reserve_wan_oz  黄金储备（**万盎司**，如 7747）
展示层再换算：吨 = 万盎司 × 1e4 × 31.1035 ÷ 1e6。

⚠️ 已见**源缺口**：

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.database`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_f`, `ensure_table`, `bulk_upsert`, `gold_reserve_tonnes`, `load`, `series_with_share`, `stats`

## 已知坑

- 储备信号月度。

规模：145 行。
