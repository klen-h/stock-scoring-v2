# P1 代码审查：flash 调度器及配套模块

审查范围：6 文件 / 约 4924 行 / 2026-10-24

## 红线核查结论（对照审查重点）

- **复盘 loop "失败也 mark_schedule_done" 旧 bug**：修复在位。`scheduler.py:196-197` 只有 `result and not result.get("error")` 才 `mark_schedule_done`，失败走重试分支（`:200-212`，含 RETRY_INTERVAL_SEC 节流 + force 告警）。盘前简报同型修复也在位（`scheduler.py:619-624`：降级不标记，仅窗口末 11:00 兜底标记）。
- **同型新 bug**：残留两处弱化版——`mainflow_refresh_loop`（scheduler.py:1047 部分失败也 mark_done，仅 >30% 失败才告警，靠次日历史窗口回填自愈）与 `event_alert_loop`（scheduler.py:1705 用消息文本子串判断"未就绪"）。见下文 P3 条目。
- **_heavy() 与 READ_ONLY**：`start()`（scheduler.py:2420-2459）共包 18 条重循环；macro_daily / intraday_alert / review / trader_brief / daily_report 等 LLM 叙事类刻意保留（有注释留档）。calendar_loop 虽被 `_heavy` 关闭，但日批有 `task_calendar`（scripts/daily_batch.py:124）兜底，无遗漏。
- **盘前简报早产缓存事故**：防护在位。`trader_brief.py:458-468`（缓存 data_window < today 视为过期）+ `:492-498`（candidates_date 未就绪不生成不落库）；日批 `task_trader_brief` 显式传 `date=`（daily_batch.py:962）；调度器盘前路径恒 `force=True`（scheduler.py:614-615）不读缓存。
- **LLM 一次刷新最多 1 次 / 批量打包 / 按日缓存+指纹**：flash_brief.analyze 每次调用只发 1 个 `call_llm`、全部重要条进同一 prompt（flash_brief.py:500-501）✓；但"按日缓存+指纹"的 TTL 判断有 aware/naive TypeError（下文 P2），且日上限统计恒不生效（下文 P2）。
- **timezone**：scheduler / intraday_alerts / calendar / wechat 均统一 `rules.beijing_now` ✓；残留两处例外：flash_brief.py:189 的 `datetime.fromtimestamp`（服务器时区）与 :479/:552 的 naive-aware 混用（见条目）。
- **吞异常静默失败**：`_run_sync` 吞异常是既定设计（有日志）；本次发现的静默失效点：scheduler.py:1421（NameError 被吞致整条功能死亡）、scheduler.py:275（NameError 被吞致巡检失效）、flash_brief.py:479/:552（TypeError 被吞致缓存判断恒 False）。

---

## backend/app/flash/scheduler.py

### [P1] backend/app/flash/scheduler.py:1421 trade_plan_trigger_loop 引用未定义的 flash_rules，整条 A4 交易计划触发功能静默死亡
- 原文: `is_open = bool(flash_rules.get_china_market_status().get("is_open"))`
- 问题: 本文件从未导入 `flash_rules`（模块级导入只有 `from app.flash import service, store, rules, calendar, wechat`，第 26 行），`rules` 才是 flash.rules；NameError 被 1422 行的 `except Exception: pass` 吞掉，`is_open` 恒为 False。
- 影响: 第 1424 行闸门 `now.weekday() < 5 and 570 <= t < 900 and is_open` 永不通过——止损/兑现/买点/高开/放量过价全部提醒永不触发（`user_trade_plans` 的自动触发只有这一处，routers/user.py 只做 CRUD），且无任何报错与告警；start() 第 2354-2356 行注释还声称该循环"已有假日感知门"，与事实相反。
- 建议: 改为 `rules.get_china_market_status()`（与本文件 120 行同款），并把该 NameError 从裸 `except Exception` 中显式打出。
- 置信度: 高

