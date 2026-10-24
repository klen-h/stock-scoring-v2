# intraday_path.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】指数**日内分时路径**归档 + 读取（P2-1，PLAN_RESERVE_SIGNALS §6.5）
【为什么必须归档（时钟属性，同 `market_amount` 的教训）】
腾讯分时**只有当日**（实测）⇒ 不落库 = 明天永远没有历史 ⇒ 日内研究无从谈起。
本项目已多次吃到"晚一天接入就永远少一天历史"的亏（`zzshare_daily` 文件头同款说明）。
P1-1 的结论里最缺的正是**日内拼图**（日线看不到"早盘杀跌 → 午后拉升"这种 V 型），
而"午盘前跌>1% → 午后拉升"这个命题**必须先有分时归档才可检验**。

【数据源与口径】
· 腾讯分时：`https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=<code>`
—— 与 `market_amount._fetch_intraday` **同一接口**（那边取 `parts[3]

## 输入 / 输出

- 【文件作用】指数**日内分时路径**归档 + 读取（P2-1，PLAN_RESERVE_SIGNALS §6.5）
- 腾讯分时**只有当日**（实测）⇒ 不落库 = 明天永远没有历史 ⇒ 日内研究无从谈起。

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- `scripts/daily_batch.py`
- `scripts/intraday_midday_reversal_check.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_fetch`, `_auto_day`, `save_day`, `load`, `coverage`, `days_for`

## 已知坑

- 指数日内分时路径归档；成交额/涨跌比历史起点 2026-10-09 / 2026-09-25（更早 insufficient）。

规模：212 行。
