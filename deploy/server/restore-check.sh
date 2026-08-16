#!/usr/bin/env bash
set -euo pipefail

RELEASE_DIR="$1"
REMOTE_ROOT="$2"
BACKUP_FILE="${3:-}"
BACKUP_ROOT="$REMOTE_ROOT/backups"

if [ -z "$BACKUP_FILE" ]; then
  BACKUP_FILE="$(ls -t "$BACKUP_ROOT"/backup-*.tar.gz 2>/dev/null | head -1)"
fi
if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
  echo "未找到备份包（可显式传入 backup-*.tar.gz 路径）"; exit 1
fi
echo "[restore-check] backup=$BACKUP_FILE"

WORK="$BACKUP_ROOT/_restore_check_$$"
mkdir -p "$WORK"
tar -xzf "$BACKUP_FILE" -C "$WORK"

ENV_FILE="$REMOTE_ROOT/.env.server"
PG_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
MINIO_USER="$(grep -E '^MINIO_ROOT_USER=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
MINIO_PASS="$(grep -E '^MINIO_ROOT_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
COMPOSE_PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-deepsearcher-study}"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"
TEST_DB="deepsearcher_restore_check"
TEST_BUCKET="deepsearcher-restore-check"

cd "$RELEASE_DIR"
FAIL=0

echo "[restore-check] 1/4 PostgreSQL 恢复到独立测试库 $TEST_DB"
$COMPOSE exec -T postgres psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $TEST_DB WITH (FORCE);" \
  -c "CREATE DATABASE $TEST_DB;" >/dev/null
cat "$WORK/postgres.dump" | $COMPOSE exec -T postgres pg_restore -U "$PG_USER" -d "$TEST_DB" --no-owner --no-privileges
echo "  pg_restore 完成"

echo "[restore-check] 2/4 验证业务表与行数"
TABLES="users user_sessions knowledge_bases documents ingest_jobs worker_heartbeats conversations conversation_summaries messages answer_claims citations"
for t in $TABLES; do
  cnt="$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$TEST_DB" -tAc "SELECT count(*) FROM $t" | tr -d '[:space:]')"
  echo "  table $t rows=$cnt"
  if [ -z "$cnt" ]; then FAIL=1; echo "  !! 表 $t 不存在"; fi
done
VER="$($COMPOSE exec -T postgres psql -U "$PG_USER" -d "$TEST_DB" -tAc "SELECT version_num FROM alembic_version" | tr -d '[:space:]')"
echo "  alembic_version=$VER"
[ -z "$VER" ] && { FAIL=1; echo "  !! alembic_version 缺失"; }

echo "[restore-check] 3/4 MinIO 恢复到测试 bucket $TEST_BUCKET"
if [ -d "$WORK/minio-objects" ]; then
  docker run --rm \
    --network "${COMPOSE_PROJECT}_deepsearcher" \
    --entrypoint /bin/sh \
    -e "MINIO_USER=$MINIO_USER" \
    -e "MINIO_PASS=$MINIO_PASS" \
    -e "TEST_BUCKET=$TEST_BUCKET" \
    -v "$WORK:/work" \
    minio/mc:RELEASE.2025-04-16T18-13-26Z \
    -ec 'mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null && mc mb --ignore-existing "local/$TEST_BUCKET" && mc mirror --overwrite /work/minio-objects "local/$TEST_BUCKET"'
  SRC_CNT="$(find "$WORK/minio-objects" -type f | wc -l | tr -d '[:space:]')"
  DST_CNT="$(docker run --rm --network "${COMPOSE_PROJECT}_deepsearcher" --entrypoint /bin/sh -e "MINIO_USER=$MINIO_USER" -e "MINIO_PASS=$MINIO_PASS" -e "TEST_BUCKET=$TEST_BUCKET" minio/mc:RELEASE.2025-04-16T18-13-26Z -ec 'mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null && mc find "local/$TEST_BUCKET" --print=name | wc -l' 2>/dev/null | tr -d '[:space:]')"
  echo "  source_objects=$SRC_CNT restored_objects=$DST_CNT"
  if [ "$SRC_CNT" != "$DST_CNT" ]; then FAIL=1; echo "  !! 对象数量不一致"; fi
else
  echo "  备份中无 minio-objects 目录，跳过 MinIO 恢复检查"
fi

echo "[restore-check] 4/4 Milvus 持久卷与重建路径确认"
docker volume ls --format '{{.Name}}' | grep -q "${COMPOSE_PROJECT}_milvus-data" && echo "  milvus-data 卷存在" || echo "  !! milvus-data 卷未找到（若向量数据丢失，需按文档从 PostgreSQL+MinIO 重建）"

echo "[restore-check] 清理测试库与测试 bucket"
$COMPOSE exec -T postgres psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $TEST_DB WITH (FORCE);" >/dev/null
docker run --rm --network "${COMPOSE_PROJECT}_deepsearcher" --entrypoint /bin/sh -e "MINIO_USER=$MINIO_USER" -e "MINIO_PASS=$MINIO_PASS" -e "TEST_BUCKET=$TEST_BUCKET" minio/mc:RELEASE.2025-04-16T18-13-26Z -ec 'mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null && mc rb --force "local/$TEST_BUCKET"' >/dev/null 2>&1 || true
rm -rf "$WORK"

if [ "$FAIL" = "1" ]; then
  echo "[restore-check] FAIL"; exit 1
fi
echo "[restore-check] PASS"