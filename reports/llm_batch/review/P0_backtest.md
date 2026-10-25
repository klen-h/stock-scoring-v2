审查范围：9 文件 / 约 2990 行 / 2026-10-24

审查依据：docs/PROJECT_GUARDRAILS.md（时点诚实 / 缺失≠0 / 回测撮合现实性 / 涨跌停口径唯一实现 `app.limit_stats`←复用 `backtest.engine._limit_pct` / 日志 ASCII）。已交叉验证 `app/database.py`（SQLite 默认、execute 仅做 %s→? 替换）、`app/pack_source.py`（pack 日期为字符串）、`backend/schema.sql`（backtest_prices.date 为 TEXT）、`app/limit_stats.py`（板幅兜底明确复用 engine._limit_pct，故 engine 的板幅表不构成口径分叉）。

---

## backend/app/backtest/engine.py

### [P0] backend/app/backtest/engine.py:120 涨停一字判定基于前复权价四舍五入，±0.001 容差小于复权舍入误差，历史一字板约半数漏判
- 原文: `            if limit_up > 0 and eb["open"] >= limit_up and eb["low"] >= limit_up - 0.001:`
- 问题: prices_map 是前复权序列（data.fetch_history `fqt=1`/腾讯 `qfq`），真实涨停价 = round(原始昨收×(1+limit_pct), 2)×复权因子，而这里算的是 round(复权昨收×(1+limit_pct), 2)，两者可差约 0.005×(1+因子) 元（数据源对复权价再取整还叠加一层 ±0.005），远超 0.001 容差
- 影响: 除权日之前的历史信号（任一分红除权后即全部中招）里，computed limit_up 偏高约半数的一字板 `open >= limit_up` 不成立 → 不被剔除、按涨停价成交买入，回测收益/胜率系统性虚高，且 skipped_out 少记"涨停一字买不进"
- 建议: 改用比值判据避免取整（如 `eb["open"] / prev_close >= 1 + limit_pct - 1e-4` 且 `eb["low"] / prev_close >= 1 + limit_pct - 1e-4`），或把容差放宽为 `max(0.001, limit_up * 0.002)`
- 置信度: 中

### [P1] backend/app/backtest/engine.py:175 数据未走完的截断交易被标成"持有到期"并全额计入指标，无任何标记
- 原文: `            exit_i, exit_price, reason = last_i, bars[last_i]["close"], "持有到期"`
- 问题: `last_i = min(entry_i + hold - 1, len(bars) - 1)` 在数据右缘提前截断持仓期，但 exit_reason 仍写"持有到期"，trade 正常进入 win_rate/avg_hold_days/pnl 统计（skipped 只登记"一个可卖日都没有"的情形）
- 影响: 最近期（最该看对的）样本被按短持仓口径计成绩且标签失真，"样本期看起来更完整"，违反口径透明与"数据时刻≠请求时刻"两条红线
- 建议: 当 `entry_i + hold - 1 > len(bars) - 1` 时在 trade 里加 `"truncated": True`（或直接 `_skip`），并在 `_skip_summary`/sample_note 汇总展示截断笔数
- 置信度: 高

### [P2] backend/app/backtest/engine.py:129 is_etf 缺省值两处不一致（成本按 ETF、涨跌停按个股）
- 原文: `        c = costs["etf" if s.get("is_etf", True) else "stock"]`
- 问题: is_etf 缺失时成本走 ETF（stamp=0），而第 115 行 `_limit_pct(..., s.get("is_etf", False))` 的缺省是 False
- 影响: 未来任何未显式传 is_etf 的调用方，个股交易被漏计 0.1% 卖出印花税、pnl 虚高（当前两条调用链都显式传了 False，属埋雷）
- 建议: 两处统一缺省为 False（个股是更保守的方向），或在 match_signals 入口对缺失 is_etf 的信号统一补默认值
- 置信度: 高

