审查范围：7 文件 / 约 3747 行 / 2026-10-24

# backend/app/coach/rules.py（895 行）

### [P1] backend/app/coach/rules.py:401 教练文案多处为指令式操作语（Guardrails #5）
- 原文: `f"{pos['pnl_pct']:+.1f}%。优先在反弹中减仓，不追加。"),`
- 问题: 规则求值器的 message 生成点大量使用祈使句——同类还有 :355「按纪律离场」、:374「按计划离场，不临场改判」、:429「准空仓运行、只减不加」、:587「按计划从准空仓向中性加仓」，rules.yaml 的 label 亦含「不等7%直接砍」。
- 影响: 按红线 Guardrails #5，教练类输出不得含买卖/加减仓指令式表述；目前仅 `coach_tick` 推送路径有 POSITION_NOTE 免责前缀（monitor.py:93），体检卡（monitor.py:145）与落库/回放文案均无，用户可能直接当作操作指令执行。
- 建议: 文案统一改为描述性（如「已命中减仓触发条件，规则口径供复核」），或把 POSITION_NOTE 扩展到体检卡等全部教练出口，并在 yaml 为每条规则标注"建议性质"。
- 置信度: 高

### [P2] backend/app/coach/rules.py:231 真实持仓默认止损硬编码，违反本模块"阈值一律 yaml"红线
- 原文: `REAL_STOP_LOSS_PCT = -8.0`
- 问题: 模块头与 rules.yaml 头部均声明「阈值集中 rules.yaml、代码不写死」，但真实持仓默认止损 -8% 写死在代码，与前端 `usePortfolio.ALERT_CONFIG.stopLossPct` 的一致性仅靠注释人工对齐。
- 影响: 调整阈值需改代码部署；前后端两处常量必然漂移（正是注释想避免的"前端 -8% / 后端 -7%"口径冲突）。
- 建议: 移入 rules.yaml（如 `plan.real_stop_loss_pct`），代码只保留兜底缺省值并注明来源。
- 置信度: 高

### [P2] backend/app/coach/rules.py:167 _macro 重建失败不缓存时间戳，30s 轮询下持续回源
- 原文: `return _macro_cache["val"]   # 重建失败沿用旧值（fail-open）`
- 问题: 失败分支只返回旧值、不更新 `_macro_cache["ts"]`，下次调用（≤30s 后）立即再次触发 `get_macro_panel` 外网重建；同文件 `_usd_5d_change` 已按 2026-10-19 修复模式对失败态做 10 分钟缓存，此处未同步。
- 影响: 宏观源持续不可用时，light 层「零额外网络」承诺被打破（每小时至多 ~120 次外网重建尝试）。
- 建议: 失败分支同样 `update(ts=now)`，用短 TTL（如 600s）缓存失败态，到期自动重试一次。
- 置信度: 中

### [P2] backend/app/coach/rules.py:367 hold_3d_review 严格等于第 N 日触发，错过即永不补
- 原文: `    if hd != n:`
- 问题: 为修"老持仓每天重复推送"改成 `hd != n` 单日精确触发，但无补触发机制：当日进程停机/行情缓存未就绪/`_hold_days` 异常返回 0，该持仓的强制评估就静默跳过且永不重发。
- 影响: 预承诺纪律的核心硬警报（push=true）可能一次都不发，且无任何痕迹。
- 建议: 放宽为 `hd >= n` 并沿用 dedupe_key（日|规则|标的）幂等（存量老票首日一次性补触发可接受），或在体检卡对 `hd > n` 未评估持仓常驻标注。
- 置信度: 高

