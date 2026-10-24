# database.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据库核心模块

支持两种数据库：
- SQLite（默认）：本地开发，数据文件存在 backend/data/app.db
- PostgreSQL：生产部署（Supabase / Neon 等云数据库）

通过环境变量 DATABASE_URL 切换：
- 不设置 / sqlite:///...  → SQLite
- postgresql://user:pass@host/db  → PostgreSQL

使用方式：
from app.database import db
rows = db.fetch("SELECT * FROM flash_news ORDER BY time DESC LIMIT 10")
db.execute("INSERT INTO flash_news (...) VALUES (...)", params)

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- （无项目内依赖）

## 被谁依赖

- `backend/_tmp_ml.py`
- `backend/app/ai_basket_watch.py`
- `backend/app/backtest/data.py`
- `backend/app/backtest/market_regime.py`
- `backend/app/backtest/regime_review.py`
- `backend/app/backtest/report_store.py`
- `backend/app/backtest/strategies.py`
- `backend/app/benchmarks.py`
- `backend/app/coach/attribution.py`
- `backend/app/coach/audit.py`
- `backend/app/coach/explainer.py`
- `backend/app/coach/position_sizing.py`
- `backend/app/coach/regime_alert.py`
- `backend/app/coach/rules.py`
- `backend/app/contradictions/l3_scanner.py`

## 关键阈值 / 环境变量

- `DATABASE_URL`

## 顶层结构（def/class，前 25 个）

`Database`

## 已知坑

- PG/SQLite 双库兼容：占位符统一 %s 自动转换；JSON 列存对象后别再 loads；Decimal 显式 float；NULLS LAST 是 PG 专有；SQL 字面 % 必须写 %%；INSERT OR REPLACE 是整行替换（缺列清空，先读合并）。未配 DATABASE_URL 退 SQLite，行为不同别假设。

规模：342 行。
