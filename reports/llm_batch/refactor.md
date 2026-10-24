# 重构建议（refactor.md）

> 生成：2026-10-24 · 全项目清理（`docs/CLEANUP_REPORT.md` §7）。
> **只给建议不改代码**。每条含：位置（file:line 均经核对）｜建议｜收益｜风险。
> 数据来源：`_pattern_scan.md`（AST 长函数 + 确定性模式扫描）+ 审查报告
> `P0_mainforce.md` / `P0_routers_scoring_market.md`（引用均已回验）。

## 总览

| 类型 | 条目数 |
|---|---|
| 重复实现（口径/缓存/日期） | 3 类 8 条 |
| 超长函数（>120 行，AST 实测 81 个） | Top 6 |
| 隐式全局状态（`global` 语句 93 处） | 3 条 |
| 语义矛盾/口径漂移 | 3 条 |

---

## 一、重复实现

### R1 涨跌停口径：正文链路已收拢，残余两处近似实现
- 位置：`backend/app/events/signal.py:37`、`:237`（E2 事件信号用 `change_pct >= 9.9` 近似涨停，
  有 `note` 字段自我披露）；`backend/app/strategies/paper_trading.py:75`
  （`close / prev_close - 1) <= -0.099` 跌停一字守卫）。
- 建议：`events/signal.py` 改用内存行情真值限价（腾讯快照 [47]涨停价/[48]跌停价，
  `limit_stats.is_limit_up/down` 已封装）并保留 note；`paper_trading.py:75` 改为
  `price <= limit_down` 真值比较（`limit_down` 缺失时才退回板幅口径）。
  `strategies/*` 的 `>=9.5` 是**战法定义**（改它作废既有回测证据，须预登记），**不动**。
- 收益：家数/闸门口径全站唯一，20cm 股事件信号不再高估（实测口径差最高 150 vs 82 家）。
- 风险：E2（涨停潮）与模拟盘卖出顺延的历史结论按旧口径产出——切换必须预登记披露口径变化，
  建议与 `SIGNAL_STATUS` 联动标注。

### R2 缓存实现：至少 7 套各自为政
- 位置：`app/tencent._cache`（内存 dict+lock）、`scoring/kline_cache.py`（表，36h）、
  `scoring/indicator_cache.py`、`research_cache.py`（进程内+磁盘）、`industry_map.py`（1h）、
  `finance.py`（24h）、`strategies/recommendation.py`（6h 白名单）。
- 已知事故型差异："只缓存成功=没缓存"（`_usd_5d_change` 曾 32h 整表读 515 次）、
  "字节上限缺失⇒超限整体清空"（`_PRICES_BARS_MAX`/`_FLOW_MAP_ROWS_MAX` 才是正确姿势）、
  "多进程各持一份进程内缓存⇒TTL 收益打折"。
- 建议：抽 `app/cache_kit.py` 统一「条数上限 + 单条上限 + 取用时物理删除 + 字节级上限 +
  降级路径短 TTL」五件套（MEMORY §4 已沉淀语义），新缓存一律用它，旧缓存渐进迁移。
- 收益：消除"缓存形同虚设"类 egress 事故的复发面（1.465GB/天 事故的三大驱动之一）。
- 风险：核心缓存迁移需逐个验证"首次生成/命中/失效"三路径；建议只约束新增，不批量改旧。

### R3 日期处理：三套并存
- 位置：`flash/rules.beijing_now()`（唯一正解）vs `datetime.now()` 无时区 162 处
  （多为耗时测量/日志，无害但难分辨）、`datetime.utcnow()` 5 处、
  `routers/market.py:539` `fromtimestamp` 未带 tz（生产 UTC ⇒ 显示差 8h，同文件已两次裁决同型坑）。
- 建议：修 `market.py:539`（一行）；加一个**提交前 grep 门**（`_tmp_scan_patterns.py` 的
  `utcnow`/`fromtimestamp` 模式并入 CI 或 pre-commit），新增 naive 时刻处理直接拦。
- 收益：`"写入时刻≠数据所属时刻"` 已踩四次（MEMORY §2），门禁比约定可靠。
- 风险：几乎为零（纯检查）。

---

## 二、超长函数（AST 实测 81 个 >120 行；Top6）