### 摘要 backend/app/coach/rules.py
职责：Coach 规则求值器（W1 纯规则骨架，"事前无权"——数字全部由代码产出、LLM 只渲染）｜输入：rules.yaml（阈值）、全市场行情内存缓存（零网络）、paper_positions+user_portfolio、regime/macro/margin 缓存、backtest_prices（heavy）、limit_stats 跌停唯一口径、coach_alerts（gate_add 连续日确认）｜输出：Advice 列表（message 已注入数字 + numbers 结构化落库）与 format_batch 企微渲染｜关键阈值：集中 rules.yaml（hold 3 日/亏损 7%/ret20 30%/price_pos 0.75/涨跌比 0.8+跌停<10+连续 2 日/10Y 5%+DXY 100+情绪 30/熔断 2%/大涨 1.5%/纳指 -0.76/美元 5 日 0.66），例外 REAL_STOP_LOSS_PCT=-8.0 硬编码｜依赖：routers/market._cache、backtest.market_regime、app.macro、flash.margin_sentiment、paper_trading（_hold_days/INITIAL_CAPITAL）、portfolio_scope、limit_stats、mainforce.overlay、coach.position_sizing｜已知坑：day_pnl_pct 分母是模拟盘 INITIAL_CAPITAL 而分子含真实持仓浮盈亏（注释已自述口径边界，情绪熔断阈值据此失真）；_ev_gate_reduce 文案把 5%/100/「寒冷区」写死（params 改了文案不跟随）；heavy 规则仅 tier=all 生效；usd5 序列过期/失败均已做短 TTL 缓存（10-19 修复在位）。

# backend/app/coach/audit.py（548 行）

### [P2] backend/app/coach/audit.py:119 补列标志无条件置位，失败后本进程永不再试
- 原文: `        _columns_ready = True`
- 问题: `_ensure_columns()` 内部对"列存在性探测失败"直接 return、对单列 ALTER 失败也只打印继续，而外层无论成败都把 `_columns_ready` 置 True。
- 影响: 老库迁移期间遇到一次 DB 抖动，缺失列在本进程生命周期内不再补（写入含新列的 INSERT 将持续失败）。
- 建议: 由 `_ensure_columns` 返回是否全部成功，仅全部成功才置位（同 `_table_ready` 的 P2-⑨ 处理）。
- 置信度: 高

### [P2] backend/app/coach/audit.py:488 recent_alerts 单槽缓存被两个不同 limit 的调用方互踢
- 原文: `    if (cached is not None and _ALERT_LIST_CACHE["key"] == key`
- 问题: 缓存只有一个槽位、按 `(limit, trunc)` 全等命中，而注释自述两个前端调用方分别取 100 条与 50 条——两者交替轮询时每次都 miss，缓存近乎失效。
- 影响: 注释声称的"5 分钟进程缓存（治 20MB/32h egress）"实际命中率减半以上，egress 治理目标打折扣。
- 建议: 改为按 key 的多槽 dict 缓存（或两调用方统一 limit）。
- 置信度: 高

### [P2] backend/app/coach/audit.py:520 T+5 回填每次只扫最新 200 条，永久 NULL 行会饿死旧警报
- 原文: `            "ORDER BY id DESC LIMIT 200")`
- 问题: 每轮只取最新 200 条 `outcome_pct IS NULL` 的行；`backtest_prices` 无该股数据的行（如退市/代码脏）永远回填不了，又按 id DESC 恒占窗口。
- 影响: 未回填积压超过 200 条后，较早的可回填警报永远轮不到，对照审计样本系统性偏向近期。
- 建议: 加 `alert_date <=` 下界游标或按最旧优先分批；对连续 N 次无价格的 code 记 `outcome_pct = 0` 之外的显式"无价"标记跳过。
- 置信度: 中

