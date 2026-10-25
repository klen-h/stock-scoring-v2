审查范围：3 文件 / 约 2754 行 / 2026-10-24

# backend/app/tencent.py

### [P2] backend/app/tencent.py:313 字段数守卫(50)与实际访问的最大下标(51)不匹配，短格式行整行静默丢弃
- 原文: `            if len(data) < 50:`
- 问题: 守卫放行 len>=50 的行，但代码随后无条件访问 data[50]（bid_diff）和 data[51]（avg_price），字段数恰好 50/51 的行会在解析时抛 IndexError，被 361 行的 `except (IndexError, ...)` 捕获后整行跳过。
- 影响: 字段数不足 52 的品种（如部分指数/短格式行，get_index/get_stock 同走此解析）静默返回空 dict，"任何解析异常都跳过这一行"把残缺行伪装成"该股不存在"，无任何日志。
- 建议: 守卫改为 `len(data) < 52`，或 47~51 号字段改为按 `len(data)` 存在性兜底取 0，并打一条降级日志。
- 置信度: 中（守卫与下标不匹配是代码事实；实际触发面取决于哪些品种返回 <52 字段，未实测）

### [P2] backend/app/tencent.py:219 _is_stale_kline 用 naive datetime.now()，UTC 部署下跨日判定偏移 8 小时
- 原文: `    now = datetime.now()`
- 问题: 服务器本地时区（Render/Actions 均为 UTC）下，北京时间 15:00~23:59 对应 UTC hour=7~14，`now.hour >= 15` 不成立 ⇒ ref 只取到"昨日"，而口径应是 15:00 后 ref=今日。
- 影响: 收盘后至北京时间次日 08:00 之间，WAF 冷却回退路径（713、764 行）和 DB K 线兜底会把**上一交易日**的 K 线判为"当日仍新鲜"放行——正是文件头 08-28/08-31 信号事故的判定口径，窗口每天放宽约 8 小时。
- 建议: 换用 `app.flash.rules.beijing_now()`（与 514 行 `_price_date_now` 同源），消除本地时区依赖。
- 置信度: 高

### [P2] backend/app/tencent.py:425 刷新全失败/部分失败时仍无条件整体替换行情缓存
- 原文: `        _cache["stocks"] = stocks`
- 问题: 每批请求的异常在 414 行被打印后吞掉继续，循环结束后不管成功几批（哪怕 0 批）都用本次结果整体覆盖 `_cache["stocks"]`，并刷新 `last_update` 进入 60s 冷却。
- 影响: 一段网络故障（>2 分钟）会把全市场好缓存替换成空/残缺 dict——首页行情、榜单、`score_top` 全部空转，且冷却期内不会自动重试；与 2026-09-29"行情缓存两天不动"同属缓存供给类事故形态。
- 建议: 当 `len(stocks)==0` 且原缓存非空时保留旧缓存并保持原 `last_update`（或仅在其显著大于 0 时才覆盖），让下次刷新自然重试。
- 置信度: 高

### [P3] backend/app/tencent.py:643 return 之后的不可达代码
- 原文: `    print(f"[snapshot] 已恢复 {len(_valid_codes)} 个有效代码（行情待盘中刷新）")`
- 问题: 642 行 `return restore_market_snapshot(...)` 之后还有一条 print 和 `return False`，永远不会执行（疑似历史版本收口语句残留）。
- 影响: 无功能影响，但"已恢复 N 个有效代码"日志从不出现，排障时会被误导为该路径没跑。
- 建议: 删除 643~644 两行死代码。
- 置信度: 高

