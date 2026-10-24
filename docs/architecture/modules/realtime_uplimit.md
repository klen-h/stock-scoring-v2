# realtime_uplimit.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】盘中实时涨停/跌停/炸板统计（zzshare rt_k 落地，2026-09-29）
背景（为什么需要它）：
现有涨停口径有三处已知局限，都写在各处注释里，本模块正是为补掉它们而生：
1. ~~`routers/market.overview` 的涨跌停家数用 `change_pct >= 9.9` 近似~~
**【已于 2026-09-30 修掉】** —— `overview`/`market_temperature` 已切
「rt_k 精确优先 + 按板幅回退 + `limit_src` 口径披露」（`routers/market._limit_counts_best`）；
**2026-10-10 起该判据进一步收拢为共享模块 `app/limit_stats.py`**
（`market_regime` / `coach/rules` / `flash/intraday_alerts` 一并接入

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.routers.market`
- `app.zzshare_client`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`peek`, `_bj_now`, `_f`, `_fetch_all`, `fetch_all`, `classify`, `compute`, `_safe_promotion`, `snapshot`, `refresh`

## 已知坑

- snapshot() 是 fail-open（单批失败保留已拿到的行）⇒ 会静默漏计；消费方必须配合 trading 数量守卫；盘前/休市给的是上一交易日收盘定稿（is_intraday=False）。

规模：291 行。