### [P2] backend/app/flash/scheduler.py:275 行情缓存陈旧巡检的兜底分支引用未导入的 time，NameError 被吞致巡检在最需要时整体失效
- 原文: `or (time.strftime("%Y-%m-%d", time.gmtime(_ts + 8 * 3600)) if _ts else "?"))`
- 问题: 本文件模块级只导入 asyncio/os（23-24 行），`time` 仅在函数内以 `import time as _time` 局部导入过（2009 行），此处 `time` 未定义；当 `data_date` 缺失且 `data_ts` 存在时表达式求值抛 NameError，被 285-286 行 `except Exception: pass` 静默吞掉，该轮巡检（含 data_date 主路径之前已算出的部分）整体跳过。
- 影响: 这段巡检正是 2026-09-29 为对抗"静默失效"加的机制；一旦快照恢复/某写入路径没带 `data_date` 字段（恰是最可能陈旧的场景），巡检不但不告警反而每轮静默死亡——反静默机制自身静默失效。
- 建议: 文件头部补 `import time`（或改用 `rules.beijing_now()`/`datetime.fromtimestamp(_ts + 8*3600, timezone(timedelta(hours=8)))`），并把该 except 收窄为按字段判空而非整段吞。
- 置信度: 高

### [P2] backend/app/flash/scheduler.py:441 跨天重置依赖 `h==0 and m<5` 恰好命中，与 300s 轮询周期等宽，相位错开则每日刷新永久停摆
- 原文: `if h == 0 and m < 5:`
- 问题: 循环每 `asyncio.sleep(300)` 醒一次而重置窗口恰为 5 分钟，醒来时刻一旦相位锁定在 00:05 之后（如 00:05:0x），该窗口每天都错过；且标记不复位 → 15:30 的刷新永远不触发 → 循环体不再有耗时操作 → 相位漂移趋近于零，形成"不跑就不漂、不漂就永远错过"的自锁，直到进程重启（indicator_cache_refresh_loop 第 496-497 行为同型代码）。
- 影响: K 线/指标缓存某次相位错开后连续多日不再刷新，读侧回退实时拉取或用旧指标，故障表现滞后且难排查。
- 建议: 把"跨天重置"改为比较 `today != last_reset_date`（醒来即判、与时刻无关），或在标记为 True 且 `now` 已跨入新交易日时直接复位。
- 置信度: 中

### [P3] backend/app/flash/scheduler.py:421 K 线刷新闸门 `h>=15 and m>=30` 组合错误，16:00-16:29 等时段被误排除
- 原文: `if (_is_td and h >= 15 and m >= 30`
- 问题: 意图是"15:30-23:59"（注释与 start() 打印均为每日 15:30），但 `h>=15 and m>=30` 在 16:00-16:29、17:00-17:29 等 `m<30` 的时段恒为 False，正确写法应为 `h > 15 or (h == 15 and m >= 30)`。
- 影响: 机器 15:30-15:59 窗口未开机而 16:05 开机时，刷新要再等到 16:30，当日刷新被无谓推迟（极端连续错过前几个半时段才实质影响评分读侧）。
- 建议: 改为 `t >= 15 * 60 + 30`（用分钟数比较，与文件其余窗口写法统一）。
- 置信度: 高

### [P3] backend/app/flash/scheduler.py:549 daily_report_loop 失败重试无节流，与 RETRY_INTERVAL_SEC 修复模式不一致
- 原文: `await asyncio.sleep(120)`
- 问题: 窗口 19:30-23:59 内失败不标记完成即每 120s 重试一次（最长约 135 次），而同文件复盘/盘前简报已因"几分钟内撞 provider 30 分钟熔断"教训加了 10 分钟节流（第 44-49 行），此循环没有；每次重试都会重新跑 `run_daily_report` 的采集与 LLM 调用。
- 影响: LLM/数据源持续故障时，晚间数小时内高频重试，加速触发 provider 熔断并放大外部请求量。
- 建议: 复用 `_review_last_try` 模式加 `RETRY_INTERVAL_SEC` 节流（失败路径计一次时间戳）。
- 置信度: 中