### 摘要 backend/app/tencent.py
职责：腾讯行情/K线唯一数据源 + 全市场内存缓存（60s 冷却，调度器 120s 一轮）、收盘快照存取、涨速自算采样、WAF 限流保护与 K 线磁盘持久化｜输入：qt.gtimg.cn 实时行情（80 只/批）、web.ifzq.gtimg.cn K线、DB 收盘快照（restore/sync）｜输出：`_cache["stocks"]` 全市场行情、get_stock/get_kline/search_stocks、market_snapshot 落库（stocks+valid_codes+data_date）、kline_cache.json｜关键阈值：BATCH_SIZE=80、刷新冷却 60s、K线 TTL 60s(盘中)/300s、WAF 冷却 120s、字段数守卫 50、涨速窗口 6min/覆盖度 0.6｜依赖：requests、app.flash.rules（交易日/北京时间/市场状态）、app.flash.store（快照）、app.scoring.kline_cache（WAF 兜底）｜已知坑：44/45 单位是亿元（×10000 存万元）、PB 在 46 非 40、47~51 为真值实测（涨停/跌停/量比/委差/均价）；last_update≠data_ts≠data_date 三个时间语义必须分开；快照恢复路径不能进涨速采样；涨速缺数据返回 None 绝不填 0

# backend/app/database.py

### [P2] backend/app/database.py:148 DDL 在执行前就记入 _DDL_DONE，失败后同一条建表语句永不重试
- 原文: `            _DDL_DONE.add(_key)`
- 问题: 去重集合在 `cur.execute(sql)` 之前添加；若该 DDL 两次尝试都失败（如 169 行 lock_timeout 15s 到期、瞬时死锁），函数向上抛异常，但之后任何调用方再次执行同一条 DDL 都会在 147 行直接 `return 0`——假装成功。
- 影响: ensure_table 类调用在启动期一次性跑，失败后表一直缺失，后续 INSERT 报 relation does not exist，且重试入口已被去重逻辑焊死（"表面执行过、实际没建成"）。
- 建议: 把 `_DDL_DONE.add(_key)` 移到 execute 成功之后（return 前），失败路径不要留痕。
- 置信度: 高

### [P2] backend/app/database.py:258 SQLite 的 upsert 用 INSERT OR REPLACE，与 PG ON CONFLICT 语义不一致
- 原文: `            sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"`
- 问题: SQLite 路径完全忽略 `conflict_columns`（按表上任意唯一约束/主键碰撞替换），且 REPLACE 是"删旧行+插新行"：data 未提供的列回退为默认值/NULL，`foreign_keys=ON`（65 行开启）下还会触发被删行的级联删除；PG 路径是按指定冲突列 DO UPDATE、保留未提供列。
- 影响: 同一份业务代码在本地 SQLite（开发）与 Supabase（生产）产出不同结果——部分列 upsert 在 SQLite 下丢数据，多唯一约束表可能替换错行，开发/生产行为不可互相印证。
- 建议: SQLite 路径改用 `INSERT INTO ... ON CONFLICT(conflict_cols) DO UPDATE SET ...`（SQLite 3.24+ 支持），与 PG 同构。
- 置信度: 高

### [P2] backend/app/database.py:169 lock_timeout 只对 DDL 设置，DML/批量写仍是默认 0（无界等锁），连接也无 statement_timeout
- 原文: `                                cur.execute("SET LOCAL lock_timeout = '15s'")`
- 问题: `SET LOCAL lock_timeout` 仅在 `if _is_ddl:` 分支内执行；163~168 行注释记录的"卡在一条 Supabase 连接 5.5 分钟、lifespan 永不完成"事故，其根因（写锁等待无超时）对日常 upsert/insert/update 依然存在（PG 默认 lock_timeout=0），且整库没有任何 statement_timeout。
- 影响: 日批/Render 并发写同一行时，一条 DML 可无限期挂起——日批无单任务超时（见 daily_batch 条目），整批随之挂死且无告警。
- 建议: 把 `SET LOCAL lock_timeout='15s'`（可再加 `statement_timeout`）提升到 execute/fetch/upsert_many 所有 PG 会话路径统一设置。
- 置信度: 中（机制确定；DML 锁争用实际发生频率未实测）

