# eypai.net（鳄鱼罗盘 V9.3）调研报告 —— 可抓取资产与接入建议

> 调研日期：2026-10-25｜授权：站方（用户自有）｜方式：只读 GET，串行限速
> 原始盘点数据：`reports/eypai/{inventory,probe,data_inventory,nested_shape}.json`
> 复现：见文末 §9

---

## 1. 结论摘要（先看这段）

1. **这是一个纯静态站**（Cloudflare 托管）。`/api/*` 全部 **404 + 返回 HTML**（实测 8 个端点），
   JS 注释自陈「静态站(eypai.net 无 /api/rev)回退 /rev.json 信标」「公网 /api/bundle 恒 401」。
   ⇒ **不存在可对接的后端接口，所有数据都在 `/data/*.json` 静态文件里**。
2. **数据资产规模**：`/rev.json` 白名单 **49 个 JSON**，全量 **4.92 MB**；单文件最大
   `news_flash.json` 1.30 MB、`ta_daily.json`/`ta_live.json` 各 1.35 MB。
3. **`/rev.json` 是增量信标**：`{文件名: 12 位 hash}`。拉它比对 hash 就知道哪些文件变了
   ⇒ 增量抓取成本通常 **< 1 MB/轮**，不需要整站重下。
4. **三类更新节奏**（从文件名与 JS 逻辑推断，已在 §4 标注置信度）：
   - **live**（盘中分钟级）：`market_leader_live`、`basis_today`、`buyflow_live`、`guard_intraday`、`tech_linkage_intraday`、`xiue_intraday`、`heavy_kill_intraday`
   - **daily**（收盘后落盘）：`sentiment`、`mainline_daily`、`market_snapshot`、`emo_series`、`event_cal`、`ta_daily`…
   - **历史/低频**：`emo_series`（**2867 个交易日**）、`hist_server`（按日 120–200 条）、`trade_cal`
5. **与本项目互补性最高的三块**（我们目前确实缺）：
   ① **两融 / 期指基差 / 国债期货**（`market_snapshot.json`）
   ② **2015 年至今的情绪历史序列**（`emo_series.json`，我们只有近 30 天落库）
   ③ **全年财经日历**（`event_cal.json`，我们日历靠 LLM 排期）
6. 数据里普遍带 `_shield / _src / _fp` 字段 —— 站方自己的防抓/溯源标记，**接入时应忽略内容、但不要回写**。

---

## 2. 站点地图（页面层）

`sitemap.xml` 声明 10 个页面；另探了 15 个数据页（自有站，仅 GET HTML）。

| 页面 | 状态 | 体积 | 主题 |
|---|---|---|---|
| `/index-v9.html` | 200 | 110 KB | 总看板（盘前/盘中/盘后/体系 四段导航） |
| `/leader.html` | 200 | 31 KB | 龙头双榜 |
| `/lhb.html` | 200 | 29 KB | 龙虎榜 |
| `/emo.html` | 200 | 44 KB | 情绪监控表 |
| `/events.html` | 200 | 41 KB | 事件/日历 |
| `/watch-cards-public.html` | 200 | 41 KB | 公开自选卡 |
| `/mobile.html` | 200 | 36 KB | 移动版看板 |
| `/mainline.html` | 200 | — | 主线板块 |
| `/flow_ledger.html` | 200 | — | 买盘全史/资金账本 |
| `/basis-history.html` | 200 | — | 基差全史 |
| `/force-v9.html` / `/struct-v9.html` / `/situ.html` | 200 | — | 资金势力 / 内部结构 / 情境 |
| `/factors.html` | 200 | — | 因子 |
| `/cup.html` / `/gap_stats.html` / `/vol_extreme_stats.html` | 200 | — | 摔杯 / 高开缺口 / 极值缩量 |

导航栏目（首页可见）：**盘前**（主线/事件/高开缺口/券商胜率）→ **盘中**（资金势力/结构/龙头/摔杯/大盘云图/ETF 龙队量能/基差全史/模拟账户）→ **盘后**（回放/期指龙虎榜/情绪监控表/内部结构/龙队/买盘全史/冰点/宽基 ETF 申赎/机构 ETF 追踪/恒科沽空 + 因子/情境/吸血效应/极值缩量/预测轨/总账/宏观数据 + 回测/回算/师门对照/分析/图文笔记）。

