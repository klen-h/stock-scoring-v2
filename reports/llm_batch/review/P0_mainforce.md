# P0 代码审查：backend/app/mainforce（12 文件）

审查范围：12 文件 / 约 3260 行（wc -l 实测 3261；任务书估 2860）/ 2026-10-24

说明：所有"原文"均从文件逐字复制并核对行号；只读审查，未改动任何代码、未运行任何项目脚本（仅用 grep/sed/wc 做只读检索与行数统计）。交叉验证过的库外事实（mult 无消费方、env_check 期望值、database.py 占位符替换）均在相应条目标注来源。

---

## backend/app/mainforce/gate.py

### [P1] backend/app/mainforce/gate.py:237 战法闸门对无数据/无筹码信号 fail-open 放行
- 原文: `        return {**empty, "reason": "no bars"}          # 无数据不拦截（保守放行）`
- 问题: `empty` 携带 `ok=True`，取不到 K 线、或取到 K 线但算不出 chip/phase（L241 `return {**empty, "reason": "no chip/phase"}`）时信号照常放行，与项目红线"推送/入池闸门异常时应 fail-closed"相反（注释自称"保守放行"，但保守方向搞反了：放行的是未经验证的信号）。
- 影响: 高位/拉升段股票在数据缺失日绕过已验证的过滤（闸门是当前唯一被验证有效的提纯手段，胜率 48.1%→50.3%、均收益 +0.07→+0.44%），且静默无告警计数，稀释口径无人可知。
- 建议: 至少把 "no chip/phase"（有 bars 但算不出）改为 `ok=False`；"no bars" 若确需放行，应加显式降级计数/告警并在 reason 之外单独打标，避免与正常放行混同。
- 置信度: 高

### [P2] backend/app/mainforce/gate.py:201 _float_map 把空浮筹表连同时间戳缓存 6 小时
- 原文: `            _fs_cache["ts"] = time.time()`
- 问题: `get_float_shares_from_snapshot()` 在快照未就绪/读失败时按设计返回 `{}` 且"空结果不缓存"（flow.py L328-330 明示），但本包装把返回值无条件写入 `_fs_cache` 并刷新 ts（L200-201），空结果同样被缓存 6 小时。
- 影响: 一次冷启/快照故障后，6 小时内所有筹码退化为相对量代理换手，price_pos/winner_ratio 系统性漂移，可能跨过 0.75 阈值改变拦截结论，且与 flow.py 的空结果语义直接矛盾。
- 建议: 结果为空时不刷新 `ts`（保持下次调用重试），与 `get_float_shares_from_snapshot` 的空结果处理对齐。
- 置信度: 高

### 摘要 backend/app/mainforce/gate.py
职责：战法信号主力过滤闸门（price_pos>0.75 或 phase=markup → 拦截推送/入池）+ 批量还原信号日当日闸门状态（无前视）｜输入：评分K线缓存/backtest_prices（经 research_cache 本机缓存优先）、mainflow_history（经 research_cache.flow_map）、market_snapshot 浮筹｜输出：{ok, reason, phase, price_pos, winner_ratio, flow5}（单只 strategy_gate + 批量 gate_states_for_signals，键 (code,date)）｜关键阈值：HIGH_POS_THRESHOLD=0.75、bars≥130、chip warmup 界 bars[69]、闸门 30min 进程缓存、浮筹 6h 缓存、_retry_read 4 次｜依赖：chips/phases/flow、research_cache、app.backtest.strategies._load_prices_map、pack_source｜已知坑：无数据/无筹码 fail-open（P1）；空浮筹缓存 6h（P2）；DB 回源分支（L164-165 `_load_prices_map`）不做 `_norm_date` 日期类型归一，与缓存分支不对称，日期类型错位时 `chip_series(dates_out=...)` 静默命中不到 → 状态缺失 → 消费方按 ok=True 放行（_norm_date docstring 自述的风险在回源分支仍存在）；bars<130 的代码被直接 continue，无状态记录。

---

## backend/app/mainforce/trade_gate.py

