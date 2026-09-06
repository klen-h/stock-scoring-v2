#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# 数据库每日备份：pg_dump → 本地轮转（保留 14 份）
# crontab（服务器上）：每天 03:00 备份
#   0 3 * * * bash /opt/stock-scoring-v2/deploy/backup_db.sh >> /opt/stock-scoring-v2/deploy/backup.log 2>&1
# ════════════════════════════════════════════════════════════════
set -euo pipefail

CONTAINER="${1:-deploy-postgres-1}"
KEEP=14
DIR="$(cd "$(dirname "$0")" && pwd)/backups"
STAMP="$(date +%Y%m%d_%H%M)"

mkdir -p "$DIR"

OUT="$DIR/stockapp_${STAMP}.dump"
echo "[backup] 导出中..."
docker exec "$CONTAINER" pg_dump -U stockapp -d stockapp --format=custom > "$OUT"

# 只留最近 KEEP 份
ls -1t "$DIR"/stockapp_*.dump | tail -n +$((KEEP + 1)) | xargs -r rm --

echo "[backup] 完成: $OUT ($(du -h "$OUT" | cut -f1))"
