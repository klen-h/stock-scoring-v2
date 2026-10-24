# P0 审查报告：backend/app/scoring

审查范围：7 文件 / 约 4670 行 / 2026-10-24

审查方式：完整通读 7 个文件，并交叉核实了调用方（routers/scoring.py、flash/scheduler.py、strategies/paper_trading.py、scripts/daily_batch.py）、pack_source 新鲜度判定、前端 scoringEngine.js（scoreFlow5 / nb 钳制 / 对齐表）及关键行的 git 引入时间。未运行任何项目脚本，未修改任何代码。

---

## backend/app/scoring/ranking_history.py

### [P1] backend/app/scoring/ranking_history.py:347 可信度建议直接输出"放心买/可重仓"，与 2026-10-11 评分 KILL 预登记冲突
- 原文: `        return "放心买，极强共识"`
- 问题: `_trust_grade`（:330-341，A+ 注释"极强共识，放心买"）与 `_trust_advice`（:344-355，"放心买，极强共识"/"可重仓，持续强势"/"可轻仓"）把 total_score（trust 公式中占 40%，:315 `score_part = min(total_score * 0.4, 40)`）和连续上榜天数直接翻译成买入操作建议，并经 `POST /ranking-persistence`（routers/scoring.py:1939-1949，已核实）返回给前端。
- 影响: 评分已被预登记 KILL（2026-10-11：不构成买入依据），该接口仍在以评分为主成分向用户下发"放心买/可重仓"级建议，属红线上仍在服役的违规出口（git 显示该段 2026-08-21 引入，早于 KILL，非 10-11 后新增，但 KILL 落地时未回收）。
- 建议: 将 advice 文案改为中性口径（如"连续上榜 N 日（观察参考，不构成买入依据）"）或下线 advice/trust_grade 字段，与 KILL 预登记对齐。
- 置信度: 高

### [P2] backend/app/scoring/ranking_history.py:99 import 时即执行 DDL，与 kline/indicator_cache 的 P1-18 懒执行修复不一致
- 原文: `init_ranking_history_table()`
- 问题: 模块被 import 即执行 1 条 CREATE TABLE + 1 条 CREATE INDEX + 3 条 ALTER（:67-94），而 kline_cache.py:66-78 与 indicator_cache.py:60-69 已按 2026-09-13 P1-18 审查改为"首次使用时执行一次"（注释明确记录：每次 DDL 触发 PostgREST schema cache 全量重载 ≈120KB 结构性流量，Render 重启/每个 Actions job/本地脚本都会重放）。
- 影响: 每个进程启动都重放 5 条 DDL，是 P1-18 治理后的遗留漏网；egress 与启动延迟白耗。
- 建议: 照搬 `ensure_kline_cache_table` 模式改为懒执行（`_init_done` 标志 + 失败不置位），文件内各函数入口补 `ensure_ranking_history_table()`。
- 置信度: 高

### [P2] backend/app/scoring/ranking_history.py:797 收益验证用自然日比较，周末会把最新快照"伪验证"成约 0 收益
- 原文: `            if now_p > 0 and price > 0 and d < today:`
- 问题: `today = _today_str()` 是北京自然日；周末/节假日访问时，最新交易日（如周五）快照满足 `d < today` 被判"已验证"，而三级兜底现价（:681-744）在周末取到的也是周五收盘，returnPct ≈ 0，却计入 winRate/avgReturn。
- 影响: 周末打开回查页时最近交易日的 winRate/avgReturn 被"零推进"记录污染（≈50% 计为胜），面板统计失真；与注释本意"次日才计算"（指下一交易日）不符。
- 建议: 判定改为 `d < latest_completed_trading_day()`（与 rules 同源），非交易日不新增验证记录。
- 置信度: 中

### [P2] backend/app/scoring/ranking_history.py:138 replace_day 先删后逐行插非事务，中途失败固化"半日快照"
- 原文: `        db.execute("DELETE FROM ranking_history WHERE rank_date = %s", (today,))`
- 问题: DELETE 与后续逐行 INSERT 之间无事务；日批/手动权威快照写到一半异常时当天只剩部分行，且之后 `only_if_empty=True` 的盘中兜底（routers/scoring.py:1234，已核实）会因"当天已有记录"跳过，缺口永久保留到次日。
- 影响: 权重优化/胜率回查在该日的样本不完整（如 Top50 只剩前十几只），且不易察觉。
- 建议: 删除+插入放入同一事务，或改为先写临时键再原子更名/交换。
- 置信度: 中

