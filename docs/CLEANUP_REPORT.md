# CLEANUP_REPORT —— 全项目清理归档 + 代码审查 + 文档生成 汇报

> 执行：2026-10-24（分支 `chore/cleanup-20261024`）· 执行方：ZCode（仓库执行工程师角色）。
> 上游文件：`docs/PROJECT_GUARDRAILS.md`（红线）、`docs/EXECUTION_PLAN.md`（阶段与验收）、
> `docs/DEV_CHECKLIST.md`（环境）、`reports/llm_batch/manifest.json`（606 文件清单）。
> 本次**未删除任何文件**、未触碰生产环境变量/GitHub Secrets/线上库结构、未运行 `publish_packs_local.py`。

---

## 1. 总览

| 项 | 计数 | 说明 |
|---|---|---|
| **完成项** | **7 / 7** | A 文档归档 ✅ · B 代码审查 ✅ · C 测试生成 ✅ · D 待办盘点 ✅ · E 模块摘要 ✅ · F 重构建议 ✅ · G Skill 沉淀 ✅ |
| **未完成项** | **0 类** | 无整类未完成；覆盖深度受限项见 §9（P1/P2 非全量逐文件、pytest 仅本地环境验证） |
| **判定失效项** | **9 条** | 见 `docs/BACKLOG.md` §C（消息分验证已完成 FAIL、batch_runs 已提交、指标刷新已迁 Actions 等） |

提交序列（每块一个 commit，均写明 做了什么/为什么/验证方式）：

| Commit | 内容 |
|---|---|
| `c728462f` | chore(cleanup): 文档归档（46 个 md 迁入 docs/ 六目录 + INDEX + ARCHIVE_POLICY + 链接修复） |
| `7cdec8f6` | test: 生成 P0 模块 pytest 用例 64 项 |
| `2675270f` | feat(skills): 沉淀 3 个操作技能 |
| `696476c2` | docs: backend/app 76 个顶层单元模块摘要 |
| `60ca9d2d` | feat(review): 全量代码审查 12 批 + 模式扫描 + 质量抽检 + 重构建议 |
| `0ee9fc4a` | docs: BACKLOG 定稿 69 条 |
| （本次） | docs: CLEANUP_REPORT 收口汇报 |

## 2. 文档迁移清单

根目录 `.md` 51 个 → 白名单保留 5 个（README/CONTEXT/DEPLOY/EGRESS/logs），**46 个全部 `git mv`**（`R100` 相似度，历史完整）：

| 原路径 | 新路径 | 分类 | 状态 |
|---|---|---|---|
| 开发文档.md / 顶层框架与数据流_20260920 / 交易日全流程 / 评分系统详解_20260920 / 回测系统详解_20260920 / 股票逻辑全景_20260912 / 主力节奏跟随框架_20260916 / 主力节奏框架_开发计划_v1.0 / 战法体系框架_基于A股拐点规律_20260926 / 战法体系重构_周期框架设计_20260926 / 交易员教练_模拟盘接入说明_20260915 / PLAN_PATTERN_SCAN / 工作台开发需求_20260925 | `docs/architecture/`（13 个） | 架构/口径/流程 | ✅ R100 |
| PLAN_NEXT / PLAN_SCORE_EDGE / PLAN_MAINFORCE_EDGE / PLAN_PATTERN_LAUNCH_EDGE / PLAN_SECTOR_LINKAGE / PLAN_RESERVE_SIGNALS / PLAN_战法体系重设计_20260926 / 交易方法论落地计划_20260925 / PLAN_EVENT_PLAYBOOK_20260915 | `docs/plans/`（9 个） | 仍在执行/等时钟 | ✅ R100 |
| AUDIT_PLAN_2026-09-12 / 全项目审查_20260913 / 评分系统体检_20260920 / 项目专业审视_20260920 / 专业度缺口_20260929 | `docs/reviews/`（5 个） | 审查/体检/审视 | ✅ R100 |
| A2_价格源扩容_选型分析 / _report_×6 / _事件驱动信号源_交付清单 / _本轮改动与验证结论_最终清单 / zzshare功能盘点 / 主力节奏框架_Gate判定书 / 外部研判与功能需求简报 / 评分倒U改进_001368 | `docs/reports/`（13 个） | 一次性分析/选型/盘点 | ✅ R100 |
| 上线观测单_20260930 / Agent知识库_完整操作手册_v3.1 | `docs/ops/`（2 个） | 部署/观测/手册 | ✅ R100 |
| PLAN_2026-09-12 / PLAN_ARCHIVE_2026-09-12 / PLAN_LHB_BACKFILL / 项目进展_20260906 | `docs/archive/`（4 个） | 被取代的旧计划 | ✅ R100 |

