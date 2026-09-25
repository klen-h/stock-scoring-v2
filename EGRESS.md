# Supabase 出站流量（egress）治理指南

> **核心原则一：egress 是账号级的。** 本地进程与线上 Render **共用同一个额度** ——
> 所以「本地跑」不是免费的，本地读一次就记在同一个账上。免费档 5GB/月 ≈ **167MB/天**，超了就是 402。
>
> **核心原则二：先量再改。** 本文所有数字都来自 `pg_stat_statements` 实测，不是估算。
> 项目自带 `scripts/egress_probe.py`（快照落 `backend/data/egress_probe.json`，两次快照做差
> 即真实字节数；其 `_NOISE` 列表已排除 Supabase 平台自身的语句）。

---

## 一、怎么量（排障标准动作）

```
① 查 pg_stat_statements，按 rows 降序   —— rows 是 egress 的最佳代理（返回行数越多越费）
② 必查 stats_reset                     —— 否则会把 N 天的累计当成一天（最容易犯的错）
③ 看完整 SQL，不要 LEFT(query, N)       —— 截断会误判批大小
```

可直接复制的查询：

```sql
-- 起点：不查这个，后面所有数字都可能是 21 天的累计
SELECT stats_reset FROM pg_stat_statements_info;

-- 按返回行数排序（= egress 大头）
SELECT calls, rows, LEFT(query, 120) AS q
FROM pg_stat_statements ORDER BY rows DESC LIMIT 15;

-- 高频小查询（单次小但次数多，也可能累积）
SELECT calls, rows, LEFT(query, 120) AS q
FROM pg_stat_statements ORDER BY calls DESC LIMIT 15;
```

**三个实测踩过的坑：**

| 坑 | 现象 | 正确做法 |
|---|---|---|
| **不查 `stats_reset`** | 把 6 天累计当一天，结论差 6 倍 | 先查 `pg_stat_statements_info.stats_reset` |
| **`LEFT(query, N)` 截断** | 看到 `$1..$56` 就以为是"56 只一批"，实际有几百只 → 源头上找不到 | `SELECT query` 取完整文本 |
| **`%` 被当成占位符** | 写 `LIKE '%xxx%'` → `IndexError: tuple index out of range` | 走参数传：`LIKE %s` + `('%xxx%',)` |

**另一个必须分清的概念**：表级 `pg_stat_user_tables.seq_tup_read` **高 ≠ egress 高** ——
那是服务器**内部**扫描的行数（读出来又被 WHERE 丢掉的不出网）。**egress 只看 `rows`。**

---

## 二、病根模式：**缓存是「进程内」，而数据是「低频」**

这是本项目 egress 问题的**唯一主要模式**。凡是符合下面两条的读取，都会被放大：

1. 缓存是**进程内**的（模块级变量 / TTL 缓存）
2. 数据**每天或每季度只变一次**

⇒ **每重启一个新进程就整份重读一次**。而本地开发恰恰是"高重启"环境：

```
backend/run.py 默认带 --reload  →  改一次代码就重启一次
各种 scripts/*.py               →  每个都是新进程
```

**已实测的四个实例**（全部同源）：

| 读取点 | 单次成本 | 6 天实测 | 放大机制 |
|---|---|---|---|
| `mainflow_history` 整表读（`load_flow_map`） | 8.8 万行 ≈ **8.7MB** | **68 次** | 每进程一次；本地单日可冲几十次 |
| `market_snapshot` 整行读（`flow` + `store` 两处） | 1.1MB 文本 ≈ **336KB** | **70 + 85 次** | 每进程一次 **+ 常驻进程每 300s 重读**（见 §五） |
| `stock_industry JOIN stock_finance`（`engine._industry_dist`） | 16,810 行 ≈ **1MB** | **49 次** | 每进程一次 |
| `backtest_prices` 批量读（研究脚本，**无 `start`**） | 22.7 万行 ≈ **7~8MB** | **12 次** | 每次跑脚本一次，且读**全历史** |

**治理前常态基线 ≈ 140MB/天**（≈ 167MB/天 的天花板），其中
`mainflow_history` ≈ 99MB/天、`backtest_prices` ≈ 42MB/天。

---

## 三、四种跨进程版本门控手段（可直接复用）

修法统一为：**内存缓存 → 本机持久缓存（`backend/data/`）→ 才回源**。
难点在"**怎么判断本机缓存还有效**"——数据在别的机器上变了，本进程不知道。
四种手段按场景选：

