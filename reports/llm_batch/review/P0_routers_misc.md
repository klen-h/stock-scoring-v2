# P0 路由杂项审查（routers: backtest/capital/coach/contradictions/daily_report/macro/paper/pattern/performance/sector/__init__）

审查范围：11 文件 / 约 1622 行 / 2026-10-24

核实过的背景事实（下文引用依据，非问题）：
- `app/main.py:234-258` 注册各 router 时均无全局 `dependencies=[...]`，且仅有 CORSMiddleware（main.py:213-214）⇒ 鉴权完全由各端点自理，无 `Depends(get_current_user)` 即为匿名可达。
- `eastmoney.get_northbound/get_sectors/get_sector_flow` 内部均走 `_get_cached(key, TTL, loader)`（eastmoney.py:86, 305, 350, 375, 465）⇒ 东财实时端点可承受 60s 轮询。
- `macro.get_macro_snapshot/get_macro_panel` 内部有 60s TTL 缓存（app/macro.py:49-52, 359-361）⇒ `/api/macro/snapshot` 轮询安全。
- `paper_trading._latest_bar` 无数据时返回 `(0.0, "")`（paper_trading.py:689-695）⇒ paper.py:87 `price <= 0` 判断无 None 风险（该疑点排除）。
- `contradictions/scanner.py:139` 扫描器内部含东财指数 K 线 `urllib.request.urlopen`（3 次重试，每次 sleep 递增）。
- `paper_trading.auto_refresh_whitelist(min_trades=30, win_threshold=55.0)` 签名无 `dry_run` 参数，且函数体只返回建议清单、不写白名单（paper_trading.py:987-1000）。

---

## backend/app/routers/backtest.py（305 行）

### [P1] backend/app/routers/backtest.py:187 匿名 GET 可在缓存全空时触发全量回测重算，且无单飞锁
- 原文: `return _compute_and_cache(name)`
- 问题: `/api/backtest/strategy` 端点无鉴权（对比同项目 coach/performance 均有 JWT），内存缓存与 DB 持久缓存都未命中时（首次部署/清库/换实例）同步走全量计算——文件自述该计算要拉几百只票日线（~2.5 万行/次）；该路径也无并发单飞锁，多个请求同时 miss 会各自重算一遍。
- 影响: Render 0.1 CPU 上匿名用户一次（或并发几次）GET 即可打满实例并放大 Supabase egress，正是本文件注释记载的 "748 次 / 1890 万行" 事故的残余触发面。
- 建议: 给 `/strategy` 加 `Depends(get_current_user)`；至少为 `_compute_and_cache` 加进程级单飞锁（threading.Lock + 双重检查），miss 时返回"计算中"提示而非同步计算。
- 置信度: 高

### 摘要 backend/app/routers/backtest.py
职责：三类策略回测（signals/warfare/macro）查询 + 周度报告归档读取｜输入：策略名 name、报告文件名｜输出：裁剪后的回测指标 / Top10 逐笔（去 daily 路径）/ 净值概要 / 报告 markdown｜关键阈值：内存缓存 600s；持久缓存后台刷新条件 ≥6h 且冷却 1800s；preheat 默认跳过 <3h 新缓存；top_trades 取 |pnl_pct| 前 10｜依赖：app.backtest.strategies、app.backtest.report_store、app.backtest.run.REPORT_DIR、db、threading 后台刷新｜已知坑：持久缓存曾因 RENDER_READ_ONLY 从未刷新导致每次访问退化重算（2026-10-18 修复，刷新改由日批 preheat_all 驱动）；`/reports/content` 有 basename 防穿越校验；曲线概要自算最大回撤段；计算路径无鉴权无单飞锁（见 P1）。

---

## backend/app/routers/capital.py（217 行）