### [P3] backend/app/flash/scheduler.py:1518 open_confirmation_loop 失败路径每 60s 无节流重试并反复批量拉行情
- 原文: `print("[scheduler] 开盘买点确认无有效消息，稍后重试")`
- 问题: `sent==0 且非 no_signals`（典型为行情源对信号股持续无报价）时既不标记也不限频，窗口 09:35-11:20 内每 60s 重跑 `run_open_confirmation`，每轮都对全部信号股 `get_stocks_batch` 一次。
- 影响: 数据源异常时一个上午可产生上百轮批量请求，放大被限流/WAF 风险。
- 建议: 失败路径套用 `_brief_try` 同款节流（如 10 分钟），或对"quotes 全空"直接计次熔断。
- 置信度: 中

### [P3] backend/app/flash/scheduler.py:1705 event_alert_loop 用消息文本子串判断"未就绪"，文案一变就误 mark_done
- 原文: `if "行情缓存不可用" not in msg:`
- 问题: 是否标记当日完成取决于 `run_alert` 返回的中文案是否包含特定子串，属字符串耦合的状态判定。
- 影响: 上游文案措辞调整（或出现另一类"未就绪"返回）后，当日事件推送被静默标记完成、当天不再重试——与"失败也 mark_done"同型的脆弱形态。
- 建议: 让 `run_alert` 返回结构化结果（如 `{"ready": bool, "msg": str}`），调度器按字段判断。
- 置信度: 中

### [P3] backend/app/flash/scheduler.py:1047 mainflow_refresh_loop 部分失败也标记完成（"失败不 mark_done"修复的同型残留）
- 原文: `store.mark_schedule_done(task_key)`
- 问题: `backfill_all` 返回 ok/fail 统计后无条件 mark_done，仅当失败率 >30% 才 `_notify_failure`（1050-1053 行），1-29% 的部分失败当日静默接受；对比同文件回测回填已改为"不完整（缺≥50）就不标记"（993-999 行）。
- 影响: 当日部分个股主力资金流缺失只能靠次日历史窗口回填自愈，且 30% 以下无任何告警。
- 建议: 仿照回填循环给一个缺失数阈值（如 fail>0 即不标记 + 节流重试，或至少把告警阈值降到与自愈窗口匹配）。
- 置信度: 高

### [P3] backend/app/flash/scheduler.py:1969 news_alert_loop 扫描持仓未按 portfolio_scope 过滤，与消息快照路径的修复不一致
- 原文: `rows = db.fetch("SELECT DISTINCT code, name FROM user_portfolio")`
- 问题: 同文件 `take_news_snapshot_once` 已于 2026-09-17 加 `portfolio_where` 过滤（2018-2021 行注释明言"快照池此前含所有账号的持仓"），而负面消息提醒仍取全表。
- 影响: 多账号场景下会对他账号持仓推负面消息告警；单账号部署无实际影响。
- 建议: 与快照路径同样套 `_pf_where2` 过滤。
- 置信度: 中

### 摘要 backend/app/flash/scheduler.py
职责：FastAPI lifespan 启动的 asyncio 调度中枢，注册约 30 条循环（快讯轮询/信号跟踪/三段复盘/盘中告警/盘前简报/各类日批兜底），窗口+`schedule_state` 双重幂等｜输入：`rules.beijing_now` 时钟、`store.is_schedule_done` 幂等标记、各业务模块（service/trader_brief/daily_report/mainforce/strategies/…）｜输出：schedule_state 标记、企微推送（`_notify_failure`/`push_markdown_batched`）、`status` 字典（/api/flash/status）｜关键阈值：`RETRY_INTERVAL_SEC=600`（复盘/盘前简报重试节流）、REVIEW_WINDOWS 09:10-11:30 / 11:32-13:00 / 15:03-23:59、`TRADER_BRIEF_GIVEUP_MIN=11:00` 兜底、回填不完整缺≥50 不 mark_done、`BACKFILL_RETRY_INTERVAL_SEC=1800`｜依赖：app.flash.store（fail-open 幂等）、app.flash.rules（唯一时钟）、wechat、llm（连续 2 次失败→30min 熔断，是重试节流的动因）｜已知坑：trade_plan_trigger_loop 因 `flash_rules` 未定义整体失效（P1）；stock_cache 巡检兜底分支 `time` 未导入（P2）；kline/indicator 跨天重置相位脆弱（P2）；daily_report 与日批双写日报、日报 LLM 每天烧两次（代码 524-526 行已留档未根治）；复盘/盘前简报/回填三处"失败不 mark_done"修复均在位。