### [P1] backend/app/mainforce/trade_gate.py:138 筹码数据缺失时条件 B 默认通过（fail-open）
- 原文: `    crowded = (pos is not None and pos > HIGH_POS_THRESHOLD) or \`
- 问题: `pos`/`winner` 任一为 None 即不参与拥挤判定；两者皆缺（mf 为空 dict、chip_json 解析失败为 `{}`）时 crowded=False、cond_b=True——L150 的文案自己都写着「数据缺」，布尔上却算通过。
- 影响: ready 计数虚高：gate_history.snapshot 只落 ready≥2 的候选（其文件头明示"候选只落 ready≥2"），无筹码数据的个股可白拿 B 条件进入观察池/用户操作动线，与"缺数据不得放行"的红线相悖。
- 建议: pos 与 winner 同时为 None 时 cond_b 置 False（或单列"数据缺"状态不计入 ready），缺数据不加分。
- 置信度: 高

### [P2] backend/app/mainforce/trade_gate.py:158 市场状态未知时仍给 30% 基准仓位（fail-open）
- 原文: `    pct = REGIME_POSITION.get(regime, 30 if regime else 30)`
- 问题: `_regime()` 异常/缓存未恢复时返回 `""`，落入默认 30%（`30 if regime else 30` 两分支相同，是死代码）；cond_c=False 仅打对折 → 最终输出「轻仓试探 10%」且 allow=True。defensive 明确 0% 禁买，"判不出状态"反而给仓位，方向开反。
- 影响: regime 判不出（新进程恢复失败/异常）时门禁偏开，观察池与建议仓位在信息最不全的时刻给出可执行数字。
- 建议: regime 为空/未知时基准取 0（或 cond_c=False 时直接 pct=0），与 defensive 同待遇（fail-closed）。
- 置信度: 高

### 摘要 backend/app/mainforce/trade_gate.py
职责：买入三条件门禁（A 主力根据/B 不追高/C 状态允许）+ 建议仓位档位 + 就绪摘要 summarize（唯一事实源）+ 前端参数下发 rules()｜输入：mainforce_state.load_latest、market_regime 缓存（异常回 restore）、contradictions 当日 L1/L2 计数（30s 缓存）｜输出：conditions/position_pct/position_label/reasons/allow；summarize 的 ready(0~3)/label/hint；rules() 阈值镜像｜关键阈值：HIGH_POS_THRESHOLD=0.75（复用 gate）、WINNER_RATIO_CROWDED=0.7、REGIME_POSITION={offensive:80, neutral:50, neutral_bearish:20, defensive:0}、未知 regime 基准 30、拥挤/无根据各 ×0.5、吸筹三条件满足时至少 50%、POSITION_STEPS=(0,5,10,20,50,80)｜依赖：gate.HIGH_POS_THRESHOLD、app.backtest.market_regime、app.contradictions.store｜已知坑：B 条件缺数据白送（P1）、未知 regime 给 30% 基准（P2）；文件头"不降反升一档（80% 封顶）"与代码"max(pct,50)"表述有出入；summarize 的 label 优先级在"缺A 且追高"同时成立时只报"缺主力根据"。

---

## backend/app/mainforce/chips.py

### [P2] backend/app/mainforce/chips.py:96 无流通股本兜底路径 MA120 在序列两端被零填充低估
- 原文: `        ma_vol = np.convolve(vols, np.ones(120) / 120.0, mode="same")`
- 问题: `np.convolve(..., mode="same")` 对边界零填充：最近约 60 根的 MA120 被低估约一半（历史 <120 根时几乎全部样本点受影响）→ 兜底换手率 `vol/base*0.02` 高估约一倍（再被 TURNOVER_CAP=0.40 截断），且恰好吃到 `_metrics_at` 读取的末根；warmup=60 只挡住输出端，污染会随后续衰减残留。
- 影响: float_shares 缺失时（快照未就绪、snapshot 无该票）winner_ratio/price_pos 系统性偏差，而这两个值正被 0.75/0.7 阈值（gate/trade_gate/overlay）直接消费。
- 建议: 对窗口覆盖不足的索引按实际窗口长度归一（或改用滑动窗口均值实现），保证 base 是真实均量。
- 置信度: 高（零填充机制确定；具体漂移幅度随个股数据而异）

### 摘要 backend/app/mainforce/chips.py
职责：CYQ 筹码分布滚动引擎（获利盘/平均成本/90% 成本分位/集中度/价格位置），纯函数+numpy，评分引擎/回测/脚本共用｜输入：日线 bars（有效行 ≥30 根，升序）+ 可选 float_shares（股）｜输出：{date: {winner_ratio, cost_bias, avg_cost, concentration, price_pos, p5, p95}}；chip_metrics 取末根单点｜关键阈值：N_BINS=150、BASE_TURNOVER=0.02、TURNOVER_CAP=0.40、TURNOVER_FLOOR=0.0005、warmup=60、换手=vol×100/float_shares（2026-09-16 单位修复，volume 按"手"）｜依赖：仅 numpy｜已知坑：volume 单位"手"×100 为硬编码假设，源数据单位变化即静默失真；无 float_shares 时 MA120 零填充边界低估（P2）；价格网格覆盖全历史 min/max，除权跳空拉大网格降精度（文件头自认可接受）；除零均有防护（total>0、avg_cost>0、base>0、span≤0 回退 0.5），空数据/单日数据（<30 根）返回空——数值稳定性本身合格。

---

## backend/app/mainforce/flow.py

### [P2] backend/app/mainforce/flow.py:711 mainflow_history 无 code 索引，load_flow 逐股查询全表扫
- 原文: `                    "close, pct_chg FROM mainflow_history WHERE code = %s "`
- 问题: ensure_table 只建了 `idx_mainflow_date`（L150-152），`WHERE code = %s` 只能顺序扫描 8 万+ 行；lhb.py 已为同类"详情页逐股查"场景补 `idx_lhb_code` 并自述"否则每次详情页都要全表扫（含大字段所在的页）"，本表漏了同款处理。
- 影响: strategy_gate 逐信号调 load_flow（gate.py L245）、详情页/实时叠加高频触发时，每次都是全表扫，DB 压力与延迟放大。
- 建议: ensure_table 增加 `CREATE INDEX IF NOT EXISTS idx_mainflow_code ON mainflow_history (code)`（PG/SQLite 双分支同加）。
- 置信度: 高

### [P2] backend/app/mainforce/flow.py:169 东财兜底无熔断，新浪大面积失败时全池转打东财
- 原文: `    return fetch_flow_history_em(code, days)`
- 问题: 新浪返回空（风控/接口变更）时逐股无差别降级东财；backfill_all 对 2000+ 只按 0.25s 间隔连打东财，足以重现文件头自述的"2026-09-05 实测连续 ~600 次请求后被封"，且没有连续失败计数/熔断。
- 影响: 封 IP 后东财兜底彻底失效，回填 fail 数激增但无来源区分，第二天日批继续白打直到再次被封。
- 建议: 加进程级熔断：东财连续 N 次（如 10 次）空返回即停用兜底、本轮只记 fail，下轮再试。
- 置信度: 高

### [P3] backend/app/mainforce/flow.py:55 _REQ_GAP 从未使用，文件头"东财 0.12s"与实际不符
- 原文: `_REQ_GAP = 0.12   # 逐股间隔（东财高频断连保护）`
- 问题: 全文件无任何引用（grep 仅定义处）；东财请求节奏实际由 backfill_all 的 sina gap（0.25s）间接决定，fetch_flow_history_em 自身只在重试间 sleep(1.0)。
- 影响: 文档与实现漂移，后来者会误信存在 0.12s 东财限速。
- 建议: 删除该常量，或在 fetch_flow_history_em 调用链中真正落实该间隔。
- 置信度: 高