### [P2] backend/app/routers/capital.py:198 龙虎榜统计 SQL 使用 PG 专有 `::text`，违反同函数内自述的双库兼容铁律
- 原文: `"SUM(CASE WHEN seats_json IS NOT NULL AND seats_json::text NOT IN ('[]', 'null') "`
- 问题: `::text` 是 PostgreSQL 专有语法，SQLite 直接语法报错；而同一函数 188-189 行注释刚强调"项目要求双库兼容，db 层只转占位符、不转方言"（为此特意用 COALESCE 替代 NULLS LAST），转头又用了方言转换。
- 影响: SQLite 环境（开发库）下该统计查询抛异常，被外层 except 吞掉后返回 `stats={}`、`seats_count=0` 且 note="读取失败"——但 top_buy/top_sell 已填充，响应自相矛盾（部分数据 + 全局失败标记），席位覆盖统计永远丢失。
- 建议: 改用双库兼容写法，如 `SUM(CASE WHEN seats_json IS NOT NULL AND seats_json NOT IN ('[]', 'null', '') THEN 1 ELSE 0 END)`（PG 的 json 类型可先统一在 _seats_of 层处理）。
- 置信度: 高

### [P3] backend/app/routers/capital.py:47 北向资金 docstring 与数据层"死数据"修复脱节
- 原文: `休市/非交易时段净流入为 0；数据不可用时返回 {}。`
- 问题: 数据层 `get_northbound` 已于 2026-09-27 修复为显式返回 `{available: False, stopped_since: "2024-08-19", ...}`（北向净流入 2024-08 起停止披露），本路由 docstring 仍沿用旧口径"非交易时段为 0 属正常"，未提及 available/stopped_since 标记。
- 影响: 前端按 docstring 对接会把"永久停止披露"误处理为"休市零流入"，正是数据层注释里点名批评过的掩盖式语义（路由本体透传标记，行为是诚实的，属文档漂移）。
- 建议: 更新 docstring，写明恒定返回 `available` 字段、`available=False` 时 `total_net=0` 仅为兼容 falsy 判断。
- 置信度: 高

### 摘要 backend/app/routers/capital.py
职责：资金流向数据代理（北向资金 / 个股主力流排名 / 龙虎榜读库）｜输入：order/limit/date/code 查询参数｜输出：列表类统一 {data, total}，金额单位元并附 *_wan 万元换算｜关键阈值：limit 1-500（main-flow）/ 1-200（dragon-tiger）；席位明细仅对池内个股富化（实测覆盖 ~2%）｜依赖：app.eastmoney（内部 TTL 缓存）、db、lhb_history 表（每日 17:45 同步）｜已知坑：PG json 列与 SQLite TEXT 双形态兼容（_seats_of）；PG SUM(numeric) 回 Decimal 需转 float 否则序列化成字符串；北向数据为死数据、数据层已标 available=False；stats 查询含 PG 专有语法（见 P2）。

---

## backend/app/routers/coach.py（112 行）

### [P2] backend/app/routers/coach.py:52 全部端点注入 user 却从不校验资源属主（横向越权）
- 原文: `if not db.fetch_one("SELECT id FROM coach_alerts WHERE id=%s", (alert_id,)):`
- 问题: 存在性校验只查 id 不过滤属主；`user: dict = Depends(get_current_user)` 在 8 个端点全部注入但无一使用——execute_alert/abandon_plan 可回写任意用户的 alert/plan，get_alerts/get_consistency/get_plans 返回全体用户数据。
- 影响: JWT 保护暗示多用户体系，任何登录用户可篡改他人执行回写（污染执行一致性 KPI、周报复盘）或读取他人教练数据；个人单用户场景影响有限，但鉴权面已存在。
- 建议: coach_alerts/coach_plans 增加 user_id 列（或与现用户表关联），所有读写 SQL 加 `WHERE user_id = %s`。
- 置信度: 高

### 摘要 backend/app/routers/coach.py
职责：交易员教练执行闭环——建议历史 / 执行回写 / 一致性 KPI / 预承诺执行率 / 剧本查询与放弃 / 事后归因（全部 JWT 保护）｜输入：alert_id/plan_id、{executed, reason}、days/day/position_ids｜输出：回写确认 + 分组统计（建议类型/阶段/执行结果/放弃理由）｜关键阈值：limit 钳制 1-200（alerts）/1-100（abandon-reasons）；days 钳制 1-365、归因 7-365；放弃（executed=no）与 abandon_plan 理由必填｜依赖：app.coach.audit、app.coach.attribution、db、get_current_user｜已知坑：设计上"放弃须填理由"治"再等等看"；归因口径注明买入类建议无 code ⇒ 入场理由不可评估；端点不校验属主（见 P2）；本路由只做执行回写与度量，不生成任何买卖建议（红线合规）。