⚠️ 页面本身是 **JS 渲染**（29 个 `<script>`，正文仅 ~4.4 KB），直接抓 HTML 只能拿到导航壳；
**要数据必须抓 `/data/`**，不要指望抓页面 DOM。

---

## 3. 数据清单（49 个文件，按主题分组）

> 「更新节奏」中：**live** = 盘中分钟级；**daily** = 收盘后；**hist** = 低频/长历史；
> 括号内为置信度（实测文件名 + JS 逻辑推断，非站方文档）。

### 3.1 情绪 / 温度（与本项目「情绪预判、温度计」直接对照）
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `data/sentiment.json` | 7.6 KB | daily | `score100`、`level`、`bull_ratio`、`bear_ratio`、`extreme_ratio`、`top_bull`、`top_bear` |
| `data/emo_series.json` | 426 KB | daily（长历史） | `days[2867]` + `ser{red,green,zt,dt,zb,zbr,lb,lb2,lb3,lb4...}`（**2015 至今逐日序列**） |
| `data/market_leader_live.json` | 3.6 KB | live | `sentiment{gt,it,state,digest,max_lb,promote,ice}`、`styles{gaobiao,leader}`、`pool_leader` |
| `data/tr_snapshot.json` / `v92_live.json` / `lhb_score_result.json` | <1 KB | live/daily | 情绪比、v9.2 实时分、龙虎榜评分 |

### 3.2 大盘 / 结构
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `market_snapshot.json` | 42 KB | daily（含实时块） | `margin{sse_yi,szse_yi,rzye_yi,total_yi,add_yi}`、`basis{items[4]}`、`gov_bond{fut,yield}`、`new_high` |
| `core_snapshot.json` / `core_today.json` | 1/15 KB | live | 指数要点 + 当日分时 `series` |
| `data/ta_daily.json` / `ta_live.json` | 1.35 MB ×2 | daily/live | `codes{SH,HS300,CYB,KC50,...}` 的 `bars`（指数技术面） |
| `data/idx_volume_daily.json` | 7.6 KB | daily | 指数量能 `vol` |
| `data/guard_heat.json` / `guard_stage.json` / `guard_intraday.json` | 14/3/1 KB | live | 四象限温度、阶段、盘中档位 |
| `data/fusion_daily.json` | 211 B | daily | `label/band/power/phase/risk_pct`（融合结论） |
| `data/logbias_snapshot.json` | 1.5 KB | daily | 对数乖离快照 |
| `data/hstech_ss_daily.json` | 3.3 KB | daily | 恒科沽空指标 |
| `data/hkliq_summary.json` | 144 B | daily | 港股流动性结论 |
| `hist_server.json` | 238 KB | hist | `days{日期: [120~200 条]}`（逐日明细，**疑似涨停/龙头池历史**） |

### 3.3 板块 / 主线
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `data/mainline_daily.json` | 29 KB | daily | `market{zt_count,lianban,max_h,yest_lianban_mean,rotation,prism}`、`sectors[8]`、`bias_reversal{items[31]}` |
| `data/tech_linkage_daily.json` / `_intraday.json` | 3.6/0.9 KB | daily/live | 科技联动（日/盘中） |
| `data/hotwords.json` | 1.1 KB | live | 热词 |

### 3.4 资金 / 买卖盘
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `data/buyflow_live.json` | 175 B | live | `points`、`last`（买盘力度） |
| `data/bfi.json` | 11.5 KB | daily | `latest`、`history`（买盘指数） |
| `data/cup_state.json` | 1.4 KB | live | `count/total/items/tier`（摔杯状态） |
| `data/heavy_kill.json` / `_intraday.json` | 6.4/0.5 KB | daily/live | `latest/history/pool_names`、`killed/pools_hit/mkt_amt_yi` |
| `data/xiue_intraday.json` | 679 B | live | 吸血效应 `text/xs/rs` |
| `data/risk_aversion.json` | 2.2 KB | daily | 风险厌恶 `total/full_total` |
| `wx_buyflow.json` | 402 B | live | 微信侧买盘 `stage/final` |