### [P2] backend/app/backtest/engine.py:150 _sellable 对空头直接放行，涨停一字日回补被虚构成交
- 原文: `            if direction < 0 or limit_pct <= 0 or j <= 0:`
- 问题: 空头平仓是"买入"，涨停一字（只有买盘排队、无卖单）恰恰买不进，与多头"涨停可卖、跌停顺延"的逻辑正好相反，这里却无条件返回可卖
- 影响: 若出现 direction=-1 信号（当前战法流全为 long，属潜伏问题），回补按涨停价成交、止损/到期价不现实
- 建议: direction<0 时把封板判据反转为涨停一字（`b["low"] >= limit_up - ε and b["open"] >= limit_up - ε` → 顺延到下一可买日开盘）
- 置信度: 高

### 摘要 backend/app/backtest/engine.py
职责：日线级信号撮合（T+1 开盘入场、T+1 可卖、止损/止盈/到期/跌停顺延）→ 逐笔 trade（含每日收益路径 daily）→ 等权组合净值曲线 → 绩效指标｜输入：signals（date/code/direction/stop_loss/take_profit/hold_days/position_ratio/is_etf）、prices_map（升序 K 线）、skipped_out 收集列表｜输出：trades、curve、metrics（胜率/盈亏比/年化/回撤/夏普/超额）｜关键阈值：TRADING_DAYS=244、RISK_FREE=0.02、DEFAULT_POSITION_RATIO=0.2、一字板容差 ±0.001｜依赖：collections/math（无本项目 import；板幅表 `_limit_pct` 是全项目唯一实现，被 app.limit_stats 反向复用）｜已知坑：数据右缘截断交易无标记；前复权价取整影响一字/跌停判定精度；is_etf 缺省两处不一致

---

## backend/app/backtest/data.py

### [P2] backend/app/backtest/data.py:145 东财"空数据"计入连续失败计数，3 次即全局禁用东财且进程内永不恢复
- 原文: `        _em_failures += 1`
- 问题: 空数据（代码无效/长期停牌/未上市）与网络异常走同一条 `_em_failures += 1` 路径，累计 3 次后 `DISABLE_EASTMONEY = True`，无任何复位或按 code 区分的逻辑
- 影响: 一次批量回填遇到 3 个空数据代码后，本进程后续全部拉取被迫走腾讯源，放大 WAF 501 与全局冷却概率，回填变慢
- 建议: 只在 `raise_for_status`/except 分支累加 `_em_failures`，"空数据"分支单独计数或直接返回
- 置信度: 高

### [P2] backend/app/backtest/data.py:101 日志含中文文案，违反「日志一律 ASCII」红线
- 原文: `                print(f"[backtest] {code} 腾讯WAF拦截(501)，全局冷却 {_TENCENT_WAF_COOLDOWN}s")`
- 问题: pack_source.py:70 已明确记录"Windows GBK 控制台/日志重向下 print 会 UnicodeEncodeError"的先例，本文件 L101/114/148/150 及 run.py:442、market_regime.py 多处 print 中文
- 影响: GBK 控制台/日志重定向下 print 抛 UnicodeEncodeError，回填/兜底任务在非业务逻辑处中断
- 建议: 将这些 print 改为 ASCII 文案（如 `[backtest] {code} tencent WAF 501, cooldown {N}s`），中文说明走返回值或报告
- 置信度: 高

### [P2] backend/app/backtest/data.py:217 回填失效钩子漏掉 market_regime 的指数 30 分钟缓存
- 原文: `        _st.invalidate_prices_cache()`
- 问题: invalidate_price_caches 只清本模块与 strategies 的缓存，market_regime._BARS_CACHE（sh000300，TTL 1800s，见 market_regime.py:562-575）不在失效范围内
- 影响: 晚间回填指数后 ≤30 分钟内，`load_regime_history()` 非强制路径（app/strategies/market_regime.py:93 的市况接口）仍读回填前 K 线，当日收盘状态缺失
- 建议: 在 invalidate_price_caches 里追加 `from app.backtest import market_regime; market_regime._BARS_CACHE.update({"ts": 0.0, "bars": None})`
- 置信度: 高