| # | 手段 | 适用场景 | 本项目用例 |
|---|---|---|---|
| ① | **`sync_meta` 版本号** | **写入方能配合**打版本号（写完后 `sync_meta.touch(key)`） | `mainflow_history`（`load_flow_map`） |
| ② | **数据自身的 `saved_at`** | 写入方在**别的机器**（Actions / 自建服务器），**无法要求它配合** | `market_snapshot`（`store.load_market_snapshot`） |
| ③ | **系统视图写入计数指纹** | 表**没有可靠的时间戳列**可依赖 | `pg_stat_user_tables` 的 `n_tup_ins+n_tup_upd+n_tup_del`（`_ind_dist_fingerprint`） |
| ④ | **兜底 TTL** | 上面三种都拿不到时的最后一道 | 各 12h ~ 7 天，配合前三者使用 |

**两个实现要点（都踩过）：**

- **用「只取版本列」的极轻探测代替整行读**。例：`market_snapshot` 的版本探测是
  `SELECT saved_at FROM market_snapshot WHERE key='latest'`（单行几十字节），
  命中就用本机 → **零大流量**。若直接读整行，探测本身就等于没优化。
- **本机缓存路径要跟项目约定对齐**。约定见 `app/research_cache.py:32-34`：
  数据落 **`backend/data/`**。注意 `app/mainforce/flow.py` 在 `app/mainforce/` 下，
  要**上溯 3 层**（`mainforce → app → backend`）才是 backend —— 少算一层会落到 `backend/app/data/`。
  `backend/.gitignore` 的 `data/` 会覆盖这类文件，**不会误提交**。

**另有一种"数据源分层"（非版本门控）**，见 `app/research_cache.py`：

```
本机 SQLite  →  本地数据包（pack，零 egress）  →  才回源 DB  →  回填本机
```

`ohlc_for(codes)` 就是这个模式（按需读、不进全量内存）。**回填时刻意不改
`ohlc_updated_at`** —— 补几行不该让全量缓存被误判成"刚更新"而延后重建。

---

## 四、一个反直觉的坑：常驻进程的**短 TTL**

`market_snapshot` 原来有两套**进程内**缓存：`flow` 侧 30min、`store` 侧 **5min**。
后者在常驻进程里意味着：

> **300s TTL → 最坏 288 次/天 × 336KB ≈ 97MB/天**，而这份数据**每天只在 15:10 落库一次**。

**教训：TTL 长短要和数据的更新频率匹配。** 「每 5 分钟刷新一次」对日频数据是纯浪费；
短 TTL 只是把"每进程一次"变成了"每进程多次"，比长 TTL 更糟。

---

## 五、三个容易误判的对象

| 看到的东西 | 真相 | 动作 |
|---|---|---|
| `SELECT name FROM pg_timezone_names`（701 次 / 838,396 行） | **非本项目** —— Supabase 平台 exporter 的语句，`egress_probe.py` 的 `_NOISE` 已列明 | **不改**（差点白改一场） |
| `SELECT * FROM pgbouncer.get_auth($1)`（29807 次） | pgbouncer 连接认证，返回 **1 行 1 列**，不是数据集 | 忽略 |
| 表级 `seq_tup_read` = 3 亿 | 服务器**内部**扫描行数（含被 WHERE 丢弃的），**不出网** | 别看它，看 `rows` |

---

## 六、已治理清单

| # | 目标 | 修前 | 修后 | 手段 | 文件 |
|---|---|---|---|---|---|
| 1 | `mainflow_history` 整表读 | 8.7MB × 每次进程重启（单日可达 592MB） | ~9MB/天 | ① `sync_meta` 版本 | `app/mainforce/flow.py` |
| 2 | `market_snapshot` 整行读 | ~8MB/天（常驻进程最坏 97MB/天） | ~0.4MB/天 | ② `saved_at` | `app/flash/store.py`、`app/mainforce/flow.py` |
| 3 | `backtest_prices` 批量读 | 42MB/天 | **0** | 分层：本机→pack→回源 | `app/research_cache.py` + 3 个研究脚本 |
| 4 | `stock_industry JOIN stock_finance` | ~1MB × 每次进程重启 | ~0（仅指纹探测） | ③ 写入计数指纹 | `app/scoring/engine.py` |

**验证方式统一为"两个新进程对比 `calls` 增量"**：

```
第一次（新进程）: calls +1（回源一次 + 落本机）
第二次（新进程）: calls +0  ★ 且结果与后端逐项一致
```