### 摘要 backend/app/coach/audit.py
职责：coach_plans（预承诺入场剧本）/coach_alerts（建议+执行回写）两表幂等落库与对照审计：执行一致性（分母=已决策）、预承诺执行率、放弃理由、T+5 结果回填、买入入口留痕｜输入：evaluate 结果、模拟盘平仓事件（close_plan）、前端回写（write_back_execution/abandon_plan，放弃必填理由）、trader_brief._merged_buy_list（gate_entry 留痕）｜输出：两表行、execution_consistency/plan_execution_rate 统计、recent_alerts 列表（列裁剪+message 截断 300）｜关键阈值：回填 T+5、每轮 LIMIT 200、列表缓存 300s、dedupe_key=日|规则|标的 + 唯一索引双保险｜依赖：app.database、flash.rules.beijing_now（唯一时间源）、strategies.recommendation、trader_brief｜已知坑：gate_entry 留痕文案含「（建议 X%）」仓位表述（push=False 只落库不推，属买入入口产品线）；write_back_execution 不调 ensure_tables（依赖其他入口先建表）；写入侧均主动失效列表缓存（正确）。

# backend/app/coach/attribution.py（382 行）

### [P2] backend/app/coach/attribution.py:344 归因失败不写缓存，持续失败时每次请求全量重跑
- 原文: `        return {"available": False, "reason": f"归因失败：{str(e)[:80]}"}`
- 问题: 只有成功结果才写 `_CACHE`（30 分钟）；失败路径直接返回，UI 轮询会按请求频率反复执行 60 天 coach_alerts 全量 + 逐只 backtest_prices 分片查询。
- 影响: 与本文件头自述的 egress 治理（实测 27MB/32h）矛盾——故障期间的重复读库恰是最该避免的。
- 建议: 失败态也写短 TTL（如 300-600s）缓存，到期自动重试（同 rules.py `_usd_5d_change` 的 10-19 修复模式）。
- 置信度: 中

### 摘要 backend/app/coach/attribution.py
职责：教练建议事后归因（缺口 3）：按建议类型/主力阶段/执行与否/放弃理由四维聚合 T+5、T+20 绝对收益与超额，输出"期望最差"排行与规则生成的 sentence；纯只读不改闸门｜输入：coach_alerts（60 天、仅带 code 行）、backtest_prices（200 一批分片）、mainforce_state（按 (code,date) join 当日阶段）、app.benchmarks（基准唯一事实源）｜输出：by_rule/by_executed/by_phase/by_abandon 分组、worst Top5、与库内 outcome_pct 的一致性核对（容差 0.5pt）、selftest 总数核对｜关键阈值：HOLDS=(5,20)、MIN_N=5（不足不排行）、SMALL_N=30（仅方向参考）、OUTCOME_TOL=0.5pt、缓存 1800s｜依赖：app.benchmarks（pick_bench/ret_in_window）、mainforce.phases.PHASE_CN、flash.rules.beijing_now、app.database｜已知坑：买入/市场类建议无 code ⇒「入场理由」归因是数据缺口（note 已显式声明）；执行维度刻意不做方向判定（卖出类建议"执行"与收益方向相反，红线自觉在位）；T+20 普遍未到期为正常现象。

# backend/app/coach/position_sizing.py（370 行）

### [P2] backend/app/coach/position_sizing.py:303 组合回撤 advice 为指令式表述（本模块属"仓位建议"产品线，仍建议收敛措辞）
- 原文: `                         f"总仓位上限 ×{DD_TOTAL_SCALE:g}，暂不加新仓")`
- 问题: 产品线标注：本模块是**仓位建议引擎**（模块头自述"替代前端写死的 calcPositionSize"，服务 /api/user/position-sizing，并被 coach `_reduce_limit_note` 引用为"唯一事实源"），输出 suggested_pct 属其产品职责、非观察器越权；但 `advice` 字段「按纪律降仓……暂不加新仓」是祈使句，超出了"数值+依据"的描述范畴。
- 影响: 前端若直接展示该文本，用户收到的是不加新仓的操作指令而非可解释的数值约束。
- 建议: 改为描述性（「触发周回撤熔断：总仓位上限按纪律 ×0.5 至 X%（需人工确认执行）」），与 market_reasons 的可解释风格统一。
- 置信度: 高

