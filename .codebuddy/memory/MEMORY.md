# 长期记忆（stock-scoring-v2）

## 架构与运行形态
- 单进程 FastAPI（`backend/app/main.py`）提供 `/api/*` 与前端静态页；生产用 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，**不要 `--reload`**。
- **前端生产部署 = GitHub Pages 自动**：`.github/workflows/deploy-preview.yml` 用 **pnpm** 构建并推 `gh-pages`，主入口 `https://klen-h.github.io/stock-scoring-v2`；Render 后端仅兜底 `frontend/dist`。
- 数据库默认 SQLite，设 `DATABASE_URL` 走 PostgreSQL（Supabase 东京）；`app/database.py` 自动转换 `%s` 占位符兼容两者。
- 调度器：`app/flash/scheduler.py` 起约 30 个 asyncio loop，按日幂等。**LLM/轻量推送类用 `asyncio.create_task`**（保留），重活走 `*_heavy`（`RENDER_READ_ONLY=1` 全关）。
- 决策简报：盘后 Actions 日批生成，`trader_briefs` 按 `(date, phase)` 落库；盘前 Render 09:10–11:30 窗口幂等生成。盘后链路：K线 15:30 → 指标 16:40（Actions）→ 快照 18:00 → 主线/消息分/日报 19:15+。

## 数据与缓存
- `kline_cache` 存原始 OHLCV（`CACHE_POOL_SIZE=500`，`CACHE_KLINE_COUNT=500`），服务详情页与筹码/主力叠加；`indicator_cache` 存预计算指标序列，服务评分链路（`batch/top/bottom`、快照）。
- `indicator_cache` 有效期 `MAX_INDICATOR_AGE_HOURS=36`；`kline_count` 落在 (0,250) 视为截断、指标跳过。
- 缓存命中决定评分吞吐：命中时并发 10 无 sleep，否则并发 3 + `sleep(0.3)` 防 WAF。
- 版本门控 `app/sync_meta.py` 管理日更远端数据；研究脚本走 `app/research_cache.py` 并优先读本地包。
- 两条链路并存：后端读 DB 包；前端本地评分读 GitHub Pages 的 `kline-pack` + `indicators-pack`。**`backend-pack.db.gz` 只给 Python，浏览器不读**。
- 已知待修：盘中技术面是昨收；`score_single` 实时算与 `batch/top` 缓存算盘中不同分；`incremental_update` 是死代码；`score_snapshot_loop` 15:15 早于数据刷新。

## 部署与资源
- 后端常驻 ≈100 MB；云上最低 2核2G（需 swap），**建议 2核4G**。
- 生产 Python 3.9；禁用 PEP 604（`str | None`），用 `Optional` 或 `from __future__ import annotations`。
- FastAPI 路由：没有 `await` 的必须写 `def`，禁止假 async 阻塞事件循环。

## 战法推送白名单
- 动态计算（`app/strategies/recommendation.py`），从 `strategy_results` 重放撮合；`PUSH_STRATEGY_WHITELIST` 仅兜底。
- 低胜率 + 高盈亏比体系；判据看均收益/盈亏比，**不看胜率**。
- 可配：`WHITELIST_CRITERION`（默认 win_rate）、`WHITELIST_MIN_AVG_RET`（0.3%）、`WHITELIST_MIN_PROFIT_FACTOR`（1.2）、`WHITELIST_MIN_SAMPLES`（30）。
- 双轨 + 半衰期监控（2026-09-13）：全期 + 近 250 交易日任一轨跌破即暂停；后半段胜率 < 前半段 ×50% 告警。

## 环境传导链
- 四层合成：`app/mainforce/confluence.py` = 宏观 ±2 + regime ±2/±1 + 板块 ±1（拥挤主线只减不加）+ 个股 mainforce_state ±2，sum(-7~+7)。
- 2026-09-13 修复：补 `db_fetch`、行业名归一化；修复后板块层从长期 0 恢复真实值，会改变判级。

## 交易员教练 Coach
- 模块：`rules.yaml` · `rules.py` · `audit.py` · `monitor.py`。
- 红线：数字代码注入；30s 轮询只读 market 缓存；`coach_loop` 挂 `asyncio.create_task`；执行一致性分母=已决策。
- 持仓源：`paper_positions` + `user_portfolio`；真实持仓默认止损 = 成本 × 0.92。
- W2（2026-09-15）：前端教练 Tab + 执行回写 + LLM 翻译层 `explainer.py`（LLM 输出禁数字）+ 模拟盘剧本闭环（`coach_plans` 含 status/abandon_reason/closed_at/followed，平仓自动关 / 主动放弃接口 / 预承诺执行率）。
- API：`/api/coach/alerts|consistency|abandon-reasons|plans/execution-rate|plans|plans/{id}/abandon`。

