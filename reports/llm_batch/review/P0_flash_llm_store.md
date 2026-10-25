审查范围：2 文件 / 约 2552 行 / 2026-10-24

审查对象：
- backend/app/flash/llm.py（1713 行）
- backend/app/flash/store.py（839 行）

---

## 一、backend/app/flash/llm.py

### [P2] backend/app/flash/llm.py:573 日熔断为 check-then-act，并发下可越过 LLM_DAILY_MAX_CALLS
- 原文: `    blocked = llm_blocked_reason()`
- 问题: `call_llm` 先查 `_today_calls()`（DB 读）再放行，而计数在调用完成后由 `_call_provider` 内 `_record_usage`（:672）才累加，检查与累加之间无原子性（`_usage_lock` 只护写不护检查）。
- 影响: 并发在飞的正式调用（轮询线程 + API 手动触发 + 批处理）在计数达到上限后仍可全部发出，单次并发窗口内最多超额 N（在飞数）次，日熔断这道"唯一成本保护"是软上限。
- 建议: 把"检查+预占"合并为一次原子 SQL（如调用前 `UPDATE llm_usage_daily SET calls=calls+1 WHERE day=%s AND calls<%s`，rowcount=0 即熔断），完成后只补 token 数。
- 置信度: 高

### [P2] backend/app/flash/llm.py:705 `_call_json` 不校验顶层 JSON 类型，非 dict 会让诊断链路抛 TypeError
- 原文: `        return json.loads(text)`
- 问题: `json.loads` 可返回 list/int 等非 dict 顶层值，`_call_json` 原样返回；`analyze_with_llm` 随后执行 `result["_model"] = LLM_MODEL`（:1472）对 list 赋值抛 TypeError，本文件内无捕获。
- 影响: service.py 轮询函数的外层 except 会吞掉异常，但该轮周期中断——`_update_state_after_push`/`save_state`/`save_raw_data` 全部跳过，游标不前进，下一轮把同一批簇当新簇重复送 LLM 分析并重复推送（多花钱 + 重复打扰），而不是按设计降级为占位结果。
- 建议: `_call_json` 末尾加 `return result if isinstance(result, dict) else {}`，让非 dict 输出走既有的 `degraded: llm_failed` 降级路径。
- 置信度: 高

### [P3] backend/app/flash/llm.py:52 `or 50` 把显式配置 0 吞成默认值，LLM_DAILY_MAX_CALLS=0 无法"全停"
- 原文: `LLM_DAILY_MAX_CALLS = int(os.environ.get("LLM_DAILY_MAX_CALLS", "50") or 50)`
- 问题: `"0"` 是合法配置（意为当日禁用 LLM），却因 `or` 的 falsy 回退被解析成 50；同模式还有 :84/:85（LLM_TIMEOUT_FAST/SLOW，设 "0" 得到 timeout=0 必然全超时）与 :518。
- 影响: 运维想用 0 紧急停用 LLM 时静默失效，仍按 50 次放行；属配置语义陷阱而非运行期错误。
- 建议: 仅当 env 为空串/未设时用默认值（`v = os.environ.get(...); LLM_DAILY_MAX_CALLS = int(v) if v and v.strip() else 50`），并对 <=0 给出告警日志。
- 置信度: 高

### [P3] backend/app/flash/llm.py:292 用量文件兜底路径不区分 toward_budget，影子调用也计入日熔断预算
- 原文: `        d["calls"] += 1`
- 问题: `_record_usage` 的 DB 路径按 `toward_budget` 写 0/1，但 DB 写失败退回文件时无条件 `+1`，且 `provider` 参数被丢弃；与 :229 注释"影子不计入日熔断预算"矛盾。
- 影响: 仅在"DB 写失败 + LLM_FREE_SHADOW=1"窗口生效：影子调用消耗正式预算（或反过来低估 DB 侧与文件侧账目），分站对账明细在兜底期丢失。
- 建议: 文件兜底同样判断 `toward_budget`（影子只累加 token 不加 calls），并尽量记录 provider 前缀。
- 置信度: 高

