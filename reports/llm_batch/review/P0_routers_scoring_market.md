# P0 审查报告：routers/scoring.py + routers/market.py

审查范围：2 文件 / 约 4716 行 / 2026-10-24

---

### [P1] backend/app/routers/scoring.py:1440 async 端点内同步调用 `_batch_news_scores`（事件循环上跑同步 HTTP）
- 原文: `        news_scores = _batch_news_scores([x["code"] for x in _ann_items])`
- 问题: `score_top` 是 `async def`（L1271），`_batch_news_scores`（L1248）→ `get_global_news()`（90s TTL，miss 时 `requests` 同步请求、timeout=10、重试 2 次含 `time.sleep(1)`）在事件循环上直接执行。
- 影响: 快讯缓存每 90s 过期一次，此刻到达的榜单请求会把整个进程的所有并发请求阻塞最长约 20 秒（前端超时/502）。
- 建议: 改为 `news_scores = await asyncio.to_thread(_batch_news_scores, [x["code"] for x in _ann_items])`（同文件 L406 对 `get_finance_batch` 的既有做法）。
- 置信度: 高

### [P1] backend/app/routers/scoring.py:1358 async 端点内同步 DB 读全量精算榜（未进线程池）
- 原文: `            _stored = _load_live_ranking(limit=1000)  # 组合分排序需更大池（覆盖基本面强但总分中游的票）`
- 问题: `score_top`（async def）内直接同步调 `live_ranking.load`（DB 读 1000 行 + payload JSON，进程缓存 TTL 300s/3600s，过期即跨区 DB RTT）；同文件 L696 注释自定规则「同步 DB 查询（跨区 RTT），必须跑线程池，不能占事件循环」，此处未遵守。
- 影响: 缓存过期后的首个榜单请求阻塞事件循环一个跨区 RTT（百毫秒~秒级），期间所有请求（含 60s 级轮询）排队。
- 建议: `_stored = await asyncio.to_thread(_load_live_ranking, limit=1000)`；同函数 L1341/L1460 的 `_mf_load(...)` 与 L446 的 `get_cache_status()`（含 `ensure_kline_cache_table` DDL + COUNT(*)）同样直跑在事件循环上，一并 `asyncio.to_thread` 包裹。
- 置信度: 高

### [P1] backend/app/routers/scoring.py:1572 shadow_rank 异步端点内同步 DB（`use_cache=False` 每请求必打库）
- 原文: `    st = _load_live(limit=1000, use_cache=False)`
- 问题: `shadow_rank`（L1554，async def）每请求强制绕过缓存同步读 1000 行榜单，随后 L1578 `fin = get_finance_batch(codes) or {}`、L1579 `resolve_weights()`（可回退查 `market_regime_history`）也全部同步执行在事件循环上——对比 `_batch_with_precise_top` 里同一批函数都包了 `asyncio.to_thread`。
- 影响: 每次打开影子榜都把事件循环卡住 2 次跨区 DB 往返以上；与其它页面并发时整体尾延迟被放大。
- 建议: 三处调用分别包 `asyncio.to_thread`；`use_cache=False` 若非必需改为走缓存。
- 置信度: 高

### [P1] backend/app/routers/scoring.py:636 backtest 异步端点在事件循环上跑数十秒级纯 CPU + 同步批量 DB
- 原文: `            dim = engine._score_technical(hist)`
- 问题: `backtest`（L536，async def）内 L572 `get_cached_klines_batch(pool_codes)`（100 只 K 线批量同步 DB 读）、L620 `_calc_technical`（纯 Python 指标，~100 只×500 根）与 L624-666 的 `bt_days×股票数` 次逐日评分全部直接执行；docstring 自述「单次计算要几十秒」。
- 影响: 首次计算/30 分钟缓存过期后的那次请求会把整个事件循环卡住数秒到数十秒，全部轮询接口同时无响应。
- 建议: 把「批量读缓存 → 预计算 → 逐日回测」整段搬进同步函数并用 `await asyncio.to_thread(...)` 执行（结果缓存逻辑保留）。
- 置信度: 高