---

## backend/app/routers/contradictions.py（108 行）

### [P2] backend/app/routers/contradictions.py:71 匿名 POST /scan 可反复触发含外部请求的矛盾扫描
- 原文: `items = scan_all(date=target)`
- 问题: `/scan`、`/report`、`/resolve`、`/validate` 四个写/触发端点均无鉴权；scan_all 会跑全部扫描器，其中含东财指数 K 线外部请求（scanner.py:139，每次 3 次重试 + 递增 sleep）并落库覆盖当日。
- 影响: 匿名调用方可循环 POST 打外部接口与数据库写（同日幂等覆盖能防数据错乱，但防不了 CPU/egress 消耗）；`/resolve` 可被匿名篡改矛盾兑现标记。
- 建议: 四个 POST 端点加 `Depends(get_current_user)`；或对 /scan 加最小间隔限流（进程内时间戳即可）。
- 置信度: 高

### 摘要 backend/app/routers/contradictions.py
职责：矛盾扫描引擎的查询与触发——列表/摘要/报告读取、手动扫描与报告生成、兑现标记、次日行情验证闭环｜输入：date/level/severity/ctype/note/days｜输出：矛盾列表（total+data）、severity 三档 breakdown、报告 markdown、验证准确率统计｜关键阈值：severity severe/obvious/minor；severe_cards 取前 3；validation days 1-180｜依赖：app.contradictions.{scanner, report, store, validator}（store 私有 _today 也被引用）｜已知坑：POST 端点无鉴权（见 P2）；scan_all 内部逐扫描器 try/except fail-open；summary 有 `store._today()` 兜底而 list_contradictions 无（库空时 date=None）。

---

## backend/app/routers/daily_report.py（70 行）

（未发现问题）

### 摘要 backend/app/routers/daily_report.py
职责：大盘日报与周复盘的只读读取（列表元信息 / 单日 markdown+conclusion / 周复盘聚合）｜输入：date/days/end/limit｜输出：daily_reports 行 + 读侧派生 conclusion 字段、周复盘 JSON｜关键阈值：limit 1-120；days 1-90；conclusion 取"## 今日主线"首段、超 120 字置空（宁缺勿猜）｜依赖：db.daily_reports（scheduler 16:20 落库）、app.insight.section_first_paragraph、app.weekly_review（只读聚合零外呼）｜已知坑：conclusion 是确定性抽取非模型猜测；markdown 原样透传不影响企微推送；date 未校验格式但参数化查询 + 空结果返回 {}，无注入与 500 风险。

---

## backend/app/routers/macro.py（86 行）

### [P2] backend/app/routers/macro.py:67 GET 端点的 refresh 参数允许匿名绕过 6h 缓存反复全量重算
- 原文: `return scan(limit=limit, refresh=refresh)`
- 问题: `/earnings-pressure` 是无鉴权 GET，`refresh=true` 直接穿透 `earnings_pressure.scan` 的 6h 进程内缓存（earnings_pressure.py:179 `if not refresh and ...`），每次都全量聚合 stock_finance 报告期数据。
- 影响: 0.1 CPU 实例上匿名调用方可用循环请求持续绕过缓存制造全表聚合负载，缓存形同虚设；且"读接口带强制刷新副作用"不符合 GET 语义。
- 建议: 把 refresh 改为 POST 子端点并加鉴权（如 `/earnings-pressure/refresh`），GET 只读缓存。
- 置信度: 高

### 摘要 backend/app/routers/macro.py
职责：宏观面板快照（LLM-ready 自包含 JSON）/ 每日归档读取 / 规则配置表 / 盈利承压监测清单 / 央行储备月度面板｜输入：date/days/limit/top/months/refresh｜输出：panel+derived+rules+tags+direction 快照、监测清单（附行业分布与 note）、储备序列｜关键阈值：快照/面板 60s TTL（macro.py 内部）；earnings 6h 进程内缓存且缓存全量只截断返回；days 1-60、top 1-100｜依赖：app.macro、app.flash.store、app.earnings_pressure、app.reserve_monthly｜已知坑：文档明确"方向分不进个股评分、earnings-pressure 非卖出信号、reserves 不进决策链"（观察器红线合规）；refresh 参数可被匿名滥用（见 P2）。

