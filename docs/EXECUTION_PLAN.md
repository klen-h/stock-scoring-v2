# 全项目清理 / 归档 / 5 亿 token 执行计划（2026-10-24 ~ 10-26）

> 承接：用户 2026-10-24 的六项目标（文档归档、开发清单、未完成盘点、5 亿 token、清理 vs skills、执行计划）。
> 配套文件：`docs/PROJECT_GUARDRAILS.md`（红线）、`docs/DEV_CHECKLIST.md`（环境与坑）、
> `reports/llm_batch/manifest.json`（文件清单，由 `scripts/build_llm_manifest.py` 生成）。
> 执行方（人或 LLM/agent）**必须先读 Guardrails**，再按本文件的阶段推进。

---

## 0. 现状（只读盘点结论）

| 项 | 数量 | 备注 |
|---|---|---|
| 全仓 `.md` | **341** | 根目录约 50 个，与代码混放 |
| `backend/app` 顶层模块 | **66** | 核心链路密度最高的目录 |
| `scripts/` | **137** | 一次性脚本多，需标注"可归档/可删" |
| TODO/FIXME 标记 | **~90** | 含历史注释，不能按关键字直接转待办 |
| `docs/` | **本次新建** | 之前不存在 |

关键判断：**上下文噪声本身就是成本**（341 个 md + 90 处 TODO 会持续污染每次阅读/检索），
所以「先清理、再批量烧 token」比反过来更省 token，也更少幻觉。

### 0.1 清单实测（`reports/llm_batch/manifest.json`）

| 指标 | 数值 |
|---|---|
| 纳入文件 | **606** |
| 总行数 | **183,133** |
| 单遍通读估算 | **≈ 258 万 tokens** |
| 需切片的大文件（>800 行） | **39** |

⇒ **5 亿 tokens ≈ 全仓通读 194 遍**。所以真正的约束不是"读不完"，而是
**"读很多遍但没验证"**——因此每类任务都必须绑定可核对的产出（行号、可运行测试、可用率抽检），
禁止只做"再读一遍然后写感想"。

---

## 1. 文档归档（目标 1）

### 1.1 目录结构

```
docs/
├─ INDEX.md              # 人读索引：每份文档一句话定位 + 状态 + 最后更新
├─ ARCHIVE_POLICY.md     # 保留/更新/废弃三分法 + 命名规范
├─ architecture/         # 顶层框架、数据流、评分、回测、交易日全流程
├─ plans/                # 仍在执行的计划（PLAN_NEXT 等）
├─ reviews/              # 全项目审查、专业审视、逻辑冲突
├─ reports/              # 一次性分析/选型/体检报告（_report_*、A2_*、zzshare 盘点）
├─ ops/                  # 上线观测单、EGRESS、部署、故障复盘
└─ archive/              # 已被取代但需留档（PLAN_ARCHIVE、旧计划）
```

根目录**保留白名单**：`README.md`、`CONTEXT.md`、`DEPLOY.md`、`EGRESS.md`、`logs.md`。
其余 `.md` 一律 `git mv` 进 `docs/`（保历史，不 `git rm`）。

### 1.2 三分法（写进 `ARCHIVE_POLICY.md`）

| 分类 | 判据 | 动作 |
|---|---|---|
| **保留（active）** | 描述当前系统事实（架构/口径/流程），且 30 天内被引用过 | 移入 `docs/architecture` 或 `ops`，`INDEX` 标 `active` |
| **更新（living）** | 仍在执行的计划/清单（`PLAN_NEXT`、`BACKLOG`） | 移入 `docs/plans`，标 `living` + 负责人 + 下次复核日 |
| **废弃（archive）** | 已被新计划取代（如 `PLAN_ARCHIVE_*`、`_report_*` 一次性报告） | 移入 `docs/archive`，标 `superseded-by: <新文档>`；**不删除**（除用户明确批准） |

命名规范：新文档 `YYYYMMDD_主题.md`（中文主题可保留）；`PLAN_*`/`_report_*` 前缀停止新增。

### 1.3 验收

- 根目录 `.md` ≤ 5 个；`docs/INDEX.md` 覆盖 `docs/` 下 100% 文件；
- `git log --follow docs/...` 能追到原路径（验证 `git mv` 成功）；
- 全仓 grep 旧路径引用为 0（README/CI/脚本里的路径同步更新）。

---

## 2. 开发注意事项清单（目标 2）

产出 `docs/DEV_CHECKLIST.md`（**已完成初版**）：环境变量、依赖命令、数据源限额、
发布/回滚、验证纪律、Top 12 常见坑、协作约定。

补充动作：从 `.codebuddy/memory/MEMORY.md` 抽取「跨会话稳定事实」，与本清单去重后合并。

验收：新接手的人只读 `README + GUARDRAILS + DEV_CHECKLIST` 能在 30 分钟内跑起本地环境。

---