### [P2] backend/app/routers/scoring.py:1288 缓存为空时由请求路径触发全市场刷新（无去重标志）
- 原文: `            background_tasks.add_task(refresh_all_stocks)`
- 问题: 红线要求「缓存空就如实降级，预热交给调度器」；此处每个看到空缓存的请求都会追加一个后台全量刷新任务，而 `refresh_all_stocks` 全程持有 `_cache["lock"]`（14s~4 分钟）、`last_update` 在扫描结束才写（tencent.py L430），并发请求的任务会在 AnyIO 线程池里排队等锁。
- 影响: 冷启动窗口（多页同时打开）后台线程被锁等待占满，拖累所有 sync 端点（score_single 等）的线程池配额；虽不阻塞事件循环，仍是"请求路径触发全量拉取"。
- 建议: 触发前检查模块级"正在刷新"标志（或 `lock.acquire(blocking=False)` 探测），命中则直接返回 loading；预热交给既有 `stock_cache_refresh_loop`。
- 置信度: 中

### [P2] backend/app/routers/scoring.py:717 bucket/composite 进程缓存不区分查询参数
- 原文: `    if _bucket_cache["data"] is not None and now - _bucket_cache["ts"] < _BUCKET_CACHE_TTL:`
- 问题: 缓存键没有包含 `days`（L734）；L747 的 `_composite_cache` 同样没含 `days`/`horizon`——不同参数在 10 分钟内返回同一份结果且标 `cached: True`。
- 影响: 用户切换统计窗口/持有期看到的是上一个参数的结果，统计数字与页面控件不一致（时点诚实性受损）。
- 建议: 缓存结构改为 `{(days[, horizon]): {ts, data}}` 或至少把参数拼进键；TTL 不变。
- 置信度: 高

### [P2] backend/app/routers/scoring.py:1691 score_by_signal 无结果缓存与并发防重
- 原文: `    all_precise = await _batch_with_precise_top(`
- 问题: `score_top` 有 3 分钟结果缓存 + `computing` 防重（L1291-1324），而同源的 `score_by_signal` 每请求都对头尾各 limit+50（默认约 200 只）做两阶段精算，未命中 K 线缓存时会实时拉腾讯。
- 影响: 该端点被轮询/多开时外部请求与 DB 压力翻倍，叠加信号筛选常与榜单同屏使用，重复精算同一批票（WAF 风险）。
- 建议: 复用 `_rank_result_cache` 模式：按 signal+limit 缓存精算结果 3 分钟，并用同一把锁防并发重算。
- 置信度: 中

### [P3] backend/app/routers/scoring.py:787 快照价缺失填 0（依赖下游过滤兜底）
- 原文: `         "price": (stocks_map.get(r["code"]) or {}).get("price") or 0,`
- 问题: 行情缓存缺失该股时快照价写 0（L1231 `_record_ranking` 同款）；`record_daily_ranking` 与 `get_verified_records` 虽有 `price > 0` 过滤，不污染收益，但该行从此退出胜率回查/权重分析样本且无人知晓。
- 影响: 行情偶发缺失的票静默丢失统计样本（缺失被伪装成有效 0，违背"0 与缺失必须可区分"）。
- 建议: 价格取不到时写 `None`（列可空），下游 SQL 已按 `price IS NOT NULL` 过滤，语义更诚实。
- 置信度: 中

### [P3] backend/app/routers/scoring.py:1297 榜单缓存按首次请求的 limit 截断
- 原文: `            sliced = cached_data[:limit]`
- 问题: 缓存里的 `data` 是按首个请求的 `limit` 精算截断的（L1365 `top = _cand[:limit]` 后再入缓存），3 分钟内更大的 limit 请求只能拿到更短的列表，`total` 却不变。
- 影响: 先小后大的两次请求返回行数不一致，前端分页/排序看起来"少了一截"。
- 建议: 缓存统一按 le 上限 200 行生成，命中后再按请求 limit 截取。
- 置信度: 高

### [P3] backend/app/routers/scoring.py:1385 中文日志直写控制台（GBK 铁律）
- 原文: `            print(f"[rank] 全量精算榜不可用（回退在线两阶段）: {_e}")`
- 问题: 护栏 §9「日志一律 ASCII」，本文件数十处 print 含中文（L132、L408、L1367、L1431、L1495 等），生产 Windows/GBK 控制台有编码异常风险。
- 影响: 异常路径打印可能抛 UnicodeEncodeError 掩盖原始错误，或日志乱码不可读。
- 建议: 统一改 ASCII 短语（如 `[rank] live-ranking unavailable, fallback: ...`），中文说明走返回值。
- 置信度: 高