### [P3] backend/app/mainforce/flow.py:685 SQLite + codes 组合产出带悬挂占位符的 SQL
- 原文: `        rows = db.fetch(sql, tuple(params)) if (params and not codes) else db.fetch(sql)`
- 问题: `codes` 非空时丢弃 params，但 SQL 已拼入 `code = ANY(%s)`（database.py L187 会把 `%s` 替换为 `?`），SQLite 下无论传不传参都必抛错（ANY 非法/绑定数不符）；注释只声明"只在 PG 下走 codes 分支"，但代码留下的是必崩组合而非显式拒绝。
- 影响: 当前仓库内没有 codes+SQLite 的调用方（research_cache.py:279 与 state.refresh_all 均不传 codes），属埋雷；未来任何本地脚本传 codes 即异常。
- 建议: SQLite 分支把 `code = ANY(%s)` 改写为 `code IN (?,?,...)`，或该组合下显式 raise 带说明的错误。
- 置信度: 高（行为确定；当前无触发路径）

### 摘要 backend/app/mainforce/flow.py
职责：主力资金流数据层——新浪主源+东财兜底回填落库（mainflow_history，UNIQUE(code,date)）、流通股本（market_snapshot 反推+东财 clist 兜底）、load_flow/load_flow_map 读取与"进程内存→本机 SQLite→回源"三层缓存、consec 连续净流入分档唯一映射源｜输入：新浪 MoneyFlow.ssl_qsfx_lscjfb、东财 fflow/daykline 与 clist、market_snapshot（float_cap/price）、app.sync_meta 版本号｜输出：mainflow_history 行、{code:[{date,main_net,...}]}、{code: 流通股本}、consec_tier/consec_label｜关键阈值：_SINA_GAP=0.25s/股、_REQ_GAP=0.12（死常量）、_FLOW_FAST_TTL=300s、_FLOW_LONG_TTL=6h、磁盘缓存 TTL 12h、_FLOW_MAP_CACHE_MAX=2 条/12 万行（OOM 治理）、flow_consec 分档 weak 2/mid 5/strong 10、窗口 _FLOW_WIN_TOL=1.7+pad 10 天、陈旧代码自动退回整表读｜依赖：requests、sqlite3、app.database、app.sync_meta、app.flash.store｜已知坑：无 code 索引（P2）；EM 兜底无熔断（P2）；SQLite+codes 必崩（P3）；北交所 4/8 开头代码在 `_sina_code`/`to_secid` 中被误映射为深市（`"sh" if c[0]=="6" else "sz"`），这类票资金流静默为空（池内大概率无北交所，未单列）；backfill_all 写 `name=""` 会在 PG upsert 时把已有 name 覆盖为空；窗口化读上限语义（flow_consec 封顶）已有注释与告警，治理到位。