---

## backend/app/flash_brief.py

### [P2] backend/app/flash_brief.py:621 盘中自动解读的"日上限"统计在 date 主键表上计数，恒≤1，DAILY_MAX 永不生效
- 原文: `if _daily_auto_count(d) > DAILY_MAX:`
- 问题: `_daily_auto_count`（571-576 行）执行 `SELECT COUNT(*) ... WHERE date = d`，而 `flash_brief` 表 `date TEXT PRIMARY KEY` 且 analyze 用 `conflict_columns=["date"]` upsert——每天最多 1 行，计数永远 ≤1，永远小于默认 8。
- 影响: 自动触发仅受 30min 冷却约束；进程重启冷却清零后，同一交易日可超出预设的 8 次上限继续烧 LLM（tier=fast），成本闸门形同虚设。
- 建议: 用独立计数（如 `flash_brief_auto_log` 按日计数行，或 schedule_state 记 `flash_brief_auto:{date}` 次数），而非复用缓存表行数。
- 置信度: 高

### [P2] backend/app/flash_brief.py:479 缓存新鲜度判断 aware/naive 相减必抛 TypeError，"TTL 内读缓存"永不命中
- 原文: `fresh = (_now().replace(tzinfo=None) - ts) < timedelta(minutes=ttl)`
- 问题: `created_at` 以 `_now().isoformat()`（aware，带 +08:00）落库（516 行），`fromisoformat` 解析回 aware；而 `_now().replace(tzinfo=None)` 是 naive，`naive - aware` 抛 TypeError，被 480-481 行捕获后 `fresh=False`——缓存命中分支（482 行起）永不返回。
- 影响: 文档承诺的"refresh=False 且缓存新鲜 ⇒ 不烧 LLM"（463 行）实际失效；当前仓库内 analyze(refresh=False) 尚无调用方（router 仅 130 行 `fb.load` 与 133 行 `refresh=True`），故未实际烧钱，但任何按文档语义接入的轮询读路径都会每次烧一次 LLM，违背"一次刷新最多 1 次 + 按日缓存"铁律。
- 建议: 统一用 `rules.to_beijing(row.get("created_at"))` 与 `_now()`（均 aware）相减，或在落库时去掉时区并全程 naive。
- 置信度: 高

### [P3] backend/app/flash_brief.py:552 load() 的 stale_snap 判断与 479 行同型 TypeError，恒判为过期
- 原文: `stale_snap = (_now().replace(tzinfo=None) - ts) > timedelta(minutes=10)`
- 问题: 同为 naive-aware 相减，异常被 553-554 行捕获后 `stale_snap=True`。
- 影响: 前端每次轮询 `fb.load` 都重算快照（指数/板块外部请求，仅 20s 进程缓存缓解），落库 payload 的"定格快照"语义失效；无 LLM 成本。
- 建议: 与 479 行一并修正时区口径。
- 置信度: 高

### [P3] backend/app/flash_brief.py:189 as_of 用 datetime.fromtimestamp 依赖服务器本地时区
- 原文: `out["as_of"] = datetime.fromtimestamp(float(ts)).strftime("%H:%M")`
- 问题: `fromtimestamp` 无 tz 参数按服务器本地时区解释，项目其他处（如 scheduler.py:269-275 巡检）已明确"与服务器时区无关"的 +8h gmtime 口径。
- 影响: 部署在 UTC（Render）时卡片"数据时点"偏 8 小时，与 LLM prompt（390 行引用同一 as_of）一起误导读者/模型。
- 建议: 改为 `datetime.fromtimestamp(float(ts), timezone(timedelta(hours=8)))`。
- 置信度: 中

