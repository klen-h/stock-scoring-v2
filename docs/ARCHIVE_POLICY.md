# 文档归档与命名政策（ARCHIVE POLICY）

> 用途：规定每份文档属于「保留 / 更新 / 废弃」哪一类、放哪个目录、命名怎么起。
> 配套：`docs/INDEX.md`（人读索引）、`docs/PROJECT_GUARDRAILS.md`（红线，优先级最高）。
> 建立：2026-10-24（全项目清理归档，见 `docs/CLEANUP_REPORT.md`）。

## 1. 目录职责

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `docs/architecture/` | 描述**当前系统事实**的文档：架构、数据流、评分/回测口径、交易日流程、模块摘要 | 计划、一次性报告 |
| `docs/plans/` | **仍在执行或待执行**的计划（PLAN_NEXT、预登记判据等） | 已完成且不再引用的计划 |
| `docs/reviews/` | 审查 / 审计 / 体检 / 专业审视类文档 | — |
| `docs/reports/` | **一次性**分析、选型、盘点、交付清单（结论已落档，不再演化） | 需要持续更新的清单 |
| `docs/ops/` | 部署、观测、操作手册、故障复盘 | — |
| `docs/archive/` | 已被取代或已执行完毕的旧计划 / 旧快照（**不删除**，保历史） | 任何"现行"文档 |

根目录白名单（只允许这 5 个）：`README.md`、`CONTEXT.md`、`DEPLOY.md`、`EGRESS.md`、`logs.md`。

## 2. 三分法（判定一价新文档去哪里）

| 分类 | 判据 | 动作 | INDEX 状态标 |
|---|---|---|---|
| **保留（active）** | 描述当前系统事实（架构/口径/流程），且近期（30 天内）被引用过 | 移入 `docs/architecture` 或 `docs/ops` | `active` |
| **更新（living）** | 仍在执行的计划 / 清单（PLAN_NEXT、BACKLOG、预登记判据等待时钟项） | 移入 `docs/plans`；INDEX 标负责人与下次复核时点 | `living` |
| **废弃（archive）** | 已被新计划取代（`PLAN_ARCHIVE_*`、旧看板）、已完成的一次性报告 | 移入 `docs/archive`（或 `docs/reports`，若结论有复用价值）；INDEX 标 `superseded-by: <文档>` | `archive` |

判定口诀：**"它现在还对不对？"** 对 ⇒ active；"它还在变吗？" 变 ⇒ living；"它被谁取代了？" 答得出 ⇒ archive，答不出 ⇒ reports（一次性证据，留档备查）。

## 3. 命名规范

- 新文档一律 `YYYYMMDD_主题.md`（中文主题保留），如 `20261024_清理汇报.md`。
- **停止新增**这些历史前缀：`PLAN_*`、`_report_*`、`A2_*`、`_tmp_*`（下划线开头的临时脚本用完即删，不入库）。
- 预登记判据类文档沿用"判据冻结后不改、结论只追加"纪律（见 `docs/plans/PLAN_SCORE_EDGE.md` 头部）。
- 计划完成后**不删除**：移入 `docs/archive/` 并在 INDEX 标 `superseded-by`；删除文件须用户明确批准并留清单（Guardrails #0）。

## 4. 引用与留痕

- 引用文档一律写**从仓库根出发的路径**（如 `docs/plans/PLAN_NEXT.md`），或同名文件的相对链接；
  提交前跑 `_tmp_doclink_check.py`（临时脚本，用法见 CLEANUP_REPORT §10）确认 markdown 链接 0 失效。
- `git mv` 保历史；验收用 `git log --follow <新路径>` 能追到原路径。
- 历史日志（`.codebuddy/memory/`）中的旧文件名引用**不改写**（保历史原貌），文件名未变即可检索。