---

## backend/app/routers/paper.py（144 行）

### [P2] backend/app/routers/paper.py:126 whitelist_refresh 的 dry_run 参数被静默吞掉，接口契约与实现不符
- 原文: `def whitelist_refresh(dry_run: bool = True):`
- 问题: 端点接收 `dry_run` 查询参数但从不使用；实际调用的 `auto_refresh_whitelist(min_trades, win_threshold)` 根本没有 dry_run 参数，且无论传什么都只返回建议清单（函数自述"默认不自动写…返回建议清单"）。
- 影响: 调用方传 `dry_run=false` 以为会真正刷新白名单，实际永远只拿到建议——契约误导；反之若未来有人按 docstring 补"非 dry_run 真写"，也会因参数从未接线而错位。
- 建议: 删除该参数并在 docstring 写明"本端点只出建议，写入需人工确认"；或把 dry_run 显式传下去并在数据层实现真写分支。
- 置信度: 高

### [P2] backend/app/routers/paper.py:143 模拟盘全部端点无鉴权，平仓/取消/解除熔断可被匿名操作
- 原文: `return paper_trading.unfreeze()`
- 问题: 与 coach 路由的 JWT 保护形成鲜明反差，/api/paper 下 11 个端点（含 POST close、DELETE positions、POST risk/unfuse）无任何鉴权；`risk/unfreeze` 可匿名解除回撤熔断，绕过为保护样本记录设计的 G1-G4 gate。
- 影响: 服务公开部署（Render）时任何人可手动平仓/取消仓位/解除熔断，污染"前瞻真实记录"这一项目最看重的统计轨道；真实下单边界本身无风险（全文件仅写 paper_* 表，无券商通道）。
- 建议: 至少对写操作端点（close/cancel/manual/unfreeze）加 `Depends(get_current_user)`；模拟盘若有唯一用户，可用环境变量口令兜底。
- 置信度: 高

### 摘要 backend/app/routers/paper.py
职责：模拟盘管理——持仓查看 / 手动转仓（pending）/ 手动平仓 / 取消 / 分战法统计 / 归因 / 账户 / 白名单建议 / 风控状态与解熔断｜输入：strategy_name+code+signal_date、pid、status｜输出：持仓列表（战法名转中文）、模拟绩效、风控事件（只读写 paper_positions/paper_account/paper_risk_events，与真实下单完全隔离）｜关键阈值：平仓价必须 >0（_latest_bar 缺数据回 (0.0,"") 由该判断兜底）；仅 pending 可取消；价格与价格日期成对取（2026-10-18 修复 exit_date 与价格错日）｜依赖：app.strategies.paper_trading、app.flash.rules、db、STRATEGY_ZH｜已知坑：全部端点无鉴权（见 P2）；whitelist_refresh 参数失效（见 P2）；manual_ingest 依赖 strategy_results.results_json 中存在该 code。

---

## backend/app/routers/pattern.py（75 行）

### [P1] backend/app/routers/pattern.py:28 匿名 GET 传历史日期可触发逐只全量读的现场补扫
- 原文: `result = pl.scan_latest(date)`
- 问题: `/scan` 无鉴权，且当请求日期无落库快照时现场兜底 `scan_latest(date)`；对任意**非最新**历史日期，pattern_launch 内部 `_tail=None → _bulk=None → _codes=all_codes()`，逐只 `load_bars(code)` 全历史读（pattern_launch.py:344-347 注释自认"只有补扫历史日才逐只全量读"），且无锁、无频控、可重复触发。
- 影响: 匿名用户对同一历史日期循环 GET（缓存始终为空，因为结果不落库），每次都是数百到上千次 DB 往返，0.1 CPU 实例上一次即可拖垮；等于把管理员的"罕见补扫"暴露成公开的放大器。
- 建议: 兜底扫描仅允许"最新交易日"（历史日直接返回"暂无数据，等待日批补扫"）；或加鉴权 + 单飞锁 + 结果落库（persist_scan）避免重复计算。
- 置信度: 高