### [P3] backend/app/flash_brief.py:395 prompt 文案仍写"按 ≥9.8% 粗算"，与已切换的 limit_stats 精确口径矛盾
- 原文: `lines.append(f"- 涨停/跌停（按 ≥9.8% 粗算）：{m.get('limit_up')}/{m.get('limit_down')}")`
- 问题: 174-181 行已改用 `limit_stats.counts_best`（板幅 ST5/10/20/30cm 精确口径），喂给 LLM 的数字是精确值，prompt 却声明它是 ≥9.8% 粗算。
- 影响: LLM 收到自相矛盾的口径说明，可能在解读中沿用"粗算"定性、低估跌停数字的可信度。
- 建议: 文案改为"（按板幅真值限价口径）"。
- 置信度: 高

### 摘要 backend/app/flash_brief.py
职责：快讯解读卡（大盘/板块/个股异动/重要快讯规则打分）+ LLM 批量解读（1 次 prompt 打包全部重要条）+ 盘中自动触发（maybe_auto_run → /flash/notifications 浏览器通知）｜输入：tencent 指数与内存行情缓存、eastmoney 板块/资金流、flash_news 表、routers.stock._detect_anomaly、limit_stats.counts_best｜输出：flash_brief 表（date 主键：payload/llm/conclusion/notified_at）、通知事件｜关键阈值：TTL 30min / AUTO_TTL 20min、冷却 30min、日上限 8（实际失效）、指数 |涨跌|≥1%、涨跌比 ≤0.4/≥2.5、30min 强快讯≥2、快讯打分强词 +2｜依赖：app.flash.llm.call_llm（tier=fast）、app.flash.rules.beijing_now、app.database（SQLite）｜已知坑：日上限恒不生效（P2）、缓存 TTL/快照新鲜度两处 aware-naive TypeError（P2/P3）、as_of 服务器时区（P3）；早产缓存方面 auto 路径恒 refresh=True 不读缓存 + date 主键按日隔离，防护在位。

---

## backend/app/flash/rules.py

### [P3] backend/app/flash/rules.py:333 HOLIDAYS 仅收录 2026，2027-01-01 起节假日识别静默失效
- 原文: `    2026: [`
- 问题: `is_trading_day`/`get_china_market_status` 对 `HOLIDAYS.get(now.year, [])` 缺年返回空列表，2027 年起只按周末判断，全部以 `is_trading_day` 为门禁的调度循环（复盘/日报/战法扫描等，scheduler 已统一依赖它）会在节假日照跑。
- 影响: 重演 2026-10-11 清理过的"假日键错位行"事故（36 个假日键、白烧 LLM token）；calendar.a_stock_holidays 的注释（371-374 行）也明言该日历"判错会白烧 LLM token"。
- 建议: 在 2026-12 前追加 2027 日历，并给"当前年份不在 HOLIDAYS"加一条启动告警（log 或企微）以防再次静默过期。
- 置信度: 高

### 摘要 backend/app/flash/rules.py
职责：纯函数规则层（无 IO）——快讯硬过滤/事件簇聚类/重大更新重推判定 + 全项目唯一市场时钟（beijing_now / get_market_clock / get_china_market_status / is_trading_day / latest_completed_trading_day）+ 数据质量分级降级｜输入：快讯 item 列表、macro panel、holdings、可选 datetime｜输出：过滤/聚类结果、交易状态 dict、数据质量等级与激活/中止步骤｜关键阈值：A股 9:15 集合竞价-11:30 / 13:00-15:00、`latest_completed_trading_day` 以 15:00 分界、缺纽约原油=严重不足、缺≥3 项=中止多情景、format_for_llm 口径 min_star=4（在 calendar.py）｜依赖：无（纯函数，仅 re/datetime）｜已知坑：HOLIDAYS 仅 2026（P3）；`get_market_clock` 的 `is_*_trading` 只看时刻不含节假日（代码注释已警示消费方必须叠加 is_trading_day，intraday_alerts._trading_session 已照做）。

---

## backend/app/flash/intraday_alerts.py

（未发现需立条的问题）