### [P2] backend/app/coach/position_sizing.py:65 降档触发阈值硬编码，与 rules.yaml gate_reduce 是同一纪律的两套定义
- 原文: `    if us10y is not None and us10y > 5.0:`
- 问题: 10Y>5%、DXY、情绪≥80、跌停≥100、涨跌比<0.3 等触发条件写死在此，而 rules.py 的 gate_reduce 同一套条件从 rules.yaml params（us10y_pct/dxy/margin_cold）读取——rules.py:447 注释还称引擎是"唯一事实源"。
- 影响: yaml 调参后教练文案与引擎计算口径分叉，"唯一事实源"承诺失效。
- 建议: 触发阈值常量收口到本模块顶部（或 rules.yaml 独立段），gate_reduce params 引用同一来源。
- 置信度: 高

### [P2] backend/app/coach/position_sizing.py:60 regime 数据缺失时默认 30% 上限，方向不保守
- 原文: `    total = float(REGIME_POSITION.get(regime, 30))`
- 问题: `_regime()` fail-open 返回 `{}` 时 regime=None，`REGIME_POSITION.get(None, 30)` 给出 30% 上限——高于 defensive(0) 与 nb(20) 两档；trade_gate 同款默认（口径一致），但对"保护型上限"而言，未知状态取了中档而非最保守档或显式"未知"。
- 影响: regime 缓存冷启动/回源失败期间，引擎输出的总上限偏宽松且不体现"数据缺失"。
- 建议: regime 为空时输出 `total_limit_pct=None` 或在 market_reasons 显式标注「regime 未知，暂按 30%」。
- 置信度: 中

### 摘要 backend/app/coach/position_sizing.py
职责：仓位建议引擎（W1.5，**仓位建议产品线**——非观察器：市场层总上限 × 降档因子 × 个股 trade_gate 档位 × 组合周回撤熔断，全部只减不增）｜输入：REGIME_POSITION、macro.us10y、margin(chg5/score)、breadth(limit_down/up_down_ratio，跌停口径同 limit_stats)、mainforce.trade_gate.evaluate、user_portfolio（多用户过滤）、tencent.get_kline（回撤曲线，3 分钟缓存）｜输出：total_limit_pct + market_reasons（中文可解释）+ 每股 position_pct/suggested_pct/conditions（区分市况禁买 vs 个股不行）；portfolio_drawdown 明细与 note｜关键阈值：10Y>5%×0.8、两融净减×0.85、情绪≥80×0.7、跌停≥100×0.7、涨跌比<0.3×0.7（硬编码）；回撤窗口 5 交易日、阈值引用 G2 DRAWDOWN_FREEZE_PCT=5.0（单一事实源正确）、触发 ×0.5、缓存 180s｜依赖：trade_gate、coach.rules 数据源适配、paper_trading、portfolio_scope、tencent｜已知坑：回撤曲线"按当前持仓回算"近似（加减仓失真，note 已声明且要求前端展示）；交易日轴取各票日期并集（停牌少点，已声明）；个股档位不按持仓数归一化（天花板×意愿，注释已论证）。

# backend/app/coach/monitor.py（301 行）

### [P2] backend/app/coach/monitor.py:145 体检卡推送不带"非自动交易指令"免责前缀
- 原文: `    _push("🎓 教练体检卡", body)`
- 问题: POSITION_NOTE（「需人工确认执行——不是自动交易指令」）只挂在 coach_tick 的警报推送（:93），体检卡推送不带；而体检卡内容含 gate 类指令式文案（每票命中 label 如「高位+主力出货（不等7%直接砍）」、市场级「环境：-【加仓条件确认…】按计划从准空仓向中性加仓」）。
- 影响: 同一份纪律内容，警报路径有免责、体检卡路径没有，用户在体检卡场景更易误读为系统指令。
- 建议: `push_health_card` 同样拼接 POSITION_NOTE（或将其下沉到 `_push` 统一加注）。
- 置信度: 高