### [P2] backend/app/backtest/data.py:181 save_prices 跨模块读取 db._use_postgres 私有属性做方言分支
- 原文: `        if db._use_postgres:`
- 问题: 直接依赖 Database 类的内部私有字段判断 SQL 方言，方言知识散落在调用方
- 影响: database 层若改名/新增后端，save_prices 静默走错分支（`ON CONFLICT` 在 SQLite 直接语法报错），且 AttributeError 只有在真正回填时才暴露
- 建议: 在 Database 上暴露公开属性（如 `db.use_postgres`）或提供 `db.insert_many(table, rows, on_conflict=...)`，save_prices 改调公开接口
- 置信度: 高

### 摘要 backend/app/backtest/data.py
职责：backtest_prices 读写（增量/覆盖双语义）+ 东财/腾讯双源历史日线拉取 + 进程内价格缓存 + 源数据异常日（price_anomalies）读侧接口｜输入：code/start/end、K 线原始响应｜输出：升序 [{date,open,high,low,close,volume}]（date 为 TEXT 字符串）、save_prices 提交行数｜关键阈值：重试 2 次、退避 [2,4]s、东财连续失败 3 次禁用、腾讯 WAF 冷却 300s、价格缓存 TTL 1800s/200 条/单条 ≤900 根｜依赖：app.database.db、app.pack_source（局部导入）、app.backtest.strategies（局部导入防循环）｜已知坑：空数据会推进东财禁用计数且不复位；失效钩子不含 market_regime 指数缓存；anomalies 查询 fail-open 返回 []（设计如此，调用方须知情）

---

## backend/app/backtest/crowding.py

### [P2] backend/app/backtest/crowding.py:43 px() 用 `or` 兜底会把 0/None 收盘价替换成首根收盘价，且全 None 时 float(None) 崩溃
- 原文: `        return float(bars[-n]["close"] or bars[0]["close"])`
- 问题: close 为 0 或 None（停牌占位/脏数据）时静默用 bars[0] 的收盘（可能相差数年）替代计算收益率；bars[0]["close"] 也为 None 时 `float(None)` 直接 TypeError，`highs` 列表推导（第 45 行）同理
- 影响: ret_20/ret_60 变成极端值 → crowding_score 被打到 <30 → 正常标的被乘 0.85 惩罚（乘数只降不升，错杀且无提示），或整个评分流程崩溃
- 建议: 显式判 `c = bars[-n]["close"]; if not c or float(c) <= 0: return None`（语义="无拥挤信息 → mult=1.0"），删除首根价兜底
- 置信度: 中

### 摘要 backend/app/backtest/crowding.py
职责：拥挤度因子三件套（metrics 计算 → 0~100 评分 → [0.85,1.0] 惩罚乘数），评分引擎与离线回测共用的纯函数｜输入：升序日线 bars（≥21 根，high 可缺省降级为 close）｜输出：metrics dict / score / multiplier｜关键阈值：<21 根返回 None；ret_20>30/-50、>20/-40、>10/-25；ret_60>50/-20；dist_from_high<5/-15；乘数分档 <30→0.85、<50→0.95｜依赖：无（纯函数）｜已知坑：px() 对 0/None close 的兜底会扭曲收益率；ret_20 实为 21 根前收盘（20 日涨幅），命名与窗口差 1

---

## backend/app/backtest/market_regime.py

### [P1] backend/app/backtest/market_regime.py:604 建表 DDL 用 Postgres 专属 SERIAL，本地默认 SQLite 下必失败
- 原文: `            id SERIAL PRIMARY KEY,`
- 问题: database.execute 对 SQLite 只做 %s→? 替换，`SERIAL` 是 PostgreSQL 语法；本地默认 SQLite 下 `_ensure_history_table` 抛 OperationalError，且 db.execute 的 `_DDL_DONE` 在执行前登记，失败 DDL 本进程不会重试
- 影响: SQLite 模式下 market_regime_history 永远建不出来 → nb 两日确认查不到昨日 raw（退化为单日）、状态不落库、重启恢复必失败，全部被 except 吞掉只剩日志，线上 Postgres 不受影响
- 建议: 按方言分支建表（`id INTEGER PRIMARY KEY AUTOINCREMENT` vs `id SERIAL PRIMARY KEY`），与 data.save_prices 的 `_use_postgres` 分支同法；顺带把 database.py 的 `_DDL_DONE.add` 移到执行成功之后
- 置信度: 高