---

### [P2] backend/app/routers/market.py:301 market_overview（30s 轮询首页接口）每请求实时外呼腾讯指数，无 TTL 缓存
- 原文: `        data = _fetch_tencent(codes_str)`
- 问题: `_fetch_tencent` 直连无缓存（tencent.py L277-297），而本接口前端 30s 轮询（本文件 L172 注释自述）；对照同文件 `market_temperature`（60s 缓存）与 `_limit_counts_best`（rt_k 60s 缓存）都有进程内 TTL。
- 影响: 一页开一天 ≈ 2880 次外呼/天，egress 与 WAF 风险持续存在；腾讯慢时该请求尾延迟直接叠加到首页。
- 建议: 指数块加 30~60s 进程内 TTL 缓存（复用 `_temp_cache` 同款 `{data, ts}` 模式），缓存空再外呼。
- 置信度: 高

### [P2] backend/app/routers/market.py:330 overview 统计块对 `change_pct`/`amount` 直接下标访问，None 即整接口 500（fail-closed 反例）
- 原文: `        up = sum(1 for s in stocks.values() if s["change_pct"] > 0)     # 上涨家数`
- 问题: 同一份 `_cache["stocks"]` 池，scoring.py:1330 明确按「`change_pct` 为 None 的停牌/异常股」过滤（L1329 注释自认池内存在 None），`_breadth_now`（L1184-1185）与 `market_emotion`（L1491）也都做了 None 防御，唯独 overview 的 L330/L340/L358 直接下标访问。
- 影响: 一条 change_pct/amount 异常的记录会让整个 overview（指数+统计+风格）500，首页全挂——正是红线要防的"一个坏块拖垮整卡"。
- 建议: 与 `_breadth_now` 对齐：`s.get("change_pct") is not None` 过滤后再统计，`amount` 用 `s.get("amount") or 0`。
- 置信度: 中

### [P2] backend/app/routers/market.py:539 refresh-status 用 naive `datetime.fromtimestamp`（生产 UTC 差 8 小时）
- 原文: `        "last_update": datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S") if last else "未刷新",`
- 问题: 不带 tz 按服务器本地时区（生产 Render=UTC）解释 epoch；本文件已两次裁决此坑（L240-244 大小盘 as_of、L1710-1714 quote_as_of 均改显式 +08:00），此处漏改。
- 影响: 前端"最后刷新时间"在生产环境显示成 8 小时前，误导"数据停更"判断。
- 建议: 改 `datetime.fromtimestamp(last, tz=timezone(timedelta(hours=8)))`（文件顶部已 import）。
- 置信度: 高

### [P2] backend/app/routers/market.py:373 刷新进行中每个轮询都再排一个 `refresh_all_stocks` 后台任务
- 原文: `        background_tasks.add_task(refresh_all_stocks)   # 非阻塞，立即返回响应`
- 问题: 触发条件 `now - last_update > 120` 用的是"上次刷新完成时刻"，而刷新全程持 `_cache["lock"]` 且 `last_update` 在结束时才写 ⇒ 2-4 分钟冷扫描期间，30s 轮询每次都新增一个后台任务并在线程池里阻塞等锁。
- 影响: 冷启动时线程池被锁等待占用（AnyIO 默认 40 线程），与其它 sync 端点争抢，极端时出现整站 sync 接口无响应。
- 建议: 加模块级 `_refresh_pending` 标志（任务开始置位/结束清除），置位期间不再 add_task；或 `lock.acquire(blocking=False)` 探测。
- 置信度: 中

### [P3] backend/app/routers/market.py:1292 tail-review 每请求实时外呼指数快照，无缓存
- 原文: `        n_idx = _index_snapshot() or b_idx           # 取不到实时就退化为基线（Δ=0）`
- 问题: `_index_snapshot()` 每次调用都发一次腾讯 HTTP（`_fetch_tencent` 无缓存）；sync def 虽在线程池执行不阻塞循环，但该页若轮询则 egress 与 overview 同病。
- 影响: 尾盘承接页开一天持续外呼；腾讯慢时该接口尾延迟放大。
- 建议: 给 `_index_snapshot` 加 60s 进程内缓存（指数价 60s 粒度足够）。
- 置信度: 中

