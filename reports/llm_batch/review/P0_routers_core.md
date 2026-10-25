# P0 路由核心审查（routers）

审查范围：7 文件 / 约 3338 行 / 2026-10-24

---

## backend/app/routers/stock.py（1049 行）

### [P1] backend/app/routers/stock.py:281 /finance-refresh 未鉴权且在请求内做全市场财报抓取
- 原文: `def finance_refresh(reports: int = 2):`
- 问题: POST 接口无任何鉴权，处理函数内直接调 `refresh(...)`（L287 `return refresh(min(max(reports, 1), 8))`），`app/finance.py` 的 `refresh` 按 500 行/页、最多 60 页全市场抓财报（实测每次约 27 页/14 秒），最多 8 期连拉。
- 影响: 公网任何人一次 POST 即可在请求线程内触发数分钟的全市场外网抓取+入库，可被循环滥用打爆线程池与出口流量，直接违反"接口内禁止全市场同步拉取"红线。
- 建议: 移到调度器/日批执行，本接口改为只读"上次刷新状态"；或至少加 `Depends(get_current_user)` 并把 reports 上限压到 1、后台任务化立即返回。
- 置信度: 高

### [P2] backend/app/routers/stock.py:403 涨跌停判定自造 0.005 元容差，偏离唯一口径 app/limit_stats
- 原文: `    up_hit = (price >= limit_up - 0.005) if limit_up else (9.8 <= change_pct <= 10.1)`
- 问题: 项目唯一口径 `app/limit_stats.is_limit_up` 用相对容差 `price >= lu * (1 - _BREAK_TOL)`（0.2%），其文档明确警告"固定 0.005 元容差会系统性少算（实测差 24 只）"；本文件异动判定自造绝对容差，且无真值价时的 9.8~10.1 启发式缺 limit_stats 的"超出板幅即不算"守卫（新股/退整期误判面）。
- 影响: 高价股（如 100 元股 0.2%≈0.2 元）贴板时 limit_stats 判涨停而异动列表不判，两页面对同一票口径打架，违反"涨跌停口径唯一"红线。
- 建议: `_detect_anomaly` 改调 `limit_stats.is_limit_up/is_limit_down`，删除本文件内联容差与启发式。
- 置信度: 高

### [P2] backend/app/routers/stock.py:236 基本面缺失字段回 0（缺失≠0）
- 原文: `            "市盈率(动态)": info.get("pe", 0),`
- 问题: 快照里 PE/PB/市值/换手取不到时统一回 0，前端会把"数据缺失"渲染成"市盈率为 0"。
- 影响: 违反"缺失≠0"时点诚实红线：0 倍 PE 会被当成真实读数，误导选股与前端展示。
- 建议: 缺失时返回 `null`（`info.get("pe")`），让前端按"无数据"渲染。
- 置信度: 高

### [P2] backend/app/routers/stock.py:351 消息面失败伪装成"中性 0 分"并缓存
- 原文: `        result = {"score": 0, "level": 0, "level_text": "中性", "items": []}`
- 问题: 抓取/打分抛异常时返回与"真实中性"同构的 score=0 结果，且 L353 会把它写入 `_news_cache` 缓存 60s，没有 `error` 或 `missing` 标记。
- 影响: 数据源故障期间详情页显示"消息面中性 0 分"，失败与真实中性不可区分（fail-open 且污染时点诚实）。
- 建议: 异常路径返回 `{"score": None, "error": ..., "items": []}` 且不写缓存，让下次请求重试。
- 置信度: 高

### [P2] backend/app/routers/stock.py:305 消息面两个进程级缓存只增不减
- 原文: `_news_cache = {}`
- 问题: `_news_cache`（L353 每只票都写）与 `_news_history_cache`（L329）只写入、TTL 仅用于读判断，永不清理条目，键空间随访问过的股票数（及 days 1~90 组合）无界增长。
- 影响: 在 500MB 内存上限的 Render 实例上慢性泄漏（system.py 内存探针里已单列这两项），最终加速 OOM 重启。
- 建议: 读时顺手清过期键，或换 `cachetools.TTLCache(maxsize=2000, ttl=60/300)`。
- 置信度: 高