### [P3] backend/app/database.py:47 postgres:// 旧式 scheme 会被静默误判为 SQLite
- 原文: `        self._use_postgres = DATABASE_URL.startswith("postgresql://")`
- 问题: 仅识别 `postgresql://` 前缀；`postgres://`（psycopg2 同样接受的旧式 scheme）会走 SQLite 分支，悄悄连本地 backend/data/app.db。
- 影响: 若环境变量换成旧 scheme，全部写入落到本地临时文件，云库停更且日志只有一行"[database] 使用 SQLite"——典型的"表面正常实际没连上"。
- 建议: 判定改为 `("postgresql://", "postgres://")` 任一前缀即用 PG，其余 scheme 打 WARN。
- 置信度: 高

### [P3] backend/app/database.py:43 文档声称"连接池管理"，实现实为线程本地连接（无池、无上限治理）
- 原文: `      - PostgreSQL：使用 psycopg2，连接池管理`
- 问题: `_get_pg_conn` 是每线程一条长连接（threading.local），没有连接池，也没有空闲回收（只检查 `conn.closed`）。
- 影响: FastAPI 同步端点线程池 + 调度器线程各自持一条 Supabase 连接直到线程结束，线程多时逼近云库连接数上限；排障时按"池"理解会走偏。
- 建议: 文档改为"线程本地连接"，或引入 psycopg2.pool.SimpleConnectionManager/ThreadedConnectionPool 加上限。
- 置信度: 高

### [P3] backend/app/database.py:281 SQLite 批量 upsert 逐条单事务，与 PG 单事务行为不一致
- 原文: `                self.upsert(table, r, conflict_columns)`
- 问题: PG 路径是 execute_values 单事务（297~305 行），SQLite 路径循环复用单条 upsert，每条各自 commit。
- 影响: 批量写一半失败时 SQLite 已落库前半部分（非原子），与 PG 整批回滚的行为分叉；本地大批量也偏慢。
- 建议: SQLite 路径包一层显式 `BEGIN`/`COMMIT`（或 executemany）保持与 PG 同为单事务。
- 置信度: 高

### 摘要 backend/app/database.py
职责：SQLite/PostgreSQL 双库适配层——execute/fetch/fetch_one/insert/insert_many/upsert/upsert_many/init_tables，DDL 去重、死连接重建并重试一次｜输入：各模块 SQL+params、DATABASE_URL 环境变量、backend/schema.sql｜输出：List[Dict] 查询结果/受影响行数；线程本地连接（SQLite WAL + foreign_keys=ON；PG RealDictCursor + keepalives）｜关键阈值：PG connect_timeout=10、keepalives_idle=30、DDL lock_timeout=15s、失败重试 1 次、upsert_many page_size=100（每 100 行一次往返）｜依赖：sqlite3、psycopg2、threading.local、contextmanager｜已知坑：Supabase 空闲断连（closed 检查+_reset_pg_conn）、Supavisor 忽略连接级 options（必须 SET LOCAL）、每条 DDL 触发 PostgREST schema cache 全量重载（流量黑洞，故有 _DDL_DONE）、单条 upsert 实测 0.5s/条（全市场写入必须走 upsert_many）

# scripts/daily_batch.py

### [P1] scripts/daily_batch.py:1198 risk_calendar 已注册但不在 DEFAULT_ORDER，--tasks all 永远不跑，且无其他调用方
- 原文: `    "risk_calendar": (task_risk_calendar, "风险事件日历（限售解禁，C1 闸门底座）"),`
- 问题: `risk_calendar` 存在于 TASKS（1198 行）但不在 DEFAULT_ORDER（1286~1301 行，逐一核对缺它一个）⇒ 生产唯一路径 `--tasks all` 从不执行；全仓库 grep 确认 `sync_lift_calendar` 仅此一处调用（risk_events.py 自己注释"由日批调用"），Render 侧只读模式也没有对应循环。
- 影响: C1 风险闸门/交易员简报负面清单的限售解禁底座（risk_events 表）自 2026-09-25 接入日起从未被日批更新，属文件头所述"迁移进日批却漏配"（同 market_regime 09-08 断档）的同型静默断档。
- 建议: 把 `"risk_calendar"` 加进 DEFAULT_ORDER（紧随 `"calendar"` 之后，满足其 docstring"行情刷新之后"的约束——它排在 market_snapshot 后即可）。
- 置信度: 高