### [P3] backend/app/routers/market.py:472 market_realtime 分页参数无上限校验
- 原文: `    page: int = 1, size: int = 50,`
- 问题: `size` 未用 `Query(ge=, le=)` 约束，size=100000 会一次性排序并序列化全市场 ~4000 行；`page<=0` 时负切片返回空页。
- 影响: 异常参数下单响应 MB 级（带宽/序列化开销），负页码静默空页难排查。
- 建议: 改 `page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)`。
- 置信度: 高

### [P3] backend/app/routers/market.py:1859 `_first_seen_map` 查询失败也把空结果缓存 1 小时
- 原文: `    _first_seen_cache.update(ts=now, map=out)`
- 问题: `db.fetch` 抛异常时 `out={}` 仍被写入缓存（对比 scoring.py `_load_signal_map` L2158「失败不写缓存」的纪律），新股剔除依据 1 小时内持续为空。
- 影响: DB 抖动后 1 小时内炸板率分母混入新股（`skip_new` 失效），统计口径悄悄漂移。
- 建议: 仅 `out` 非空时写缓存，或失败时保留旧值/缩短 TTL。
- 置信度: 中

### [P3] backend/app/routers/market.py:640 口径注释已过时（代码 2026-10-24 已改板幅口径）
- 原文: `#  口径注：连板/涨停判定用 ≥9.5% 近似（10cm 主板口径，20cm/ST 未细分），`
- 问题: 下方 L1501-1506/L1533/L1559 实际已全部切到 `_limit_counts_best`/`_is_limit_up_close`（复用 `app.limit_stats` 板幅实现），块注释仍宣称 ≥9.5% 一刀切。
- 影响: 下一个维护者按注释理解口径，可能"照注释改回去"或误判两处口径不一。
- 建议: 更新该注释为「板幅口径（rt_k 精确优先），详见 `_is_limit_up_close` 与 `app.limit_stats`」。
- 置信度: 高

### [P3] backend/app/routers/market.py:2205 limit-review 快照缺失时每请求直连 zzshare，无负缓存/冷却
- 原文: `        df = api.review_uplimit_hot_step(date1=d)`
- 问题: 主路径读快照正确，但对没有快照的 `date`（未来日/拼错/日批未跑），每个请求都落到直连外部接口，且失败后还会再试 30 天回溯（L2223 `load_range(_shift_days(d, -30), d)`）。
- 影响: 异常 date 被反复请求时持续打外部分析接口（匿名口径数据受限且慢），无冷却。
- 建议: 直连失败结果做 5-10 分钟进程内负缓存（按 date 键）。
- 置信度: 中

---

### 摘要 backend/app/routers/scoring.py
职责：`/api/score/*` 评分域路由——单股完整评分（`/{symbol}`、`/diag`、`/ask`）、Top/Bottom/信号榜单（两阶段"简化筛选+候选池精算"，保证列表分==详情分）、全量精算榜（ranking_live）优先服务、nb 市组合分排序、回测/分桶/组合因子统计、评分快照落库、买入闸门观察池（快照主路径+live 兜底）、持仓雷达、K线/指标缓存管理端点｜输入：tencent 内存行情 `_cache`、K线/指标 DB 缓存、财报库（finance）、mainforce_state、ranking_live/gate_snapshot_history/strategy_results、市场状态缓存（market_regime）｜输出：评分卡与榜单 JSON（total_score/dimensions/buy_point/trend_health/gate/mainforce/news_score/composite_score/data_sector 等，含 cached/stale/cache_status/as_of 类时点字段）｜关键阈值：池门槛流通市值 50 亿/股价 3 元；榜单缓存 TTL 180s+computing 防重；回测缓存 1800s；分桶/组合统计缓存 600s；观察池快照缓存 60s、live 缓存 300s、快照最大陈旧 5 天（GATE_SNAPSHOT_MAX_AGE_DAYS）；战法信号缓存 1800s（失败不缓存）；行业缓存 600s｜依赖：app.tencent、app.scoring.engine/kline_cache/indicator_cache/live_ranking/ranking_history/decay、app.finance、app.mainforce.state/trade_gate/overlay/lhb、app.backtest.market_regime、app.eastmoney_news/news_sentiment、app.database、app.stock_diagnosis、app.sector_strength、app.portfolio_radar｜已知坑：`/{symbol}` 单段通配路由吞静态路径（多处两段路径+`-`/`_` 404 防线）；盘中/盘后两套指标口径（预计算 vs 实时+当日半根 bar，合成序列绝不写回缓存）；本审查新发现 4 处 async 端点内同步 DB/HTTP/CPU 直跑事件循环（L572/636、1358、1440、1572）。

