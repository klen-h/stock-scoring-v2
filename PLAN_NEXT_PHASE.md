# 下一阶段计划（2026-09-11 起）· 数据面收口 + 脱离 Supabase

> 承接：本文件 09-05 版（战法复健 / 龙虎榜 / 模拟盘卖出侧等已落地，未完成项归入本文 P2）。
> 定位：**功能层已基本完备，脆弱点全在"数据面"**——包新鲜度、包覆盖缺口、榜单口径、DB 配额。
> 本阶段目标：把这两天暴露的五类数据面问题一次性收口，并把数据库从 Supabase 迁到自有服务器。

---

## 背景：09-10 / 09-11 两天暴露的问题（已修，等验证）

| # | 问题 | 根因 | 修复 | 状态 |
|---|---|---|---|---|
| 1 | 日批拿到 09-09 旧包 | CDN `max-age=600` + Pages 部署空窗；重试循环只置 `_ready_checked`，`_db_fresh()` 看 mtime 判"刚下过=新鲜"→ 30 分钟一次都没重下 | 下载带唯一查询串击穿缓存；新增 `redownload()`，重试改为强制重下 | `1ddfacf` 待验证 |
| 2 | Render 挂着旧包供旧 K 线 | 常驻进程 `_ready_checked` 短路后永不复查；且 `_parse_pack_date` 只认 `%Y-%m-%d`（实际是 `YYYYMMDD`）→ 陈旧判定形同虚设 | 新增 30 分钟周期自检 `_maybe_refresh()`；日期双格式解析 | `1ddfacf` 待验证 |
| 3 | 战法扫描回源撞 WAF + Supabase 流量 456MB/天 | 包只覆盖 1431 只，战法池需 2052 只 → 缺 650 只每天回源（`backtest_prices` 单只全历史 1100 万行/21 天、`kline_cache` 30 万次查询） | 池口径改为 `market_snapshot`（战法池同口径 2052 只）；后端包超时 150→180 分钟 | `1ddfacf` 待验证 |
| 4 | 前端包 739/1534 只少最后一根 K 线（本地评分与后端差 3.8 分） | 腾讯复权序列收盘后**逐只**补齐，18:00 拉时近半还没更新；后端包 20:30 拉就齐 | 打包脚本新增"末根补齐重试"；前端 job 超时 60→90 分钟 | `041d552` 待验证 |
| 5 | 详情页 K 线停在旧日期 | 详情页"本地优先"不校验新鲜度 → 旧 IndexedDB 压过后端新数据 | 新增包新鲜度门（落后即回退后端） | `041d552` 已生效 |
| 6 | 本地榜有、后端榜没有（002452） | 后端榜单两阶段：简化分（动量+换手+PE）取前 100 当候选池 → 基本面型股票（简化 352 名/精算 69.8）永不入池 | 日批全量精算 → `ranking_live` 落库；后端当日直接读、盘中并入候选池 | `9baeef3` 待首跑 |
| 7 | 主力分桶恒为空 | ① `if mf_key != "none"` 让 none 桶永不累加 ② 850 行 `mainforce_signal` 全 NULL（前端保存快照路径漏写标签） | 两处修复 + 回填 36 行（accum 26/distribution 10） | `b1b5a1a` 已生效 |
| 8 | 市场状态权重停在 09-08 | `regime_cache_loop` 是 Render 15:40 循环（只读模式被关），而日批任务清单漏了它 | 日批新增 `market_regime` 任务（backfill 之后） | `6fd1d7b` 待首跑 |

### 09-11 夜间追加（用户当日巡检暴露）

