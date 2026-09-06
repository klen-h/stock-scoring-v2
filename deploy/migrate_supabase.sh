#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# Supabase → 同机 Postgres 一次性迁移脚本
# 在【阿里云服务器】上执行（需要 pg_dump/pg_restore 客户端 + SUPABASE 直连串）
# ════════════════════════════════════════════════════════════════
#
# 用法：
#   SUPABASE_DUMP_URL="postgresql://postgres:密码@db.xxx.supabase.co:5432/postgres" \
#   bash migrate_supabase.sh
#
# ★ 连接串注意：pg_dump 必须用 Supabase 的【直连串】（db.xxx.supabase.co:5432，
#   Session 模式），不能用 6543 事务池化串（pooler 不支持 pg_dump 所需的
#   完整目录查询）。直连串在 Supabase 控制台 → Connect → Direct connection。
# ════════════════════════════════════════════════════════════════
set -euo pipefail

SUPABASE_URL="${SUPABASE_DUMP_URL:?请设置 SUPABASE_DUMP_URL（Supabase 直连串）}"
LOCAL_CONTAINER="deploy-postgres-1"       # compose 项目前缀可能不同，docker ps 核对
LOCAL_DB="stockapp"
LOCAL_USER="stockapp"

echo "── 1/3 从 Supabase 导出（-Fc 自定义格式，含大表 backtest_prices ~40MB）"
pg_dump "${SUPABASE_URL}" \
  --format=custom \
  --no-owner --no-privileges \
  --exclude-schema='storage' \
  --exclude-schema='auth' \
  --exclude-schema='extensions' \
  --file /tmp/supabase.dump
echo "   导出完成: $(du -h /tmp/supabase.dump | cut -f1)"

echo "── 2/3 拷入 Postgres 容器"
docker cp /tmp/supabase.dump "${LOCAL_CONTAINER}:/tmp/supabase.dump"

echo "── 3/3 恢复到本地库（清掉同名的自动创建对象冲突由 --clean 处理）"
docker exec "${LOCAL_CONTAINER}" psql -U "${LOCAL_USER}" -d "${LOCAL_DB}" \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
docker exec "${LOCAL_CONTAINER}" pg_restore \
  -U "${LOCAL_USER}" -d "${LOCAL_DB}" \
  --no-owner --no-privileges \
  /tmp/supabase.dump
docker exec "${LOCAL_CONTAINER}" rm /tmp/supabase.dump
rm -f /tmp/supabase.dump

echo "── 核对（行数应与 Supabase 控制台一致）"
docker exec "${LOCAL_CONTAINER}" psql -U "${LOCAL_USER}" -d "${LOCAL_DB}" -c "
  SELECT 'backtest_prices' t, COUNT(*) FROM backtest_prices
  UNION ALL SELECT 'kline_cache', COUNT(*) FROM kline_cache
  UNION ALL SELECT 'ranking_history', COUNT(*) FROM ranking_history
  UNION ALL SELECT 'user_watchlist', COUNT(*) FROM user_watchlist
  UNION ALL SELECT 'user_portfolio', COUNT(*) FROM user_portfolio
  UNION ALL SELECT 'paper_positions', COUNT(*) FROM paper_positions
  ORDER BY 1;"

echo "✅ 迁移完成。重启后端使连接生效：docker compose restart backend"
