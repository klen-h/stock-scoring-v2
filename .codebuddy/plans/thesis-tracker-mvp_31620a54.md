---
name: thesis-tracker-mvp
overview: 落地「宏观剧本追踪器（Thesis Tracker）」MVP：两张表（thesis_registry + thesis_events，含 backfilled/nature/resolution_source 三字段）+ 登记脚本 + 一条真假设（12/9 FOMC 加息），并配最小裁决器保证「登记→裁决」闭环成立。核心是事前登记可证伪假设、事后机器/人工裁决，用 backfilled 隔离回填样本、nature 区分命题性质，规避 SQLite/PostgreSQL 双库坑。
todos:
  - id: create-thesis-module
    content: 创建 thesis_tracker.py 核心模块（两张表+登记/修订/失效/证据/查询函数）
    status: completed
  - id: implement-resolver
    content: 实现裁决器 resolve_open_theses（auto/manual 条件评估+状态流转）
    status: completed
    dependencies:
      - create-thesis-module
  - id: create-cli
    content: 创建 thesis_cli.py（seed/register/revise/evidence/resolve/list 子命令）
    status: completed
    dependencies:
      - create-thesis-module
  - id: seed-data
    content: 登记种子数据（首条真假设+9/16 回填样本，验证幂等）
    status: completed
    dependencies:
      - create-cli
  - id: verify-dual-db
    content: 双库建表与卫生门验证（SQLite/PG 兼容+py_compile+code_hygiene_check）
    status: completed
    dependencies:
      - seed-data
---

## 产品概述

新增「宏观剧本追踪器（Thesis Tracker）」后端模块：把宏观判断从"人肉记忆"升级为"事前登记、事后裁决、全程留痕"的可追踪系统。本次落地 MVP，核心是两张表、一个登记/修订/裁决 CLI、以及一条真实假设样本。

## 核心功能

- **假设登记簿**：登记"离散、带 deadline、可裁决"的概率命题（如 12/9 FOMC 加息 25bp 且点阵图 ≤4.1%），记录 p_mine、p_market、deadline、resolution_rule、invalidation_early
- **修订留痕**：概率修订必须写原因与触发事件，历史修订 append-only 可回溯，不允许裸改
- **回填隔离**：已裁决样本（如 9/16 加息）打 backfilled 标记，永久排除在统计之外，只作流程演示
- **人工证据录入**：无自动采集通道的字段（如点阵图 2027 中值）由人工录入，强制携带原文出处链接
- **裁决器 v1**：deadline 到达后自动评估——auto 字段从 macro_daily 快照取值、manual 字段从证据流取值，缺证据时保持 open 并提示"待录入"，全部满足判 win，任一不满足判 lose

## 本次明确不做

证据采集日批任务、周报/决策卡、LLM 抽取层、校准曲线与折扣系数产出、前端页面。校准统计在 N≥30 条真实样本前不产出结论。

## 技术栈

- Python 3（复用现有 backend 环境，无新依赖）
- 数据层：复用 `app.database.db`（自动适配 SQLite 本地 / PostgreSQL 生产，统一 `%s` 占位符）
- CLI：argparse
- 不修改现有文件（`database.py`、`daily_batch.py` 均不动）

## 实现方案

新增独立模块 `backend/app/thesis_tracker.py` + 命令行入口 `scripts/thesis_cli.py`，与 `scenario_watch`、`contradiction_validations`、`edge_verify` 边界清晰，命名统一 `thesis_*` 避开 regime 语义。这是项目"预登记纪律"从策略层推广到判断层，复用 stagflation.py 的模块风格（文档头 + `_READY` 标志 + `ensure_tables` + 阈值 env 可调）。

## 表结构设计

两张表均用 `db.is_postgres` 分支建表，规避"SQLite 下 SERIAL 不自增、JSONB 不识别"的双库坑：