### [P2] backend/app/routers/stock.py:40 symbol 未校验直接拼进腾讯出站 URL
- 原文: `    return get_kline(symbol, period=period)`
- 问题: 路径参数 symbol 未经格式校验即透传 `tencent.get_stock/get_kline`，最终在 `tencent.py:296` `url = f"https://qt.gtimg.cn/q={codes_str}"` 裸拼接（已核实）；URL 编码的 `%3F`/`%26` 等可进入出站查询串。
- 影响: 未鉴权端点可被用作向第三方行情接口注入任意查询参数的跳板，并污染解析缓存与 WAF 状态。
- 建议: 各端点入口用 `re.fullmatch(r"\d{6}", symbol)` 校验，不合法直接 400。
- 置信度: 中

### [P3] backend/app/routers/stock.py:897 /anomaly-analysis GET 未鉴权且 refresh=true 绕过复用窗口
- 原文: `def stock_anomaly_analysis(symbol: str, refresh: bool = False):`
- 问题: GET 请求即可能触发 1 次 LLM 调用，`?refresh=true` 跳过 30 分钟复用窗口；端点无鉴权，成本仅靠全局日熔断与 6s 节流兜底。
- 影响: 公网爬虫/脚本可消耗当日 LLM 额度，把真正的按需分析额度提前烧完。
- 建议: 加 `Depends(get_current_user)`，或对 refresh=true 的匿名调用按 IP 限频。
- 置信度: 中

### 摘要 backend/app/routers/stock.py
职责：个股 K线/实时/搜索/技术指标(MA/MACD/RSI/KDJ/BOLL)/基本面/财报/消息面 + 全市场异动扫描与单票异动闸门、单股异动 LLM 解读｜输入：URL 路径 symbol、period/limit/report_date/asof 查询参数、POST codes 列表｜输出：K线+指标数组、估值 dict、异动 signals/tags、LLM JSON 解读（含 support/against/watch_levels/missing）｜关键阈值：技术指标最少 30 根 K线（拉 500 根）；急涨/急跌 ±5%（±8% 升级 severity 2）；涨停/跌停=真值价±0.005 元（回退 9.8~10.1%）；高换手>10%；大振幅>8%；放量=量比≥3 且 |涨跌幅|≥2；急拉/跳水=5 分钟涨速 ±3%；消息面缓存 60s、历史 300s；LLM 结论复用 30 分钟；finance/batch 上限 1000 只｜依赖：app.tencent（_cache/speed_pct）、app.finance、app.eastmoney_news/news_sentiment、app.news_history、app.mainforce、app.backtest.data、app.flash.llm/_rules、app.flash_history、app.daily_report._ORDER_WORDS｜已知坑：async 改同步 def 防事件循环卡死（已修）；前端 tags 字段曾恒 undefined（已补）；异动涨跌停自造容差未走 limit_stats（本次 P2）；消息面失败伪装 0 分、两缓存无界（本次 P2）。

---

## backend/app/routers/system.py（1115 行）

### [P2] backend/app/routers/system.py:114 日批回调共享密钥非常量时间比较
- 原文: `    if not _BATCH_TOKEN or x_batch_token != _BATCH_TOKEN:`
- 问题: `POST /batch-done` 是唯一无需 JWT 的写副作用端点（推企微），token 用 `!=` 逐字节比较，且无失败限速。
- 影响: 理论上可时序侧信道逐字符猜共享密钥（配合无限速爆破），命中后可任意刷企微推送。
- 建议: 改 `hmac.compare_digest(x_batch_token, _BATCH_TOKEN)`，并对 403 加简单计数限速。
- 置信度: 高