### [P3] backend/app/scoring/ranking_history.py:886 固定 T+N 的交易日历取自快照日期去重，缺快照日会压缩持有窗口
- 原文: `    cal_asc = list(reversed(_trading_days(120)))   # 升序交易日`
- 问题: 交易日历来自 `ranking_history` 的 DISTINCT rank_date 而非 rules 交易日历；某交易日日批失败（无快照）或历史脏行都会使 `idx` 错位。
- 影响: 跨缺口的快照 T+5 实际变成 T+4 或更短，收益窗口口径漂移，分桶统计结论被稀释。
- 建议: 用 `app.flash.rules` 的交易日历（HOLIDAYS/交易日判断）生成 cal_asc，快照日期只做样本筛选。
- 置信度: 中

### [P3] backend/app/scoring/ranking_history.py:377 auto_record_ranking 全仓无调用方，且其写入口径与权威快照相悖
- 原文: `    results = engine.score_batch(valid[:200])  # 只算前200只`
- 问题: backend/scripts 全量搜索仅定义处（backend/app/scoring/ranking_history.py:359），无任何调用；一旦被复用，它会以简化分（无 dimensions、无交易日参数、默认 upsert）覆盖当日已落库的精算快照。
- 影响: 当前无运行时影响；属口径陷阱/死代码。
- 建议: 删除，或改造为 `record_daily_ranking(top_stocks, only_if_empty=True, date=_batch_trading_day())` 后再启用。
- 置信度: 高

### [P3] backend/app/scoring/ranking_history.py:964 get_composite_stats 的 days 参数未参与过滤，报告窗口标注失真
- 原文: `    records = get_verified_records(min_age_days=2, horizon_days=horizon)`
- 问题: `days` 只回显进 `result["days"]`，样本实际取全部历史（get_verified_records 内无 days 约束，:843-850）。
- 影响: 调用方以为看的是近 90 天口径，实际样本可能跨数月，结论窗口标注与事实不符。
- 建议: 在函数内按 `rank_date >= now-days` 过滤 records，或移除该参数避免误导。
- 置信度: 高

### 摘要 backend/app/scoring/ranking_history.py
职责：评分 Top50 按日快照落库（ranking_history 表）＋连续上榜可信度＋分桶胜率/固定 T+N 验证＋组合因子前瞻统计｜输入：榜单 stocks（code/score/signal/dimensions/price/mainforce）、backtest_prices 与 kline_cache 价格序列｜输出：快照条数、get_ranking_persistence（trust/advice）、get_bucket_stats、get_daily_rankings（含 sector_stocks 平行字段）、get_verified_records、get_composite_stats｜关键阈值：交易日守卫（周末＋HOLIDAYS，校验目标日而非"此刻"，2026-10-11 修复）；trust＝评分40%＋连续40%（5 天满）＋信号20%；分桶 90/80/70/60；min_age 2 自然日｜依赖：app.flash.rules.HOLIDAYS、backtest_prices、kline_cache、ranking_history_sector（只读平行表）、db｜已知坑：import 即 DDL（P2）；_trust_advice"放心买"与 KILL 预登记冲突（P1）；周末伪验证（P2）；T+N 日历取自快照日期；auto_record_ranking 死代码

---

## backend/app/scoring/kline_cache.py

### [P2] backend/app/scoring/kline_cache.py:316 批量读无 36h TTL 门槛，陈旧缓存只在单只路径被判过期
- 原文: `                if expect and (klines[-1].get("date") or "")[:10] < expect:`
- 问题: `get_cached_klines_batch`（:265-324）SELECT 了 updated_at/kline_count 却从不使用，也没有单只路径的 `MAX_CACHE_AGE_HOURS` 检查（对比 get_cached_klines:236-241）；仅复刻了"缺当日 bar 合成"校验，而该校验只在交易日 15:00 后（expect 非 None）生效。
- 影响: 盘前/周末/节假日（expect=None）任意陈旧的行在批量路径直接返回，而单只路径判过期走实时拉取——批量评分（routers/scoring.py:572、paper_trading.py:416、scheduler.py:1387，均已核实调用）与单只详情口径分叉。
- 建议: 批量查询后按 `updated_at >= now-36h` 过滤（并把 kline_count∈(0,250) 门槛一并对齐）。
- 置信度: 高