---

## backend/app/mainforce/state.py

### [P2] backend/app/mainforce/state.py:280 bars 全空时失败率防护被绕过，空跑仍按成功返回
- 原文: `    if total and failed > total * 0.2:`
- 问题: `_load_bars_all` 两路读库异常均被吞（L176/L198 仅打印），bars_map 为空 → total=0 → 短路不触发抛错，refresh_all 返回 codes=0 的"成功"结果；而本函数自己的设计初衷（docstring）正是"连接/数据源级故障不能大面积缺失却标记成功"。
- 影响: DB 级故障当天 mainforce_state 为空表且调度器无重试依据，观察池/详情页/评分标注静默缺数据，恰是该防护要防的场景。
- 建议: 未显式传 codes 且 total==0 时按同一语义抛 RuntimeError（本轮不标记完成，下轮续传）。
- 置信度: 高（逻辑漏洞确定；触发需读库级故障，项目历史证明此类故障真实发生过）

### 摘要 backend/app/mainforce/state.py
职责：日批全池计算主力状态（overlay → mainforce_state 落库，UNIQUE(code,date) 幂等）+ load_latest 读缓存 + 断点续传/逐只容错｜输入：backtest_prices（260 交易日窗口，分块 ANY 查询）+ kline_cache（kline_count≥250 补覆盖）/ pack 数据包、flow_map(days=20 窗口)、market_snapshot 浮筹｜输出：mainforce_state 行（code,date,phase,signal,mult,chip_json,flow5_amt,flow5_amt_yuan,flow_consec）；load_latest 返回 {code: state_dict}（含 phase_cn/flow_consec_tier/cn 补齐）｜关键阈值：BARS_WINDOW=260、FLOW_WINDOW_DAYS=20、_LATEST_TTL_SEC=1800、失败率>20% 抛错不标记完成｜依赖：flow（浮筹/load_flow_map/consec）、overlay.mainforce_overlay、PHASE_CN、pack_source、kline_cache｜已知坑：bars 全空绕过失败率防护（P2）；`code = ANY(%s)` 与 `DISTINCT ON` 均 PG 专用，SQLite 本地测试直接异常、靠调用方 try/except 吞掉（与 flow.py 注释承认的同一惯例）；断点续传以"第一个代码的末根日期"为基准，停牌股末根日期不齐时会被重算（幂等 upsert，仅浪费）。

---

## backend/app/mainforce/lhb.py

### [P2] backend/app/mainforce/lhb.py:130 fetch_detail_seats 的 detail 分支是死变量，解析只认顶层 traders
- 原文: `        d = (resp or {}).get("detail") if isinstance(resp, dict) else None`
- 问题: `d` 赋值后从未使用；函数也未复用本模块已有的 `_unwrap` 解包，若 zzshare 把席位挂在 `detail` 子对象或 `{data: {...}}` 包装下，`traders` 恒为 None → 函数返回 None → seats_json 永远不落库，且无任何日志。
- 影响: 席位明细可能长期静默为空（save_rows 对 None 直接写 NULL），文件头规划的"游资跟踪/机构席位交叉验证"无从积累样本。
- 建议: 删除死变量；或对 `resp`/`d` 两处兜底取 traders，并加一条"解析到 0 席位"的可观测日志。
- 置信度: 中（死代码与"只认顶层 traders"是事实；实际响应结构无法在本仓库验证）

