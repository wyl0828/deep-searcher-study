#!/usr/bin/env bash
set -euo pipefail

RELEASE_DIR="$1"
REMOTE_ROOT="$2"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="$REMOTE_ROOT/backups"
BK="$BACKUP_ROOT/$TS"
mkdir -p "$BK"
cd "$RELEASE_DIR"

ENV_FILE="$REMOTE_ROOT/.env.server"
PG_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
PG_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
MINIO_USER="$(grep -E '^MINIO_ROOT_USER=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
MINIO_PASS="$(grep -E '^MINIO_ROOT_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
BUCKET="$(grep -E '^DEEPSEARCHER_S3_BUCKET=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
COMPOSE_PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-deepsearcher-study}"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"

echo "[backup] TS=$TS"

echo "[backup] 1/4 PostgreSQL pg_dump -> $BK/postgres.dump"
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -F c > "$BK/postgres.dump"

echo "[backup] 2/4 MinIO mirror bucket=$BUCKET"
mkdir -p "$BK/minio-objects"
docker run --rm \
  --network "${COMPOSE_PROJECT}_deepsearcher" \
  --entrypoint /bin/sh \
  -e "MINIO_USER=$MINIO_USER" \
  -e "MINIO_PASS=$MINIO_PASS" \
  -e "BUCKET=$BUCKET" \
  -v "$BK:/backup" \
  minio/mc:RELEASE.2025-04-16T18-13-26Z \
  -ec 'mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null && mc mirror --overwrite "local/$BUCKET" /backup/minio-objects' \
  || { echo "[backup] minio mirror 失败（数据库备份不受影响，请人工检查 MinIO）"; }

echo "[backup] 3/4 配置快照"
mkdir -p "$BK/config"
cp -a "$ENV_FILE" "$BK/config/.env.server"
cp -a compose.yaml compose.server.yaml compose.local.yaml "$BK/config/" 2>/dev/null || true
cp -a deploy/nginx "$BK/config/" 2>/dev/null || true
[ -f /etc/nginx/sites-available/deepsearcher ] && cp -a /etc/nginx/sites-available/deepsearcher "$BK/config/nginx-deepsearcher.conf" || true
[ -f /etc/nginx/nginx.conf ] && cp -a /etc/nginx/nginx.conf "$BK/config/nginx.conf" || true
(cd "$RELEASE_DIR" && git describe --tags --always 2>/dev/null || echo unknown) > "$BK/config/VERSION.txt"
$COMPOSE ps --format '{{.Name}}\t{{.Image}}\t{{.Status}}' > "$BK/config/containers.txt" 2>/dev/null || true
docker volume ls --format '{{.Name}}' | grep "$COMPOSE_PROJECT" > "$BK/config/volumes.txt" 2>/dev/null || true

echo "[backup] 4/4 打包"
cd "$BACKUP_ROOT"
tar -czf "backup-$TS.tar.gz" -C "$TS" .
rm -rf "$BK"
ls -lh "backup-$TS.tar.gz"
echo "BACKUP_FILE=$BACKUP_ROOT/backup-$TS.tar.gz"