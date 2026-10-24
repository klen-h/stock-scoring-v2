# flash_brief.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.database`
- `app.eastmoney`
- `app.flash`
- `app.flash.llm`
- `app.flash.rules`
- `app.routers.stock`
- `app.tencent`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `FLASH_BRIEF_AUTO_TTL_MIN`、`FLASH_BRIEF_COOLDOWN_MIN`、`FLASH_BRIEF_DAILY_MAX`、`FLASH_BRIEF_IDX_TRIGGER`、`FLASH_BRIEF_NEWS`、`FLASH_BRIEF_NEWS_TRIGGER`、`FLASH_BRIEF_RATIO_HIGH`、`FLASH_BRIEF_RATIO_LOW`、`FLASH_BRIEF_SCAN`、`FLASH_BRIEF_TTL_MIN`

## 顶层结构（def/class，前 25 个）

`_ensure`, `_now`, `_today`, `_num`, `_r2`, `_stocks`, `_market_section`, `_sector_section`, `_movers_section`, `_score_news`, `_news_section`, `_template_gist`, `_template_direction`, `build_snapshot`, `_user_prompt`, `_fallback_llm`, `_parse_json`, `analyze`, `load`, `_daily_auto_count`, `detect_trigger`, `maybe_auto_run`, `mark_notified`, `recent_notifications`

## 已知坑

- 一次批量 LLM（conclusion + news[]）；按日缓存 + sig 指纹；GET /flash/brief 只读、POST refresh 才烧 LLM；盘中 brief_loop 触发（冷却 30min、日上限 FLASH_BRIEF_DAILY_MAX）；早产缓存防护：candidates_date<今日视为过期。

规模：676 行。