## 3. 未完成内容盘点（目标 3）

产出 `docs/BACKLOG.md`，列：`ID | 模块 | 标题 | 现状证据 | 优先级 | 依赖 | 预估 | 验收口径`。

数据来源（三处交叉，不靠关键字拍脑袋）：
1. `PLAN_NEXT.md` 未勾选项（人工逐条判定「已完成/已废弃/仍待办」）；
2. 全仓 90 处 `TODO/FIXME/待补`（**逐条**标注是否仍然成立）；
3. `.codebuddy/memory/MEMORY.md` 里的"遗留/未做/已知限制"条目。

模块分派（用户选定）：**后端 / 前端 / 数据流水线 / 文档**（责任人统一为「用户」，按域分列）。

优先级定义：
- **P0**：影响正确性/资金安全/线上可用（如口径错误、数据回滚、熔断误伤）；
- **P1**：影响决策质量（观察器缺失信号、简报不准、缓存陈旧）；
- **P2**：体验/债务（文档、命名、重复实现、脚本归并）。

验收：每条都有「证据」（文件:行 或 现象），不接受"感觉没做完"。

---

## 4. 5 亿 token 规划（目标 4，真实执行）

### 4.1 总量与分配

总额 **500,000,000 tokens**（prompt + completion 合计，按 `llm_batch_usage` 记账）。

| 任务 | 占比 | tokens | 适合原因 | 主要风险 |
|---|---|---|---|---|
| 代码审查（逐文件/逐模块） | **40%** | 2.00 亿 | 内容固定、可切片、产出可结构化（问题清单） | 幻觉引用行号 ⇒ 必须给行号并要求原文引用 |
| 测试生成 | **25%** | 1.25 亿 | 输入=函数签名+实现，输出=可运行 pytest | 生成的测试可能"自证实现" ⇒ 强制断言业务语义 |
| 文档生成 | **20%** | 1.00 亿 | 341 个 md + 66 模块，天然适合批量摘要 | 与既有文档冲突 ⇒ 必须"以代码为准" |
| 重构建议 | **15%** | 0.75 亿 | 跨模块重复实现（涨跌停 3 套、缓存 4 套） | 建议不可执行 ⇒ 只出建议不出自动改码 |

### 4.2 时间摊派（10-24 ~ 10-26）

| 日期 | 占比 | tokens | 重点 |
|---|---|---|---|
| D0 10-24（周六） | 20% | 1.0 亿 | 先跑通 pipeline：manifest → 小批审查 → 质量抽检；清理文档（`git mv`） |
| D1 10-25（周日） | 45% | 2.25 亿 | 代码审查全量 + 测试生成（P0 模块优先） |
| D2 10-26（周一） | 35% | 1.75 亿 | 文档生成 + 重构建议 + 汇总报告；**23:59 前对账** |

预留 **10% 缓冲**（约 5000 万）用于重跑/补齐，不预先分配。

### 4.3 安全阀（强制执行）

- `--max-calls`：单次运行调用上限（建议 ≤ 2000）；
- `--max-tokens`：单次运行 token 上限（建议 ≤ 3000 万）；
- **STOP 文件**：`reports/llm_batch/STOP` 存在即优雅退出并写断点；
- **独立记账**：`llm_batch_usage(day, task, calls, prompt_tokens, completion_tokens)`，
  **不写 `llm_usage_daily`**（否则线上日熔断被打满，盘中简报/复盘全挂）；
- **幂等**：`reports/llm_batch/<date>/<task>/*.done.json`（prompt hash + tokens），重跑跳过；
- **质量抽检**：每任务随机 5% 人工复核，`quality.json` 记录 `usable_rate / hallucination_rate`；
  可用率 < 70% ⇒ 该任务停止并改提示词。

### 4.4 适合 / 不适合批量烧 token

**适合**：单文件语义审查、模块级依赖梳理、为纯函数补测试、把代码注释汇成文档、
重复实现检测（相同阈值散落多处）、历史报告摘要与去重。

**不适合**：需要跑起来才有结论的（回测调参、性能剖析）、涉及实时行情的判断、
需要跨 10+ 文件强推理的重构落地、任何会直接改生产数据的操作。

---

## 5. 清理优化 vs 形成 Skills（目标 5）

### 5.1 对比

| 维度 | 全项目清理优化 | 形成 Skills |
|---|---|---|
| 解决什么 | 上下文噪声、重复实现、文档失序 | 重复流程的"每次都要人提醒" |
| 一次性成本 | 中（搬 50 个 md、90 处 TODO 判定） | 低-中（每个 skill 需写清触发条件与红线） |
| 持续成本 | 低（索引 + 规范） | **中**（代码演进后 skill 会漂移） |
| 收益兑现 | 立即（检索/阅读变快） | 延迟（下次做同类任务才体现） |
| 失败模式 | 搬错路径、引用失效 | skill 与代码不一致 ⇒ 按旧流程操作 |

