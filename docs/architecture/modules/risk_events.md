# risk_events.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】风险事件闸门（框架 C1）—— 解禁/减持/质押/问询 ⇒ 命中即"只减不加"

【为什么需要】（《项目专业审视》C2 原话）
"强势股最典型的死亡路径：**大额解禁 / 股东减持公告 / 质押平仓风险 / 商誉减值 /
立案调查**。系统目前对这些**零感知** —— 技术面会继续给高分直到崩塌。"
其中**解禁是唯一可前瞻的**（日期提前数月公开）⇒ 先做它，优先级最高。

【本轮实现范围】只做**限售解禁**（数据源已验证可用）：
· 减持/问询/立案：暂无结构化数据源（新闻维度可识别，但滞后）⇒ 见文件末尾"未做"。
· 商誉/净现比：已在 `portfolio_radar._fundamental_flags` 与 `contradictions.l3_scanner` 覆盖。

【★★ 阈值依据 —— 实测回测，不是拍脑袋】（2026-09-25，脚本见当日 daily 记录）
方法：过去 12 个月 1778 条

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.eastmoney`
- `app.flash.rules`
- `app.tencent`

## 被谁依赖

- `backend/app/trader_brief.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_ensure_table`, `sync_lift_calendar`, `upcoming_risks`

## 已知坑

- 风险事件。

规模：174 行。
