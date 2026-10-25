# cache_release.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】进程内存「主动释放」—— 把按需加载的大缓存还回去，避免 OOM 被杀重启
背景（2026-09-28 定位；用户："盘后是不是可以将一些不必要的缓存释放掉"）：
Render **500MB** 实例反复 OOM。`memory_probe` 跨重启采样把形态钉死了 ——
· 09-24 09:35  RSS 159MB（已知缓存 18MB）
· 09-24 09:44  RSS 384MB（已知缓存 **358MB**）← 10 分钟内跳升
· 09-24 13:35  RSS 409MB（81.8%）⇒ **企微告警**
· 09-24 19:05  RSS 420MB（83.9%，当日峰值）
· 09-24 19:31  RSS 105MB  ← **被杀重启**（缓存清零）
· 09-24 20:01  RSS 367MB  ← **半小时又填满**
⇒ ① 342MB 全在**已知模块

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.flash`
- `app.routers.system`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_clear_one`, `_rss_pct`, `release`, `_tick`, `periodic_loop`

## 已知坑

- 盘后 19:00 每日一次 + RSS>=75% 自适应（120min 防抖）；绝不释放 tencent._cache（收盘定稿）、flash.*、wechat._token_cache、signal_bus、sync_meta。

规模：216 行。