其中第 2、3、4 项还额外验证了**结果口径不变**（第 4 项的 `pe`/`pb` 分布仍来自实时行情，
只缓存了低频的 DB 源行）。相关提交：`bd03f36`。

**⚠️ 一个必须知道的正确性约束（第 4 项差点做错）**：
`_industry_dist()` 的 `dist` 里 **`pe`/`pb` 来自「腾讯实时行情缓存」（每天变）**，
只有 `debt_ratio`/`gross_margin`/行业映射来自低频 DB 数据 ⇒ **不能整体长缓存**。
所以只缓存 DB 源行，重建时照旧叠加实时 PE/PB。

---

## 七、待办 / 观察项

- [ ] **观察点（最优先）**：下一个完整交易日后看 Supabase egress 曲线。
      预期从 ~140MB/天 降到 **~20MB/天** 量级。若没降，用 §一 的方法重新定位。
- [x] **~~观察点~~ 2026-09-25 实测：远未达预期**（用户报「今日 332MB」后的复查）
      · 口径：`egress_probe.py snapshot --tag today` + `top`，`stats_reset = 2026-09-11 21:27`
        ⇒ **14 天窗口**（⚠️ 这正是 §一 警告的"把 N 天累计当一天"）。
      · 探针口径累计读 ≈ **3.82 GB / 14 天** ⇒ **~273 MB/天**；面板同日显示 332MB ⇒ 量级一致
        ⇒ **不是某天突增，是长期水平**。
      · §六「已治理」的 4 处里，**至少 3 处仍在大量出网**（见下表）⇒ 需逐个复查。
- **2026-09-25 实测 Top（14 天累计，探针口径；完整 SQL 已核）**
  | 语句 | 次数 | 行数 | 估计 | 定位（已核到函数） |
  |---|---|---|---|---|
  | `SELECT code,date,main_net,… FROM mainflow_history ORDER BY code,date` | 125 | 11.69M | ≤1249MB | `mainforce/flow.load_flow_map`（三层缓存**已实现**，本机 `data/flow-map.db` 存在；但仍 9 次/天回源）|
  | `SELECT code,report_date,ind_json,bal_json,cf_json FROM stock_finance_zz` | 39 | 203K | ≤392MB | **`contradictions/l3_scanner._load_reports()`** —— **完全无缓存**，每次整表读 ≈10MB |
  | `SELECT code FROM stock_industry` | 160 | 949K | ≤124MB | **`mainline._latest_unknown()`** —— 只需判 Top50 的行业，却读全表 5932 行 |
  | `stock_industry JOIN stock_finance` | 69 | 1.16M | ≤152MB | `scoring/engine._industry_dist`（有指纹门控 + 本机 `industry_dist_src.json`，~5 次/天）|
  | `backtest_prices` 大 IN（**全历史 OHLCV**） | ~105 | ~4.5M | ~500MB | `backtest/strategies._load_prices_map`（回测路径；未走 pack 时走 DB）|
  | `backtest_prices` 逐只 `code=$1 AND date<=$2 ORDER BY date` | 8848 | 726K | ≤58MB | **未定位**（单次仅 82 行 ≈ 8KB ⇒ ≈5MB/天，**低优先**）|