### [P2] backend/app/scoring/kline_cache.py:220 pack 通道早退跳过"当日 bar 合成"校验，收盘后与非 pack 通道差一根 bar
- 原文: `                return _k`
- 问题: `get_cached_klines` 的 pack 路径（:215-220）命中即返回，不做 DB 路径的 `expected_kline_date()` + `_append_today_bar` 校验（:248-258）；而 pack 需到交易日 22:00 才判过期（pack_source._PACK_READY_HHMM=(22,0)，已核实），即 15:00-22:00 窗口内 pack 返回的是截至昨日的 K 线。
- 影响: 交易日收盘后、当日包发布前，pack 模式评分少一根当日 bar，与非 pack 模式/详情页（实时 K 线含当日）口径分叉——即 2026-10-11"score_single vs batch/top 不同分"同类问题的收盘后变体。
- 建议: pack 命中后同样走 `expected_kline_date()` + `_append_today_bar(klines, code, expect)` 再返回（合成只读 tencent 内存缓存，零网络）。
- 置信度: 中

### 摘要 backend/app/scoring/kline_cache.py
职责：K 线层 DB 缓存（kline_cache 表，500 根/只 JSON），读侧新鲜度校验＋当日 bar 合成，写侧两轮重试＋退避刷腾讯｜输入：腾讯 get_kline / pack_source / tencent 实时内存缓存｜输出：K 线数组（单只/批量）、缓存状态、MIN_SCORING_KLINE_COUNT 常量｜关键阈值：TTL 36h；MIN_SCORING_KLINE_COUNT=250（读侧门槛在 _precise_score_sync/indicator_cache）；≥30 根才可评分；交易日 15:00 后要求当日 bar；连续失败 ≥10 退避 min(60, n*5)s；轮间停 8s｜依赖：app.tencent（行情缓存/get_kline/refresh_all_stocks）、app.flash.rules（交易日历/beijing_now）、pack_source、db｜已知坑：腾讯日线滞后数小时需 _append_today_bar 兜底；批量读无 TTL（P2）；pack 通道跳过当日 bar 合成（P2）；_refresh_one 失败不落库（防旧数据盖新戳，正确设计）；_tencent_cache_key 对空 code 会 IndexError（调用方均有 try 包裹）

---

## backend/app/scoring/engine.py