```sql
-- thesis_registry（假设登记簿，每条假设一行）
CREATE TABLE IF NOT EXISTS thesis_registry (
    id INTEGER PRIMARY KEY,            -- PG 分支改 SERIAL PRIMARY KEY
    claim TEXT NOT NULL,
    nature TEXT NOT NULL,              -- prediction / decision
    thesis_type TEXT,                  -- fed_event / regime / oil / a_share
    p_mine REAL,                       -- 0~1 小数
    p_market REAL,                     -- 0~1 小数，无则 NULL
    market_source TEXT,                -- fedwatch / survey / futures
    market_as_of TEXT,
    deadline TEXT,                     -- "YYYY-MM-DD HH:MM"（BJT）
    resolution_rule TEXT,              -- JSON 字符串（结构化条件数组）
    resolution_source TEXT,            -- auto / manual
    invalidation_early TEXT,
    status TEXT,                       -- open/resolved_win/resolved_lose/invalidated/expired
    backfilled INTEGER DEFAULT 0,      -- 1=回填样本，永久排除统计
    created_by TEXT,                   -- user / llm / research
    created_at TEXT
);

-- thesis_events（append-only 状态流转与修订留痕）
CREATE TABLE IF NOT EXISTS thesis_events (
    id INTEGER PRIMARY KEY,            -- PG 分支改 SERIAL PRIMARY KEY
    thesis_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,          -- register/revise/invalidate/evidence_manual/resolve
    payload TEXT,                      -- JSON 字符串
    created_by TEXT,
    created_at TEXT
);
```

关键约定：p 值一律存小数 0~1（0.65、0.844），Brier 计算可直接使用；`resolution_rule` 用结构化 JSON 条件数组而非 DSL：

```
{"logic": "AND", "conditions": [
  {"field": "rate_decision",   "op": "eq", "value": "+25bp", "source": "manual"},
  {"field": "dot_2027_median", "op": "le", "value": 4.1,      "source": "manual"}
]}
```

## 核心函数接口

```python
def ensure_tables() -> None
def register_thesis(claim, nature, p_mine, p_market=None, market_source=None,
                    deadline=None, resolution_rule=None, resolution_source="manual",
                    invalidation_early=None, created_by="user", backfilled=False) -> int
def revise_thesis(thesis_id, new_p, reason, trigger_event=None) -> bool
def invalidate_thesis(thesis_id, reason) -> bool
def add_manual_evidence(thesis_id, field, value, source_url, entered_by="user") -> bool
def resolve_open_theses() -> Dict
def load_thesis(thesis_id) -> Dict
def list_theses(status=None) -> List[Dict]
```

## 裁决器 v1 逻辑

1. 扫描 `status='open'` 的假设，解析 deadline 判断是否到期
2. 解析 `resolution_rule.conditions`，逐条评估：

- `source=auto`：从 macro_daily 最新快照按路径读值（支持 `panel.brent.price`、`panel.us10y.price`、`panel.dxy.price`、`derived.us10y_bp_change`，日期斜杠格式需解析排序）
- `source=manual`：从 thesis_events 查最新一条 `evidence_manual` 记录取值

3. 流转规则：任一条件明确不满足 → `resolved_lose`；存在不可判定的条件但无明确不满足 → 保持 open 并返回"待录入"清单；全部满足 → `resolved_win`
4. 每次裁决写入 thesis_events（event_type=resolve），更新 registry.status

## 实现注意（防回归）

- **双库兼容**：建表用 `db.is_postgres` 分支；JSON 存 TEXT 列，读取 `json.loads` 且 `isinstance(x, str)` 判断；占位符统一 `%s`
- **时区**：所有时间戳用 `datetime.now(timezone.utc)`（卫生门会拦无时区 now 调用）
- **幂等**：`ensure_tables` 用 IF NOT EXISTS；seed 按 claim 去重，重复执行不产生重复行
- **日期坑**：macro_daily 的 date 混斜杠格式，auto 字段读值必须解析成 datetime 排序（复用 stagflation.py `_parse_date` 思路）
- **claim/resolution_rule 不可改**：登记后只允许通过 revise（改概率）和 invalidate（失效），不提供 claim 更新接口
- **边界**：nature 只接受 prediction/decision 两种，状态类命题拒绝登记（返回错误提示归 scenario_watch/stagflation）

## 目录结构

```
stock-scoring-v2/
├── backend/
│   └── app/
│       └── thesis_tracker.py    # [NEW] 核心模块：建表/登记/修订/失效/证据/裁决/查询
└── scripts/
    └── thesis_cli.py            # [NEW] CLI：seed/register/revise/evidence/resolve/list
```

## 种子数据

- **首条真假设（backfilled=0）**：claim="2026-12-09 FOMC 加息 25bp 且 2027 点阵图中值 ≤4.1%"，nature=prediction，p_mine=0.65，p_market=0.844（fedwatch，10/28），deadline="2026-12-10 03:00"，resolution 两条件均 manual，invalidation="11 月中旬 CPI 后 FedWatch 跌破 65%"
- **回填演示样本（backfilled=1）**：9/16 FOMC 加息 25bp 已兑现，status=resolved_win，仅作 schema 演示与流程彩排，永久不进统计