验收结果：
- 根目录 `.md` = **5 个**（=白名单）✅；
- `git log --follow docs/plans/PLAN_NEXT.md` 可追溯到原路径（实测追至 `86611dd6`）✅；
- 全仓 markdown 链接检查 **0 失效**（README 8 处旧链接已同步改新路径）✅；
- 无 `docs/PLAN_*` 形式旧路径残留；代码注释中的**文件名提法**（如 `PLAN_SCORE_EDGE.md §4`）不属路径引用且文件名未变，保持可检索；
- `docs/INDEX.md` 覆盖 docs/ 下 **100% 文件**（128 个 md = 46 迁入 + 3 规约 + INDEX/POLICY/BACKLOG/REPORT + modules/ 76，脚本核验 `missing: []`）✅。

新增制度文件：`docs/ARCHIVE_POLICY.md`（保留/更新/废弃三分法 + `YYYYMMDD_主题.md` 命名规范 + "PLAN_*/_report_* 前缀停止新增"）。

## 3. 代码审查

| 指标 | 数值 |
|---|---|
| 审查文件 | P0 全量 56（manifest 优先级）+ P1 核心运行时 ~25（scheduler/coach/watchers/pack 脚本）= **81 文件逐行深审** |
| 审查批次 | 12 份批次报告（`reports/llm_batch/review/P0_*.md` ×9 + `P1_*.md` ×3） |
| 问题总数 | **213 条**（另加 `_pattern_scan.md` 确定性扫描：涨跌停阈值 30 处、utcnow 5、naive now 162、长函数 81、global 93、`except:pass` 2 等） |
| 严重度分布 | **P0×1 · P1×31 · P2×105 · P3×76** |
| 引用抽检 | **43 / 213（20.2%，门槛 5%）**：行号存在 **100%**、原文逐字一致 **100%** ⇒ 引用准确率 **100%**（门槛 ≥95%）、幻觉率 **0%**（`review/quality.json`） |
| 可用率 | 抽检 43 条全部含具体定位+可执行建议（无泛论）⇒ **可用率 100%**（门槛 ≥70%） |

**Top 10 问题**（完整清单见各报告与 `docs/BACKLOG.md` A 表）：

| # | 位置 | 一句话 |
|---|---|---|
| 1 | `flash/scheduler.py:1421` | `flash_rules.get_china_market_status()` 引用未导入名字，NameError 被 `except:pass` 吞 ⇒ **A4 交易计划触发（止损/兑现/买点提醒）整条链静默死亡** |
| 2 | `scripts/daily_batch.py:1198` | `risk_calendar` 注册但不在 `DEFAULT_ORDER` ⇒ 生产唯一路径 `--tasks all` **从不执行**，限售解禁底座静默断档 |
| 3 | `routers/flash.py:130` 等 | **flash/paper/coach/contradictions/macro 等路由组无鉴权**：匿名 POST 可烧 LLM、推企微、操作模拟盘、触发外网抓取 |
| 4 | `routers/user.py:186/:322` | PUT /plans 按 id upsert 可**跨用户接管交易计划** + mass assignment |
| 5 | `routers/auth.py:17` | JWT_SECRET 缺失回退机器名可预测密钥 ⇒ token 可伪造、鉴权失效 |
| 6 | `scoring/ranking_history.py:347` | `_trust_advice` 输出"**放心买，极强共识/可重仓**"，与 2026-10-11 评分 KILL 预登记直接冲突 |
| 7 | `coach/rules.py:401` 等 | 教练输出大量**指令式祈使句**（"按纪律离场/只减不加"），免责前缀只盖一条路径——违反 Guardrails #5 |
| 8 | `backtest/engine.py:120` | 涨停一字判定基于前复权价舍入+±0.001 容差 ⇒ 除权日前历史一字板约半数漏判，**回测收益系统性虚高** |
| 9 | `mainforce/gate.py:237`、`trade_gate.py:138` | 主力闸门数据缺失 **fail-open 放行**（闸门被绕过且静默） |
| 10 | `scripts/publish_packs_local.py:226/:273` | 10-24 事故残余：本地缺包文件只打印跳过仍 orphan 重建（线上同名文件被整删）；回滚护栏只盖 kline 包 |

