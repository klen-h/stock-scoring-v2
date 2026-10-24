# Skill：新增观察器（Observer）

> 目标：把"组合既有数据 → 观察结论"的新增需求做成**合规、可运维、可退役**的观察器，
> 一次做对，避免重蹈教练卡/时间线/情绪数字"同源两处渲染"与"观察器混进决策链"的覆辙。

## 触发条件

- 用户要求"新增一个观察/监测/雷达/清单/提示"类功能（如：拥挤度、关键位、市场底清单、承接盘）。
- 需要把 2 个以上**既有**数据源组合出一条新观察结论。
- 旧观察器要加新维度（走本流程的"改"分支）。

## 前置检查（动手前）

1. `grep -rn "def task_" scripts/daily_batch.py` + `grep -n '"<任务键>"' scripts/daily_batch.py`
   —— 确认日批任务键/函数名不冲突（同名 `def` 会**静默覆盖**旧任务，编译不报错）。
2. 检查该数据是否已有展示位（Guardrails #6：同一份数据只渲染一处）。
   有 ⇒ 在原处增强，不复制卡面。
3. 确认数据源限额与降级路径（`docs/DEV_CHECKLIST.md` §3）：东财会 403、zzshare 有 token、
   腾讯全量刷新 ~14s 只能在调度器跑。

## 步骤

1. **预登记声明写进模块头**：定位（纯观察器/零决策链）、判据、阈值与依据、不做什么。
   样板：`backend/app/policy_bottom_watch.py`、`backend/app/crowding_watch.py`。
2. **模块实现**（`backend/app/<name>_watch.py`）：
   - 阈值全部 env 可调（`SC_*`/`<PREFIX>_*`），默认值写进模块头注释；
   - 缺失数据 ⇒ 显式 `insufficient`/`None`，**缺失 ≠ 不满足、缺失 ≠ 0**；
   - 失败返回 `ok=0`/空结果，**不抛异常拖垮日批**；日志一律 ASCII。
3. **按日缓存 + 幂等落库**：建表（`date` 主键或按日 upsert）；重跑同日不重复推送/不重复计分。
4. **日批任务注册**（`scripts/daily_batch.py`）：
   - 任务键唯一（先 grep）；排在依赖任务**之后**、日报**之前**；
   - 失败不拖垮整批（单独 try + 显式 raise 计数，**不允许 except:pass**）；
   - 跑一次 `python scripts/daily_batch.py --list` 确认 `len(TASKS)` 与 `DEFAULT_ORDER` 一致。
5. **接口**（只读）：`GET /api/system/<name>-watch`；60s 级轮询必须读表/缓存，接口内**禁止**现算全池。
6. **前端卡**：
   - 新开卡前再确认一次"只渲染一处"；复用 `displayMeta.js` helper，不许页面内重定义；
   - 深色令牌 `bg-bg/bg-card/border-border/text-muted/text-rise/text-fall`；
   - 工作台内跳转 `target="_blank" rel="noopener"`；
   - 模板新增绑定必须 grep 脚本侧声明（未声明变量 build 不报错、运行时才炸）；
   - `npm run build` 必须 0 error。
7. **日报/简报小节（可选）**：确定性渲染，不经 LLM；同一数据仍只渲染一处。

## 红线（违反即回滚）

- **观察器不改评分、不进买入闸门、不碰模拟盘**；不给"买入/卖出/加减仓/止损止盈"指令式建议，
  只描述价格行为与事实（Guardrails #5）。
- 不写 `llm_usage_daily`；默认不自动触发 LLM（要接 LLM 走《LLM 接入模板》skill）。
- 接口内禁止全市场同步拉取；fail-open（辅助块失败只丢该块）。
- 时点诚实：每张卡能说明数据截至哪天（`as_of`/`data_date`）。
- 研究数据不落 PG（写本地 SQLite）；批量实验不写线上日熔断表。

## 验证方式

```bash
python -m py_compile backend/app/<name>_watch.py scripts/daily_batch.py
python scripts/daily_batch.py --list                      # 任务键唯一、顺序正确
python scripts/daily_batch.py --tasks <name> --force      # 真跑一次：落库 + 空数据路径
python -c "import sys; sys.path.insert(0,'backend'); from app import <name>_watch"  # import 冒烟
curl -s localhost:8000/api/system/<name>-watch | head     # 接口 JSON 正常（缓存命中/冷启动各一次）
cd frontend && npm run build                              # 0 error
```
- 验证三条路径：首次生成 / 命中缓存 / 空数据（insufficient 显式区分）。
- pytest（若新增纯函数）：`python -m pytest backend/tests/generated/ -q`。

## 退役

观察器过时 ⇒ 日批任务摘除 + 接口保留但标 `stopped_since`（外部源静默失效要有显式字段），
文档移 `docs/archive/` 并在 `docs/INDEX.md` 标 `superseded-by`。**不删文件**（Guardrails #0）。