### [P3] scripts/daily_batch.py:1539 任务循环无单任务超时，任一任务挂起即拖死整批
- 原文: `            summary = fn()`
- 问题: 每个任务直接同步调用，无 per-task timeout；有界点只有任务内部的各自超时（pack 等待 --max-wait 30min、data_hygiene 子进程 600s、影子线程 180s、_notify 10s），任务内部任何无界阻塞（如 PG 无 statement_timeout/lock_timeout 的查询）都会挂住主循环。
- 影响: 挂起不是异常，不会进 fail_list、不触发企微告警，只能等 Actions 6 小时上限才终止，当天日批实际中断。
- 建议: 用线程/子进程给每个任务包一个可配置上限（如 30~60min），超时记入 fail_list 继续跑后续任务。
- 置信度: 高

### [P3] scripts/daily_batch.py:416 矛盾验证失败只进返回文案，不进 fail_list（validate_yesterday 本身确已被调用）
- 原文: `        vinfo = f"；⚠ 验证失败（不影响扫描）: {e}"`
- 问题: `validate_yesterday()` 确实在日批内被调用（407~408 行，与扫描同源同日，正面确认无问题），但其异常被捕获后只拼进成功返回串——任务仍记 ✅，不进 fail_list。
- 影响: 验证环节挂掉时批次仍"全绿"、退出码 0，汇总告警不含它——与本项目"表面全绿实际没产出"红线只差半步（日志里能看到 ⚠ 文案）。
- 建议: 验证失败改为向 fail_list 追加一条（或至少把 vinfo 里的 ⚠ 纳入 ::warning:: 输出）。
- 置信度: 高

### [P3] scripts/daily_batch.py:988 cached 分支的 DB 查询异常被吞后走"补推"，存在重复推送风险
- 原文: `        head = "交易员简报: 补推"`
- 问题: cached 命中时查询 `trader_briefs.pushed_at` 判定是否已推送，查询抛异常被 `except Exception: pass` 吞掉，随后落到"补推"分支照常推企微。
- 影响: DB 瞬断/超时时，一条已推送过的简报会被再推一次（无去重，注释 947~948 行自己写明 push_markdown_batched 不去重）。
- 建议: 查询失败时跳过推送并返回"状态未知，不补推"（宁可漏推一次，不重复刷屏）。
- 置信度: 高

### [P3] scripts/daily_batch.py:1511 守卫命中日志仍写"daily_reports 已存在"，实际判据早已换成 batch_runs
- 原文: `            print(f"⏭ 跳过本次触发：{_d} 的日批已完成（daily_reports 已存在）。"`
- 问题: 2026-10-19 把判据从 daily_reports 换成自建 batch_runs（1440 行），但跳过日志文案没同步改。
- 影响: 排障时按日志去查 daily_reports 会查错表，重蹈 10-19"判据与文案不一致"的坑。
- 建议: 文案改为"（batch_runs 已有该数据日成功记录）"。
- 置信度: 高

### [P3] scripts/daily_batch.py:1454 finished_at 用 naive datetime.now()，Actions(UTC) 下与全系统北京时间口径混用
- 原文: `            "finished_at": datetime.now().isoformat(),`
- 问题: Actions/Render 上 `datetime.now()` 是 UTC，落库的完成时刻没有时区标注；同型的还有 1565 行 `_notify` 里的 `%m-%d %H:%M`。
- 影响: batch_runs.finished_at 与库内其他北京时间字段（date 键、pushed_at 等）相差 8 小时，人工对账/巡检时看起来像"凌晨就完成了"。
- 建议: 统一改用 `app.flash.rules.beijing_now().isoformat()`。
- 置信度: 高