抽检方式与复现：`python _tmp_quality_check.py`（临时脚本已删，逻辑见 §10）。

## 4. 测试生成

| 指标 | 数值 |
|---|---|
| 文件数 | 5 个测试文件 + conftest（`backend/tests/generated/`） |
| 用例数 | **64**（limit_stats 15 / engine 板幅 10 / scoring decay 11 / flash rules 时间与时钟 17 / mainforce chips 11） |
| pytest 结果 | **64 passed, 0 failed（通过率 100%，门槛 ≥60%）**，耗时 0.91s |
| 断言性质 | 全部**业务语义**：20cm 股涨 10% 不算涨停（10-09 事故回归）、涨跌幅超板幅不计数（宁少报）、真值限价优先（涨停 0.2% 缓冲/跌停严格）、创业板 ST 仍 20cm、15:00 交易日分界、换手率 vol×100/float_shares 单位、mode 拼错名静默不衰减等 |

已知不可测点（本次未覆盖，需环境/网络/DB）：
- `resolve_weights`（依赖 regime 缓存/DB 回退链）、`counts_best` 的**真实** rt_k 缓存路径（测试用 stub 注入）；
- `flash/scheduler` 的 ~30 个 asyncio loop、`daily_batch` 全链（需 pack/DB fixture，建议后续以日批 dry-run 方式补）；
- 前端 Vue 组件（本次仅审查未生成 js 测试）。

## 5. BACKLOG（`docs/BACKLOG.md`）

- **总数 69 条**：P0×18 / P1×35 / P2×16；后端 55 / 前端 6 / 数据流水线 7 / 文档 1。
- 等时钟 4 项（A2/A3 ≈11 月中、C5 前瞻样本、月度复检）；B 类待拍板 0。
- 淘汰 9 条（判定理由逐条写在 C 表）。
- **P0 清单（18 条，含证据）**：
  BK-01 接口内触发全市场刷新（scoring.py:1288/market.py:373）· BK-02 主力闸门 fail-open（gate.py:237/trade_gate.py:138）· BK-03 overview 裸下标 500（market.py:330-358）· BK-04 async 内同步阻塞×5（scoring.py:1440 等+tasks.py:62）· BK-04a flash 路由无鉴权 · BK-04b JWT_SECRET 可预测回退 · BK-04c /plans 跨用户 upsert · BK-04d /finance-refresh 未鉴权全市场抓取 · BK-33 回测一字板漏判（engine.py:120）· BK-34 缺失渲染成 0（strategies.py:642）· BK-39 回测/形态/行业地图接口无鉴权重算 · BK-42 "放心买"措辞 vs 评分 KILL（ranking_history.py:347）· BK-47 risk_calendar 从不执行（daily_batch.py:1198）· BK-49 行情全失败清空缓存（tencent.py:425）· BK-51 A4 交易计划静默死亡（scheduler.py:1421）· BK-58 教练祈使句（rules.py:355-587）· BK-59 pbw 幂等标记自毁（policy_bottom_watch.py:768）· BK-61 publish orphan 残余（publish_packs_local.py:226/273）。
  P0 数超 EXECUTION_PLAN「≤10」建议值的原因与修复分组顺序已写在 BACKLOG 文末。

## 6. 文档生成（模块摘要）

