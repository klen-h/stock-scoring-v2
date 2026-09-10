#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
# 数据库每日备份：pg_dump → 本地轮转（保留 14 份）
#
# crontab（服务器上）：每天 03:00 备份
#   0 3 * * * bash /opt/stock-scoring-v2/deploy/backup_db.sh >> /opt/stock-scoring-v2/deploy/backup.log 2>&1
#
# 2026-09-11：容器名改为自动探测（原来写死 deploy-postgres-1，compose 起的是
#   stock-postgres），并支持 $1 显式指定容器。
# ════════════════════════════════════════════════════════════════════
set -euo pipefail

CONTAINER="${1:-$(docker ps --format '{{.Names}}' | grep -i postgres | head -1 || true)}"
if [ -z "$CONTAINER" ]; then
  echo "[backup] 没有运行中的 Postgres 容器，退出"
  exit 1
fi

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/backend/.env"
DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
DB="${DB:-stockapp}"
USER="${USER:-stockapp}"

KEEP=14
DIR="$(cd "$(dirname "$0")" && pwd)/backups"
STAMP="$(date +%Y%m%d_%H%M)"
mkdir -p "$DIR"

OUT="$DIR/stockapp_${STAMP}.dump"
echo "[backup] 导出中（容器=$CONTAINER 库=$DB）..."
docker exec "$CONTAINER" pg_dump -U "$USER" -d "$DB" --format=custom > "$OUT"

# 只留最近 KEEP 份
ls -1t "$DIR"/stockapp_*.dump | tail -n +$((KEEP + 1)) | xargs -r rm --

echo "[backup] 完成: $OUT ($(du -h "$OUT" | cut -f1))"
