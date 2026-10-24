# portfolio_radar.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】持仓雷达 —— 以**用户持仓**为锚的状态聚合（2026-09-23 新增）

为什么需要（用户 2026-09-23 提出）：
系统此前所有"看板"都以**全市场**为主语（榜单/观察池/矛盾/盘中警示），而持仓只在
LLM prompt 与教练止损里出现 ⇒ 用户看到的是「156 只候选」，却答不出「我手里那三只
怎么样了」。本模块把散落在各处的状态**按持仓聚合**成一个视图。

★ 定位（沿用 `trade_gate.summarize` 的纪律，避免误用）：
这是**状态展示 + 提示聚合**，不是买卖信号 —— 不参与排序、不改评分、不新增网络请求。
每条 alert 都指向某个**已有模块的既有结论**（闸门就绪 / 主力阶段 / 战法 / 矛盾 /
涨跌），不引入新的判据（防止出现"第五套口径"）。

数据来源（**全部复用既有单一事实源**，不重算）：
· 持仓         `user_portfol

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.contradictions.store`
- `app.database`
- `app.flash.rules`
- `app.macro`
- `app.mainforce`
- `app.mainforce.state`
- `app.portfolio_scope`
- `app.routers.scoring`
- `app.tencent`

## 被谁依赖

- `backend/app/trader_brief.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`invalidate_holdings`, `_holdings`, `_bj_ts_str`, `_tq_cache`, `_quotes`, `_watch_map`, `_latest_rank`, `_contradiction_context`, `_fundamental_flags`, `is_financial`, `_alerts_for`, `_warming_placeholder`, `build`, `_cap_band`, `_portfolio_risk`, `_build_impl`

## 已知坑

- 持仓雷达：主力行为 × L3 断层 × 持仓交叉；浮盈/浮亏差异化表述；观察器不进决策链。

规模：678 行。