### [P3] backend/app/flash/llm.py:693 站内重试与 429 退避绕过 LLM_MIN_INTERVAL 全局间隔闸
- 原文: `            time.sleep(attempt * 3)`
- 问题: `_respect_call_interval`（:593）只在 `call_llm` 入口执行一次；`_call_provider` 内的重试仅 sleep 3s/6s，429 退避 `4.0*attempt`（:655 首次仅 4s），均可能小于默认 6s 下限，与 :524 "任意两次请求发出之间至少间隔"的注释不符。
- 影响: 恰好在最易触发 RPM 限流的 429 之后，重试请求仍可能低于安全间隔再次 429，削弱 LLM_MIN_INTERVAL 对免费站的保护（正是 :510-517 要根治的"整链失败"形态）。
- 建议: 把间隔闸下沉到 `_call_provider` 每次发请求前（或 429 退避取 `max(wait, LLM_MIN_INTERVAL)`）。
- 置信度: 高

### [P3] backend/app/flash/llm.py:695 429 重试耗尽仍标记熔断，持续限流会把唯一 provider 熔断 30 分钟
- 原文: `        _provider_mark(pkey, False, _last_error)`
- 问题: 429 走退避重试（:647-660）但重试耗尽后仍落入统一失败标记；:546 docstring"站点级故障（401/403/429/5xx）不重试直接降级下一站"与 :517"429 被归类为站点级故障"的旧注释均与现实现不一致。
- 影响: 免费站持续限流（如 TPM 不足）时，两次调用即熔断唯一配置的 provider 30 分钟，重现"某些时段 LLM 全不可用"；文档与代码漂移也会误导排障。
- 建议: 明确语义——若认定 429 瞬时则重试耗尽不 `_provider_mark`（只返回失败），或更新 :510-517/:546 注释为"429 重试 N 次后仍失败则计入熔断"。
- 置信度: 中

### [P3] backend/app/flash/llm.py:625 影子线程与主链竞写全局 `_last_error`，诊断接口可见最后错误被污染
- 原文: `    global _last_error`
- 问题: `_call_provider` 无条件写模块级 `_last_error`，影子调用（`_shadow_run` → `_call_provider(mark=False)`）在后台线程同样写该全局，且无锁。
- 影响: 影子失败会覆盖主链成功后清空的 `_last_error`（或反之），`/api/system/llm-usage` 展示的 last_error 与真实主链状态不符，误导排障。
- 建议: 影子路径不写全局（`toward_budget=False` 时同步跳过 `_last_error` 赋值），或改为线程本地+主链显式汇总。
- 置信度: 高

### [P3] backend/app/flash/llm.py:471 `_shadow_threads` 只增不清，常驻进程慢性泄漏
- 原文: `    _shadow_threads.append(t)`
- 问题: 每次影子 spawn 都 append 到模块级列表，`wait_shadow_threads` 只读不删，无任何清理点。
- 影响: LLM_FREE_SHADOW=1 的常驻进程随每次主链调用累积 Thread 对象（每天数十个，量级小但只增不减），列表遍历成本也随之增长。
- 建议: spawn 时顺手清理已结束线程（`_shadow_threads[:] = [t for t in _shadow_threads if t.is_alive()]`），或用有界队列。
- 置信度: 高

### [P3] backend/app/flash/llm.py:1472 模型归因失真：free 槽/兜底模型产出仍记为 LLM_MODEL
- 原文: `    result["_model"] = LLM_MODEL`
- 问题: provider 链成功返回时（可能来自 free 槽或站内兜底模型），诊断流 `_model` 与复盘流返回的 model（:1666 `return clean_md, signals, LLM_MODEL`）都硬编码主力槽主模型。
- 影响: 落盘 flash_analyses/flash_reviews 的 model 字段与前端展示失真，多 provider 对比期无法从数据侧确认"这条是谁生成的"。
- 建议: 让 `call_llm`/`_call_provider` 返回实际命中的 (provider, model)，随结果一并传出。
- 置信度: 高

### [P3] backend/app/flash/llm.py:251 llm_usage_daily 与 llm_usage_by_provider 两条 upsert 不在同一事务
- 原文: `                    "INSERT INTO llm_usage_by_provider (day, provider, calls, "`
- 问题: `db.execute` 每次独立 commit（database.py `_get_conn` 上下文管理器逐次提交），日表与分站表两次累加之间进程崩溃/断连会出现单侧入账。
- 影响: 熔断计数与分站对账短暂不一致（日表已加、分站表未加或反之）；影响仅限对账精度，不影响熔断正确性（日表自身为原子累加）。
- 建议: 若 database.py 支持事务边界，把两条 upsert 包进同一事务；否则在摘要中注明两表非强一致。
- 置信度: 高

