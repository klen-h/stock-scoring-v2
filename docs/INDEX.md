# docs/ 文档索引（INDEX）

> 每份文档一行：路径 | 一句话定位 | 状态 | 最后更新。
> 状态：`active` = 描述当前系统事实 ｜ `living` = 仍在执行/等时钟 ｜ `archive` = 已被取代或已完成（`superseded-by` 指向接替者）。
> 归类判据见 `docs/ARCHIVE_POLICY.md`。根目录只保留白名单 5 件（README / CONTEXT / DEPLOY / EGRESS / logs）。

## 0. 规约与总控

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/PROJECT_GUARDRAILS.md` | 10 条硬红线（发布纪律/时点诚实/LLM 纪律/口径唯一），动手前必读 | active | 2026-10-24 |
| `docs/EXECUTION_PLAN.md` | 清理/归档/批量 token 的总执行计划（阶段、产出、验收） | living | 2026-10-24 |
| `docs/DEV_CHECKLIST.md` | 环境变量、依赖命令、数据源限额、发布/回滚、Top12 坑 | active | 2026-10-24 |
| `docs/ARCHIVE_POLICY.md` | 文档保留/更新/废弃三分法 + 命名规范 | active | 2026-10-24 |
| `docs/INDEX.md` | 本索引 | living | 2026-10-24 |
| `docs/BACKLOG.md` | 全项目待办盘点（三处交叉：PLAN_NEXT/TODO/FIXME/MEMORY），每条带证据 | living | 2026-10-24 |
| `docs/CLEANUP_REPORT.md` | 本次清理归档+审查+测试+文档生成的总汇报 | archive | 2026-10-24 |

## 1. architecture/（系统事实）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/architecture/开发文档.md` | 工程约定主文档：红线/架构速查/已验证·已证伪/路线图（README 首选入口） | active | 2026-10-24 |
| `docs/architecture/顶层框架与数据流_20260920.md` | 顶层框架与数据流总览（各数据源→模块→出口） | active | 2026-10-24 |
| `docs/architecture/交易日全流程.md` | 交易日盘前/盘中/盘后/日批全流程与各环节产出 | active | 2026-10-24 |
| `docs/architecture/评分系统详解_20260920.md` | 三维评分的指标、权重与口径详解 | active | 2026-10-24 |
| `docs/architecture/回测系统详解_20260920.md` | 回测引擎/撮合口径/策略回放详解 | active | 2026-10-24 |
| `docs/architecture/股票逻辑全景_20260912.md` | 项目早期全景（评分/主力/闸门体系缘起） | archive（背景读物，现状以开发文档为准） | 2026-10-24 |
| `docs/architecture/主力节奏跟随框架_20260916.md` | "跟对主力节奏"北极星/方法论层顶层设计 | active | 2026-10-24 |
| `docs/architecture/主力节奏框架_开发计划_v1.0.md` | 主力节奏机制的机制与验证设计（北极星的工程化） | active | 2026-10-24 |
| `docs/architecture/战法体系框架_基于A股拐点规律_20260926.md` | 战法体系底层框架（拐点规律假设层） | archive（已被 v1.0 融合版吸收，superseded-by: `docs/plans/PLAN_战法体系重设计_20260926.md`） | 2026-10-24 |
| `docs/architecture/战法体系重构_周期框架设计_20260926.md` | 战法体系周期框架设计（仓位梯度/灰度/三底展示资产） | archive（已被 v1.0 融合版吸收，superseded-by: `docs/plans/PLAN_战法体系重设计_20260926.md`） | 2026-10-24 |
| `docs/architecture/交易员教练_模拟盘接入说明_20260915.md` | 交易员教练与模拟盘的接入说明 | active | 2026-10-24 |
| `docs/architecture/PLAN_PATTERN_SCAN.md` | 「缩量调整→放量启动」每日扫描功能的设计说明（已上线的观察工具） | active | 2026-10-24 |
| `docs/architecture/工作台开发需求_20260925.md` | 工作台（一天时间线单页）开发需求 v1.0（已实施） | archive（需求已交付，superseded-by: `frontend/src/views/Workbench.vue` 实现） | 2026-10-24 |
| `docs/architecture/modules/` | backend/app 66 个顶层模块各一份摘要（职责/输入输出/阈值/依赖/坑） | active | 2026-10-24 |