### [P2] backend/app/mainforce/lhb.py:174 SQLite 分支 INSERT OR REPLACE 会用 NULL 覆盖已有 seats_json/circ_mktcap
- 原文: `                    INSERT OR REPLACE INTO lhb_history (code, name, date, net_buy,`
- 问题: PG 分支对两列有 `COALESCE(EXCLUDED.x, lhb_history.x)` 保护（L166-167），SQLite 分支是整行替换——重跑 backfill_days 时，当天未富化席位的行（非池内/超 detail_recent 窗口）会把历史已积累的 seats_json/circ_mktcap 抹成 NULL。
- 影响: 本地/测试库重复回填会静默丢失席位明细与上榜日流通市值（数据丢失不可逆）。
- 建议: SQLite 分支改为"存在即跳过 seats_json/circ_mktcap 的更新"（先 SELECT 判存在，或拆两条 UPDATE）。
- 置信度: 高

### 摘要 backend/app/mainforce/lhb.py
职责：龙虎榜数据层（榜单/席位明细落库、净买强度四档、空头侧风险标签 recent_summary）｜输入：zzshare lhb_list/lhb_detail、旧行回退用 scoring.kline_cache 收盘价 + flow.get_float_shares_from_snapshot 股本｜输出：lhb_history 行（UNIQUE(code,date)，含 seats_json/circ_mktcap）、recent_summary{n/net/buckets/last_bucket/risk/risk_cn/risk_why}｜关键阈值：LHB_BIG=1.0%（占流通市值，与 scripts/lhb_edge_check.py v2 定案逐字同源）、四档 big_buy/small_buy/small_sell/big_sell、detail 富化仅池内且近 10 天、gap=0.6s、风险标签仅"最近一次上榜=big_sell"｜依赖：app.zzshare_client.get_api、app.database、app.scoring.kline_cache、app.mainforce.flow｜已知坑：fetch_detail_seats 死变量/顶层 traders 假设（P2）；SQLite 分支 INSERT OR REPLACE 抹历史（P2）；席位结构只有 youzi 标识、无机构席位区分字段——与文件头"机构席位净买入持续性"用途不匹配，机构席只能靠 trader_name 含"机构"推断（seats_json 消费方需自行字符串匹配）；backfill_days 的 `range(days, -1, -1)` 实跑 days+1 天（无害偏差）。读取侧健壮性好：load_lhb/recent_summary 的解析均有 try/except 兜底，强度分档缺输入返回 None 不猜。

---

## backend/app/mainforce/overlay.py

### [P2] backend/app/mainforce/overlay.py:96 截断 260 根改变筹码网格，与 gate/回测的全历史口径不一致
- 原文: `        bars = bars[-260:]`
- 问题: chip 价格网格由传入 bars 的 min/max 决定（chips.py L84-91），gate.py 两次注释明示"缩短历史会改变网格 → price_pos 跨过 0.75 阈值（实测 n 136→149）"并因此拒绝截断（gate.py L47-48、L331-333）；本函数却无条件截 260 根——state 日批（kline_cache/pack 路径喂 500+ 根）与详情页现算走 260 网格，战法闸门走全历史网格，同一个 0.75/0.7 阈值吃两套口径。
- 影响: 同日同股两条路径的 price_pos/winner_ratio 不同，trade_gate 的 B 条件（消费 state 的 chip）与战法闸门可能给出相反结论；与"阈值经全池截面回测验证"的口径声明存在偏差。
- 建议: 统一口径——要么去掉本处截断（CPU 护栏改由调用方限并发/限深），要么 gate 与 overlay 固定同一窗口长度，并把偏差登记进两个文件的阈值注释。
- 置信度: 高（"截断改变网格"机制是仓库内注释里的实测结论；两路径实际漂移幅度未复测）