## 项目约定
- 根目录只保留 `PLAN_<日期>.md`（现行）与 `PLAN_ARCHIVE_<日期>.md`；旧专题 plan 已归档。
- 改动后"先验证再提交"；未明确要求不自动 git commit。

## 主力节奏跟随框架（2026-09-16，项目北极星/底层座基）
- **一句话**：跟对主力节奏，但永远慢一步——**识别状态、顺应方向、不猜时点**。
- **三层节奏模型（可靠度递减）**：①状态（吸筹/拉升/出货/洗盘，`mainforce_state`，有回测背书→可决策）②方向（flow5 倒U/北向/两融持续趋势，IC 正但单日噪声大→看连续趋势）③时点（哪天进撤，样本少→仅形态参考，**永不进决策链**）。
- **红线（区分跟节奏 vs 追涨杀跌）**：跟「已确认的节奏」（慢一步），不抢跑「未发生的节奏」；盘中观察→盘后确认→次日执行。
- **"主力"= 可观测代理指标，非机构意图**（大单轧差口径、解释非事实、东财/新浪两源不可互校验）。
- **现有模块已隐式执行**：评分资金面=方向、主力标签=状态、白名单闸门=状态闸门、教练=纪律、风控=逆势止损、组合分=错杀识别。不重构，只标注归位。
- 详见 `主力节奏跟随框架_20260916.md`（北极星/认知论）+ `主力节奏框架_开发计划_v1.0.md`（机制/验证设计，能直接开工）；事件应对是其具体实例（`PLAN_EVENT_PLAYBOOK_20260915.md`）。
- **★ 关键区分（开发地基）**：flow5 IC / accum/distribution 标签全是**横截面证据**（选股），而「跟节奏」座基是**时间序列命题**（市场级流入期该重仓）——横截面证据 ≠ 时间序列证据。显式拆成命题A（横截面，归选股）+ 命题B（时间序列，当前=4次FOMC+半年≈零证据，Phase 0 待验证核心）。
- **不对称现实·防守优先**：识别出货灵(-7.5pt)、识别吸筹不灵(+1.1pt)；流入是脉冲、流出是趋势 → 座基第一形态大概率是防守型（回避流出），进攻侧挂 Gate B 通过后。
- **事件应对日期口径铁律**：D0=A股反应日（美东决议日+1 的北京交易日）；曾因标签偏移把结论说反，务必写清 D-1/D0/D+1。
- **Phase 0 画像结论（127 日，Gate 判定书 2026-09-16 二审）**：Gate A NO-GO（episode 与随机切割不可区分，null 模拟 p=0.473）、Gate B 进攻侧无信号（B2 t=0.286）、防守侧=价格状态别名（控制后 -1.94%→-0.69% 不显著，provisional）、净产出两条半（反转纪律 R_{t-1}=-0.169 为主 + B3 涨日主力净卖为注脚；原「顶部选择器-236bp」控制后塌缩并入反转）。**时间序列半边未 KILL，等宽度重算器（三年价量代理）终判**。

## 工程方法论（评审沉淀，2026-09-16 记入制度）
- **「形式达标是 null 的预言」**：白噪声累计=随机游走，zigzag 切随机游走天然产生"n≥5、中位≥3、θ平滑"的表象。任何"节拍/周期"类检验必须先跑 null 模拟（bootstrap 重采样摧毁时序结构）对比，否则把随机性当结构。
- **代理/重算前必查原判据的数据依赖**：dist 有纯价量退化路径、accum 依赖资金流无退化路径——不查就重算会死于"不知道自己代理丢了什么"（代理的代理，失败不反证原标签）。
- **控制变量是生死项（1:20 打样）**：小样本先跑控制回归；表观效应被价格状态解释掉 2/3 是常态（-1.94%→-0.69%）。
- **稀有标签的一致性指标有基数率 bug**：原始一致率被「双方都无标签」格点主导，垃圾代理也能 ~81%。必须 sensitivity/precision/κ 条件化，且先算理论上限。
- **工程失败 ≠ 科学证伪**：仪器坏了（一致率不过）命题只是"暂不可检验"（可重开），只有仪器有效下主检验失败才是真 KILL。三终局必须分开写。
- **未控制的归因数字不可进结论**：对照组 -236bp 未控制 → 控制后 -1.08% 不显著。归因型结论必须过同款控制回归。
- **失败条款先于结果落盘 + 角色定义「不是翻案」**：预注册写死 GO/DOWNGRADE/KILL 与失败条款，防看完结果改规则（forking paths）。

## 本机环境 / 用户偏好
- Windows 本机 Python DNS 对跨国域名间歇故障（路由器 DNS 抖动），重试即可；生产无影响。
- 日报不推企微，只在前端 `/report` 查看。