### 摘要 backend/app/coach/monitor.py
职责：Coach 常驻编排——30s light 轮询（落库去重→新触发推企微+explainer 挂载 fail-open）、10:30/14:45 体检卡（schedule_done 幂等）、开仓写入预承诺剧本 write_plan（阈值全读 yaml）、盘后收盘回写+T+5 回填（15:40-24:00、is_trading_day、幂等）｜输入：coach_rules.evaluate_all/build_context、rules.yaml（plan.trail_trigger_pct/price_pos_cut、hold_days）、flash_rules 交易日/开市/北京时间、flash.store 幂等标记｜输出：企微推送（coach 通道 force=True）、coach_plans 行、daily_close_job 统计｜关键阈值：POLL_SECONDS=30、CARD_TIMES 10:30/14:45（窗口 5 分钟错过不补）、盘后窗口 t∈[940,1440)、TRAIL 15%/评估 3 日仅作 yaml 缺省兜底｜依赖：coach.rules/audit/explainer、flash.wechat.notify、flash.store、strategies.market_regime.REGIME_LABELS、paper_trading（W2 explainer"输出禁数字"契约）｜已知坑：POSITION_NOTE 只覆盖警报推送（见问题 12）；_nth_trading_day 依赖 is_trading_day 的硬编码节假日表（异常时退化为自然日）；write_plan 的"强制评估/移动止损/提前退出"条件文本属剧本（预承诺）产品线，指令语义为其设计目的。

# backend/app/scenario_watch.py（470 行）

### [P2] backend/app/scenario_watch.py:235 成交额信号对 pbw"不足"分支的标量 value 取下标，会 TypeError
- 原文: `    amt = (a.get("value") or [None])[-1]`
- 问题: 本代码假设 `amount_dry_up.value` 是列表；但 policy_bottom_watch.evaluate 在 `insufficient=True` 且尚有部分数据时 value 是**标量 float**（`round(series[-1][1], 0)`）——`(13400.0)[-1]` 直接抛 TypeError。
- 影响: 数据不足 3 日的日期（回放早期历史、新部署积累期）整张记分卡 evaluate 失败，run_daily 返回 ok:False、当日无落库。
- 建议: 兼容标量：`v = a.get("value"); amt = _num(v[-1] if isinstance(v, list) else v)`。
- 置信度: 高

### [P2] backend/app/scenario_watch.py:54 SC_SH_LAST 默认 3574 与模块头文档"<3550 空"不一致，且与 SC_SH_ANCHOR 重复定义
- 原文: `SC_SH_LAST = float(os.environ.get("SC_SH_LAST") or 3574.0)    # 上证锚点（2026-10-23 低点）`
- 问题: 文件头判定口径写「上证：<3550 空（最后缓冲之下）」，代码空档阈值实为 `shc < SC_SH_LAST`=3574；且 SC_SH_LAST 与 SC_SH_ANCHOR 是同为 3574.0 的两个独立常量，env 只改其一即口径分裂。
- 影响: 按文档理解记分卡的用户对"空"档判断与实际差 24 点；调参时容易出现两个"锚点"各改各的。
- 建议: 文档改为人性口径或把 SC_SH_LAST 对齐 3550（以用户推演原稿为准并留痕）；SC_SH_ANCHOR 直接引用 SC_SH_LAST。
- 置信度: 高

### [P2] backend/app/scenario_watch.py:276 银行补跌/创业板周线数据不足时记"中性"而非"不足"，口径与同卡其他信号不一致
- 原文: `                     "多" if bank.get("checked") else "中性",`
- 问题: 第 1-4、7 项信号在数据缺失时 lean=「不足」，而第 5 项（本行）、第 6 项（:283 同款）在 `insufficient=True` 时因 checked=False 落入"中性"，第 8 项 ETF（:319）同理。
- 影响: 缺失项在记分卡上看起来像"已评估且中性"，与本项目"缺失≠不满足、也≠中性"的时点诚实纪律不符（n_up/n_dn 虽不受污染，但展示语义失真）。
- 建议: 这三项按 `insufficient`（及 ETF 无数据）显式输出 lean=「不足」。
- 置信度: 高