### 5.2 结论：**先清理，再沉淀 3 个 skill，不做"全量 skill 化"**

理由：
1. 清理的收益是**所有后续任务的地基**（包括 skill 编写本身）；
2. skill 只对**高频 + 步骤固定 + 易错**的流程有价值，项目里符合的只有 3~5 个；
3. 项目已多次因"同一事实两处维护"漂移（教练卡/时间线/涨跌停口径）——skill 越全越容易漂移。

**建议沉淀（按优先级）**：
1. `新增观察器`（组合既有数据 → 按日缓存 + 指纹 → 前端卡 → 日批任务注册）；
2. `数据包发布与回滚`（含"禁止本地 publish"纪律 + 日期护栏）；
3. `LLM 接入模板`（结构化输出 + 降级 + 成本闸门 + 验收）。

其余流程（发版验证、日批加任务）先写进 `DEV_CHECKLIST`，等重复出现 3 次再抽 skill。

---

## 6. 分阶段执行计划与验收（目标 6）

### D0 · 10-24（今天）· 打地基

| # | 任务 | 产出物 | 验收标准 |
|---|---|---|---|
| 1 | 建 `docs/` 六目录 + `git mv` 根目录文档 | 目录树 + 迁移清单 | 根目录 `.md` ≤ 5；`git log --follow` 可追溯 |
| 2 | 写 `docs/INDEX.md` / `ARCHIVE_POLICY.md` | 两份文档 | 索引覆盖 100% 文件；三分法有判据 |
| 3 | 生成 `manifest.json` | `reports/llm_batch/manifest.json` | `files/loc/est_tokens` 与实测一致 |
| 4 | 批量运行器 dry-run + 中断演练 | 运行日志 | STOP 文件能中断；`.done.json` 生效 |
| 5 | 小批真实跑（≤2000 万 tokens，P0 模块审查） | `reports/llm_batch/D0/review/` | 抽检 5 条：引用行号真实、结论可执行 |

### D1 · 10-25 · 主攻代码审查 + 测试

| # | 任务 | 产出物 | 验收标准 |
|---|---|---|---|
| 6 | 全量代码审查（P0 → P1） | `.../D1/review/*.md` + 汇总 | 每条问题带 `文件:行`；抽检可用率 ≥70% |
| 7 | P0 模块测试生成 | `backend/tests/generated/*.py` | `pytest` 可收集；至少 60% 通过 |
| 8 | `docs/BACKLOG.md` 定稿 | 待办清单 | 每条有证据；P0 ≤ 10 条 |

### D2 · 10-26 · 文档生成 + 重构建议 + 对账

| # | 任务 | 产出物 | 验收标准 |
|---|---|---|---|
| 9 | 模块文档生成（66 个模块摘要） | `docs/architecture/modules/*.md` | 与代码一致（抽样 5 个人工核对） |
| 10 | 重构建议（重复实现/超长函数） | `reports/llm_batch/D2/refactor.md` | 只出建议不改码；每条给收益/风险 |
| 11 | 3 个 skill 沉淀 | `.codebuddy/skills/*` | 触发条件 + 步骤 + 红线齐全 |
| 12 | **对账 + 收口报告** | `reports/llm_batch/REPORT.md` | 已用 tokens / 剩余 / 各任务可用率 |

### 回滚点

- 文档搬迁：`git revert <commit>` 即回到原路径（`git mv` 保历史）。
- 批量产出：全部落 `reports/llm_batch/`，**不进业务代码**；测试文件单独目录，可整体删除。
- 运行器：任何时刻 `touch reports/llm_batch/STOP` 即停。

---

## 7. 交给执行方的最小上下文包

1. 本文件 `docs/EXECUTION_PLAN.md`
2. `docs/PROJECT_GUARDRAILS.md`（红线，优先于本文件）
3. `docs/DEV_CHECKLIST.md`（环境/命令/坑）
4. `reports/llm_batch/manifest.json`（分批依据）
5. 模型接入参数：`LLM_BASE_URL` / `LLM_MODEL=glm-5.3` / `LLM_MODEL_FALLBACK=glm-5.3-flash`
   （若 zcode 非 OpenAI 兼容协议，需先加适配层，不能直接套 `call_llm`）

**执行前置 prompt（可直接粘贴）**：

```
先读 docs/PROJECT_GUARDRAILS.md、docs/EXECUTION_PLAN.md、docs/DEV_CHECKLIST.md。
只在 chore/cleanup-20261024 分支改动；删除文件需先列清单待确认。
批量 LLM 只允许用 scripts/llm_batch.py（须带 --max-calls / --max-tokens / STOP 文件），
记账写 llm_batch_usage，不得写入 llm_usage_daily。
禁止运行 scripts/publish_packs_local.py；前端改动只 push 走 Actions。
每任务产出落 reports/llm_batch/<date>/<task>/，并写 quality.json（5% 抽检）。
```