## 2. plans/（仍在执行）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/plans/20261026_选股事实宽表设计_v0.md` | 选股工作台宽表列清单（A基础/B评分KILL徽章/C主力/D闸门/E龙虎榜回避/F形态预登记/G板块影子/H防御组；只放事实，加权=新信号须预登记） | living（列清单待确认） | 2026-10-26 |
| `docs/plans/20261026_评分分市况重构方案_v0.md` | 评分分市况重构方案（判据已冻结 v1.0：H4 标签上线、H2' 防御影子榜影子期 10-23 起算、H3 双锚并跑中；等权锚 eq_anchor_history 为判态单一事实源） | living（执行中） | 2026-10-25 |
| `docs/plans/PLAN_NEXT.md` | 收口后的等待/决策/立项清单（A 等时钟/B 等决策/C 待立项），计划体系入口 | living | 2026-10-24 |
| `docs/plans/PLAN_SCORE_EDGE.md` | 评分预测力预登记判据 v1.1 + 首跑 FAIL 结果 + 回避侧 A2 待复检 | living（A1/A2 等时钟，≈2026-10-23 与 11 月中旬） | 2026-10-24 |
| `docs/plans/PLAN_MAINFORCE_EDGE.md` | 主力节奏假设（吸筹∧三绿）预登记判据 v1.0（回填已过一致性闸，等首跑发令） | living | 2026-10-24 |
| `docs/plans/PLAN_PATTERN_LAUNCH_EDGE.md` | 「缩量调整→放量启动」前瞻预登记判据 v1.0（只对 ≥2026-10-15 前瞻样本生效） | living（等前瞻样本，1~3 个月） | 2026-10-24 |
| `docs/plans/PLAN_SECTOR_LINKAGE.md` | 板块联动筛选预登记判据 v1.0（影子期 `SECTOR_LINKAGE_MODE=shadow`） | living（影子期，切 on 须拍板） | 2026-10-24 |
| `docs/plans/PLAN_RESERVE_SIGNALS.md` | 外储再配置→A 股警示信号开发文档（草案待评审） | living | 2026-10-24 |
| `docs/plans/PLAN_战法体系重设计_20260926.md` | 战法体系重设计 v1.0 融合版框架（每项需回测背书才能上线） | living | 2026-09-26 |
| `docs/plans/交易方法论落地计划_20260925.md` | 交易方法论框架→系统能力映射与排期（[已有]/[可算]/[缺口]） | living | 2026-10-24 |
| `docs/plans/PLAN_EVENT_PLAYBOOK_20260915.md` | 事件应对剧本设计（Phase 1 已落地，Phase 2/3 待开发） | living | 2026-10-24 |

## 3. reviews/（审查/审计/体检）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/reviews/AUDIT_PLAN_2026-09-12.md` | 旧看板 PLAN_2026-09-12 的 36 项逐项审计（收口为 8 项真待办） | archive（结论已回写 PLAN_NEXT） | 2026-10-24 |
| `docs/reviews/全项目审查_逻辑冲突与优化建议_20260913.md` | 全项目逻辑冲突与优化建议审查 | archive（历史结论，部分已被后续修复） | 2026-10-24 |
| `docs/reviews/评分系统体检_子指标IC与优化建议_20260920.md` | 评分子指标 IC 体检与优化建议 | archive（后续已由预登记检验收口） | 2026-10-24 |
| `docs/reviews/项目专业审视_不足与优化建议_20260920.md` | 项目专业度不足与优化建议（交易方法论落地计划的上游） | archive | 2026-10-24 |
| `docs/reviews/专业度缺口与优先级建议_20260929.md` | 专业度缺口与优先级建议 | archive | 2026-10-24 |