### [P3] backend/app/flash/llm.py:1493 D 状态合规"代码层审查"只复述 LLM 自报，无独立校验
- 原文: `        _strip(lambda e: "黄金ETF" in (e.get("target") or "") and e.get("action") == "减仓",`
- 问题: `strip_d_state_violations` 的剔除条件以 `d_state_compliance` 自报布尔为前提，且 target 用精确子串匹配（"黄金ETF"/"标普油气ETF"），不独立扫描 `top_events` 实际建议、也不校验 LLM 自报与 `correlation_diagnosis.correlation_state` 的一致性。
- 影响: LLM 漏报自报字段或 ETF 名称写法稍异（如"油气ETF"）时，D 状态违禁建议（做空黄金/抄底油气）原样通过并推送。
- 建议: 当 `correlation_state == "D状态"` 时无条件按规则扫描 top_events 剔除违禁组合（不再以自报为前提），ETF 名匹配放宽为关键词集合。
- 置信度: 中

### 摘要 backend/app/flash/llm.py
职责：LLM provider 链客户端（free→main，站内重试 3s/6s、第 2 次切兜底模型、401/403/5xx 降级、连续 2 败熔断 30min、影子模式）+ 日熔断与用量记账（DB 为准、文件兜底、保留 30 天）+ 诊断流（JSON 模式，D 状态合规剥离）与复盘流（Markdown + 末尾 ```json 信号块提取校验）的 prompt 构建。｜输入：事件簇、宏观面板、ETF/个股持仓、数据质量评估、env 配置。｜输出：诊断 dict（失败返回 `degraded` 占位）、(markdown, 校验后 signals, model)、`get_llm_usage` 用量/熔断状态。｜关键阈值/环境变量：LLM_DAILY_MAX_CALLS=50、LLM_MAX_TOKENS=8192 / LLM_MAX_TOKENS_CAP=32768（截断翻倍封顶）、LLM_TIMEOUT_FAST=300 / LLM_TIMEOUT_SLOW=600、LLM_MIN_INTERVAL=6、LLM_FREE_SHADOW、LLM_FREE_REASONING_EFFORT、LLM_MODEL_FALLBACK、LLM_PRICE_IN/OUT。｜依赖：app.flash.rules（beijing_now/数据质量/聚类）、app.flash.store（_load/_save、各 load_*）、app.database.db、requests.Session；懒加载 market/macro/eastmoney/flash.calendar/cb_gold/treasury/portfolio_scope/tencent。｜已知坑：推理模型必须放 free 槽（reasoning_effort 仅注入该槽）；fast 超时仅"链首为 free 或显式配置"时生效；影子线程为 daemon，批处理退出前需 `wait_shadow_threads` 兜底；429 语义经多次修订（现退避重试但耗尽仍计入熔断）；重试与 429 退避不受全局间隔闸约束。

---

## 二、backend/app/flash/store.py

### [P1] backend/app/flash/store.py:544 etf_close 日期用非零填充 "YYYY/M/D" 且按文本 ORDER BY，保留/读取全部错位
- 原文: `    today = _bj_date()`
- 问题: `save_etf_close` 以 `_bj_date()`（如 "2026/10/9"）为 date 主键，而 trim（:560）、`load_etf_close`（:569）、`load_etf_close_history`（:584）全部对该 TEXT 列做字典序 `ORDER BY date DESC`——"2026/10/9" > "2026/10/23"，"2026/9/x" > 全部 10 月。这正是 :507-511 在 `load_macro_daily_history` 已修过并写明教训的同款 bug，etf_close 未修。
- 影响: ① trim 只保留"文本最大"的 30 行：9 月整月排最前，10 月两位数日期（10/10 起）被当最旧删除 = 新数据被清理；② `load_etf_close` 在 10 月返回 9 月上旬快照，复盘 prompt 的"昨日收盘ETF参考"（llm.py:1595）与 `format_etf_history`（llm.py:1201）喂给 LLM 数周前的旧价。
- 建议: date 统一改存 ISO 零填充（迁移旧值），或读/删两侧改用与 `_key()`（:526-531）同款的解析后排序；trim 改按解析日期。
- 置信度: 高

### [P2] backend/app/flash/store.py:203 `WHERE id = ANY(%s)` 为 PostgreSQL 专有语法，SQLite 后端必抛错
- 原文: `                    "WHERE id = ANY(%s)", (list(dict.fromkeys(want)),))`
- 问题: 模块声明支持 SQLite/PG 双后端（文件头 :6，`_record_usage` 在 llm.py 也按 `db._use_postgres` 分叉），但 `= ANY(...)` 仅 PG 可用，SQLite 会直接报 SQL 语法/函数错误。
- 影响: SQLite 部署下 `load_raw_items_by_ids` 抛异常 → `format_cluster_text` → `build_review_prompt` → `run_review_llm` 全链失败（该函数无 try 包裹），复盘流完全不可用。
- 建议: 按 `db._use_postgres` 分叉：PG 用 `= ANY(%s)`，SQLite 用 `IN (动态占位符)`。
- 置信度: 高（代码层面）；实际影响取决于是否仍有 SQLite 部署——中

### [P2] backend/app/flash/store.py:127 快讯去重 cutoff 用无时区 `datetime.now()`，与同函数 :154 的 UTC+8 写法不一致
- 原文: `        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")`
- 问题: `flash_news.time` 为北京时间字符串，此处用服务器本地 naive 时间（Render=UTC）算 3 天前，而同函数清理旧数据处（:154）用 `datetime.now(timezone.utc)+8h` 的正确换算；属项目红线明令的 utcnow/时区混用（rules.to_beijing 注释亦记载过三套时间混用事故）。
- 影响: UTC 服务器上去重窗口实际为"3 天 + 8 小时"，方向保守（多保留 ID）暂无实害，但两套时间基准并存，一旦有人反向复制此写法（把 cut 写小）就会误删或漏去重。
- 建议: 统一改用 `rules.beijing_now()`（tz-aware）后 `strftime`，与 :154 同一来源。
- 置信度: 高