- `docs/architecture/modules/` **76 份**（11 包 + 65 顶层 .py），每份含：职责（文件头 docstring 原文摘录）/ 输入输出（docstring 自声明提取）/ 依赖谁（AST import）/ 被谁依赖（全仓反查）/ 关键阈值与环境变量（正则提取）/ 顶层结构 / 已知坑（约 35 个核心模块人工补写自 MEMORY 事故记录 + 审查报告）。
- **抽样核对 5 份**：`limit_stats` / `main` / `database` / `flash_brief` / `sector_strength` —— 职责与 docstring 一致、依赖清单与 import 一致、坑位与 MEMORY/审查结论一致 ✅。
- **发现的文档-代码冲突（均以代码为准并已在文内标注）**：
  1. `EXECUTION_PLAN.md` 记「backend/app 顶层模块 66」——实测 **11 包 + 65 顶层 py = 76 个单元**（数量以代码为准）；
  2. `docs/ops/Agent知识库_完整操作手册_v3.1.md`（2026-09-04）中的评分/提示词规则与现行代码多处不一致（其 INDEX 行已标"冲突处以代码为准"）；
  3. `CONTEXT.md` 自述停在 2026-09-06（其头部已有警示，INDEX 标 archive）；
  4. `docs/architecture/战法体系框架/战法体系重构` 两份框架文档已被 `docs/plans/PLAN_战法体系重设计_20260926.md` 融合版吸收（INDEX 标 superseded-by）。

## 7. 重构建议（`reports/llm_batch/refactor.md`）

条目 **10 条**；按类型：重复实现 3（涨跌停/缓存×7 套/日期三套并存）· 超长函数 Top6（81 个 >120 行，最大 381 行）· 隐式全局状态 1（`global` 93 处）· 语义矛盾/漂移 4（空结果缓存、overlay/gate 筹码网格、convolve 零填充、mainflow 索引）。

**Top 5（收益/风险）**：
1. **R1 涨跌停残余两处近似改真值**（events/signal.py、paper_trading.py）——收益：口径全站唯一、20cm 不再高估；风险：E2/模拟盘历史结论按旧口径，切换须预登记披露。
2. **R2 缓存五件套统一 cache_kit**——收益：消除"只缓存成功=没缓存/字节上限缺失"类 egress 事故复发面；风险：核心缓存迁移需三路径回归，建议只约束新增。
3. **R3 日期门禁**（修 market.py:539 + grep 门）——收益：`写入时刻≠数据所属时刻` 已踩四次的模式被工具拦截；风险：≈0。
4. **R6 空结果缓存语义统一**（gate.py:201 vs flow 惯例）——收益：price_pos 漂移与整表读黑洞同时消除；风险：闸门重算频率上升（有 30min 结果缓存兜底）。
5. **R10 mainflow_history 补 code 索引**——收益：gate/回放路径点查化（该表是 10-21 egress 事故主角）；风险：索引占用，语句需幂等。

## 8. Skills（`.codebuddy/skills/`，3 个）

| Skill | 触发条件 | 红线摘要 |
|---|---|---|
| `新增观察器/SKILL.md` | 新增"观察/监测/雷达/清单"类需求，或旧观察器加维度 | 零决策链（不改分/不进闸门/不碰模拟盘）、不给指令式建议、日批任务键先 grep 防同名覆盖、同一数据只渲染一处、缺失≠0、失败不拖垮日批 |
| `数据包发布与回滚/SKILL.md` | 发布/更新三类数据包、包疑似回滚、前端部署 | 禁止本地 publish 旧包（orphan 重建=10-24 事故）、日期用最近已完成交易日、`--allow-downgrade` 须人工确认、符号链接须实体化、zzshare 只追加不整包替换 |
| `LLM接入模板/SKILL.md` | 新增/改动任何 LLM 功能点、单条调用合并批量、LLM 事故复盘 | 默认不自动触发、一次刷新最多 1 次且批量打包、按日缓存+sig 指纹、批量实验不写 `llm_usage_daily`、结构化 JSON+代码校验+降级模板、降级路径也要缓存、观点输出走 InsightBody/md-body |

## 9. 风险与遗留

**需要用户确认/拍板的（未自动处理）**：
1. **删除文件清单**（红线要求先确认，本次一个未删）：
   - 根目录遗留临时脚本 4 个：`_tmp_dr_check.py`、`_tmp_live_etf.py`、`_tmp_pack_cost.py`、`_tmp_runs_today.py`（DEV_CHECKLIST §7"用完即删"，判定可删）；
   - `frontend/dist/`（构建产物入库，污染全仓检索；**删除前必须先确认 CI 是否依赖仓内 dist**，见 refactor.md R7）；
   - 本次会话的 4 个 `_tmp_*.py` 工具脚本已自行删除（未入库）。