### 摘要 backend/app/mainforce/overlay.py
职责：主力行为叠加器——筹码×阶段×资金流合成 distribution/accum 标签 + 出货乘数（2026-10-18 定案 off，仅保留字段）｜输入：bars（≥120 根）、flow_rows（可缺，缺时出货组合退化弱口径）、float_shares、regime｜输出：phase/phase_cn/chip/flow5_amt/flow5_amt_yuan/flow_consec(+tier/cn)/signal/signal_cn/reason/mult/active｜关键阈值：高位 price_pos>0.75、高获利 winner_ratio>0.7、低位密集 price_pos<0.35 且 concentration<0.25、DISTRIBUTION_MULT=0.85、GATE_REGIMES={neutral,neutral_bearish,defensive}、bars 截 260 根、consec 用 flow.consec_* 唯一映射｜依赖：chips.chip_metrics、phases.detect_phase、flow.consec_tier/consec_label（局部 import 防循环）｜已知坑：260 截断与 gate 全历史网格口径不一致（P2）；`mult=0.85` 现无任何乘法消费方（全库核实：scoring.py L1450-1476 仅拼展示字段，其注释明示"只拼标签，不乘分"，env_check 期望 off）——处于 fail-closed 定案状态，勿在无预登记情况下接回排序；flow_rows 传空列表时 flow5_amt=None 走弱口径（设计内，非填 0）。

---

## backend/app/mainforce/confluence.py

### [P2] backend/app/mainforce/confluence.py:183 数据缺失层以 0 分参与共振合成（"未知"与"中性"混同）
- 原文: `        layers["macro"] = 0`
- 问题: 宏观/市场/板块任一层缺数据时按 0 计入 total（市场层 L189 `market_map.get(regime, 0)`、板块层 flow=None 得 0 同理），reasons 文案虽诚实写"未知"，但 verdict 阈值合成把"没数据"当"中性票"——例：宏观未知(0)+市场 offensive(+2)+板块主线(+1)+个股吸筹(+2)=+5 仍判「顺流共振（四层同向）」，实际只有 2 层有数据。
- 影响: "顺流共振"作为推送最高依据在数据不全时被放大，语义名不副实；与红线"数据缺失不得填 0"的精神相悖（分数层填 0、文案层诚实，两者不一致）。
- 建议: 缺失层记 None 并在合成时剔除：verdict 升降档要求有效层数 ≥3，aligned 统计已排除 0 值层（L246-247）可复用同一口径。
- 置信度: 中（混同是代码事实；是否判为红线违规属口径取舍，故降级为 P2/中）

### 摘要 backend/app/mainforce/confluence.py
职责：环境传导链——宏观→市场→板块→个股四层方向分合成"环境一致度"（verdict），供推送/日报复用｜输入：macro_daily（data_json.direction.score）、market_regime 缓存、sector_daily（东财细分名归一化累加、排除"其它行业"兜底桶）、industry_mainline（主线/拥挤主线）、mainforce_state.load_latest｜输出：{layers, total, verdict, reasons, sector, in_mainline, crowded_mainline}；confluence_line 一行文案｜关键阈值：宏观 ≥+30→+2 / ≤-30→-2；顺流共振 ≥+4、偏多 ≥+2、中性 ≥-1、偏逆 ≥-3、逆流 ≤-4；拥挤主线只减不加（2026-09-13 回测驱动）｜依赖：app.database（db_fetch/db_fetch_one 延迟导入防循环）、app.backtest.market_regime、app.sector_industry._normalize_industry｜已知坑：缺数据层按 0 参与合成（P2）；_env_snapshot 四段 try/except 吞异常后，部分空快照仍整体缓存 30min；市场 regime 字符串大小写/别名完全依赖 market_regime 的枚举，无归一。

---

## backend/app/mainforce/phases.py

### [P3] backend/app/mainforce/phases.py:92 吸筹规则代码含"量比<1.2"，文件头规则清单缺该项
- 原文: `    if ret_60 < 12 and abs(ma20 / ma60 - 1) < 0.03 and obv_up and vol_ratio < 1.2:`
- 问题: 文件头 L20 的吸筹规则只列"ret_60 < +12% 且 均线粘合<3% 且 OBV 20日上行"三条件，与 `_rule` 实际四条件（多一个 vol_ratio<1.2）不一致——阈值自称"集中在此便于回测回调"，文档却漏了一项。
- 影响: 后续按文档复现/回调阈值时会漏掉量比上限，改变吸筹段判定而无人察觉。
- 建议: 头注释补"且 量比<1.2"。
- 置信度: 高