### [P3] backend/app/flash/store.py:323 `load_review`/`load_review_history` 的 json.loads 无异常保护
- 原文: `        "signals": json.loads(row["signals_json"]) if row.get("signals_json") else []`
- 问题: signals_json/markdown 行损坏（非合法 JSON）时直接抛 JSONDecodeError；同文件 `load_analyses`（:269-276）对同样场景做了 try/except，两处口径不一致。
- 影响: 单行脏数据让"最新复盘/复盘历史"接口 500，且因取最新一条往往正好命中坏行，表现为持续性不可用而非偶发。
- 建议: 仿 `load_analyses` 包 try/except 降级为 `[]`。
- 置信度: 高

### [P3] backend/app/flash/store.py:757 `_snap_disk_save` 固定 ".tmp" 路径，并发写同一 tmp 文件存在竞态
- 原文: `        tmp = _SNAP_DISK_PATH + ".tmp"`
- 问题: 多线程同时回源（300s 缓存失效风暴/多 worker）会并发连接同一个 tmp SQLite 文件写入再 `os.replace`，可能出现写入互相踩踏或 rename 了半成品。
- 影响: 本机缓存文件偶发损坏——读取侧 `_snap_disk_load` 异常返回 None 会回源自愈，故仅损失一次缓存收益，无数据正确性问题。
- 建议: tmp 文件名加进程/线程唯一后缀（`f"{_SNAP_DISK_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"`）。
- 置信度: 中

### [P3] backend/app/flash/store.py:178 进程缓存对象直接共享返回，调用方原地修改会污染缓存
- 原文: `        return _RAW_CACHE["data"]`
- 问题: `load_raw_items`（:178）与 `load_macro_history`（:377）TTL 命中时返回同一 list/dict 对象；同文件 `load_market_snapshot`（:791）因"tencent.restore_market_snapshot 会原地改写"特意 `copy.deepcopy`，口径不一致。
- 影响: 任一调用方对返回结构原地增删改（本项目已发生过同类事故），缓存窗口内（120s/600s）所有后续调用方读到被污染数据。
- 建议: 与 market_snapshot 对齐返回浅/深拷贝，或在 docstring 显式声明"只读，禁止修改"。
- 置信度: 中

