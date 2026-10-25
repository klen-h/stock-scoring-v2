# memory_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】进程内存看护：采样持久化（跨重启）+ 定时巡检 + 阈值企微告警
背景（2026-09-23）：Render **500MB** 实例反复 OOM。监控图显示内存呈**锯齿状** ——
从 ~10% 缓慢爬升到 80~90% → 骤降（进程被杀 + 自动重启），48 小时内多次；CPU 长期 <5%
⇒ 不是算力不足，是内存；且形态是「几小时累积爬升」而非瞬时尖峰。

`GET /api/system/memory`（`routers/system.py`）能回答「谁在占内存 / 谁在涨」，但它的 diff
基准存在**进程内存**里 ⇒ **一 OOM 重启就清零，而那一刻恰恰最需要对比**（重启前的最后
状态 vs 重启后）。本模块补上这一点：

· **采样落库**（`memory_probe` 表）⇒ diff 基准取**库里上一条** ⇒ **跨重启连续**
· **定时巡检**：每 `SAMPLE_INTERVAL_MIN` 分钟采

## 输入 / 输出

- · **采样落库**（`memory_probe` 表）⇒ diff 基准取**库里上一条** ⇒ **跨重启连续**

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash`
- `app.flash.rules`
- `app.flash.wechat`
- `app.routers.system`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `RENDER_INSTANCE_ID`

## 顶层结构（def/class，前 25 个）

`_ensure_table`, `_to_float`, `_fmt_key`, `_row_out`, `latest`, `history`, `record_sample`, `_maybe_alert`, `cleanup`, `periodic_loop`

## 已知坑

- Render free 500MB 内存观察；OOM 诊断先问"哪个请求一次性灌入"。

规模：283 行。