| # | 问题 | 根因 | 修复 | 状态 |
|---|---|---|---|---|
| 9 | 消息分快照落后 3 天（停在 09-08） | `news_history_loop` 在 `_heavy()` 里 → 只读模式同样被关；日批漏配 | 日批新增 `news_snapshot` 任务（排在 score_snapshot 后） | `e8f45f2`，**已手工补跑 09-11（54 只）** |
| 10 | 回测中心最新报告停在 09-05 | `backtest_report_loop` 同样在 `_heavy()`；且报告只写文件，**Render 文件系统是临时的**（部署即清空） | 日批新增 `weekly_report`（周五）+ 报告正文落库 `backtest_reports`，前端库→文件兜底；追加「本周无报任意工作日自愈 / `--tasks weekly_report` 点名即跑」；`regime_review` 逐股远程查询改批量加载 | ✅ **2026-09-11 23:49 已上线**（Actions run #9，83 秒完成，前端回测中心最新一份 = `backtest_report_weekly_20260911_154945.md`） |
| 11 | 决策简报 AI 暂不可用（空响应） | `call_llm` 收到 HTTP 200 但 content 为空时**直接 return ""**（注释宣称重试实则没有）；推理模型思考与答案共用 `max_tokens` | 空响应纳入重试 + 2 次起切 `LLM_MODEL_FALLBACK` + `finish_reason=length` 时 max_tokens 翻倍（上限 32768）+ 失败原因透传到简报与 `/api/flash/status` | `e8f45f2`/`60d00ba`，本地实测真实简报提示词正常（Render 待部署后看 `last_error`） |
| 12 | Supabase egress 364MB/天（免费档 5GB/月） | 大结果集读：`SELECT * backtest_prices WHERE code=$1`（18037 次/1112 万行）、`mainflow_history` 整表（99 次/724 万行）、`flash_news` 整行（6534 次/187 万行） | 列裁剪（去掉 id/code/name）+ `load_prices` 30min 进程缓存 + `load_flow_map`/`load_flow` 30min 缓存 + `load_raw_items` 120s 缓存（写入即失效）+ id 查询收窄到近 3 天 | `e8f45f2`；今晚起叠加"包覆盖 2052 只不再回源" |

---

## P0 · 今天 ~ 明早（应急 + 验收）

### 1. Supabase 配额决策（**今天 09-11 宽限期最后一天**）
- **现状**：宽限期内读写正常；**阶段 1 冷备库已完成**——腾讯云 `stock-postgres`（PG17，内网 only）已含全量数据，逐表行数一致（backtest_prices 478,795 / mainflow 81,735 / mainforce_state 4,459 / kline_cache 2,220 / ranking_history 850 …），每日 03:00 自动备份（保留 14 份）。
- **已定**：暂不买域名 → 前端继续吃 Supabase，接受 402 一来切本地模式。
- **今天要做的判断**：明早看 Supabase 流量面板
  - 若流量降到配额内（免费档 ~167MB/天）→ 维持现状，等域名
  - 若仍 400MB+ → 二选一：① Supabase Pro $25 保命 ② 立即上域名走完整迁移（P1）
- **应急动作（0 风险，随时可做）**：
  1. 前端排行榜页开「本地计算」开关（零 API，包在 Pages）→ 核心评分可用
  2. 启用冷备库：重跑 `bash deploy/migrate_supabase.sh` 同步 → `DATABASE_URL` 指向 `postgres:5432` → `docker compose restart backend`

### 2. 今晚 Actions 验收（5 个数字，日志里逐条对）
```
① backend-pack  → 「战法池口径覆盖: 2052/2052（缺口 0，目标 0）」      ← 缺 659 只归零
② kline-data    → 「末根补齐第 1 轮: N 只停在 20260910 之前」→ 残留应趋 0
③ daily-batch   → 「[2/15] 市场状态: 2026-09-11 neutral 权重={...}」  ← regime 追到当日
④ daily-batch   → 「[12/15] 全量精算榜: 300 行落库（date=…, 池=1561, 榜首 …）」
⑤ daily-batch   → 日志里 `[WAF] K线请求被拦截` / `判过期走实时拉取` 应大幅减少；
                  且不再出现「数据包日期 != 今天」
```

### 3. 前端/接口验证（明早）
| 检查点 | 期望 |
|---|---|
| 排行榜（API 模式） | 002452 出现在榜上；`/api/score/batch/top` 的 `total=1561` |
| 详情页 000567 | 资金面出现第 5 因子「主力净流入」（等 Render 部署到最新 commit） |
| BucketStats | `mainforce.none` 有样本（814 行基数）；accum 26 / distribution 10（n<20 灰显） |
| 详情页 K 线 | 末根 = 09-10/09-11（前端包补齐 + 新鲜度门生效） |

### 4. 两项配置补齐（需你操作，各 1 分钟）
- **GitHub secrets**：`LLM_BASE_URL=https://api.siliconflow.cn/v1`、`LLM_MODEL=deepseek-ai/DeepSeek-R1`
  （现为空 → 矛盾报告/日报的 AI 解读全部失败：`Invalid URL '/chat/completions'`）
- **Render**：Events 确认部署到最新 commit（`6fd1d7b`/`190c988`），没有就 Manual Deploy

---

## P1 · 本周末（迁移闭环：彻底脱离 Supabase）