### 摘要 backend/app/flash/store.py
职责：快讯原始数据（120 天/2 万行清理、id 幂等 upsert、写侧失效读缓存）、LLM 诊断/复盘落盘（50/20 条保留）、宏观历史与每日快照、ETF 收盘历史、调度状态（fail-open 查询）、全市场收盘快照（三层缓存：进程 300s → 本机 SQLite saved_at 版本门控 12h → 回源）的持久化层。｜输入：JSON 化 dict/list（快讯 items、analysis dict、宏观面板、holdings、signals）。｜输出：DB 行 + 进程缓存对象（market_snapshot 为 deepcopy，其余共享引用）。｜关键阈值/环境变量：FLASH_KEEP_DAYS=120、FLASH_KEEP_ROWS=20000、_RAW_TTL=120s、_MACRO_TTL=600s、_SNAP_TTL=300s、_SNAP_DISK_TTL=12h；保留条数硬编码（快讯 300 / 诊断 50 / 复盘 20 / 宏观 150 / ETF 30 天）。｜依赖：app.database.db（PG/SQLite 双后端，execute 逐条 commit）、app.flash.rules（beijing_now/beijing_now_iso/交易日）、sqlite3（本机快照缓存）、app.mainforce.flow（快照写入联动失效）、app.macro_daily（盘后转存）。｜已知坑：etf_close/macro_daily/schedule_state 的 date 键为 "YYYY/M/D" 非零填充（macro_daily 读侧已解析排序修复，etf_close 侧未修）；`load_raw_items_by_ids` 为 PG 专有 `= ANY` 语法；data/*.json 仅剩 LLM 用量兜底与备份路径兼容用途，其余已迁库；market_snapshot 为全库最大单行（约 1.1MB），读路径设计高度依赖 saved_at 版本门控。

---

## 未发现问题

- **空响应重试有界、无无限循环/重复计费**：`_call_provider` 为 `for attempt in range(1, retries+1)` 封闭循环，max_tokens 翻倍受 `LLM_MAX_TOKENS_CAP`（32768）封顶（:687）；每次 HTTP 200 恰好记账一次，逐次重试各自计费与 provider 实际账单一致，无双倍计数。
- **DB 路径影子调用不计日熔断预算**：`toward_budget=False` 时 `calls_val=0`（:233）且分站表 +1，实现与注释一致（例外仅文件兜底路径，见 llm.py:292 问题条）。
- **用量累加无并发重复计数**：日表/分站表均为 SQL 侧 `calls = calls + EXCLUDED.calls` 原子累加，进程内另有 `_usage_lock` 串行化，跨进程亦安全；`_import_file_once` 用 `ON CONFLICT DO NOTHING` 幂等补历史。
- **结构化输出红线达标**：诊断流 JSON 模式 + 失败返回 `degraded` 占位（:1454/:1470）；复盘流信号走 `extract_structured_signals` 代码侧校验（direction 白名单 long/short、support/resistance 必须为正数且 support<resistance、字段截断、上限 5 个），失败返回空列表并从推送正文中剥离 json 块（:1665）——前端消费的是结构化字段与 Markdown，不存在"前端解析自然语言数字"。
- **`append_macro_history` 3 分钟去重的时区比较正确**：`_now_iso()` 来自 `rules.beijing_now_iso()`（带 +08:00），schema.sql 中 `macro_history.time` 为 TEXT，`datetime.fromisoformat` 得到 aware 对象，与 `datetime.now().timestamp()` 同一 epoch 基准，UTC 服务器上亦正确。
- **无数据库会话泄漏**：store.py 打开的 sqlite3 连接均在 `finally: conn.close()`；主库连接由 database.py 线程局部 + 上下文管理器统一提交/回滚。
- **upsert 幂等性达标**：PG 走 `ON CONFLICT DO UPDATE`、SQLite 走 `INSERT OR REPLACE`（database.py:232-260），flash_state/schedule_state/etf_close/market_snapshot/flash_news 重复写安全；`is_schedule_done` 查询失败按"未完成"fail-open 为有意设计且有事故注释支撑。
- **本两文件不存在"按日缓存 + sig 数据指纹"逻辑**：llm.py:594 的 `task_key`（md5 前 12 位）仅用于影子日志关联，不参与调用判重或缓存；该审查点在本范围内无对应代码，无法评估"指纹没变是否不重复调用"（相关逻辑如存在，位于 flash service 等其它文件）。
