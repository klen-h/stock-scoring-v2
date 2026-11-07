---
name: supabase账号迁移
description: 把本项目数据库从一个 Supabase 项目/账号迁移到另一个时使用（配额耗尽换号、搬库、换区域）。覆盖 ref 核对、结构/数据迁移、大 JSON 表断线兜底、三处连接串切换、逐表验收与回滚。
---

# Skill：Supabase 换号迁移

> 背景（2026-09-12 实测）：旧账号 egress 16.24GB 打爆免费档 5GB → 项目被 402 限制。
> 决策换新账号，用 `pg_dump -Fc` + `pg_restore` 迁完 46 张表（106MB），
> 大 JSON 表（`kline_cache` / `indicator_cache`）上一轮稳定断线，靠
> `scripts/migrate_big_tables.py` 兜底。本 skill 固化流程与护栏。

## 触发条件

- 需要把数据库迁到**另一个 Supabase 账号/项目**（换号、配额耗尽、换区域）。
- 需要把 Supabase 迁到自建 Postgres（见 `deploy/migrate_supabase.sh`，那条路本 skill 只做参考）。

## 铁律（违反 = 迁错库，比不迁更糟）

1. **先确认 ref 谁是新、谁是旧，再碰任何连接串。**
   2026-09-12 用户给的"新项目直连串"其实是**旧项目**（`opzpugbgabwolcvmfkky`，
   与 `backend/.env` 里一模一样）。若照做，等于把库往自己身上导。
   核对方法见步骤 0，**不要凭记忆或对话里的一句话判断**。
2. **应用运行时用 Pooler（6543），`pg_dump`/`pg_restore` 必须用 Session 模式（5432）。**
   - 运行时：`postgres.<ref>@aws-0-<region>.pooler.supabase.com:6543`
   - dump/restore：`...pooler.supabase.com:5432`（或 `db.<ref>.supabase.co:5432`）
   - 6543 是事务池化，**不支持 pg_dump 所需的完整目录查询**。
   - `db.<ref>.supabase.co` 可能是 **IPv6-only**（只有 AAAA 记录）；服务器无 IPv6 时
     解析直接失败 ⇒ 优先用 `pooler:5432`，它同样支持 dump 且走 IPv4。
3. **`pg_dump` 大版本必须 ≥ 源库版本。** Supabase 现为 PG 17.6，
   用 `postgres:16-alpine` 会直接 `aborting because of server version mismatch`。
   统一用 `postgres:17-alpine` 容器跑，服务器不需要装客户端。
4. **Supabase CLI（`link` / `migration new` / `db push`）只管表结构，不搬数据。**
   数据必须走 `pg_dump` / `pg_restore` / 兜底脚本。
5. **目标库先建 `extensions` schema 再 restore。**
   Supabase dump 里有 `CREATE EXTENSION ... WITH SCHEMA extensions`，
   目标库没有该 schema 会报 `schema "extensions" does not exist`。
6. **`pg_restore` 不要加 `--exit-on-error`。**
   平台对象（`pgrst_ddl_watch` 事件触发器、`grant_pg_net_access`、
   `pg_stat_statements` 等）在别处必然失败，但它们与业务数据无关；
   真正的验收看步骤 6 的行数比对。
7. **验收用 `COUNT(*)` + `MAX(date)`，不要用 `pg_stat_statements` 的累计返回行数当表大小。**
   那个是累计值（`stock_finance_zz` 21 次 × 1991 行 = 41811，而表只有 1991 行）。

## 步骤

### 0. 前置核对（不写一行代码，先排除最贵的错误）

```bash
# 旧项目 ref（正在用的）
grep -E '^DATABASE_URL' backend/.env

# 新项目 ref（从 Supabase 控制台 → Project Settings → General 页看 Reference ID）
# 两者必须不同；相同 = 你拿到的还是旧项目，停下来问清楚

# 容量体检（决定"能不能塞进新账号免费档"）
python scripts/probe_db.py     # 容量 + 建连 + 单次查询耗时 + 结论
```

**容量要提前算清**：2026-11-04 实测 397.8MB / 免费档 500MB（80%），
其中 `backtest_prices` 124.9MB、`mainflow_history` 78.6MB、`kline_cache` 41.7MB。
直接搬过去仍然贴着上限 ⇒ 迁移前先决定：清理历史、还是新账号开付费档。

```sql
-- 各表体积（在源库跑）
SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 20;
```

### 1. 新项目准备