### [P3] backend/app/routers/system.py:867 db-usage 缓存命中时忽略 top 参数
- 原文: `        return dict(_DB_CACHE["val"], cached=True)`
- 问题: 60s 缓存命中时直接返回首次调用按其 `top` 算出的分表清单，后续不同 `top` 请求拿到的是别人的截断结果。
- 影响: 同一分钟内 Dashboard(top=8) 与其它调用方(top=12) 看到的表数量不一致，判读困惑。
- 建议: 缓存存全量分表，命中后按请求的 top 再切片。
- 置信度: 高

### 摘要 backend/app/routers/system.py
职责：数据新鲜度仪表盘(/status)、日批完成企微回调(/batch-done)、进程内存诊断(/memory)、缓存手动释放(/cache-release)、数据库体积诊断(/db-usage)、工作台回放聚合(day-index/day/decision-card)与各类观察器只读出口｜输入：查询参数 days/date/top/types/history/refresh，batch-done 的 Body date + X-Batch-Token 头｜输出：各数据源 last/status(ok/stale/missing)/lag_days、RSS/缓存占用/增长 diff、PG 分表体积、回放日历与聚合｜关键阈值：新鲜度容忍 2 个交易日（周任务 8）；batch-done 就绪窗口 6h（BATCH_DONE_WINDOW_HOURS）；内存上限 500MB（MEM_LIMIT_MB）、RSS>85% 跳过 GC 类型分布；DB 上限 500MB（DB_LIMIT_MB）+60s 缓存，80%/100% 分级提示；内存增长噪声阈值 0.5MB 或 20 条；db-usage 表 <1KB 不入榜｜依赖：app.auth.get_current_user、app.database.db、app.flash.rules(beijing_now/HOLIDAYS)、app.flash.scheduler/status、app.memory_watch、app.cache_release、app.data_files、app.flash.wechat｜已知坑：_to_dt 兼容 PG `+00` 时区后缀；时间统一 beijing_now 防 UTC 差 8 小时；日期键"运行日/交易日"两派故 batch-done 改判"最新行写入时间"；状态接口只泄露表名/缓存键/Python 版本（均需登录）；/status 无 TTL 但前端仅进页调用一次（已核实非 60s 轮询）。

---

## backend/app/routers/flash.py（475 行）