### [P3] scripts/daily_batch.py:784 task_weekly_report 中 bj 赋值后从未使用（死代码）
- 原文: `    bj = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))`
- 问题: `bj` 在函数内无任何后续引用（判据已全部改用 `_batch_trading_day()`），是 2026-09-19 重构残留。
- 影响: 无功能影响，仅误导阅读（以为窗口判定还在用此刻时间）。
- 建议: 删除该行及 `import datetime as _dt`（若无他用）。
- 置信度: 高

### 摘要 scripts/daily_batch.py
职责：盘后唯一计算入口——43 个任务（回填/行情快照/日历/主力状态/闸门留痕/扫描/评分快照/榜单/观察器/LLM 日报/清理/保留期）按依赖顺序串行执行，统一汇总、企微告警、退出码 0/1/2/3｜输入：backend-pack.db 数据包（DATA_SOURCE=pack 强制）、ensure_quotes 拉一次全市场行情进 tencent._cache、backend/.env、参数 --tasks/--force/--no-pack-check/--max-wait｜输出：各业务表落库（strategy_results/ranking_history/contradictions/gate_snapshot_history/...）、batch_runs 完成标记、企微通知、::error:: 日志｜关键阈值：包校验等待 --max-wait 默认 30min（每 3min 强制重下）、重复跑守卫=全量+workflow_run+非 force+batch_runs n_fail=0、data_hygiene 子进程 timeout=600s、影子线程等待 180s｜依赖：app.* 全家桶（rules/pack_source/database/tencent/...）、GitHub Actions daily-batch.yml（workflow_run+dispatch 两种触发，cron 已移除）、batch_runs 自建表、rules.latest_completed_trading_day｜已知坑：跨午夜补跑一律用 _batch_trading_day()（不得用 now()）；"零落库"必须显式 raise（9-15~9-18 事故）；daily_reports 不能当完成标记（10-19 事故，已改 batch_runs）；包日期与 scan_date 脱钩静默（9-23 事故，ensure_pack_fresh 返回值已接住并中止）；GBK 控制台编码崩溃（stdout/stderr reconfigure errors=replace）

## 未发现问题
- daily_batch.py 顶层任务循环对每个任务的失败都捕获、记入 fail_list、打印 ❌+traceback，最终以退出码 1 + `::error::` + 企微告警外显——不存在"失败被吞、整批仍 0 退出"的顶层路径（个别任务内部的 fail-open 均有注释明示为设计决定，如 calendar/观察器类）。
- `_batch_already_done()` 守卫与 workflow 触发器匹配：daily-batch.yml 仅 `workflow_run` + `workflow_dispatch` 两种触发（cron 已删），守卫精确覆盖唯一的自动触发路径；dispatch/单任务/--force 属有意放行，非漏覆盖。
- 矛盾验证 `validate_yesterday` 确实在日批内被调用（task_contradiction_scan，与扫描同源同日，max_age_days 默认 10 与签名一致）。
- cached 标志语义闭环：trader_brief 的 `premature` 已显式 raise（10-23 修复在场），cached+已推送则收口不重推，cached+未推送才补推。
- get_kline 缓存键带 count（防短拉取污染全量缓存）、WAF 冷却期只回"当日仍新鲜"缓存、K 线磁盘持久化用 tmp+os.replace 原子写——三处防护均在且口径一致。
- ensure_pack_fresh 的返回值已被 main 接住并以退出码 3 中止（旧"打 ::error:: 却继续跑"的坑已修）。
- tencent.py 中 last_update / data_ts / data_date 三个时间语义分离正确，快照恢复路径不进涨速采样，涨速缺数据返回 None 不填 0。
- 三个文件均未使用 `datetime.utcnow()`；`_is_trading_hours`/`_record_speed`/`task_weekly_report` 的时区处理正确（后者仅剩死变量）。
