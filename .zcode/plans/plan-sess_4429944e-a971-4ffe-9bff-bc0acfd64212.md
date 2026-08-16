# flash-monitor 全量移植进 stock-scoring-v2

## 定位
flash-monitor 成为 v2 的「事件面 + 信号面」，与已有的规则方向分（外）、市场温度（内）、个股评分（券）组成四层闭环。LLM 输入升级为：宏观面板 + ETF 行情 + **市场温度 + 板块资金流 + 方向分**（flash-monitor 原来看不到的）。

## 后端新增（8 个模块）

### 1. `app/flash/rules.py` — 常量与规则层（移植 rules.js）
- `EXCLUDE_PATTERNS` / `LOW_VALUE_KEYWORDS` / `A_STOCK_KEYWORDS`（~60关键词8组）/ `URGENT_TIME_KEYWORDS` / `isSectorMove`
- `EVENT_CLUSTERS`（10 个事件簇关键词表）
- `get_market_clock()`：**单一实现**（修掉原项目两份不一致实现），A股/恒科延展/日经/美盘时段
- `evaluate_data_quality()`：7 项数据源检查 + 严重不足/部分缺失/充足三级降级（精华设计，原样保留）
- 中国节假日日历（chinaMarket.js 的 2026 表，做成可扩展 dict）

### 2. `app/flash/source.py` — 金十快讯源（移植 fetchJin10 + 过滤聚类）
- 抓取：`GET {JIN10_SUBDOMAIN}.z3c.jin10.com/flash` + `FLASH_COOKIE`(env) + 浏览器伪装头
- `pre_filter` → `cluster_item` → 去重状态机（lastId 游标 + 簇升级重推 + `is_major_update` 军事/紧急关键词规则）
- Cookie 失效 → 返回空列表并记录状态，不崩

### 3. `app/flash/store.py` — JSON 持久化
- `backend/data/`：`flash_state.json` / `flash.json`（≤300条）/ `analyses.json`（**LLM 全文输出**，修原项目不落盘的缺陷）/ `reviews.json`（三段复盘）/ `tracking.json` / `etf_close.json`
- 线程锁 + 原子写（写临时文件再 rename）

### 4. `app/flash/llm.py` — LLM 客户端与提示词（移植 prompt.js + analyzeWithLLM + review）
- OpenAI 兼容调用：`LLM_API_KEY` / `LLM_BASE_URL`(默认 SiliconFlow) / `LLM_MODEL`，3 次重试
- 诊断流（JSON 模式）：油金相关性 2×2 / D 状态 D1(供给)/D2(衰退) / 情景数限制 / 传导链 / `d_state_compliance` 自报+代码审查剥离
- 复盘流（盘前/午间/盘后三段技能）+ 趋势上下文（关键点位改为**配置文件**，修硬编码）
- **信号输出改为结构化 JSON schema**（替代原正则从 Markdown 抓数字的缺陷）：`{signals:[{etf, code, direction, entry, stop_loss, take_profit, support, resistance, reasoning}]}`
- 无 API key → 整模块降级为纯规则展示

### 5. `app/flash/wechat.py` — 可选企业微信推送
- `WECHAT_WEBHOOK` env 配了才推；markdown 按 4KB 分批；未配置静默跳过

### 6. `app/flash/scheduler.py` — 内置定时器（FastAPI lifespan 启动）
- 快讯轮询：**每 10 分钟全天**（无新簇时近零成本：只有新事件才触发 LLM）
- 信号跟踪：交易日 09:15-11:30 / 13:00-15:00 每 15 分钟
- 三段复盘：交易日 09:10 / 11:32 / 15:03
- asyncio 后台任务 + 优雅取消；任务幂等（重启安全）

### 7. `app/signals/tracker.py` — 信号状态机（移植 tracker.js + pro-trader.js）
- ETF 池 22 只（`HOLDINGS_MAP` 移植）；行情直接用 `tencent.py`（**需验证 ETF 代码字段数≥59**）
- 入场门槛：≤5 持仓 / 相关性组≤2 / 总风险≤6% / 技术分（RSI+均线趋势）≥50
- 状态机：waiting→active→closed（入场/止损/止盈/触阻力），价格历史、胜率统计
- **不再用正则解析 LLM 输出**——直接消费 llm.py 的结构化信号

### 8. `app/routers/flash.py` — API
- `GET /api/flash/events`（事件流，分页）/ `GET /api/flash/diagnosis`（最新诊断）/ `GET /api/flash/review/{phase}`（三段复盘）/ `GET /api/flash/signals`（信号+胜率）/ `POST /api/flash/ingest`（手动触发轮询）/ `GET /api/flash/status`（调度器状态）

### 9. main.py 修改
- 注册 flash 路由；lifespan 启动 scheduler；`.gitignore` 加 `backend/data/`

## 前端

### 10. `views/MonitorView.vue` — 新导航页「监控」，3 个子 tab
- **今日诊断**：相关性状态/D状态徽章/情景列表/仓位策略 + 最新三段复盘
- **事件流**：聚类快讯列表（爆/沸/热、紧急、簇计数标签）
- **信号跟踪**：活跃信号表（ETF/方向/入场/止损/止盈/状态）+ 已平仓历史 + 胜率统计卡
### 11. Dashboard 顶部头条行
- 最新诊断的一句话摘要（D 状态/主导情景/仓位建议），与方向分卡、温度卡并排
### 12. `router/index.js` + `App.vue` 导航「监控」；`api/index.js` 新增 5 个接口函数

## 降级链（每层独立失败不拖垮整体）
- 无 `FLASH_COOKIE` → 事件流空，诊断/信号照常
- 无 `LLM_API_KEY` → 诊断/复盘显示「未配置」，事件流/温度/方向分照常
- 无 `WECHAT_WEBHOOK` → 只走 Web 界面
- 金十/新浪单源挂 → `evaluate_data_quality` 分级降级 LLM 步骤（原设计保留）

## 复用与增强
- 宏观面板/16条规则/方向分：直接用 `app/macro.py`；LLM 输入额外注入市场温度+板块资金流（诊断质量提升点）
- ETF 行情：`tencent.py._fetch_tencent`（验证 sh510300/sz159611 可解析）
- flash-monitor 的 `public/data/*.json`（300条快讯/79条宏观历史/真实信号）→ **作为单测夹具**，并可作 data/ 种子数据

## 实施顺序（全量内部的推进序）
1. rules + store（纯函数）→ 用真实夹具跑单测
2. source（金十）→ live 验证（无 cookie 验证降级）
3. llm + prompts → mock 验证 schema；信号结构化输出
4. tracker 状态机 → 用夹具（tracking.json 真实信号）驱动测试
5. wechat + scheduler + routers + main.py
6. 前端 MonitorView + Dashboard 头条 + 路由导航
7. 全量验证：py_compile 全部 + 单测 + ETF 实抓验证 + npm build

## 验证标准
- 过滤/聚类/数据质量/状态机：单测通过（flash-monitor 真实数据夹具）
- 降级路径：无 cookie/无 key/无 webhook 三种配置下模块不崩
- ETF 报价：tencent 实抓 22 只解析成功
- 前端 build 通过，三个 tab 可渲染空态