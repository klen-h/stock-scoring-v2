# spring_festival.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】春节「日历例外」—— 防御市里唯一被验证过的可用窗口（P2-a 落地）

【为什么有这个东西】
项目长期结论：**regime 只看沪深300 均线 => 在 V 型反转处系统性滞后**，
而 E2/E3 等宽度事件要么极稀有（2026 年 0 次）、要么无 edge（E3 WEAK）。
=> 用户在 2026-09-16（反弹第一天）问"什么情况下会买"，答案是"三条路径全失效"。

用户随后提出方向：**事件驱动 / 提前预案**。查证发现——
· `flash_calendar`（金十）只存 15 天窗口，**无历史积累** => 「财经日历事件 vs 反弹日」
的相关性**无法回溯验证**；
· 事件"利好/利空"方向**无客观映射**（同一 FOMC 随预期差反向）；
· 系统**没有任何"事件前布局"机制**（`risk_events` 只做负面避险）。
=> 退化为**日期确定的固定

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.flash`
- `app.flash.rules`

## 被谁依赖

- `backend/app/trader_brief.py`
- `scripts/test_decision_card_sf.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_d`, `_iso`, `_festival`, `_rules_holiday_range`, `_calendar_holidays`, `_all_holidays`, `_longest_gap`, `_is_td`, `_prev_td`, `_next_td`, `resolve_window`, `_td_span`, `window`, `calendar_exception`, `rules`

## 已知坑

- 春节效应：calendar_exception 独立字段；overrides 仅在 regime∈{defensive, neutral_bearish} 给出；不改 trade_gate.evaluate/REGIME_ALLOWED/REGIME_POSITION；定位长假用「当月最长休市缺口」不硬编码。

规模：470 行。