**唯一前置**：买一个域名（¥30-60/年）→ NS 指到 Cloudflare。
（国内地域 80/443 需 ICP 备案走不通；命名隧道是**出站**方案，不需要备案。）

1. **命名隧道替换快速隧道**：cloudflared named tunnel → `https://api.<域名>` 固定地址（快速隧道重启即换域名，绝不能当生产）
2. **DB 切到服务器本机 Postgres**：重跑 migrate 同步 → 改 `DATABASE_URL` → 重启 → 验证
3. **解决"外部写入方访问 DB"**（关键设计决策，三选一）：
   - **方案 α（推荐，最干净）**：API + 日批都搬到腾讯云（服务器自己写自己的库）；Actions 只留**不碰库**的 `kline-data`；`backend-pack` 改造为不读库或一并搬走
   - **方案 β**：`cloudflared access tcp` 暴露 `db.<域名>:5432`（服务令牌）给 Actions/Render（改动小，链路多一跳）
   - **方案 γ**：Postgres 开公网 + IP 白名单（**不推荐**：GitHub/Render 出口 IP 动态）
4. **前端切换**：`VITE_API_BASE_URL=https://api.<域名>/api` → push → deploy-preview 重建 → 硬刷新验证
5. **下线与归档**：Render 观察 1-2 天后停用；Supabase 保留只读 1 周后归档
6. **备份外迁**：`deploy/backups/*.dump` 定期 scp/OSS 出一份（防单机故障）

**验收**：`api.<域名>/api/health` 200 · 前端全页面可用（登录/排行/详情/日报/模拟盘）· 日批写库成功 · Supabase 流量归零。

---

## P1 · 本周（收口功能缺口）

1. **决策简报自动化**：日批加 `trader_brief`（postmarket）+ 企微推送 —— 现在只有"打开页面才生成"，盘前 9:10 / 盘后 19:35 推送是 PLAN_TRADER_WORKFLOW 的 Phase 1 收尾项
2. **榜单单一事实源评审**：前端「本地模式」与服务端「全量精算榜」仍可能有几分差异 → 要么本地模式直接展示服务端榜，要么在 UI 上标注口径（避免又被当成 bug）
3. **`backtest_prices` 读放大治理**：即便包齐了，`SELECT * FROM backtest_prices WHERE code=$1`（单只全历史）仍是最大 egress 源 → 加列裁剪（只要 date/close）+ 进程内 LRU
4. **回填配额**：backtest_prices 每日 150 只 → 594 只缺口要 4 天，按"距评分池远近"排优先级

---

## P2 · 本月（承接 09-05 版未完成项）

- 个股层矛盾扫描：`评分 ≥65 买入` ∩ `出货嫌疑` → 自动降级提醒 + 企微标注
- 消息分验证定版：IC ≥0.10（需 20 个快照日，现 +0.202/3 日）
- MAINFORCE_MODE=auto 观察期结论（需覆盖一次大跌段）
- Watchlist / 组合页评分口径统一到前端引擎
- backtest_prices 池扩展 544 → 3000 只（用 kline pack 反灌）
- LLM 复盘提效：R1 → V3 分档（R1 只做周度深度复盘）

---

## 观察清单（不投开发，等阈值）

| 项 | 阈值 | 当前 |
|---|---|---|
| 消息分 IC | ≥0.10（20 快照日） | +0.202 / 3 日 |
| 主力因子 offensive 段 | 样本 ≥500 | ~330 |
| 矛盾扫描 | ≥60 条兑现率统计 | 4 条 |
| 主力分桶（吸筹/出货） | n ≥20 | accum 26 / distribution 10 |
| 全量精算榜 vs 前端本地榜 | 差异 ≤1 分 | 待首跑对比 |
| Supabase 流量 | <167MB/天（免费档） | 456MB/天（09-10） |

---

## 风险与回滚

| 场景 | 回滚 / 兜底 |
|---|---|
| Supabase 402 / 配额 | 冷备库已就绪：migrate 同步 → `DATABASE_URL` 切换 → restart；代码层无改动，改回 Supabase 串即回滚 |
| Actions 包失败 | 读侧 `_is_stale()` 自动回退 DB（不会静默用两三天前的数据算分） |
| API 不可用 / 隧道失效 | 前端开「本地计算」（包在 Pages，零 API） |
| 迁移数据不一致 | 迁移脚本自带源/目标行数比对；Supabase 原库不删，只读保留 1 周 |