### [P1] backend/app/routers/flash.py:130 整个路由无鉴权，匿名 POST 可烧 LLM/推企微/触发外网轮询
- 原文: `def flash_brief_refresh(date: str = None):`
- 问题: 本文件全部端点（含 POST /brief/refresh 一次批量 LLM、POST /review/{phase}/run 复盘 LLM、POST /ingest 外网快讯轮询、POST /wechat-test 向企微群发消息、GET /radar-analysis 缓存未命中即调 LLM）都没有 `Depends(get_current_user)`，main.py 也无全局鉴权中间件（已核实仅 CORS）。
- 影响: 公网任何人可匿名消耗 LLM 日额度、向用户企微群发消息、驱动外网轮询，违反"单接口 fail-open/成本可见"纪律且与同项目 /api/system/* 全鉴权形成反差。
- 建议: 全 router 统一 `APIRouter(dependencies=[Depends(get_current_user)])`；如需保留 /status 只读匿名，单独拆出。
- 置信度: 高

### [P2] backend/app/routers/flash.py:53 /calendar 缓存为空时在 GET 请求内现场外拉
- 原文: `    items = calendar.upcoming(days=days, min_star=min_star,`
- 问题: docstring 自述"缓存为空时会现场拉一次再返回"，调用链 `upcoming → get_items(refresh_if_empty=True) → refresh()`（已核实 calendar.py:292-297）会在请求线程内做多页外网抓取。
- 影响: 违反"接口只读缓存，缓存空如实降级"红线；缓存被清或首次冷启动时，匿名 GET 直接承受外部抓取延迟/失败。
- 建议: 缓存空时返回 `{"items": [], "stale": true}` 并由调度器补数，或 refresh_if_empty=False。
- 置信度: 高

### 摘要 backend/app/routers/flash.py
职责：快讯事件流/财经日历（全量+核心前瞻）/LLM 诊断与三段复盘读取、手动触发（ingest/run/brief/refresh）、信号跟踪与审计、推送时间线、系统自洽性审查(radar-analysis)、页面通知轮询源(notifications)、调度器状态(/status)｜输入：page/size/limit/days/min_star/kind/date/phase/since 查询参数与 path phase｜输出：事件分页、日历 items、markdown 复盘与结构化结论(conclusion/reasons)、信号漏斗审计、通知事件列表(≤20 条)、调度+健康+LLM 用量聚合｜关键阈值：events size≤200；calendar days 1-60、min_star 0-5；radar-analysis 喂 40 条×正文 300 字、按日落库；push-log 正文截 400 字符（egress 治理）；notifications 每分钟轮询、最多返回 20 条；诊断 limit≤20｜依赖：app.flash.store/service/scheduler/wechat/rules、app.signals.tracker、app.flash.signal_bus、app.flash.llm.call_llm、app.insight、app.flash.calendar/calendar_ahead、app.health、app.flash_brief｜已知坑：整路由无鉴权（本次 P1）；/calendar 空缓存现场外拉（本次 P2）；所有时间戳已统一 beijing_now；notifications 已把 since 下推 SQL 且统一北京时间（历史 8 小时重弹已修）；backup/restore 已退役。

---

## backend/app/routers/user.py（357 行）

### [P1] backend/app/routers/user.py:186 PUT /plans/{id} 按 id upsert 可接管他人交易计划（越权+mass assignment）
- 原文: `    db.upsert("user_trade_plans", updates, conflict_columns=["id"])`
- 问题: `updates` 来自客户端任意键（仅过滤 None），只强改 id/user_id 两个键后 upsert；`db.upsert` 对 PG 生成 `ON CONFLICT (id) DO UPDATE SET user_id=EXCLUDED.user_id,...`（已核实 database.py:248-253）。传入他人 plan_id 即把该行整体改写到自己名下；且任意键直接成为 SQL 列名（不存在的列 500，存在的列如 created_at 可被覆盖）。
- 影响: 多用户部署下可横向越权改/占有他人交易计划，违反"是否有越权"红线。
- 建议: 改为 `UPDATE ... WHERE id=%s AND user_id=%s`（先查归属再更新），白名单字段集合，冲突键不带业务列。
- 置信度: 高

### [P1] backend/app/routers/user.py:322 /sync 计划 id 由客户端提供，同 upsert 链可覆盖他人计划
- 原文: `                "id": item.get("id", f"tp_{int(datetime.now().timestamp() * 1000)}"),`
- 问题: 批量同步先删本用户行，再用客户端传入的 id 逐条 upsert（conflict_columns=["id"]、含 user_id 列），与他人计划的主键冲突时同样整行覆盖改写归属（同 database.py upsert 语义）。
- 影响: 与上一条同根因的第二入口：一次 /sync 即可批量抢占/篡改其他用户的计划。
- 建议: 服务端生成 id（忽略客户端 id 或校验 `id` 前缀归属），或 upsert 冲突键改为 (user_id, id) 并先验归属。
- 置信度: 高

### [P2] backend/app/routers/user.py:306 /sync 先删后插非事务，中途失败丢数据
- 原文: `        db.execute("DELETE FROM user_watchlist WHERE user_id = %s", (uid,))`
- 问题: 三段"先 DELETE 再循环 upsert"各自独立提交（db.execute 每条即提交），循环中任一条失败即留下半删半插状态。
- 影响: 客户端一次网络抖动/脏数据可造成该用户自选股/持仓永久性部分丢失（无回滚）。
- 建议: 包一个事务（连接级 BEGIN/COMMIT）或改为先写临时快照校验通过后再原子替换。
- 置信度: 高

### [P2] backend/app/routers/user.py:284 /exit-advice 不按请求用户隔离
- 原文: `    return build_exit_advice()`
- 问题: 端点已鉴权但 `build_exit_advice()` 无 user 参数（已核实 app/exit_advice.py:231 `def build() -> Dict`），内部复用全局 portfolio_radar 数据；对照 `/position-sizing`（L266）是显式传入当前用户 codes 的。
- 影响: 多用户部署下任何登录用户拿到的是同一份（全局/首个用户）出场建议，数据隔离失效。
- 建议: 给 `build()` 传 `user_id` 并让 portfolio_radar 按用户取持仓，或在文档明示本项目单用户语义。
- 置信度: 中

### 摘要 backend/app/routers/user.py
职责：自选股/交易计划/持仓的 CRUD、批量全量同步(/sync)、仓位建议(/position-sizing)、统一出场建议(/exit-advice)｜输入：JWT（get_current_user）+ Body dict（code/name/price/status/plan_type/trigger_* 等）｜输出：`{data: [...]}` 列表或 `{success: true}`｜关键阈值：watchlist/portfolio 冲突键 (user_id, code)；plans 冲突键 id；plan_type 缺省 "trial"；_ensure_plan_columns 进程内一次性补齐 3 列（plan_type/trigger_high_open/trigger_volume_break）｜依赖：app.database.db、app.auth.get_current_user、app.portfolio_radar.invalidate_holdings、app.coach.position_sizing、app.exit_advice｜已知坑：schema.sql 的 IF NOT EXISTS 不给老表加列故在路由内 ALTER（幂等）；plans upsert 按 id 冲突存在跨用户接管面（本次 P1）；/sync 非事务（本次 P2）；SQL 全参数化无注入。

---

## backend/app/routers/tasks.py（127 行）

### [P1] backend/app/routers/tasks.py:62 async def 内同步阻塞 requests.post，卡事件循环最长 15 秒
- 原文: `async def trigger_task(body: TriggerIn, user: dict = Depends(get_current_user)):`
- 问题: 处理函数声明为 `async def` 却在 L88 用同步 `requests.post(..., timeout=15)` 直调 GitHub API，阻塞的是事件循环本身（stock.py 头注释记载的同款事故：一个慢出站请求卡死整个进程、全部并发请求 502）。
- 影响: GitHub API 抖动时（最长 15s）所有并发请求（含 preflight/轮询接口）被冻结，前端表现为整站无响应。
- 建议: 去掉 async 改普通 def（线程池执行），或换 httpx.AsyncClient。
- 置信度: 高

### 摘要 backend/app/routers/tasks.py
职责：把"重活"（战法扫描/矛盾扫描/日批各任务）转发给 GitHub Actions dispatch，本进程只发一次 HTTPS 出网不做计算｜输入：POST /trigger {task, force}（JWT 鉴权），GET /config 无鉴权｜输出：queued/task/repo/actions_url 或 400/502/503 错误｜关键阈值：ALLOWED_TASKS 白名单 19 项（与 daily_batch.DEFAULT_ORDER 对齐）；GitHub 调用 timeout=15s；未配置 GITHUB_TOKEN 返回 503｜依赖：os.environ(GITHUB_TOKEN/GITHUB_REPO/GITHUB_REF_NAME/DAILY_BATCH_WORKFLOW)、requests、app.auth.get_current_user｜已知坑：2026-09-11 前该接口完全开放（已加 JWT）；/config 刻意保留匿名（只暴露是否配置 token+任务名）；async+requests 阻塞事件循环（本次 P1）。

---

## backend/app/routers/auth.py（108 行）

### [P1] backend/app/routers/auth.py:17 JWT_SECRET 未配置时回退为可预测密钥，token 可伪造（越权全线）
- 原文: `from app.auth import hash_password, verify_password, create_token, get_current_user`
- 问题: 依赖的 `app/auth.py:24-29`（已核实）在 JWT_SECRET 环境变量缺失时用 `sha256(f"stock-scoring-{COMPUTERNAME/HOSTNAME}-secret-2026")` 生成固定密钥；Render 容器主机名可被猜中/泄露后，任何人可离线伪造任意 user_id 的 10 年有效 token。
- 影响: 全部挂 `Depends(get_current_user)` 的端点（user/system/tasks/coach…）鉴权形同虚设，直接越权读写他人数据、触发 Actions。
- 建议: JWT_SECRET 未配置时启动即 fail-fast 拒绝起服务（而非静默回退），并在 Render 控制台配置强随机值。
- 置信度: 高

### [P2] backend/app/routers/auth.py:89 密码哈希实为 salt+SHA-256（文档称 bcrypt）且非常量时间比较
- 原文: `    if not verify_password(password, user["password_hash"]):`
- 问题: `app/auth.py:38-51`（已核实）实为 `sha256(salt+password)` 单轮哈希——文件头注释写"bcrypt"与实现不符，且 SHA-256 抗 GPU 暴力破解远弱于 bcrypt/argon2；`verify_password` 用 `==` 比较。
- 影响: 库泄露时 4 位起的弱密码可被秒级离线爆破；比较侧道次之。
- 建议: 换 `bcrypt.hashpw/checkpw`（或 hashlib.scrypt），比较用 `hmac.compare_digest`。
- 置信度: 高

### [P2] backend/app/routers/auth.py:45 密码下限仅 4 字符
- 原文: `    if len(password) < 4:`
- 问题: 注册仅校验长度 ≥4，无复杂度要求；配合 10 年有效期的 JWT（app/auth.py JWT_EXPIRE_HOURS 默认 87600）放大后果。
- 影响: 公网部署下弱口令可被在线爆破成功后长期持有会话。
- 建议: 下限提到 8 并附常用弱口令黑名单；登录失败加简单限速。
- 置信度: 高

### [P3] backend/app/routers/auth.py:49 注册"查重-插入"非原子，并发同名注册 500
- 原文: `    existing = db.fetch_one("SELECT id FROM users WHERE username = %s", (username,))`
- 问题: SELECT 查重与 INSERT 之间无锁，两个并发请求可通过查重后同时 INSERT，后者撞唯一约束抛未处理异常。
- 影响: 注册接口在竞态下返回 500 而非"用户名已存在"（功能可用性小问题，非安全）。
- 建议: 捕获唯一约束异常转 400，或 INSERT ... ON CONFLICT DO NOTHING 后按行数判断。
- 置信度: 中

### 摘要 backend/app/routers/auth.py
职责：注册/登录/获取当前用户三个端点，签发与校验 JWT｜输入：Body {username, password}（pydantic BaseModel）｜输出：{token, user:{id, username}} 或 400/401｜关键阈值：用户名 3-20 字符；密码 ≥4 字符；JWT HS256、有效期默认 87600h（10 年）、密钥 JWT_SECRET 缺失时机器名回退｜依赖：app.database.db、app.auth(hash_password/verify_password/create_token/get_current_user)｜已知坑：登录失败文案统一"用户名或密码错误"（无用户名枚举）；真实风险集中在 app/auth.py 的密钥回退与 SHA-256 口令哈希（本次 P1/P2，均已在 app/auth.py 核实原文）。

---

## backend/app/routers/wecom_callback.py（107 行）

### [P2] backend/app/routers/wecom_callback.py:61 企微回调签名比较非常量时间
- 原文: `    if _signature(token, timestamp, nonce, encrypt) != msg_signature:`
- 问题: GET 验证与 POST 事件共用 `_verify`，sha1 签名用 `!=` 比较，无失败限速。
- 影响: 理论时序侧信道逐字符猜 WECHAT_WECOM_TOKEN；命中后可向回调端点投递伪造"事件"污染日志。
- 建议: 改 `hmac.compare_digest(_signature(...), msg_signature)`。
- 置信度: 高

### [P3] backend/app/routers/wecom_callback.py:59 无时间戳新鲜度校验，重放窗口无限
- 原文: `def _verify(token: str, timestamp: str, nonce: str, encrypt: str,`
- 问题: 验签只比对 sha1，不检查 timestamp 与当前时间差、也不记录 nonce 去重，同一抓包可无限期重放。
- 影响: 当前 POST 只记日志回 success、GET 只回显 echostr，实际危害有限；但一旦后续"处理事件"（如指令回调），重放即成实际攻击面。
- 建议: 校验 |now - timestamp| ≤ 5 分钟并按 (nonce, timestamp) 做进程内/Redis 去重。
- 置信度: 高

### [P3] backend/app/routers/wecom_callback.py:98 POST 报文无 Encrypt 字段时跳过验签直接回 success
- 原文: `        encrypt = m.group(1) if m else ""`
- 问题: 正则提取失败时 `encrypt=""`，后续 `if encrypt:` 整段验签被跳过，仍返回 200 "success"（注释称为防企微重推的 fail-open）。
- 影响: 伪造/畸形报文与真实报文在响应上不可区分（都是 success），验签形同未验，也会掩盖格式变更导致的静默失联。
- 建议: 无 Encrypt 时区分处理：记 warning 并返回 success 但打点计数（观测失联），至少不与验签成功共用同一路径。
- 置信度: 高

### 摘要 backend/app/routers/wecom_callback.py
职责：企微自建应用"接收消息服务器"回调：GET 回显解密 echostr（控制台验证 URL 用）、POST 验签解密后回 success（暂不处理事件）｜输入：Query msg_signature/timestamp/nonce + GET echostr 或 POST XML 中的 Encrypt CDATA｜输出：text/plain（echostr 原文或 "success"）｜关键阈值：AESKey 必须 43 位 [A-Za-z0-9]（+ Pad 补 = 后 b64decode）；AES-256-CBC，IV=key 前 16 字节；明文结构 random(16B)+msg_len(4B 大端)+msg+receiveid；PKCS7 去垫 ≤32｜依赖：pycryptodome Crypto.Cipher.AES、env WECHAT_WECOM_TOKEN/WECHAT_WECOM_AES_KEY（模块导入时读取，改配置需重启）｜已知坑：签名比较非常量时间（本次 P2）；无重放防护（本次 P3）；无 Encrypt 时 fail-open 回 success（本次 P3）；_pkcs7_unpad 不校验垫字节内容（解密失败由外层 400/忽略兜住）。

---

## 未发现问题

- backend/app/routers/stock.py：`/anomalies` 全市场扫描**只读**进程内 `_cache`，缓存空时返回 `{"data": [], "total": 0}`，不做任何外部拉取（符合"接口只读缓存、缓存空如实降级"红线）；`detect_single_anomaly` 对 `speed_5min=None`（采样不足）全链路如实传递、不当作 0；单票缓存未命中时单拉且明确不写全市场缓存；`/finance/{symbol}` 支持 asof 公告日口径防未来函数，docstring 明示"未披露≠值为0"。
- backend/app/routers/system.py：除 /batch-done（共享密钥保护、未配置时一律 403 安全默认）外，/status、/runtime-files、/memory、/db-usage、/workbench* 等全部挂 `Depends(get_current_user)`；SQL 全部参数化或表名/列名硬编码元组，无注入面；内存诊断 RSS>85% 自动跳过最重的 GC 类型分布（观测不反噬被观测对象）。
- backend/app/routers/flash.py：/notifications 已把 since 过滤下推 SQL 并统一北京时间（历史"最近 8 小时事件反复弹"已修）；/push-log 正文截 400 字符做 egress 治理；/review/{phase} 校验 phase 白名单。
- backend/app/routers/user.py：全部 SQL 参数化；watchlist/portfolio 的 upsert 冲突键含 user_id，无越权面（问题集中在 plans 按 id 冲突，见上）；写持仓/自选后主动失效 portfolio_radar 缓存。
- backend/app/routers/tasks.py：task 白名单校验（无注入面）；GITHUB_TOKEN 未配置时 503 拒绝而非降级开放。
- backend/app/routers/wecom_callback.py：未配置 WECHAT_WECOM_TOKEN 时 500 拒绝（安全默认）；签名算法与官方规范一致（sha1 对 token/timestamp/nonce/encrypt 排序拼接）。
- backend/app/routers/auth.py：登录失败统一文案，不区分"用户不存在/密码错误"；用户名参数化查询无注入；用户名查重回 400 而非静默覆盖。