### 3.5 期指 / 基差 / 龙虎榜
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `data/basis_daily.json` | 12.7 KB | daily | `schema/basis_date/partial/degraded/basis` |
| `data/basis_today.json` | 21 KB | live | `pts[?]`、`pre_open` |
| `data/basis_conv.json` | 1.3 KB | live | `by_sym`（各合约基差） |
| `lhb_snapshot.json` | 4.2 KB | daily | `symbols`、`roll_day`、`missing` |

### 3.6 ETF / 自选 / 事件 / 新闻
| 文件 | 体积 | 节奏 | 关键字段 |
|---|---|---|---|
| `data/etf_nt_daily.json` | 34.8 KB | daily | `pool`（宽基 ETF 申赎） |
| `data/watch_cards_public.json` / `_history.json` | 4/11.9 KB | daily | `cards[4]`、`selection.items[3]`、`history.days` |
| `data/event_cal.json` | 72.7 KB | daily | `cats{政策会议/经济数据/资金机制/海外事件/业绩期/节假日}`、`by_date`、`year_grid` |
| `data/event_results.json` | 18 KB | daily | `series`（事件事后表现） |
| `data/news_flash.json` | **1.30 MB** | live | `items[200]`、`imp_today[18]`、`by_day{10 天}` |
| `data/trade_cal.json` | 114 KB | hist | 交易日历 `first/last/n` |
| `eyupai_score_result_v8.json` / `data/score_settled.json` / `data/dhx_today.json` | <1 KB | daily | 评分结果 / 结算率 / 当日一句话 |

> 另有 5 个引用但 **404** 的路径（`etf_share_hist`、`hk_four_factor`、`hkliq_daily`、
> `position_v2`、`data_source_health`）—— 已改名或已下线，接入前需按 `/rev.json` 为准，
> **不要按 JS 里的旧路径硬编码**。

---

## 4. 字段级可迁移清单（→ 本项目）

| # | eypai 数据 | 本项目现状 | 建议用途 | 优先级 |
|---|---|---|---|---|
| 1 | `emo_series`（2867 日 × 10 序列：涨停/跌停/炸板/连板分层…） | 我们只有 `market_emotion_daily` 近 30 天 | **补长历史情绪底库** → 情绪分位、涨停家数分位、连板高度分位；可直接做「今日情绪处于历史百分位」这类卡片 | **P0** |
| 2 | `market_snapshot.margin/basis/gov_bond` | 我们无两融、无期指基差、无国债期货 | 观察器新维度：杠杆资金/贴水/无风险利率；与 `10Y 美债` 并列做内外流动性 | **P0** |
| 3 | `event_cal`（全年、分类、含节假日） | 我们日历靠 LLM + 金十，覆盖不稳 | 替换/补强 `flash/calendar`：事件排期、节假日、业绩期 | **P0** |
| 4 | `mainline_daily.market{zt_count,lianban,max_h}` | 我们有涨停/连板（板幅口径已修） | **交叉校验**（两个独立实现互证，正好补 `limit_stats` 的外部对照） | P1 |
| 5 | `market_leader_live.sentiment{gt,it,max_lb,promote,ice}` | 我们有情绪温度（12 子项） | 对照/融合，做「两套情绪系统是否打架」的检测 | P1 |
| 6 | `news_flash.by_day`（10 天按日分组 + imp 标记） | 我们 `flash_news` 120 天（仅金十） | 补充非金十来源；`imp_today` 可作重要度外部标注 | P1 |
| 7 | `basis_daily/today/conv` | 无 | 期指基差观察器（贴水加深=对冲盘压力） | P1 |
| 8 | `etf_nt_daily.pool` | 我们有 `etf_share_watch`（上交所份额） | 对照申赎口径 | P2 |
| 9 | `ta_daily/ta_live`（指数 bars 1.35 MB） | 我们有本地 K 线包 | 指数技术面 cross-check（注意体积大，慎每日全量） | P2 |
| 10 | `hist_server.days` | 无 | 逐日明细（若确为涨停池）可补「历史某日涨停名单」 | P2 |
| 11 | `watch_cards_public` | 无 | 公开自选卡 → 作外部选股样本对照（勿进决策链） | P2 |