### [P2] backend/app/backtest/market_regime.py:125 dx 在 +DI/−DI 未就绪的前 14 根被写成 0 而非 nan，污染 ADX 种子
- 原文: `    dx = np.where((plus_di + minus_di) > 0, 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0)`
- 问题: plus_di/minus_di 前 14 位是 nan，`nan > 0` 为 False → dx=0（不是 nan），_wilder_smooth 以全 0 窗口均值 0 为种子启动递归，ADX 按 (13/14)^k 速度缓慢纠偏，前约 70 根系统性偏低
- 影响: 序列头部 ADX 在 20/35 阈值附近可能误判 weak/moderate；因 load_prices 全量约 3 年加载，这 70 根对应 3 年前日期，对当前复盘/市况影响很小但口径不干净
- 建议: 先 `valid = ~np.isnan(plus_di) & ~np.isnan(minus_di)`，再 `dx = np.where(valid, 100.0 * np.abs(...)/(...), np.nan)`
- 置信度: 中

### [P2] backend/app/backtest/market_regime.py:702 print 带 ⚠️ 与中文，Windows GBK 下会 UnicodeEncodeError
- 原文: `            print(f"[market_regime] ⚠️ 极端缩量（沪深300 当日额=20日均额的 "`
- 问题: 违反「日志一律 ASCII」；pack_source.py:70 的注释明确记录过 print(⚠️) 在 GBK 控制台/重定向下的崩溃先例，本文件还有多处中文 print（L471/484/661/664/721/761 等）
- 影响: 本地 Windows 跑调度/复盘时该分支抛编码异常，refresh_regime_cache 的缩量警示及其后的落库步骤中断
- 建议: 改为 ASCII（如 `[market_regime] WARN volume_ratio_20d={shrink}`），中文说明放进 _REGIME_CACHE["detail"] 返回值
- 置信度: 高

### 摘要 backend/app/backtest/market_regime.py
职责：沪深300 日线 → MA20/60+ADX(14)+ATR 分位 → offensive/neutral/(neutral_bearish)/defensive 状态序列（含 Hysteresis 平滑）；生产侧 refresh_regime_cache 落库 market_regime_history、nb 两日确认、重启恢复；get_regime_weights 提供动态权重｜输入：sh000300 升序 K 线（data.load_prices）、macro 面板/行情缓存（nb 判据）｜输出：List[MarketState]、_REGIME_CACHE（date/state/weights/detail）｜关键阈值：ADX 20/35、ATR 分位 20/80、MA 交叉 ±0.5%、HYSTERESIS_DAYS=3、nb 两日确认、缩量 <0.7｜依赖：app.backtest.data、app.database.db、app.limit_stats（唯一口径）、app.macro、app.events.signal、app.routers.market._cache、app.flash.rules（均局部导入）｜已知坑：SERIAL DDL 在 SQLite 必败；adx 前 14 根 dx 种子为 0；detect_market_regime 本身无前视（窗口均向后），nb 细化只在生产路径叠加、不进回测分层

---

## backend/app/backtest/regime_review.py

### [P2] backend/app/backtest/regime_review.py:118 错误提示指向不存在的模块 `app.backtest.fill`
- 原文: `                       "请先回填指数日线：python -m app.backtest.fill",`
- 问题: backtest/ 目录下没有 fill.py，实际回填入口是 backend/backfill_history.py（data.py 的 save_prices 注释引用的就是它）；同一文案还出现在 strategies.py:474 与 app/strategies/market_regime.py:96
- 影响: 指数数据不足时按提示执行 `python -m app.backtest.fill` 直接报 No module named，操作者卡在第一步
- 建议: 三处文案统一改为真实入口（`python backfill_history.py` 或其 `-m` 模块路径）
- 置信度: 高

