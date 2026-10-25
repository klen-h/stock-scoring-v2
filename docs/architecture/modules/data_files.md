# data_files.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】运行时数据清单 + 完整性检查（丢了/烂了要预警，不再静默蒸发）
背景（2026-09-12）：
为了对抗 Render 免费实例"每次部署清空文件系统"，历史上做了「浏览器镜像」兜底：
前端每 5 分钟把 backend/data/*.json 拉到 localStorage，服务端清零后自动回传。
但数据这些年陆续迁进了数据库，镜像清单里那 9 个文件**全部变成空壳**（flash /
analyses / reviews / tracking / macro_history / etf_close / flash_state /
schedule_state / strategies）——于是镜像只剩代价（每次为了取 len() 把 50 条
诊断正文读出来，实测 40MB/天 Supabase egress），不产生任何保护 → 已退役。

本模块接管"盯着点"的职责，清单按**数据真实存储位置**划分（2026-

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash`
- `app.flash.wechat`
- `app.wechat_fmt`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `DATA_SOURCE`、`RENDER_INSTANCE_ID`

## 顶层结构（def/class，前 25 个）

`_ensure_wm`, `_wm_load`, `_wm_touch`, `_parse_dt`, `_specs`, `_check_db_calendar`, `_check_db_llm_usage`, `_check_one`, `_pack_detail`, `check_all`, `_alert_once_per_day`, `_notify`, `startup_check`, `periodic_loop`

## 已知坑

- 本地资产盘点用 os.walk（data/zzshare_daily.db 4.2GB 等在工作区根，search_file 看不到）。

规模：297 行。