### 摘要 backend/app/mainforce/phases.py
职责：主力阶段规则引擎（accumulation/shakeout/markup/distribution/decline/sideways），指标全序列预计算+逐日规则，detect_phase 单点与 phase_series 逐日共用同一 `_rule`｜输入：日线 bars（有效 close 行 ≥70 根）｜输出：{phase, phase_cn, ret_5/ret_20/ret_60, vol_ratio, obv_slope, ma20, ma60}；i<69 或指标不足返回 None｜关键阈值：拉升 ret20>8%+量比>1.3+close>MA20>MA60；出货 ret60>25%+量比>1.2+ret5<1%；洗盘 ret20>8%+ret5<0+量比<0.85+未破MA60；吸筹 ret60<12%+粘合<3%+OBV上行+量比<1.2；下跌 close<MA60 且 MA20<MA60｜依赖：仅 numpy｜已知坑：头注释漏"量比<1.2"（P3）；_precompute 会剔除 close<=0 的行，detect_phase(bars, index) 的 index 是"过滤后序列"的下标，与原始 bars 下标错位（当前调用方都用默认末根，未触发）；v60<=0（上市不足 60 日）返回 None——fail-closed 正确。

---

## backend/app/mainforce/gate_history.py

### [P3] backend/app/mainforce/gate_history.py:100 overwrite 先 DELETE 后 INSERT 非原子，失败即丢当日快照
- 原文: `            db.execute("DELETE FROM gate_snapshot_history WHERE date = %s", (date_str,))`
- 问题: DELETE 与 INSERT 是两条独立 db.execute（各自提交），同在一个 try 但非同一事务；DELETE 成功后 INSERT 失败，该日快照永久丢失且函数返回错误文案，无自动补写。
- 影响: force 重跑（overwrite=True）中途失败造成当日历史缺口——本表的存在意义就是"晚一天少一天样本"的就绪度验证样本。
- 建议: 改单条 upsert（PG `ON CONFLICT (date) DO UPDATE`，SQLite `INSERT OR REPLACE`），与 mainflow/save_flow 的幂等写法对齐。
- 置信度: 高

### 摘要 backend/app/mainforce/gate_history.py
职责：买入闸门就绪度每日快照落库（gate_snapshot_history，date 主键幂等）+ load_range 读回（回测消费入口）｜输入：app.routers.scoring._gate_watch_live（就绪度口径唯一事实源，刻意调 _live 避免把旧快照写回）｜输出：payload{regime, counts, evaluated, strategy_date, candidates[]}（候选只落 ready≥2）｜关键阈值：counts 键 JSON 序列化后读回归一 int；写库即失效 scoring._SNAP_CACHE｜依赖：app.database、app.routers.scoring（_gate_watch_live/_SNAP_CACHE）、app.flash.rules.latest_completed_trading_day｜已知坑：overwrite 非原子（P3）；就绪度口径继承 trade_gate——B 条件缺数据白送的问题会随快照固化进历史样本（P1 的下游影响）；计算失败/查询失败均返回文案不抛错（日批日志可见，属 fail-closed 不写库，方向正确）。

---

## backend/app/mainforce/l3_history.py

### [P3] backend/app/mainforce/l3_history.py:191 __main__ 直跑路径少上溯一层，两种调用方式都无法工作
- 原文: `    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))`
- 问题: 文件位于 backend/app/mainforce/ 下，`..` 只到 app/，拼出 `backend/app/backend`（不存在）：直跑时 sys.path 插错 → `from app.database import db` ImportError；L192 的 `.env` 路径同错（`backend/app/backend/.env`）且 open 无 try 包裹 → FileNotFoundError 先崩；以 `python -m app.mainforce.l3_history` 运行时 import 可用，但 .env open 仍必崩。
- 影响: 手动回填入口（文件自带 `__main__`）实际不可用，回填只能依赖调度侧调用 sync_ocf_history。
- 建议: 改为上溯两层（`"..", ".."` 得 backend 根），.env 取 `backend/.env`；或干脆删掉 `__main__` 块只保留函数入口。
- 置信度: 高