## 4. reports/（一次性分析/选型/盘点）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/reports/A2_价格源扩容_选型分析_20260929.md` | 价格源扩容选型分析（A2 方案） | archive | 2026-10-24 |
| `docs/reports/_report_step0_by_regime_20260926.md` | 分 regime 回测 step0 报告 | archive | 2026-10-24 |
| `docs/reports/_report_step1a_breadth_20260926.md` | 宽度因子 step1a 报告 | archive | 2026-10-24 |
| `docs/reports/_report_step1b_nb_serialization_20260926.md` | nb 序列化 step1b 报告 | archive | 2026-10-24 |
| `docs/reports/_report_zzshare_全市场可行性_20260926.md` | zzshare 全市场数据可行性报告 | archive | 2026-10-24 |
| `docs/reports/_report_事件驱动信号源接入_20260927.md` | 事件驱动信号源接入分析 | archive | 2026-10-24 |
| `docs/reports/_report_分状态回测结论_20260927.md` | 分状态回测结论 | archive | 2026-10-24 |
| `docs/reports/_事件驱动信号源_交付清单_20260927.md` | 事件驱动信号源交付清单 | archive | 2026-10-24 |
| `docs/reports/_本轮改动与验证结论_最终清单_20260927.md` | 一轮改动的验证结论最终清单 | archive | 2026-10-24 |
| `docs/reports/zzshare功能盘点与接入建议_20260920.md` | zzshare 数据源功能盘点与接入建议 | archive | 2026-10-24 |
| `docs/reports/主力节奏框架_Gate判定书与重算器预注册_20260916.md` | 主力节奏 Gate 判定的预注册裁决记录 | archive | 2026-10-24 |
| `docs/reports/外部研判与功能需求简报_20260913.md` | 外部研判与功能需求简报 | archive | 2026-10-24 |
| `docs/reports/评分倒U改进_极端流入个案001368_20260914.md` | 评分倒 U（极端流入）个案分析 001368 | archive | 2026-10-24 |

## 5. ops/（部署/观测/手册）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/ops/上线观测单_20260930.md` | 上线观测单（观测点与判停条件） | archive | 2026-10-24 |
| `docs/ops/Agent知识库_完整操作手册_v3.1.md` | Agent 操作手册 v3.1（A股本质/矛盾理论/评分/提示词/日流程/风控） | active（部分内容已被开发文档与代码更新取代，冲突处以代码为准） | 2026-10-24 |
| `docs/ops/轮询清单_20261026.md` | 前端 19 个定时器 + 后端 16 个循环的间隔/时段门控/外呼成本与新增规矩 | active | 2026-10-26 |

## 6. archive/（被取代的旧计划）

| 路径 | 一句话定位 | 状态 | 最后更新 |
|---|---|---|---|
| `docs/archive/PLAN_2026-09-12.md` | 旧看板计划（自称唯一现行计划，已被审计收口） | archive（superseded-by: `docs/plans/PLAN_NEXT.md`） | 2026-10-24 |
| `docs/archive/PLAN_ARCHIVE_2026-09-12.md` | 旧计划归档合集（PLAN_MAINFORCE/PLAN_NEXT_PHASE 等） | archive | 2026-10-24 |
| `docs/archive/PLAN_LHB_BACKFILL.md` | 龙虎榜历史回填设计与结论（2026-10-14 已完成，428 日/30,706 行） | archive（已完成，superseded-by: `docs/plans/PLAN_NEXT.md` §C3） | 2026-10-24 |
| `docs/archive/项目进展_20260906.md` | 项目进展快照（2026-09-06 时点） | archive（superseded-by: `docs/architecture/开发文档.md`） | 2026-10-24 |

---
> 维护约定：新增/迁移文档必须同步本索引；`modules/` 子目录逐文件摘要见该目录内文件头（共 66 份，随模块增删更新）。