### [P3] backend/app/routers/pattern.py:57 PATCH/DELETE 观察项丢弃内部返回值，永远返回 ok=True
- 原文: `pl.watch_update(code, status=payload.get("status"), note=payload.get("note"))`
- 问题: `pl.watch_update` 的返回值被丢弃、`watch_remove` 同样（63 行后直接 `return {"ok": True}`），code 不存在或 status 非法时前端仍收到成功。
- 影响: 前端无法察觉拼错的 code 或无效更新，观察清单静默不变，排障困难。
- 建议: 透传 pl.watch_update/watch_remove 的结果或让其抛 HTTPException（404），与 watch_add 的错误处理对齐。
- 置信度: 中

### 摘要 backend/app/routers/pattern.py
职责："缩量调整→放量启动"形态的扫描快照读取 / 当日命中 code 高亮 / 观察清单 CRUD / 参数配置读写｜输入：date、code、{note,status}、config dict｜输出：落库扫描快照（items 已按 score 降序截断）、code 集合、观察清单（含 T+N 变化）｜关键阈值：max_results/launch_pct/vol_shrink 等由 config 控制并以 params_hash 区分口径｜依赖：app.pattern_launch（scan/get_scan/watch_*/config）｜已知坑：无落库时 GET 现场兜底扫描是放大器（见 P1）；PATCH/DELETE 永远 ok=True（见 P3）；本路由只输出观察/形态结果，无买卖指令式文案（红线合规）。

---

## backend/app/routers/performance.py（240 行）

### [P2] backend/app/routers/performance.py:108 排行榜轨道用 PG 专有 `ANY(%s)`，SQLite 下静默降级且 note 误导
- 原文: `        "SELECT code, date, close FROM backtest_prices WHERE code = ANY(%s) "`（108 行，107 行为 `_px_rows = db.fetch(`）
- 问题: `ANY(array)` 是 PG 语法（SQLite 不支持），异常被 `except: _px_rows = []` 吞掉后 rets 恒空 → 返回 `available=False` 且 note 写"前瞻窗口未满，等快照积累"——把查询失败伪装成数据未成熟。
- 影响: 双库部署（项目明确要求）下排行榜轨道永远不可用且无人知情；违背 capital.py 同类问题里已确立的"db 层不转方言"约定；时点诚实受损（错误被标注成"等待"）。
- 建议: 用 `WHERE code IN %s`（psycopg2 对 tuple 展开）或按 db 层双库兼容写法改写，并让 except 分支在 note 中区分"查询失败"与"窗口未满"。
- 置信度: 高

### [P3] backend/app/routers/performance.py:188 无亏损交易时盈亏比记魔法数 999.0
- 原文: `"pf": round(gw / gl, 2) if gl > 0 else 999.0,`
- 问题: gl=0（零亏损交易）时 PF 填 999.0 而非 null，违反本项目"缺失≠0/不造假"的时点诚实口径——999 会被前端当真实数值渲染成"盈亏比 999 倍"。
- 影响: 样本极小的模拟盘/回放轨（本项目反复强调统计无意义）更容易出现零亏损样本，999 直接进前端图表放大误导。
- 建议: gl=0 时返回 None 并让 note 说明"样本内无亏损交易，PF 无定义"。
- 置信度: 高

### 摘要 backend/app/routers/performance.py
职责：系统级绩效三轨对照（"有没有 edge"的诚实记录）——模拟盘（前瞻）/排行榜 Top10（半前瞻·重叠窗口）/战法回放（in-sample）｜输入：JWT 用户（无业务参数）｜输出：三轨指标 + 双基准（hs300/zz1000 并列）+ 每轨统计属性 note｜关键阈值：进程内缓存 3600s；模拟盘 <30 笔平仓标注"统计上无意义"；排行榜每日样本 ≥5 才计入；排行榜前瞻窗口 fwd=5 日｜依赖：app.benchmarks.dual_bench（唯一事实源）、app.backtest.strategies（_load_prices_map/_warfare_signal_stream/退出策略）、app.mainforce.gate、get_current_user｜已知坑：zz1000 缺失为 None 不填 0（时点诚实样板）；replay 轨显式标注 in-sample 不可外推；闸门失败 fail-open 退回全信号；ANY(%s) 兼容性与 pf=999（见 P2/P3）。

---

## backend/app/routers/sector.py（260 行）