### 摘要 backend/app/flash/intraday_alerts.py
职责：盘中风险警示——多指数急跌（各用各阈值）、指数冲高回落（日内路径口径，唯一带 LLM 解读的规则）、涨跌比极值、跌停家数激增、黑天鹅熔断（≥100 只，供模拟盘联动）｜输入：`get_index`（3 指数/轮，两规则共享一次拉取）、tencent._cache 内存行情、`limit_stats`（跌停唯一口径，2026-10-10 事故后统一）、contradictions 未兑现疑虑、format_user_holdings｜输出：企微推送（force=True, category=risk，每类每档每日一次 + 全局 5min 最小间隔）、`black_swan_active()`｜关键阈值：上证 -1.5/-2.5%、创业板/科创50 -2.5/-4.0%、冲高回落 0.8/-1.5（上证）与 1.5/-2.5（双创/科创）、跌停 ≥30/≥60/≥100、涨跌比 <0.25、交易时段 9:40-11:30 / 13:00-15:00｜依赖：store.schedule_state（`alert:*` 落库去重，读失败 fail-open、只在候选命中时查库）、llm.call_llm（tier=fast，空响应重试 1 次）｜已知坑：无——2026-09-25"一直在发企微"（跨重启落库去重）、休市日照推（_trading_session 补交易日判断）、"跌停 123 只"误报（统一 limit_stats 口径）三次事故的修复均在位且互相一致。

---

## backend/app/flash/calendar.py

（未发现需立条的问题）

### 摘要 backend/app/flash/calendar.py
职责：金十财经日历数据源——按周分段拉取（接口硬限制单次≤7 天，超限静默返回空）、三类（event/data/holiday）归一化、DB 为主 + 文件副本缓存、upcoming/format_for_llm/a_stock_holidays 消费接口｜输入：rili-open-api.jin10.com week_info（x-app-id/x-version 头，与快讯故障域隔离的独立 Session）、beijing_now 窗口（昨 ~ +14 天）｜输出：flash_calendar 表（key='latest'）、data/calendar.json、LLM prompt 文本｜关键阈值：_MAX_SPAN_DAYS=7、DEFAULT_DAYS_BACK=1/AHEAD=14、format_for_llm 默认 min_star=4、空列表视为失败并 health.record("jin10_calendar")｜依赖：requests、store（DATA_DIR/_load/_save/_now_iso）、app.health、rules.beijing_now｜已知坑：无——刷新失败保留旧缓存（"过期一天好过没有"）、鉴权头缺失的 502 特征（Gm-Error-Code 81002）已在文件头留档。

---

## backend/app/flash/wechat.py

（未发现需立条的问题）

### 摘要 backend/app/flash/wechat.py
职责：企微推送单点入口——分类通道解析（① 分类群机器人 WECHAT_HOOK_* → ② 自建应用消息 → ③ 主群 webhook 兜底）、4096 字节上限按段落分批、表格转列表全局兜底、signal_bus 埋点（进入即记，不保证送达）｜输入：title/content、category（risk/coach/brief/alert）、env 配置（WECHAT_WEBHOOK / WECHAT_HOOK_* / WECHAT_NOTIFY_* / WECHAT_APP_CORP_ID / WECHAT_BUSINESS_ALERTS）｜输出：企微消息；notify 返回是否至少一条通道送达｜关键阈值：MAX_CONTENT_BYTES=4000、应用消息 1900B、应用通道连续失败 3 次→熔断 30min（按应用分槽）、token 缓存 7000s、markdown 上限 4096B｜依赖：requests、app.wechat_fmt、app.flash.signal_bus｜已知坑：本层对 webhook 发送无自动重试（瞬时失败即丢），依赖调用方补偿（如 intraday_alerts"推送异常不 mark、下轮重推"；scheduler._notify_failure 则当次丢弃且当日不再提醒）；WECHAT_BUSINESS_ALERTS 语义一致——默认 0 时仅 force=True（失败告警/盘中警示/模拟盘摘要）与战法买入信号（push_strategy_signals 强制）通过；分类群部分批次成功后回落主群可能造成重复内容（代码 13 行已声明"通知不丢优先"取舍）；_get_token 单槽缓存按 secret 键控，多应用混用时各自反复取 token（轻微低效）。

---

## 未发现问题

- backend/app/flash/intraday_alerts.py（未发现需立条问题；历次事故修复均在位）
- backend/app/flash/calendar.py（未发现需立条问题）
- backend/app/flash/wechat.py（未发现需立条问题；无重试为设计取舍，已计入摘要）