1. 新建项目，**区域尽量与服务器/用户同区**（国内→新加坡；跨洋 RTT 会让"一页查 5 次库"变成秒级）。
2. 控制台 → Connect，取两串：
   - **Session 模式（5432）**：给 dump/restore 和 DDL
   - **Transaction 模式（6543）**：给应用运行时（`DATABASE_URL`）
3. 密码不要贴进聊天记录；若已贴过，迁完立刻轮换。

### 2. 结构迁移

先只搬结构，确认没有 DDL 冲突，再上数据（分开跑，失败时能分清是哪一步）。

```bash
SRC="postgresql://postgres.<旧ref>:<pwd>@aws-0-<region>.pooler.supabase.com:5432/postgres"
DST="postgresql://postgres.<新ref>:<pwd>@aws-0-<region>.pooler.supabase.com:5432/postgres"

# 结构导出（排除 Supabase 平台自带 schema）
docker run --rm postgres:17-alpine pg_dump "$SRC" \
  --schema-only --no-owner --no-privileges \
  --exclude-schema='storage' --exclude-schema='auth' \
  --exclude-schema='extensions' --exclude-schema='graphql' \
  --exclude-schema='realtime' --exclude-schema='supabase_migrations' \
  --exclude-schema='vault' --exclude-schema='pgbouncer' \
  -f /tmp/schema.sql
```

目标库先建 `extensions` schema（铁律 5），再导入：

```bash
docker run --rm -i postgres:17-alpine psql "$DST" -v ON_ERROR_STOP=1 \
  -c "CREATE SCHEMA IF NOT EXISTS extensions;"
docker run --rm -i -v /tmp:/tmp postgres:17-alpine psql "$DST" -f /tmp/schema.sql
```

### 3. 数据迁移

```bash
# 导出（custom 格式：可选择性恢复、中断可重跑）
docker run --rm postgres:17-alpine pg_dump "$SRC" \
  --format=custom --no-owner --no-privileges \
  --exclude-schema='storage' --exclude-schema='auth' \
  --exclude-schema='extensions' --exclude-schema='graphql' \
  --exclude-schema='realtime' --exclude-schema='supabase_migrations' \
  --exclude-schema='vault' --exclude-schema='pgbouncer' \
  -f /tmp/migrate.dump

# 恢复（不要加 --exit-on-error，见铁律 6）
docker run --rm -v /tmp:/tmp postgres:17-alpine pg_restore \
  --no-owner --no-privileges --dbname="$DST" /tmp/migrate.dump 2>&1 | tail -20
```

**大表（单行几十上百 KB 的 JSON/text）如果 restore 反复断线**，走步骤 4。

### 4. 大 JSON 表兜底

```bash
# 只读探测：连通性 / 行数 / 最大行尺寸（安全，不写任何数据）
export MIGRATE_SOURCE_URL="$SRC"
export MIGRATE_TARGET_URL="$DST"
python scripts/migrate_big_tables.py --probe

# 正式迁移（⚠️ 不要与 pg_restore 并行跑，会互相拖慢/撞锁）
python scripts/migrate_big_tables.py --tables kline_cache,indicator_cache
```

要点：
- 源连接被强制只读（`default_transaction_read_only=on`），不会写坏源库。
- keyset 分页 + 每批一个事务 + 断点续传（`backend/data/migrate_big_tables_state.json`），
  **断了重跑同一条命令即可**，从最后成功的主键继续。
- 源表没有主键时用 `--pk code`（本项目两张表都是 `code`），脚本会自动给目标建唯一索引
  以支撑 `ON CONFLICT DO UPDATE` 幂等。
- 试跑：`--max-rows 100`；调参：`--batch 20 --rows-per-stmt 5`。

### 5. 切换连接串（三处，缺一处就不算迁完）

| 位置 | 变量 | 说明 |
|---|---|---|
| 服务器 `backend/.env` | `DATABASE_URL` | 后端进程读它，改完 `systemctl restart stock-scoring` |
| GitHub Secrets | `DATABASE_URL` | 日批/指标 Actions 用它（`daily-batch.yml` / `indicator-refresh.yml`）|
| 本地 `backend/.env` | `DATABASE_URL` | 本地跑脚本时用 |

**前端不需要改**：本项目没有 `supabase-js` 直连数据库，所有数据包走 GitHub Pages。

**改 Secret 之后必须确认**：GitHub Secret 更新不会自动重跑历史 workflow，
需要等下一次定时触发，或手动 `workflow_dispatch` 验证一次写库。

