# edge_verify.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】预登记验证脚本的**月度自动复核**（2026-09-30，P1 收尾）
【为什么需要（本轮查出来的真缺口）】
项目有若干**预登记**验证脚本，判定标准都写死在各自脚本头：
· `scripts/gate_ready_backtest.py`      —— 闸门 `ready==3`（**唯一买入入口**）联合期望值
· `scripts/sector_momentum_edge_check.py` —— 板块动量延续性（因子层）
· `scripts/event_live_review.py`        —— E2 实盘样本复核（能否先行解除 defensive）
· `scripts/reversal_composite_edge_check.py` —— 反弹 vs 反转组合（★ 2026-10-10 P1-1 新增）
2026-09-30 全仓 grep 发现：**这些脚本当时没有任何调用方**

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.backtest`
- `app.flash`
- `app.flash.rules`

## 被谁依赖

- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_parse`, `run_script`, `_verdict_cn`, `_progress`, `format_markdown`, `_save`, `run_monthly`

## 已知坑

- 月度信号复检注册表 SCRIPTS：脚本须支持 --json（末行 {"script","verdict",...}）；mf_edge_check 刻意不注册（依赖本地 4.2GB 库，线上会报假 INSUFFICIENT）；LHB 两脚本在线上走 pack 备源口径（月度企微里的龙虎榜结论≠428 日定案口径）。

规模：245 行。
