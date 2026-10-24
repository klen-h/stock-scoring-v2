# health.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据源健康监控（成功率滚动窗口 + 连续失败告警 + 恢复通知）

监控 5 个外部数据源：金十快讯 / 金十财经日历 / 新浪宏观面板 / 腾讯ETF行情 / 东方财富。
各源在被调用处埋点（health.record），调度器另有探针循环保证空闲时段也定期采样。

告警规则：
- 连续失败 ≥3 次 → 产生告警事件（如"金十快讯连续失败，检查 Cookie"）
- 恢复成功且此前已告警 → 产生恢复事件
告警事件直接进 /api/flash/notifications，前端页面通知铃铛自动弹手机/桌面提醒
—— 这解决"金十 Cookie 过期后事件流悄悄死掉"的问题。

状态持久化：告警历史存 data/source_health.json（重启不丢），计数器内存即可。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.flash`
- `app.flash.rules`
- `app.flash.wechat`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `WECHAT_SOURCE_ALERTS`

## 顶层结构（def/class，前 25 个）

`_now`, `_load_alerts`, `_save_alerts`, `record`, `_push_wechat_safely`, `get_health`, `recent_alerts`

## 已知坑

- /api/health 的 build = RENDER_GIT_COMMIT 前 7 位，排查线上异常第一步对准 git rev-parse HEAD；switches_mismatch 检查 MAINFORCE_MODE 等配置漂移。

规模：221 行。