- **待修 → 2026-09-25 当天已修 ①②，③ 有方案未动，④ 查明是配置问题**
  - ✅ **① `l3_scanner._load_reports` 加三层缓存**（进程内存 10min → 本机 gzip 12h → 回源；
    指纹 = `(行数, MAX(updated_at))` 单行查询）。实测：首次 5386 行 4.9s，落盘 **1.4MB gzip**；
    模拟新进程第二次 **0.38s / 零回源**（快 13 倍）。打桩 9/9（指纹变化 ⇒ 回源、
    指纹不可用 ⇒ 回源不读无判据缓存、缓存损坏 ⇒ 回源不崩）。**预计省 ~28MB/天**。
  - ✅ **② `mainline._latest_unknown` 改 `code IN (Top50)`**（原读全表 5932 行 ⇒ ≤50 行，语义等价）。
    **预计省 ~9MB/天**。
  - ✅ **④ 查明：日批早就设了 `DATA_SOURCE: pack`**（`daily-batch.yml`）⇒ 那 ~500MB 的
    `backtest_prices` 大 IN **不是日批**，而是 **Render（默认 db）+ 本地不带 pack 的脚本**。
    ⇒ 已在 `render.yaml` 变量清单补上 `DATA_SOURCE=pack` 并写明原因（⚠️ 该文件只是文档，
    **需在 Render 控制台手动加**）；本地脚本一律先设 `DATA_SOURCE=local`（读本机包，零流量；
    ⚠️ `local` 模式包陈旧会静默回退 DB ⇒ 先 `python scripts/sync_local.py --force`）。
  - ⏳ **③ `load_flow_map` 回源（~76MB/天，最大头）——需改数据包，未动**
    现状：三层缓存（内存 → 本机 SQLite → 回源）**已实现且正常**（本机 `data/flow-map.db` 在，
    带 `sync_meta` 版本门控）。仍回源 9 次/天的原因是**环境**：
    · **Actions** 每次是全新 runner ⇒ 本机磁盘缓存天然不存在（日批 1~2 次/天）→ 这部分已由
      `DATA_SOURCE=pack` 覆盖不了（**pack 里没有 mainflow_history 这张表**）；
    · **Render** 免费实例文件系统临时 + 冷启动 ⇒ 每次重启丢缓存 ⇒ 整表 8.4MB；
    · 本地新进程：版本一变（日批回填后 touch）就回源一次。
    ⚠️ **不能靠"改 SQL 只读近期"解决**：该表本就只 ~146 个交易日/只（10.9 万行≈7.8MB），
    没有可裁的冗余。
    ⇒ **唯一根治 = 把 `mainflow_history` 并入数据包**（3 步）：① `generate_backend_pack.py` 加
    一张 `mainflow` 表（+~2-3MB gz）；② `pack_source` 加 `get_flow()`；③ `flow.load_flow_map`
    在 `pack_source.enabled()` 时优先读包。
    **未直接做的原因（诚实说明）**：改的是**每日数据链**（Actions 产包 → Pages → 各消费方），
    验证需真跑一次产包（读全表 K 线，本地跑会花大流量）⇒ 收益大但验证成本高且影响面广，
    应由用户明确批准后再动。
- **本地开发纪律（§二 的落地补充）**：改**后端**代码时**别开 `--reload`**（每改一次 = 一次重启 =
  整份重读大表）；不用本地后端时**关掉它**（进程在跑就有持续读取）；跑研究脚本前先设
  `DATA_SOURCE=pack`（本机数据包，零 egress）。
- [ ] `app/routers/performance.py:96` —— per-`(day, code)` 循环查询 ~600 次往返/次调用
      （`全项目审查_..._20260913.md` 第 21 条，**尚未实测**，可合并为 `date IN`）。
- [ ] `app/backtest/data.py:172`、`app/backtest/strategies.py:172` —— 两份价格缓存
      （30min / 6h）无「回填即失效」，晚间撮合最长读到 16:10 前的旧价
      （同报告第 20 条，**功能性**问题，不纯是 egress）。
- [ ] `app/flash/store.py` 的 `/api/flash/events` 整取 300 条含正文 —— 报告第 22 条建议列裁剪，
      但**实测只返回 13 行**（6 天），收益极小；且需前端联动。**低优先。**
- [x] ~~`indicator_cache.py` 逐股回退未全量替换~~ —— 实测 `WHERE code=$1` 仅 **101 次 / 27 行**（6 天），
      **不值得做**；`get_cached_technical_batch`（逐股版）已无调用方，属死代码。

---

## 八、方法论

- **报告会过期。** `全项目审查_逻辑冲突与优化建议_20260913.md` 的 P2-㉒㉓ 是按 9-13 的代码写的；
  9-18 实测两者都已被此前的治理顺手解决或本来就极小。**先量，再决定动不动手。**
- **看完整 SQL，不要看截断的。** 一次定位偏差（把几百只的批看成 56 只）会让源头完全找错。
- **"每进程一次"是本项目的头号放大器。** 排查时先问：这个缓存是进程内的吗？数据多久变一次？
- **本地/线上共用额度，但不会互相唤醒。** 两者是独立进程，各跑各的 loop；
  `schedule_state` 是**互斥**机制（谁先跑谁标记、另一个跳过）⇒ 本地跑了某些任务，**线上反而更省**。
  唯一真实的跨进程影响是 `sync_meta.touch()` 会让线上缓存失效并重拉一次（设计意图）。
- **改动优先做成"低成本、可复用"的**：`research_cache.ohlc_for()` 这种按需接口，
  比"每个脚本各自加缓存"更能防止问题复发。