### [P2] backend/app/scoring/engine.py:221 NB_TURNOVER_CAP 开关仅后端可关，前端硬编码钳制，开关关闭时两端口径分叉
- 原文: `NB_TURNOVER_CAP_ENABLED = (os.environ.get("NB_TURNOVER_CAP", "1") or "1").strip() \`
- 问题: 钳制开关读后端环境变量，而前端 scoringEngine.js 头部对齐表自注"后端有 NB_TURNOVER_CAP_ENABLED 开关，前端硬编码 ⇒ 后端关开关时两侧会分叉"（frontend/src/utils/scoringEngine.js:44-45，已核实），且没有任何接口把开关状态下发给前端。
- 影响: 一旦设 NB_TURNOVER_CAP=0，同一 _series 两端打分 diff≠0，直接破坏"diff=0"验收基线（neutral_bearish 市高换手股资金面可差 60 分档：100→40 封顶）。
- 建议: 通过 /api/score/weights 或 regime 接口下发该开关状态，前端按状态跳过内联钳制。
- 置信度: 高

### [P3] backend/app/scoring/engine.py:1886 _score_flow5 ≥+20 返回 0，与函数 docstring 锚点"+20→5"矛盾
- 原文: `    if flow5 >= pts[-1][0]:`
- 问题: 代码对 flow5 ≥ +20（含恰好 +20）返回 0.0（:1886-1887），而 :1878 docstring 锚点写"+20→5"；前端 scoreFlow5 同样 ≥20 返 0（frontend/src/utils/scoringEngine.js 已核实），两端实现一致、仅后端注释失真。
- 影响: 无即时打分分叉，但注释会诱导后续维护者按锚点"修正"某一端，反而制造真实分叉。
- 建议: 把 docstring 锚点改为"+20→0（≥20 截断）"，与两端实现一致。
- 置信度: 高

### [P3] backend/app/scoring/engine.py:737 _score_boll 的"带宽收窄"占位代码恒为假（死代码）
- 原文: `        if len(bw_history := [d.get("boll_upper", 0) - d.get("boll_lower", 0)`
- 问题: 列表推导只遍历 `[latest]` 一个元素，且 boll_upper 缺失已在 :706 提前返回，`bw_history` 恒长 1，`== 0` 恒 False，分支体只有 `pass`。
- 影响: 无功能影响，但误导读者以为带宽收窄已参与评分。
- 建议: 删除 :737-739 三行，或补真实历史带宽序列后再启用。
- 置信度: 高

### 摘要 backend/app/scoring/engine.py
职责：五维多因子评分（技术32/资金20/基本面18/成长18/质量12，纯业务无 IO）＋信号/加减分因素/买点/趋势健康输出｜输入：技术指标数组（≥30 条）、实时行情 dict（pe/pb/turnover_rate/amplitude/flow5_amt…）、基本面 dict｜输出：ScoreResult（total_score/signal/signal_level/dimensions/factors_up/down/buy_point/trend_health）｜关键阈值：信号 80/65/45/35/20（任一维度<20 不得强烈买入）；子项锚点（RSI 70-80→90 最强、换手 1-3%→90 等）；flow5 倒U锚点；nb 市换手>8% 资金面封顶 40（env 可关）；行业分位样本 <10 降级绝对曲线；金融按名称拆银行/证券/保险子桶｜依赖：app.backtest.market_regime、app.tencent._cache（PE/PB 行业分布）、stock_industry×stock_finance（24h TTL＋本机 industry_dist_src.json 7 天缓存＋pg_stat 写入计数指纹）、前端 scoringEngine.js 逐项对齐｜已知坑：_round1 +1e-9 平局对齐前端；缺失维度 None 不参与加权（时点诚实）；NB 开关前端硬编码（P2）；flow5 注释锚点失真；_score_boll 死代码；_IND_DIST 为模块级可变缓存（无锁）

---

## backend/app/scoring/indicator_cache.py

### [P2] backend/app/scoring/indicator_cache.py:713 增量更新整行覆盖 indicators JSON 但不含 _series，预计算指标序列被抹掉
- 原文: `                indicators = EXCLUDED.indicators,`
- 问题: `save_incremental_update` 组装的 indicator_fields（:685-701）只有顶层指标＋_state，没有 `_series`，ON CONFLICT 时用该 JSON 整体替换旧行；此后 `get_cached_technical` 因 `_series` 缺失返回 None（:144-147），`get_cached_technical_batch_sql` 也跳过该股。
- 影响: 盘中调用一次 `POST /score/indicator-cache/incremental`（前端 api/index.js:289 已接，已核实）即抹掉该股预计算序列，批量精算预载缺股→退回逐只兜底（慢）或该股缺席预计算池，直到下一次全量 refresh_indicator_cache。
- 建议: 增量落库前读回原行 JSON、合并 `_series` 后再写（或 UPDATE 仅替换顶层字段、保留原 _series）。
- 置信度: 高

### [P3] backend/app/scoring/indicator_cache.py:239 get_cached_indicators_batch 无 TTL/kline_count 门槛，且全仓无调用方
- 原文: `            if indicators.get("ma5") is not None:`
- 问题: 该批量函数（:216-244）不检查 36h 有效期、不做 (0,250) 截断门槛（对比 get_cached_technical_batch_sql:188-211 同口径门槛），backend/scripts 全量搜索无调用方。
- 影响: 当前无运行时影响；若被启用会把过期/截断缓存的指标直接放行进评分。
- 建议: 删除；或启用前补齐与单只/SQL 批量路径一致的 TTL＋kline_count 门槛。
- 置信度: 高

### [P3] backend/app/scoring/indicator_cache.py:570 incremental_update 无"同日只推进一次"守卫，重复调用按多根新 bar 平滑
- 原文: `    ema12_new = new_price * alpha12 + ema12_old * (1 - alpha12)`
- 问题: 每次调用都当作一根新 K 线推进 EMA/RSI/KDJ/MA/MA_sums 状态并经 save_incremental_update 回写（HTTP 端点 routers/scoring.py:2045-2070，已核实），没有 last_update_date 判断。
- 影响: 同日多次触发时 EMA/RSI/KDJ 向当日价反复收敛、偏离日线口径，全量刷新前该股指标失真。
- 建议: 在 _state 记录 last_update_date，同日重复调用直接返回现值、不推进状态。
- 置信度: 中（取决于端点实际调用频率）

### 摘要 backend/app/scoring/indicator_cache.py
职责：指标层 DB 缓存（indicator_cache 表：顶层指标＋_series 60 天 slim＋_state 增量状态），批量 SQL 预载评分指标｜输入：kline_cache 的 K 线、盘中最新价（增量端点）｜输出：指标 dict / series（≥30 条才可用）/ 批量 {code: series}｜关键阈值：TTL 36h；kline_count∈(0,250) 拒信（截断缓存门槛，kline_count=0 是盘中增量的合法标记）；SERIES_DAYS=60；池 300 只｜依赖：kline_cache（MIN_SCORING_KLINE_COUNT、get_cached_klines）、app.routers.scoring._calc_technical_fast（函数内延迟 import）、pack_source、tencent 缓存（池选取）｜已知坑：增量落库丢 _series（P2）；get_cached_indicators_batch 无门槛且死代码；incremental_update 无同日守卫；docstring 写在 ensure 调用之后（__doc__ 为 None，风格问题）

---

## backend/app/scoring/live_ranking.py

### [P3] backend/app/scoring/live_ranking.py:72 rank_date 已改"最近已完成交易日"，读取方仍与北京自然日比较
- 原文: `    return latest_completed_trading_day()`
- 问题: `_rank_day()` 语义 2026-09-20 修正为交易日口径（docstring 明确"跨午夜/周末=上一交易日，不要改回自然日"），但 /batch/top 的"当日榜单在库→直接服务"判断仍写 `_stored.get("date") == _today_bj()`（routers/scoring.py:1359，已核实）。
- 影响: 跨午夜补跑产出的榜单（rank_date=周五、自然日已是周六）永远不会命中快路径，退化为在线重算（多耗算力/egress）；数据本身不错，属写读口径不一致。
- 建议: 该比较改用同一来源 `latest_completed_trading_day()`（或直接复用 `_rank_day`）。
- 置信度: 中

### 摘要 backend/app/scoring/live_ranking.py
职责：全量精算榜日批产出→ranking_live 表（当日先删后批量插，UNIQUE(rank_date,code)）→后端直读（load/load_codes，盘中并入候选池修"简化分盲区"）｜输入：行情缓存池（price>0＋change_pct 非空＋EXCLUDE_LOSS_MAKING＋_pool_quality_filter）、pack 预计算指标（无指标不做实时拉取）、财报 batch、regime 同步｜输出：compute_and_store 落库行数/摘要；load 返回 {date, data[≤limit], pool_total}｜关键阈值：rank_date=最近已完成交易日（15:00 分界）；_LOAD_TTL=300s 快路径＋_LOAD_TTL_LONG=3600s 同日续用（1 行 MAX(rank_date) 探测）；limit 1000；落库 0 行即 raise｜依赖：app.routers.scoring（_precise_score_sync/_pool_quality_filter/_sync_regime_weights）、finance.get_finance_batch、indicator_cache.get_cached_technical_batch_sql、mainforce.state、db upsert｜已知坑：先删后插非事务（读侧短窗口可能见空/部分）；load 缓存内 dict 为共享引用（当前调用方只读/转 SimpleNamespace，未发现原地改写）；rank_date 交易日口径未被全部读取方跟进（P3）；本文件无板块联动逻辑（影子期红线由 ranking_history 侧平行表满足）

---

## backend/app/scoring/decay.py

### [P3] backend/app/scoring/decay.py:67 resolve_weights 吞掉所有异常后静默回退默认权重，正是注释要防的"权重不一致"坑
- 原文: `    return state, dict(ScoreEngine.DEFAULT_WEIGHTS)`
- 问题: try 块（:53-63，regime 缓存→market_regime_history 回退）里任何异常（含 DB 查询失败）都被 `except Exception: pass`（:64-65）吞掉，静默落到 DEFAULT_WEIGHTS（growth 0.18/quality 0.12），与生产 defensive（0.13/0.35）不同且无任何日志。
- 影响: DB 瞬断时 shadow 榜单用错权重且不可见——:47-50 注释记录的 2026-09-17"接口与脚本权重不一致→榜单对不上"踩坑模式会以不可观测的方式复发。
- 建议: except 分支补一条 print 告警（与文件内其它路径的日志风格一致），必要时区分"无 regime"与"查询失败"。
- 置信度: 高

### 摘要 backend/app/scoring/decay.py
职责：财报公告后成长/质量维度衰减重加权（shadow 旁路口径的单一事实源，base/zero/grad 三 variant，不改生产排序）｜输入：五维分 dict（中文名→0-100，缺失为 None）、get_regime_weights 权重、公告龄 age｜输出：decay_total 衰减总分（0-100 同尺度，无有效维度返回 None）；resolve_weights 返回 (state, weights)｜关键阈值：grad 梯度 (age≤5→乘数0，≤20→0.5，>20→1)；zero ≤25 天分数×0；VARIANT_* 与 mode 字符串严格同名｜依赖：app.backtest.market_regime（get_regime_cache/get_regime_weights）、market_regime_history 表、ScoreEngine.DEFAULT_WEIGHTS｜已知坑：variant 字符串错配曾致静默不衰减（"gradient"≠"grad"，注释已记）；独立进程 regime 缓存为空需回退历史表；异常静默回退默认权重（P3）；grad 乘数 0 时权重自然分摊给其余维度（与"语义中性"设计一致，已核对）

---

## backend/app/scoring/__init__.py

### 摘要 backend/app/scoring/__init__.py
职责：包初始化，re-export ScoreEngine（含 __all__）｜输入：无｜输出：ScoreEngine 符号｜关键阈值：无｜依赖：engine.py（import 时会连带执行 engine 模块级代码，但 engine 模块级仅有常量与函数定义，无 IO）｜已知坑：无

---

## 未发现问题

- **backend/app/scoring/__init__.py**：纯 re-export，无逻辑，未发现问题。
- **KILL 红线（新增代码）**：2026-10-11 后的本批改动（record_daily_ranking 的 date 参数 2026-10-11、板块联动 2026-10-19、live_ranking 递补池注释 2026-10-20）均未把评分当买入信号使用；板块联动严格走 `ranking_history_sector` 平行表、以 `sector_stocks`/`sector_filtered` 附带字段呈现，主榜 `stocks` 口径未被改动（ranking_history.py:767-827，已核对），符合"影子期只并行不改榜单"红线。违规点仅剩 2026-08-21 引入的遗留 `_trust_advice`（见 P1）。
- **行业分位边界（0/满）**：`_industry_pct_score` 用 `bisect_left/len(vals)`，值=最小值→0 分位、值>最大值→1、值=最大值→(n-1)/n，无越界；`len(vals) < 10` 提前返回 None 降级，无除零（engine.py:1317-1335）。
- **组合分分桶边界**：`_quantile_buckets` 的 `m = max(1, k * 3 // 10)` 保证 low/high 桶非空；秩归一化 `pos/(k-1)` 在 k>1 时端点 0/1 合法、k==1 用 0.5（ranking_history.py:983-997），无 0/满异常。
- **按日快照幂等（正常路径）**：UNIQUE(rank_date, code)＋ON CONFLICT DO UPDATE＋replace_day/only_if_empty 双守卫＋2026-10-11"目标日"参数修复，正常调用下按日快照幂等成立（非事务边缘见 P2）。
- **缓存写侧完整性**：`_refresh_one` 失败不落库（防"截至昨日的 K 线盖上新鲜 updated_at"）、`save_kline_cache` 的 name 保留 CASE、DDL 懒执行（kline/indicator 两文件），并发写为 last-write-wins，未发现会破坏数据完整性的写路径。
- **时点诚实（缺失≠0）**：`_score_growth`/`_score_quality` 三项全缺返回 score=None 不参与加权（缺失权重按比例分摊，:1048-1067）；`_score_volume_price`/`_score_turnover`/`_score_rsi` 等子项数据缺失给中性 50 而非 0 分；ranking_history 的 fwd5/returnPct 算不出时返回 None 而非 0（:447-451、:795-798）。未发现把"缺失"记 0 分的新代码。
- **除零防护**：`_score_volume_price` 的均量除法有 `len(tech_data) >= 10` 前置（:808）保证切片满 5 元素；`_score_amount`/`_score_momentum` 的除法均有 >0 判定；`_combine` 的 `w_sum <= 0`、`_score_flow5` 的区间除法由锚点单调区间保证非零。