| 位置 | 函数 | 行数 | 建议 |
|---|---|---|---|
| `backend/app/daily_report.py:536` | `build_data_md` | 381 | 按"取数/计算/渲染"拆三层，每层可独立单测 |
| `backend/app/routers/market.py:1472` | `market_emotion` | 271 | 竞价/盘中/收盘三段各自成函数（写入分支已三组互不覆盖） |
| `backend/app/routers/scoring.py:1271` | `score_top` | 251 | 池构建/精算/新闻分/板块联动四段拆分；顺带解决审查发现的同步阻塞问题 |
| `backend/app/trader_brief.py:984` | `build_decision_card` | 246 | 数据收集（`collect_brief_data` 已有）与渲染分离 |
| `scripts/backtest_ma_convergence.py:256` | `main` | 295 | 一次性研究脚本，**低优先**；改前先归档结论 |
| `backend/app/strategies/recommendation.py:142` | `_recompute_whitelist` | 188 | 白名单重算与 G 过滤/v2 退出耦合，拆出"撮合重放"纯函数 |

- 收益：评审/回归成本显著下降；`score_top`/`market_emotion` 拆分可顺带做 fail-open 化。
- 风险：纯重构不改行为 ⇒ 必须 `py_compile` + 接口响应 diff（同输入同输出）+ 前端关键卡回归；
  Render 0.1 CPU 上一次只迁一个函数。

---

## 三、隐式全局状态

### R5 `global` 语句 93 处
- 位置：集中在缓存（`_limit_pct_fn`、`_BREAK_TOL_CACHE`、`_EMOTION_SAVED`、`_cache`）与
  调度标志。已发生一次同型事故：`limit_stats.py:34` 注释记录的"缓存变量与函数同名 ⇒
  函数对象覆盖全局 ⇒ TypeError，py_compile 查不出"。
- 建议：新代码缓存统一 `cache_kit`（R2）；模块级可变状态用单一 `_STATE` 命名空间 +
  命名规范（`_CACHE_*` 不得与函数同名，AST lint 可查）。
- 收益：消除"只有真调用才暴露"类缺陷。
- 风险：低。

### R6 空结果缓存语义矛盾
- 位置：`backend/app/mainforce/gate.py:201`（空浮筹表缓存 6h）vs
  `app/mainforce/flow.py`（"空结果不写盘"，MEMORY §3 惯例）。
- 建议：统一为"空结果短 TTL（≤10min）缓存 + 显式 `reason`"，两者对齐到同一语义。
- 收益：price_pos 漂移（审查 P2）与整表读黑洞两个失败模式同时消除。
- 风险：需验证闸门重算频率上升对 egress 的影响（gate 已有 30min 结果缓存，可控）。

### R7 前端构建产物入库
- 位置：`frontend/dist/assets/*.js` 在仓库内（manifest 也把它当源码统计）。
- 建议：确认 Pages 部署是否依赖仓内 dist；若不依赖，列删除清单待用户确认后 `git rm`
  （Guardrails #0：删除需确认）；若依赖（Render 兜底 dist），至少从 manifest 排除。
- 收益：检索/审查噪声消除（dist 内压缩 JS 会污染全仓 grep）。
- 风险：若 CI 依赖仓内 dist，删除会破坏部署——**先查 `.github/workflows` 再动**。

---

## 四、跨模块语义漂移（审查发现的归纳）

### R8 `overlay.py` 与 `gate.py` 的筹码网格口径不一致
- 位置：`overlay.py:96` 截 260 根计算筹码 vs `gate.py` 明示拒绝截断（全历史网格）。
  同一 0.75 获利盘阈值在两处语义不同。MAINFORCE_MODE=off 下当前无实际影响。
- 建议：二选一固定口径（建议全历史，与 gate 一致），在两处文件头互相引用。
- 收益/风险：未来若接线 overlay（须预登记），先统一才能比较结论；当前改动零行为影响。

### R9 `chips.py:96` `np.convolve(mode="same")` 零填充
- 位置：无 float_shares 兜底路径的 MA120 量基准在序列头尾被零稀释 ⇒ 换手率高估约一倍，
  恰污染最常被读取的末根。
- 建议：改 `min_periods` 式手写窗口均值（前段用可用均值，与代码内 `vols[:i+1].mean()` 兜底一致）。
- 收益：相对量代理路径（无流通股本个股）的获利盘/price_pos 修正。
- 风险：改变该路径数值 ⇒ 已产出的 `mainforce_state` 历史与新值有阶跃；须在日报披露口径切换日。

### R10 `mainflow_history` 缺 code 索引
- 位置：`backend/app/mainforce/flow.py:711`（逐股查询全表扫；`lhb_history` 同场景已补索引）。
- 建议：`CREATE INDEX IF NOT EXISTS idx_mainflow_code ON mainflow_history(code, trade_date)`。
- 收益：gate/回放路径逐股读从全表扫变索引点查（该表是 2026-10-21 egress 事故主角之一）。
- 风险：索引占空间；建索引语句需幂等。