### 摘要 backend/app/mainforce/l3_history.py
职责：按报告期的经营现金流/扣非历史底座（stock_finance_ocf_hist，PK(code,report_date)）+ 连续失血 streak 计算，供 L3「连续失血」扫描｜输入：zzshare mp-api finance_cash_flow/finance_indicator（按报告期全市场，N 季=2N 请求）｜输出：{code: {report_date: ocf}}（load_ocf_hist）、{code: 失血期数}（bleeding_streak_map，≥1 才收录）｜关键阈值：默认回填 4 个报告期、100 行/批、失血=相邻报告期累计 OCF<0 连续计数（不除利润）｜依赖：app.zzshare_client.get_api、app.database、pandas（iterrows）｜已知坑：__main__ 路径错误（P3）；upsert 对 adjusted_profit 做 COALESCE 保留旧值、对 ocf/pub_date 直接覆盖（源缺数时会清掉旧 ocf——items 已过滤 ocf is not None，仅同 code 换源时需注意）；load_ocf_hist/bleeding_streak_map 每次全表读（4 期×~5000 行，量级可接受）；OCF 缺失的股票不落行（不填 0，时点诚实合规）。

---

## backend/app/mainforce/__init__.py

### 摘要 backend/app/mainforce/__init__.py
职责：包级 docstring——声明主力行为三视角（chips 筹码/flow 资金流/phases 阶段）与"因子先经全池截面回测验证（IC/分桶有边缘才接入评分，带开关），不达标只留报告"的设计约束｜输入：无｜输出：无（纯文档，无代码逻辑）｜关键阈值：无｜依赖：无｜已知坑：无。

---

## 未发现问题（红线核实清单，grep 只读检索验证）

- **overlay.py 出货乘数 0.85 无消费方、无静默启用路径（fail-closed 定案成立）**：`MAINFORCE_MODE` 全库引用仅 overlay.py 自身（默认 `or "off"`，L68）与 app/env_check.py（期望值 `off`，L37-41）；`mult`/`DISTRIBUTION_MULT` 的唯一库外引用点 app/routers/scoring.py L1450-1476 仅拼 `item["mainforce"]["mult"]` 展示字段，其注释明示"这里只拼标签，不乘分（乘数由 P1-12 方案A 移除）"；未发现任何把 mult 乘进分数/排序的代码路径。`active` 恒为 False（mode=off），mult 恒 1.0。
- **涨跌停口径**：12 文件内无任何涨跌停/涨停/跌停判定逻辑，无自写阈值，也未引用或绕过 app/limit_stats（grep "limit_stats|涨停|跌停|limit_up" 于 app/mainforce/ 零命中）——本模块不涉及该红线。
- **新浪主源限速**：唯一联网循环 backfill_all 每股 `time.sleep(gap)`，gap 默认 `_SINA_GAP=0.25`，满足 ≥0.25s/股；页间 0.1s 属同一只股票的分页续读（flow.py L117）。
- **mainflow_history 全表读治理**：已有"进程缓存（条数+行数双上限）→ 本机 SQLite（版本门控）→ 回源"三层 + `days=None` 整表读显式 WARN（L638-639）+ 窗口读陈旧代码自动退回整表读（宁多读不改口径）；生产路径 state.refresh_all 传 days=FLOW_WINDOW_DAYS=20。残留问题（EM 兜底熔断、code 索引）已单列。
- **seats_json 读取侧健壮性**：load_lhb 对 seats_json 解析有 try/except → None 兜底（L253-257），recent_summary 窄列读取不含 seats_json；未发现解析崩溃路径（写入/来源侧问题已单列）。
- **时点诚实（数据缺失不填 0）**：lhb.strength_pct/strength_from_mktcap 缺任一输入返回 None（"不猜"），_event_bucket 两路都缺返回 None、不降级口径；l3_history 对 OCF 缺失不落行；overlay 对 flow 缺失输出 flow5_amt=None 并走明示的弱口径，未发现把缺失填 0 参与计算的路径（confluence 合成层的"未知=0"问题已单列 P2）。

---

## 审查方法说明

- 全部 12 文件逐行通读；行号与原文以 Read 输出为准，引用未做任何改写。
- 库外交叉验证（只读 grep/sed）：app/env_check.py（MAINFORCE_MODE 期望 off）、app/routers/scoring.py L1435-1500（mult 仅展示）、app/database.py L132-207（%s→? 替换，证实 ANY(%s) 在 SQLite 不可用）、app/research_cache.py（ohlc_for/flow_map/upsert_bars 存在）、app/routers/scoring.py（_gate_watch_live 存在）、app/flash/store.py（load_market_snapshot 存在）、app/pack_source.py（enabled/get_klines/get_name_cap/get_codes 存在）——gate/gate_history/lhb/l3 的跨模块 import 均可解析。
- 未运行任何项目脚本；未修改任何代码文件。