2. **口径不一致、无法判定哪个是对的**：
   - `events/signal.py:37` 用 `>=9.9` 近似 vs `limit_stats` 真值口径——修正是"对的"，但 **E2 历史结论按旧口径产出，切换须预登记**（建议按 BACKLOG BK-05 走预登记流程，由用户拍板时点）；
   - `overlay.py` 截 260 根 vs `gate.py` 全历史筹码网格（MAINFORCE_MODE=off 下无实际影响，二选一需定）；
   - `scenario_watch` 的 `SC_SH_LAST=3574` 与文档"<3550 空"漂移（以 env 实际值为准，文档需改）。
3. **环境/协议**：
   - 本次未做任何**外网调用**（审查为只读；未运行任何需要外网/数据库的脚本）——`/api/health` 线上验证、pytest 在 CI/Render 的接入、`DATABASE_URL` 相关行为均未实测；
   - pytest **未在** `backend/requirements.txt`（本次 `pip install pytest` 仅本地）——是否把 `backend/tests/generated/` 纳入 CI 需用户决定；
   - 平台并发上限（2 个子代理）导致 P1 全量 174 文件未逐一深审：已深审 P1 核心运行时 ~25 文件（scheduler/coach/watchers/pack 脚本），其余 P1（多为一次性研究脚本）与 P2 376 文件以 `_pattern_scan.md` 确定性模式扫描覆盖——**该覆盖缺口如实披露**；
   - 仓库中存在**非本次产生的未提交改动**：`.codebuddy/plans/板块联动筛选机制设计_….md`（+183/−87 行，会话开始前即存在），本次未触碰、未提交，请用户自行处置。
4. **红线遵守情况**：未删文件、未改生产变量/Secrets/线上库结构、未运行 `publish_packs_local.py`、批量产出全部落 `docs/` 与 `reports/llm_batch/`、未触碰 `llm_usage_daily`、前端零改动（无需 push/Actions 部署）。

## 10. 复现命令

任何人按以下顺序可重跑出同样结果（Windows Git Bash；Python 3.12，fastapi/pandas/numpy 已装）：

```bash
# 0) 文件清单（manifest，若需重生成）
python scripts/build_llm_manifest.py            # → reports/llm_batch/manifest.json（606 文件/183k 行）

# 1) 文档归档验证
ls *.md                                          # 应只剩 README/CONTEXT/DEPLOY/EGRESS/logs 5 个
git log --follow --oneline -- docs/plans/PLAN_NEXT.md   # 可追溯到迁移前提交
#   链接检查（临时脚本 _tmp_doclink_check.py，提交前存在于工作区、已删；逻辑=扫描全部 .md 的
#   ](.md) 链接并按相对路径解析，重写失效项为 docs/ 新路径）

# 2) 模式扫描 + 模块摘要底稿（临时脚本，用完即删；要点如下）
#   _tmp_scan_patterns.py   → review/_pattern_scan.md（9 组正则 + AST 长函数 + TODO 盘点）
#   _tmp_module_extract.py  → review/_module_extract.json（docstring/imports/envs/被依赖）
#   _tmp_gen_modules.py     → docs/architecture/modules/*.md（76 份）
#   _tmp_quality_check.py   → review/quality.json（随机 20% 抽检行号+原文逐字比对）
#   上述脚本均已按 DEV_CHECKLIST"临时脚本用完即删"处理，未入库；如需复原可依本节描述重写

# 3) 测试
python -m pytest backend/tests/generated/ -q     # 期望：64 passed

# 4) 审查报告与质量
python -c "import json; q=json.load(open('reports/llm_batch/review/quality.json')); print(q['line_exists_rate'], q['quote_match_rate'])"
#   期望：1.0 1.0

# 5) 日批任务一致性（BACKLOG BK-47 的验证口径）
python scripts/daily_batch.py --list             # 对比 TASKS 键集 vs DEFAULT_ORDER 长度
```

审查批次（12 份）的输入提示词与文件清单在各报告开头「审查范围」行；抽检脚本以 seed=42 可复现同一批 43 条样本。