### [P1] backend/app/routers/sector.py:112 匿名 POST 可触发全市场映射重建的外部抓取，东财封 IP 会瘫痪板块快照体系
- 原文: `return build_map_full(verbose=verbose)`（112 行，111 行为 `from app.sector_industry import build_map_full`）
- 问题: `/industry-map/build-full`（约 60 次东财请求、覆盖 ~5900 只）与同族 `/industry-map/build`（60-90 次新浪请求/30 秒）、`/backfill`（东财单股查询）均为无鉴权 POST，可被匿名反复触发；而本文件 213-214 行注释自述东财"封 IP 24~48h"且快照"错过当天即永久缺失"。
- 影响: 外部攻击者或爬虫循环 POST 即可把部署出口 IP 打进东财黑名单 → `/snapshot/take` 的东财路径降级跳过 → sector_daily 快照断档（正是注释记载的"停在 2026-09-23"事故的复燃路径）；同时 30-60 秒的同步外部抓取占用 0.1 CPU 实例的线程池。
- 建议: 三个 build/backfill 端点加 `Depends(get_current_user)`（或要求管理员 token）；重建本就该由调度器月度执行，公开触发面没有存在的必要。
- 置信度: 高

### [P2] backend/app/routers/sector.py:139 GET /mainline/date 带计算+落库副作用，可被预取/爬虫误触发
- 原文: `return compute_mainline(date)`
- 问题: "分析并落库指定交易日"用 GET 方法暴露且无鉴权——浏览器预取、爬虫、监控探活都可能无意触发写库计算；同文件的 push 已正确用 POST。
- 影响: GET 副作用违反 HTTP 语义；匿名可反复重跑指定日的行业共振计算与落库（幂等能保数据一致，但白耗 CPU）。
- 建议: 改为 POST 并加鉴权，GET 侧仅保留 /mainline/summary、/mainline/performance 等只读查询。
- 置信度: 高

### 摘要 backend/app/routers/sector.py
职责：板块数据聚合入口——东财行业/概念板块与资金流（TTL 缓存）、腾讯源成交额占比、个股→行业映射表管理（stats/build/build-full/backfill）、行业主线/共振分析、板块快照与分化度、zzshare 独立序列｜输入：kind/limit/date/code/days/top/months/verbose｜输出：统一 {data, total} 包装与各类面板 JSON｜关键阈值：limit 1-500（列表）/1-1000（快照）；主线榜出现率≥一半且日均≥1.5 只；days 3-120；映射重建月度调度｜依赖：app.eastmoney、app.sector_industry、app.sector_zz、app.mainline｜已知坑：东财 BK 与 zzshare 两套 taxonomy 不兼容、独立表勿混用；快照错过当天永久缺失（zzshare 系为可回补替代）；push 受业务推送开关限制；build 类端点无鉴权（见 P1）、GET /mainline/date 有写副作用（见 P2）。

---

## backend/app/routers/__init__.py（5 行）

（未发现问题）

### 摘要 backend/app/routers/__init__.py
职责：routers 包声明（空文件）｜输入：无｜输出：无｜关键阈值：无｜依赖：无｜已知坑：无。

---

## 未发现问题

- backend/app/routers/__init__.py
- backend/app/routers/daily_report.py

红线专项核查结论（未发现违规的部分）：
- 观察器/解读类红线：coach.py（仅执行回写与度量）、macro.py（direction 分明确"不进个股评分"、earnings-pressure/reserves 显式声明非交易信号）、pattern.py、sector.py 分化度/主线等路由均无买卖、加减仓指令式文案。
- paper.py 与真实下单边界：全文件只写 paper_positions/paper_account/paper_risk_events，无任何券商/下单通道，边界干净；问题是鉴权而非越界。
- 60s 轮询承受力：capital/sector 的东财实时端点与 macro snapshot 均有数据层 TTL 缓存；performance 有 JWT + 1h 缓存；daily_report/contradictions 读库轻量。
- 时点诚实样板：performance.py 双基准"缺失≠0"（zz1000 回填前 None）、daily_report conclusion"宁缺勿猜"、capital 龙虎榜席位覆盖率如实标注——除 P3 两条（pf=999、northbound docstring）外无造假口径。