### 摘要 backend/app/routers/market.py
职责：`/api/market/*` 行情域路由——overview（指数+全市场涨跌统计+大小盘/黄白线风格）、temperature（市场温度）、emotion（情绪快照：涨跌停家数 rt_k 口径、昨涨停赚钱效应、连板、竞价判读、六档分布）、emotion-review/tail-review（预判对账/尾盘承接）、realtime 分页、指数K线、涨停复盘（快照优先→直连→stale 降级）、uplimit-realtime/amount-realtime、缓存刷新触发｜输入：tencent 内存行情缓存与 `_fetch_tencent` 实时指数、backtest_prices、market_emotion_daily/market_tail_snapshot、zzshare 日快照/直连、user_portfolio｜输出：指数与涨跌统计（含 limit_src/limit_data_date/limit_age_sec 口径披露）、style（as_of/data_date/from_snapshot）、温度与建议买入线、情绪快照（缺失返回 None 不填 0）、炸板率/大面率（板幅分桶+meta 口径自述）｜关键阈值：TEMP_TTL 60s；情绪判读缓存 120s；rt_k 60s 缓存+peek 防冷阻塞；涨停判定唯一口径 app.limit_stats（rt_k 精确优先→板幅 ST5/10/20/30cm−0.3pt 容差）；亢奋≥60 家涨停或连板≥6、冰点<20 且赚钱效应<0；尾盘 Δ涨停±5 家/指数±0.3%/Δ上涨±200 家；影子对比日志 30min｜依赖：app.tencent、app.limit_stats、app.backtest.engine（_limit_pct 唯一板幅实现，limit_stats 委托它）、app.database、app.flash.rules（交易日历）、app.realtime_uplimit、app.market_amount、app.zzshare_daily/zzshare_client、app.policy_bottom_watch/policy_announcements/etf_share_watch、app.portfolio_scope、app.trader_brief｜已知坑：首页 30s 轮询接口每请求外呼指数（无 TTL）；refresh-status naive fromtimestamp 时区；收盘快照恢复路径 `last_update` 故意标"现在"须配 data_ts/data_date 使用；overview 统计块对 change_pct/amount 无 None 防御。

---

## 未发现问题
- 消息分边界：`news_score` 仅作榜单独立标注字段（L1437-1442），未参与 total_score 与排序，"消息分不进综合分"未被破坏。
- 板块联动影子期：`data` 主榜保持原版，过滤结果独立放 `data_sector` 并附 `sector_filter` meta（L1411-1435），shadow 模式不改榜单口径。
- 涨跌停口径唯一：路由层无自写 `>=9.5/9.9` 阈值残留（market.py L1501/L1888/L1909 与 scoring 观察池均走 `app.limit_stats`；`_bucket_of` 的 `backtest.engine._limit_pct` 与 `limit_stats._limit_pct` 经核实为同一实现——后者委托前者）。
- 主力乘数：`mult` 只挂标签不动分，排序/信号用 raw total（L1444-1493 注释与实现一致，全库无消费方）。
- K 线缓存防污染：含"当日半根"的合成序列不写回缓存（L288-299），短拉取截断有 MIN_SCORING_KLINE_COUNT 门槛。
- 观察池主路径读日批快照（毫秒级）+ 60s 进程缓存，40s 的 live 重算仅在快照缺失/过期或显式 live=true 时兜底且有 300s 缓存；`uplimit-realtime`/`amount-realtime` 均有 60s 缓存、锁去重与 `{available: False}` fail-open。
- 缺失语义：`_num_or_none` 转不动返回 None 而非 0（market.py L701-715）；emotion 无行情时"今日"计数返回 None 由前端显示"—"；`limit-review` 双源失败降级旧快照并显式标 `stale`。
- 时点透出：`quote_as_of/data_ts/data_date/from_snapshot/limit_src/as_of/cached/cache_age_seconds` 等字段在各卡齐备，未发现用请求时刻顶替数据时刻的新增点。
- 评分精算的并发治理：`_batch_with_precise_top` 的 Semaphore(3/10)、`get_finance_batch`/指标批量预加载走 `asyncio.to_thread`，候选池精算不直接打全市场。
