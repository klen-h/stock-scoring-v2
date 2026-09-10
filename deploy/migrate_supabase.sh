#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
# Supabase → 服务器本机 Postgres 一次性迁移（腾讯云 Lighthouse）
#
# 用法（在服务器项目根目录执行）：
#   SUPABASE_DUMP_URL="postgresql://postgres:密码@db.xxx.supabase.co:5432/postgres" \
#   bash deploy/migrate_supabase.sh
#
# ★ 必须用 Supabase 的【直连串】（db.xxx.supabase.co:5432，Session 模式）：
#   6543 事务池化串不支持 pg_dump 所需的完整目录查询。
#   控制台 → Connect → Direct connection 里取。
#
# 设计要点（2026-09-11 重写）：
#   · 服务器上**不需要装 postgresql-client**：pg_dump/pg_restore 全部用
#     postgres:16-alpine 一次性容器执行（host 只需 docker）
#   · 导出/恢复都走文件（custom 格式），中断可重跑；恢复前会清空目标库 public
#   · 恢复完自动比对源/目标行数（不一致会红字提示）
# ════════════════════════════════════════════════════════════════════
set -euo pipefail

SUPABASE_URL="${SUPABASE_DUMP_URL:?请设置 SUPABASE_DUMP_URL（Supabase 直连串）}"
# ★ 必须与源库版本对齐：Supabase 现为 PG 17.6，用 16 的 pg_dump 会直接
#   "aborting because of server version mismatch"（2026-09-11 实测）。目标库 17。
PG_IMG="postgres:17-alpine"
DUMP="/tmp/supabase_$(date +%Y%m%d_%H%M).dump"

# 目标库信息：从 backend/.env 读（与 compose 共用同一份配置）
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/backend/.env"
LOCAL_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
LOCAL_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
LOCAL_DB="${LOCAL_DB:-stockapp}"
LOCAL_USER="${LOCAL_USER:-stockapp}"

# 目标容器（compose 起的是 stock-postgres，兼容 deploy-postgres-1 之类）
CONTAINER="$(docker ps --format '{{.Names}}' | grep -i postgres | head -1 || true)"
if [ -z "$CONTAINER" ]; then
  echo "✗ 没有运行中的 Postgres 容器。先起库："
  echo "    docker compose -f deploy/docker-compose.prod.yml up -d postgres"
  exit 1
fi
echo "目标：容器=$CONTAINER 库=$LOCAL_DB 用户=$LOCAL_USER"

CHECKS="SELECT 'backtest_prices', COUNT(*) FROM backtest_prices
  UNION ALL SELECT 'kline_cache', COUNT(*) FROM kline_cache
  UNION ALL SELECT 'indicator_cache', COUNT(*) FROM indicator_cache
  UNION ALL SELECT 'ranking_history', COUNT(*) FROM ranking_history
  UNION ALL SELECT 'mainforce_state', COUNT(*) FROM mainforce_state
  UNION ALL SELECT 'mainflow_history', COUNT(*) FROM mainflow_history
  UNION ALL SELECT 'user_watchlist', COUNT(*) FROM user_watchlist
  UNION ALL SELECT 'user_portfolio', COUNT(*) FROM user_portfolio
  UNION ALL SELECT 'paper_positions', COUNT(*) FROM paper_positions
  UNION ALL SELECT 'flash_news', COUNT(*) FROM flash_news
  ORDER BY 1;"

echo
echo "── 1/5 源库行数（Supabase）"
docker run --rm "$PG_IMG" psql "$SUPABASE_URL" -t -A -F' | ' -c "$CHECKS" > /tmp/src_counts.txt
cat /tmp/src_counts.txt

echo
echo "── 2/5 从 Supabase 导出（-Fc，含 ~81MB 的 backtest_prices）"
# 排除 Supabase 平台自带的 schema（storage/auth/extensions 等我们不用）
docker run --rm "$PG_IMG" pg_dump "$SUPABASE_URL" \
  --format=custom --no-owner --no-privileges \
  --exclude-schema='storage' --exclude-schema='auth' \
  --exclude-schema='extensions' --exclude-schema='graphql' \
  --exclude-schema='realtime' --exclude-schema='supabase_migrations' \
  --exclude-schema='vault' --exclude-schema='pgbouncer' \
  > "$DUMP"
echo "    导出完成: $(du -h "$DUMP" | cut -f1) → $DUMP"

echo
echo "── 3/5 清空目标库 public（本机库当前数据会被覆盖）"
# ★ extensions schema 必须先建：Supabase 的 dump 里有
#   `CREATE EXTENSION ... WITH SCHEMA extensions`（pg_stat_statements 等），
#   目标库没有该 schema 会报 "schema extensions does not exist"（2026-09-11 实测）
docker exec "$CONTAINER" psql -U "$LOCAL_USER" -d "$LOCAL_DB" -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; CREATE SCHEMA IF NOT EXISTS extensions;" >/dev/null

echo "── 4/5 拷入容器并恢复"
docker cp "$DUMP" "$CONTAINER:/tmp/_migrate.dump"
# ★ 不能加 --exit-on-error：Supabase 平台自带的对象（PostgREST 的 pgrst_ddl_watch
#   事件触发器、grant_pg_net_access、pg_stat_statements 等）在自建库里必然失败，
#   但它们与业务数据无关；真正的验收看下一步行数比对。
docker exec "$CONTAINER" pg_restore -U "$LOCAL_USER" -d "$LOCAL_DB" \
  --no-owner --no-privileges /tmp/_migrate.dump 2>&1 | tail -6 \
  || true
echo "    （上面若出现 Supabase 平台对象报错属正常，看行数比对）"
docker exec "$CONTAINER" rm -f /tmp/_migrate.dump

echo
echo "── 5/5 行数比对（源 vs 目标）"
docker exec "$CONTAINER" psql -U "$LOCAL_USER" -d "$LOCAL_DB" -t -A -F' | ' -c "$CHECKS" > /tmp/dst_counts.txt
if diff -q /tmp/src_counts.txt /tmp/dst_counts.txt >/dev/null; then
  echo "✅ 全部表行数一致"; cat /tmp/dst_counts.txt
else
  echo "⚠️ 行数有差异（左=Supabase，右=本机）："
  diff -y /tmp/src_counts.txt /tmp/dst_counts.txt || true
fi

rm -f "$DUMP" /tmp/src_counts.txt /tmp/dst_counts.txt
cat <<'EOF'

下一步（切换连接串）：
  1) backend/.env 里把 DATABASE_URL 改成：
       DATABASE_URL=postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@postgres:5432/<POSTGRES_DB>
     （host 用 compose 服务名 postgres；密码用同文件里的 POSTGRES_PASSWORD）
  2) docker compose -f deploy/docker-compose.prod.yml restart backend
  3) 验证：curl -s localhost:8000/api/health && 登录 + 排行榜 + 日报页
  4) 观察 1 天后，Supabase 项目保留只读备份，再删除
EOF