---

## 5. 抓取可行性（工程结论）

| 维度 | 结论 |
|---|---|
| 鉴权 | **不需要**（静态文件公开可读）；`/api/*` 不存在，别浪费时间 |
| 增量 | 读 `/rev.json`（49 条 `{path: hash}`），只下 hash 变化的文件 |
| 单轮成本 | 全量 4.92 MB；典型增量 < 1 MB（live 文件都是 KB 级） |
| 请求形态 | 普通 GET 即可；**必须判 `content-type` 含 json**——该站 404 也返回 200 字节级 HTML |
| 频率建议 | live：盘中 60s；daily：收盘后 20:00 一次；hist：每周一次 |
| 反抓标记 | 多数文件含 `_shield/_src/_fp`；未观察到限流，但**不要并发**、不要伪装浏览器指纹 |
| 稳定性 | Cloudflare 静态托管；无 SLA；文件名可能变（实测 5 个旧路径已 404） |
| 口径风险 | **站方无公开 schema 文档**，字段语义（如 `zt_count` 是否含 ST/北交所）需用我们 `limit_stats` 对账确认 |

---

## 6. 与本项目的关系：重叠 vs 互补

- **重叠（可作外部校验，不必替换）**：涨停/跌停/连板家数、情绪温度、板块主线、ETF 份额。
  价值在于「两套独立实现互证」——正好是我们 10-24 修涨跌停口径时最缺的外部参照。
- **互补（我们确实没有）**：两融余额、期指基差、国债期货、2015 年以来的情绪长序列、
  全年财经日历、逐日历史明细。
- **不要接的**：`watch_cards_public` 这类「选股结论」——它是别人的判断，
  接进来会污染「观察器不进决策链」的红线；只可作**对照样本**。

---

## 7. 接入方案（三档，建议先做 A）

**A. 最小可用（P0，1–2 天）**
1. `scripts/fetch_eypai.py`：读 `/rev.json` → 只下变更文件 → 落 `data/eypai/<name>.json`
   + 写 `data/eypai/_rev.json`（上次 hash），支持 `--full`
2. 三个读库指标进观察器：情绪历史分位、两融/基差、事件日历
3. 日批加一个任务 `eypai_sync`（收盘后跑，失败 fail-open）

**B. 交叉校验（P1）**
4. 用 `emo_series.zt/dt` 与本地 `limit_stats` 对账，产出「口径差异日报」——
   差异 >5% 就告警（这比任何单测都能发现口径漂移）

**C. 深度融合（P2，需谨慎）**
5. `ta_daily`/`hist_server` 入库（体积大，先评估 egress 与保留期）
6. `news_flash` 与本地 `flash_news` 合并去重（按正文前 30 字）

---

## 8. 未验证 / 需站方（你）确认

1. `zt_count`/`dt`/`lb` 的**精确口径**（是否含 ST、北交所、是否按板幅）——这是对账前提。
2. `emo_series.ser` 各列含义（`red/green/zt/dt/zb/zbr/lb/lb2/lb3/lb4` 我按字面推断）。
3. 各文件的**生成时点**（收盘后多久落盘），决定日批任务排几点。
4. `_shield/_fp` 是否会随请求特征变化（若会，静态文件比对会失真）。
5. 是否有**更高频/更细粒度**的数据（如分钟级板块），目前只看到日频与盘中快照。

---

## 9. 复现命令

```bash
# 站点/脚本/路径盘点（页面 + JS 里的 api/data 引用）
python scripts/fetch_eypai.py --inventory     # 待实现，等价于本次调研脚本

# 本次调研的四份原始数据（已入库）
reports/eypai/inventory.json        # 25 个页面 + 12 个 JS + 18 个 api 引用 + 25 个 data 引用
reports/eypai/probe.json            # API 探测（全部 404）+ 15 个数据文件结构
reports/eypai/data_inventory.json   # rev.json 白名单 49 个文件的体积/字段
reports/eypai/nested_shape.json     # 10 个关键文件的下钻结构
```