### [P2] backend/app/scenario_watch.py:314 ETF 份额增幅缺失被 `or 0` 记成 0%
- 原文: `    etf_max = max((_num(r.get("chg_pct")) or 0 for r in etf_rows), default=None)`
- 问题: 行内 `chg_pct` 解析失败（None）时被 `or 0` 当作 0 参与取最大值，最终 value 展示为 0.0 而非"无数据"。
- 影响: 违反"缺失≠0"红线——用户看到"当日最大份额增幅 0%"会误读为"有数据且无异动"。
- 建议: 生成器里跳过 None：`max((_num(...) for r in etf_rows), default=None)` 配合过滤。
- 置信度: 高

### [P2] backend/app/scenario_watch.py:70 事件日历文案宣称"T-1 自动降档"，但系统并无该自动化
- 原文: `     "note": "选后伊朗决策窗口=最大尾部风险；T-1 自动降档"},`
- 问题: 全仓 grep 确认「降档」自动化只存在于本文案（position_sizing 的降档因子是实时因子、无 T-1 定时动作）；模块头又声明"时间仅作倒计时，不自动执行任何动作"，两处矛盾。
- 影响: 用户读到记分卡会以为 11-03 前一交易日系统会自动降仓——实际什么都不会发生，属虚假承诺型输出。
- 建议: 改为「T-1 记得人工降档（系统不自动执行）」或删除该短语。
- 置信度: 中

### [P2] backend/app/scenario_watch.py:427 load() 无落库行时在请求线程现场 evaluate，含指数 K 线网络回退
- 原文: `        return evaluate()`
- 问题: 当日批未跑/无该日行时，`load()` 直接现场跑完整 `evaluate()`，其中 pbw.evaluate → `_load_index_kline` 是腾讯→东财的**网络请求**（已核实 scanner 实现），跑在用户请求路径上。
- 影响: 日批缺口期间打开页面会同步触发外部请求（延迟与 egress），且多次刷新重复触发。
- 建议: 无落库行时返回明确的"待日批"占位（带 reason），或仅用已落库的 pbw/klv 数据做降级视图，不在请求路径跑 K 线回退。
- 置信度: 中

### 摘要 backend/app/scenario_watch.py
职责：磨底 2.0 推演证伪记分卡（**纯观察器**，红线自述：不改分数、不进闸门、不碰模拟盘，多/空只是信号倾向）：9 项信号判多/空/中性/不足 + 情景归类（基准/熊市加深/提前修复）+ 地缘硬触发高亮与动能候选 + 事件倒计时｜输入：policy_bottom_watch.evaluate（成交额/涨跌比/10Y/油价/银行补跌/创业板周线）、key_level_watch（上证位置，缺当日回退最近一条并标 as_of）、etf_share_watch（汇金份额）、flash_news 近 7 日（地缘关键词+否定式正则+噪声剔除）｜输出：scenario_watch 日行（signals_json/lean/scenario/note/extra_json）与 load() 聚合视图｜关键阈值（env 可调）：SC_AMT_HIGH 15400/LOW 12000、SC_RATIO 1.0/0.35、SC_US10Y 5.0/5.3、SC_BRENT 85/120、SC_SH_FIX 3674×连续 2 日、SC_SH_LAST 3574、SC_SH_REDLINE 3500、SC_GEO_KINETIC 3——除上证文档漂移外与文件头一致｜依赖：policy_bottom_watch、key_level_watch、etf_share_watch、contradictions.scanner._load_index_kline（网络回退）、flash_news｜已知坑：情景切换只认油价/10Y/点位（单条新闻只 geo_alert 高亮，防过度反应）；kl_date≠day 时情景显式「待日批」；:379/:445 文案「清单进 2/5」为 pbw 旧 5 项口径残留（现为 6 项/门槛 4）。

