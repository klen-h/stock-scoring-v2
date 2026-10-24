# sync_meta.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据集版本号（sync_meta 表）——给"每天只变一次"的远端数据做版本门控
要解决的问题（2026-09-12，egress 治理第二步）：
`mainflow_history` 的整表读是 8.7MB/次，原先只靠 30 分钟 TTL 兜着：
· 资金流每天 19:00 回填一次 → TTL 到期后的重拉纯属白烧 egress（4.7 次/天）；
· 生产方（GitHub Actions 日批）与消费方（Render 常驻进程）是两个进程，
内存缓存互不可见，只能靠"猜时间"，既浪费又可能读到旧数据。

机制（比 TTL 更省也更准）：
· 生产方写完数据 → `touch(key)` 打一个版本号（就用 updated_at）
· 消费方缓存数据 + 记住当时的版本号；TTL 到期时**先查版本号**（十几字节的一行）
- 版本没变 → 直接续期，不重拉（数据没变 = 0 流量）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_ensure_table`, `touch`, `version`, `versions`

## 已知坑

- 同步元数据（tencent 快照恢复 sync_snapshot_if_newer 配套：非交易时段必须有"库里有没有更新快照"路径）。

规模：96 行。