### 6. 验收（逐表比对，不是"看起来有数据"）

```bash
CHECKS="SELECT 'backtest_prices', COUNT(*) FROM backtest_prices
  UNION ALL SELECT 'kline_cache', COUNT(*) FROM kline_cache
  UNION ALL SELECT 'indicator_cache', COUNT(*) FROM indicator_cache
  UNION ALL SELECT 'ranking_history', COUNT(*) FROM ranking_history
  UNION ALL SELECT 'mainflow_history', COUNT(*) FROM mainflow_history
  UNION ALL SELECT 'mainforce_state', COUNT(*) FROM mainforce_state
  UNION ALL SELECT 'user_watchlist', COUNT(*) FROM user_watchlist
  UNION ALL SELECT 'user_portfolio', COUNT(*) FROM user_portfolio
  UNION ALL SELECT 'paper_positions', COUNT(*) FROM paper_positions
  UNION ALL SELECT 'flash_news', COUNT(*) FROM flash_news
  ORDER BY 1;"

docker run --rm postgres:17-alpine psql "$SRC" -t -A -F' | ' -c "$CHECKS" > /tmp/src.txt
docker run --rm postgres:17-alpine psql "$DST" -t -A -F' | ' -c "$CHECKS" > /tmp/dst.txt
diff -y /tmp/src.txt /tmp/dst.txt || true
```

**三道关，全过才算迁完**：

1. **表数量**：目标库 `public` schema 的表数 == 源库（2026-09-12 那轮是 46 张）。
2. **行数**：上面每个表两边一致（大表允许少量差异，但要看得出原因）。
3. **数据截止日**：关键表 `MAX(date)` 必须等于**最近已完成交易日**：
   ```sql
   SELECT 'ranking', MAX(date) FROM ranking_history
   UNION ALL SELECT 'backtest', MAX(date) FROM backtest_prices
   UNION ALL SELECT 'mainflow', MAX(date) FROM mainflow_history
   UNION ALL SELECT 'news', MAX(snap_date) FROM news_history;
   ```
   这比总大小可靠 —— 旧库因为死元组会比新库"更大"，106MB vs 198MB 不代表缺数据。

**最后冒烟**（改完 `.env` 并重启后）：

```bash
curl -sS http://127.0.0.1:8000/api/health
curl -sS "http://127.0.0.1:8000/api/score/batch/top?count=5" | head -c 300
```

能返回榜单 + 因子齐全 = 库通了。

### 7. 观察期与旧库归档

- **旧项目保留只读 1 周**，不要当天删。发现缺表/缺数还能回去补。
- 观察项：日批是否正常写库（次日看 `ranking_history` 有没有新日期）、
  页面是否报错、egress 用量是否落回常态（本项目治理后 2~3GB/月）。
- 观察期结束再删旧项目，并轮换所有暴露过的密码。

## 回滚

改回 `.env` 与 GitHub Secret 的旧连接串 → 重启 → 冒烟。
**不要 `pg_restore` 反向覆盖**（旧库在迁移期间可能仍被写入，反向覆盖会丢新数据）。

## 报错对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `server version mismatch` | pg_dump 客户端版本低于源库 | 换成 `postgres:17-alpine` |
| `schema "extensions" does not exist` | 目标库没建 extensions | `CREATE SCHEMA IF NOT EXISTS extensions` |
| restore 到一半 `Connection reset` | 大 JSON 行 + 长连接被掐 | 走 `migrate_big_tables.py` |
| `no unique or exclusion constraint` | 源表无主键，无法 `ON CONFLICT` | `--pk code`（脚本自动建唯一索引） |
| 解析失败 / `Network is unreachable` | `db.<ref>` 是 IPv6-only | 改用 `pooler...:5432` |
| 恢复后页面全 0 行 | restore 中途失败但没报 | 看步骤 6 行数比对，重点查断线那次 |
| 迁移后 Actions 写库失败 | Secret 没改或没生效 | 核对 GitHub Secrets + 手动触发一次 |

## 相关文件

- `scripts/migrate_big_tables.py` —— 大 JSON 表分页迁移兜底（本 skill 步骤 4 使用）
- `scripts/probe_db.py` —— 容量/延迟体检（步骤 0）
- `deploy/migrate_supabase.sh` —— Supabase → 自建 Postgres 的完整脚本，注释里含大量同类坑