### [P2] backend/app/backtest/regime_review.py:179 regime_days 统计的是全量加载历史而非复盘窗口，与表题"统计窗口内"不符
- 原文: `        "regime_days": {st: sum(1 for d, s in regime_map.items() if s == st) for st in STATE_LABELS},`
- 问题: regime_map 覆盖沪深300 全部已回填历史（约 3 年），而分组样本只来自最近 120 天的 ranking_history
- 影响: "一、统计窗口内市场状态分布"实际是全历史分布，读者据此推断复盘窗口内的市况构成会失真
- 建议: 过滤到 `dates_seen` 覆盖的区间（如 `sum(1 for d, s in regime_map.items() if s == st and window_start <= d <= window_end)`）
- 置信度: 高

### [P2] backend/app/backtest/regime_review.py:156 状态查不到的记录被静默丢弃，unclassified 桶恒为 0
- 原文: `            continue  # 状态未知，跳过`
- 问题: `_nearest_regime` 返回 None（指数数据未覆盖该日/早于序列起点）时记录不计入任何组也不计数，初始化的 "unclassified" 字段从未被累加
- 影响: 指数数据缺口会让复盘样本悄悄缩水（各组样本之和 < 总记录数），结论可能基于残缺样本而无人察觉
- 建议: 该分支累计一个窗口级 `unclassified_total`（并在渲染里显示），而不是直接 continue
- 置信度: 高

### 摘要 backend/app/backtest/regime_review.py
职责：ranking_history（每日 Top N 评分）× 市场状态 分层复盘：按信号日后第 horizon 个交易日收益统计各状态胜率/均收益/评分/信号分布，输出结构化 result + markdown｜输入：ranking_history 记录、sh000300 → detect_market_regime、批量日线（strategies._load_prices_map，start=最早快照日）｜输出：{status, window, regime_days, groups{...}} 与 markdown 报告｜关键阈值：窗口 120 天、horizon=5、向前找状态最多 10 个日历日、结论要求各状态买入样本 ≥5｜依赖：app.backtest.data、app.backtest.market_regime、app.backtest.strategies._load_prices_map、app.database.db｜已知坑：_forward_return 用信号日收盘→N 日收盘（与 engine 的 T+1 开盘口径不同，不可直接对比）；regime_days 是全历史口径；nb 状态不在 STATE_LABELS（detect 不产出 nb，故不丢数据）

---

## backend/app/backtest/report_store.py

### [P1] backend/app/backtest/report_store.py:33 建表 DDL 用 SERIAL，SQLite（本地默认库）下报告归档整体静默失效
- 原文: `            id SERIAL PRIMARY KEY,`
- 问题: 与 market_regime.py:604 同因：SQLite 不认 SERIAL，ensure_table 抛错被 save_report 的 except 吞掉返回 False，list_reports/get_report 恒返回空；且 db.execute 的 `_DDL_DONE` 在执行前登记，失败 DDL 本进程不会重试
- 影响: 本地/SQLite 模式下"库是权威来源"的归档承诺失效——报告只剩临时文件（Render 重启即丢），前端回测中心最新报告退回旧行为；线上 Postgres 不受影响
- 建议: 按方言分支建表（INTEGER PRIMARY KEY AUTOINCREMENT vs SERIAL），并与 market_regime 的建表逻辑抽成 database 层公用 helper
- 置信度: 高

### 摘要 backend/app/backtest/report_store.py
职责：回测报告落库归档（backtest_reports 表，name 唯一、同名覆盖），库为权威来源、文件为兜底｜输入：报告名/正文/tag｜输出：bool（save）、报告清单（name/tag/size/mtime）、正文｜关键阈值：list 默认 60 条；_TABLE_READY 进程内幂等标记｜依赖：app.database.db（execute/fetch/fetch_one/upsert）｜已知坑：SERIAL DDL 在 SQLite 必败且失败被缓存；size 用 length(content)（字符数）与文件版字节口径不一致；created_at 为服务器本地时间（render.yaml 已设 TZ=Asia/Shanghai，暂无时区问题）