# backend/app/policy_bottom_watch.py（774 行）

### [P1] backend/app/policy_bottom_watch.py:768 跨越幂等标记自毁：重跑把 crossed_4 改写为 False 落库，第三次重跑会再次判定跨越
- 原文: `            data["crossed_4"] = False`
- 问题: run_daily 防重推的手段是把当日 `crossed_4` 强制改 False **并落库**——但落库值正是下次重跑 `already_crossed` 的判据：第 1 次重跑读到 True → 强制 False 落库；第 2 次重跑读到 False → already_crossed=False → evaluate 重新判 crossed=True → 返回 True（若日批按返回值推送则**重复推企微**）。
- 影响: 正是 docstring 自认要防的"补跑/重试重复推送"在 ≥2 次重跑（部署重叠/人工重跑/监管重启）时依然发生，幂等约定只挡得住第一次重试。
- 建议: 落库保留 crossed_4 原值，仅对**返回值**强制 False；或新增独立 `cross_pushed` 标记列作为推送幂等依据。
- 置信度: 高

### [P2] backend/app/policy_bottom_watch.py:617 position_hint 是观察器输出的仓位上限建议（已预登记，建议措辞收敛）
- 原文: `                f"建议总仓位上限 {PBW_POSITION_CAP:g}%（**提示，不自动执行**）")`
- 问题: 模块头预登记声明 position_hint"只是提示文案、不自动执行"（豁免在案），但「建议总仓位上限 30%」的措辞仍是观察器在给仓位数字建议，与 Guardrails #5 存在张力；未达标分支的"建议"尤其像操作指令。
- 影响: 清单页/日报直接展示该句，用户可能把它当系统给出的仓位指令执行。
- 建议: 措辞改为描述性（「未达 {PBW_PASS} 条；历史口径下此阶段对应仓位上限参考 30%（不构成操作建议）」），保留预登记声明的"不自动执行"。
- 置信度: 中

### [P2] backend/app/policy_bottom_watch.py:696 confirm_nation_team 重算 crossed_4 不检查当日已跨越状态
- 原文: `    data = evaluate(day, manual=manual)`
- 问题: run_daily 有 already_crossed 防重推，而人工打勾路径重算后直接把 `crossed_4` 写回（n≥4 且昨日 <4 即 True），不读当日行的已跨越状态；该返回值若被路由层用于推送，会在已推送日再次触发。
- 影响: "跨 4/5 只推一次"的模块约定存在第二条未设防路径（推送触发点不在本文件，风险取决于调用方）。
- 建议: 与 run_daily 同样先 `load(day)` 检查 already_crossed，或在函数 docstring 显式约定"调用方不得依据本返回值推送"。
- 置信度: 中

### [P2] backend/app/policy_bottom_watch.py:409 _manual_of 读库异常返回空 dict，人工打勾静默回落为自动判定
- 原文: `        return {}`
- 问题: `_manual_of` 异常时返回 `{}` ⇒ `manual_decided=False` ⇒ 第①条按 `auto_checked`（ETF 份额）计算；"人工优先"在 DB 抖动场景被静默绕过（例如人工明确取消打勾后，一次读库失败会让自动"确认"重新生效）。
- 影响: 违反"人工打勾优先于自动提示"在异常下的稳健性；且无日志、不可见。
- 建议: 异常时让第①条标 `insufficient=True`（缺失≠不满足），或至少打印告警并沿用上次已知 manual 状态。
- 置信度: 中