---

## backend/app/backtest/run.py

### [P2] backend/app/backtest/run.py:308 任意 --strategy 单策略运行都会覆盖 latest.md 并以最新时间戳落库，顶掉前端"最新报告"
- 原文: `    with open(os.path.join(REPORT_DIR, "latest.md"), "w", encoding="utf-8") as f:`
- 问题: main() 对任何 strategy（含 signals/macro/review 等单段报告）都调用 save_report → latest.md 被单段报告覆盖，且按新时间戳插入 backtest_reports；routers/backtest.py:288 的 latest.md 特判取库里"最新一条"
- 影响: 手工跑过一次单策略报告后，回测中心"最新报告"变成残缺的单段内容，直到下一次 --strategy all 才恢复（正文里虽有"本次仅生成 X 报告"提示，但入口语义已被顶掉）
- 建议: `strategy != "all"` 时跳过 latest.md 覆盖与落库（或落库时 tag 加 `partial_` 前缀并让 latest 特判优先取无该前缀的最新记录）
- 置信度: 中

### 摘要 backend/app/backtest/run.py
职责：回测 CLI + 报告渲染（signals/warfare/macro/regime_warfare/review 五段 markdown）、写文件+落库、企微精简摘要 generate_summary｜输入：--strategy/--name；strategies/regime_review/market_regime 的结果 dict｜输出：backend/backtest_reports/*.md + latest.md + backtest_reports 表行｜关键阈值：Top10 逐笔、曲线概要最近 20 日、_fmt_cell 样本 <5 加 ° ｜依赖：app.backtest.strategies、app.backtest.regime_review、app.backtest.market_regime、app.backtest.report_store、app.wechat_fmt｜已知坑：单策略运行会顶掉 latest/latest 报告；--name 路径的 label 用英文 key（未经 _strategy_zh_name）且不产出样本内外切分与曲线概要；多处中文 print（L442）

---

## backend/app/backtest/strategies.py

### [P1] backend/app/backtest/strategies.py:642 基准数据缺失时 _benchmark_ret 返回 0.0，报告把"缺失"渲染成"基准 0.00%"
- 原文: `        return 0.0`
- 问题: `len(bars) < 2`（sh000300 未回填/区间过短）与"基准恰好持平"共用同一个 0.0 返回值，compute_metrics 再以 `(benchmark_ret or 0)` 直接写进 benchmark_return/excess_return（对比：_equal_weight_ret 缺失时正确返回 None）
- 影响: 指数数据缺失的报告会显示"基准收益(沪深300) 0% / 超额收益=全部收益"，直接违反「数据缺失不得填 0，0 与缺失必须可区分」红线，"跑赢沪深300"的结论失真
- 建议: 数据不足时返回 None；compute_metrics 对 benchmark_ret is None 的情况把 benchmark_return/excess_return 置 None，渲染层显示"—"
- 置信度: 高

### [P1] backend/app/backtest/strategies.py:615 宏观回测以信号日收盘价为入场基准且不计任何调仓成本，收益系统性偏乐观
- 原文: `        ret = (bar["close"] / prev_close - 1) * exposure`
- 问题: 换向首日按 close(信号日)→close(T+1) 全段计收益（信号盘后才生成，收盘价那一刻已不可成交；实盘最早 T+1 开盘建仓，隔夜跳空吃不到），且全仓/空仓切换没有扣任何佣金/滑点（engine 对 ETF 计 0.075% 双边）
- 影响: 每次换向多计一段隔夜跳空 + 免掉约 0.075% 成本，方向频繁切换时宏观回测的收益/夏普明显虚高，"score>0 全仓"策略的有效性被高估
- 建议: 换向日记 `(close / open - 1) * exposure`（用 T+1 当日 open 入场），并在 exposure 变化的日子按 `engine.DEFAULT_COSTS["etf"]` 双边扣费
- 置信度: 高

### [P2] backend/app/backtest/strategies.py:501 by_regime 撮合不传 skipped_out，剔除信号在该路径不可见
- 原文: `    trades = engine.match_signals(signals, prices_map)`
- 问题: _run_warfare 已按 2026-09-22 口径透明要求收集 skipped 并写进 sample_note，而 backtest_warfare_by_regime（周报 regime 切片/高波准入依据）没有传 skipped_out，涨停一字/T+1 未到等剔除静默发生
- 影响: 分层表的总样本 N 比信号总量少的部分无任何交代，"各状态样本 (n)"的可信度无法核对，与自家口径透明底线不一致
- 建议: 与 _run_warfare 同法收集 skipped 并在 result 里附 `skipped` 汇总（渲染层至少给出 by_reason 计数）
- 置信度: 高

### [P2] backend/app/backtest/strategies.py:161 v1 退出策略直接沿用信号落库时的 stop_loss/target_price，与前复权价格序列口径不一致
- 原文: `                "stop_loss": it.get("stop_loss"),`
- 问题: strategy_results 里的 stop_loss/target_price 是扫描当时（未复权现价）记录的，而回测 K 线是前复权序列——扫描日之后一旦除权除息，全序列整体平移而止损/目标价不动（默认 v2 会用复权基准重算 stop 规避此问题，v1 或 base 找不到的兜底分支不规避）
- 影响: WARFARE_EXIT_POLICY=v1 回测中，除权前信号的止损/止盈触发位与价格序列错位（如名义 -7% 实为 -12% 或永不触发），胜率/盈亏比失真
- 建议: v1 路径同样以 `base_close`（扫描日复权收盘）按比例换算 stop/tp，而不是直接沿用落库绝对价
- 置信度: 中

### 摘要 backend/app/backtest/strategies.py
职责：三类回测适配器：① LLM 信号绩效追踪（tracking_state 已平仓统计）② 战法选股回测（strategy_results → T+1 撮合，v1/v2 退出策略，70/30 样本内外切分）③ 宏观方向分回测（macro_daily → 沪深300ETF 全仓/空仓）；另有 战法×市场状态/波动率分层回测（含主力闸门同口径）、双基准（沪深300 + 池等权）｜输入：strategy_results/macro_daily/tracking_state JSON、backtest_prices/pack 日线｜输出：{label, sample_note, trades, metrics, curve, by_strategy, skipped, in_sample/out_sample}｜关键阈值：WARFARE_HOLD_DAYS=5、V2=3 日/-7% 止损/不止盈、position_ratio 夹在 [0.05,1.0]、价格缓存 TTL 6h/200 条/单条 ≤900 根/总量 ≤5 万根、基准口径"首日 open→末日 close"｜依赖：app.backtest.data、app.backtest.engine、app.database.db、app.strategies.base、app.mainforce.gate、app.signals.tracker、app.pack_source（均局部导入）｜已知坑：by_regime 路径无 skipped 透明化；宏观回测入场口径偏乐观且免成本；v1 退出与前复权序列错位；_equal_weight_ret 的池=当日 backtest_prices 全体，对历史窗口含幸存者偏差

---

## backend/app/backtest/__init__.py

（无问题）

### 摘要 backend/app/backtest/__init__.py
职责：包入口，一行文档注释声明数据流 data → engine → strategies → run｜输入：无｜输出：无｜关键阈值：无｜依赖：无｜已知坑：无

---

## 未发现问题

- backend/app/backtest/__init__.py

## 复核说明（非问题项）

- `engine._limit_pct` 与 guardrail「涨跌停口径唯一实现 app/limit_stats」不冲突：app/limit_stats.py:40-50 明确以 `backtest.engine._limit_pct` 为唯一板幅实现并反向复用，本次未发现新的 `>=9.5/9.9` 一刀切残留（market_regime._market_breadth_now 已于 2026-10-10 迁移到 limit_stats.counts_best）。
- 日期口径：backtest_prices.date 为 TEXT（schema.sql:63），pack_source 也返回字符串，engine/market_regime/strategies 中的字符串日期比较安全；未发现用日历日冒充交易日、也未发现回测侧用未来数据（_nearest_regime/_forward_return/分层标签均只向后找）。