### 摘要 backend/app/policy_bottom_watch.py
职责：市场底确认 6 条清单（**纯观察器**，模块头预登记：不改分数/不参与排序/不进闸门/不改 regime/不碰模拟盘）：5 条自动判定 + 1 条人工打勾（三层证据分离：公告=事实不参与打勾、ETF 份额连续 2 日放大=行为证据参与、人工 manual_at 非空=最终结论优先），n_checked 跨 PBW_PASS 推送一次｜输入：market_amount_daily→intraday_path 回退（成交额，as_of 防前视）、market_emotion_daily.close_up/close_down（close_as_of 校验+冻结检测，无效日剔除）、指数日 K（创业板周线 ISO 周聚合、银行 399986 vs 全指 000985 补跌连续 2 日且日期对齐）、macro_daily（斜杠日期解析后排序）、etf_share_watch、policy_announcements、本表人工打勾行｜输出：items（checked/insufficient/detail）+ n_checked/crossed_4/position_hint + render_markdown；confirm_nation_team 打勾即重算落库｜关键阈值（env 可调，与文件头文档一致）：PBW_AMOUNT_YI 13000×3 日、PBW_RATIO 0.8×2 日、PBW_US10Y 5.0、PBW_BRENT 95、PBW_PASS 4、PBW_POSITION_CAP 30、PBW_CATCHDOWN_LAG 0×2 日｜依赖：app.database、flash.rules.latest_completed_trading_day（默认日=最近已完成交易日，时点诚实）、etf_share_watch、policy_announcements、contradictions.scanner｜已知坑：宽度数据 00:0x 写入实为前一日、close 冻结须检测——头部均已留痕；save() 默认 n_total=5 已过时（现 6，仅 falsy 兜底）；load() 现读现合并提示字段、不动 checked（正确）。

## 未发现问题

- backend/app/coach/audit.py — `record_advices` 的"先查后插 + 唯一索引冲突静默跳过"双保险，30s 轮询与部署重叠期幂等正确；`day` 参数使日批跨午夜补跑仍按交易日去重。
- backend/app/coach/audit.py — `execution_consistency` 分母=已决策、未响应单列（口径诚实）；`counts.get("None", 0)` 对 `executed IS NULL` 的取键正确。
- backend/app/coach/attribution.py — 缺失（未到期/无柱/基准缺数据）一律 None 跳过、不填 0；组内 n<MIN_N 标 insufficient 不参与排行、全体 n<SMALL_N 强制"仅方向参考"。
- backend/app/coach/attribution.py — `_sentence` 对执行维度刻意不做方向判定（卖出类建议"执行"与收益方向相反，本表不替用户下结论），符合"只给纪律不给指令"。
- backend/app/coach/monitor.py — coach_loop 单轮异常捕获后继续（循环不死）；体检卡 schedule_done 幂等、错过 5 分钟窗口不补（符合设计）；盘后回写窗口用 is_trading_day 而非 weekday（P2-17 修复在位）。
- backend/app/coach/rules.py — `_gate_add_prev_day_hit` 任一日无记录即返回 False（保守方向：不确认加仓）；calendar 异常时退化为自然日也不会误确认。
- backend/app/coach/rules.py — 跌停口径统一走 `app.limit_stats.counts_best` 并在 numbers 披露 `limit_src`（唯一口径红线在位，含 2026-10-09 事故对照说明）。
- backend/app/coach/position_sizing.py — 降档因子全部只减不增（方向保守）；回撤数据失败不写缓存、下轮重试；defensive 上限 0。
- backend/app/policy_bottom_watch.py — ②③④⑤⑥ 的 insufficient 显式区分（缺失≠不满足），无效日剔除并在 detail 展示剔除原因；⑤ 在两侧数据齐全度上按 OR 语义正确处理"部分缺失"。
- backend/app/policy_bottom_watch.py — run_daily 主路径（already_crossed + prev_n<PASS）单次重试场景的幂等正确；PBW_* 阈值 env 默认值与模块头文档全部一致。
- backend/app/scenario_watch.py — SC_AMT/SC_RATIO/SC_US10Y/SC_BRENT 默认值与文件头文档一致；指数数据非当日时情景显式「待日批（指数截至 X）」，不冒充当日；地缘硬触发只高亮不改情景（防单条新闻过度反应）。
