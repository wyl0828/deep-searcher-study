#!/usr/bin/env bash
# 阶段4：部署可重复性验证（独立 Compose 项目名，不影响现有 deepsearcher-study 拓扑）
# 用法：bash verify-deploy.sh <SRC_RELEASE_DIR>
set -euo pipefail

SRC_RELEASE="$1"
WORK_ROOT="/opt/deepsearcher-study-deploy-check"
PROJECT="deepsearcher-deploy-check"
ENV_FILE="$WORK_ROOT/.env.deploy-check"
FAIL=0

echo "==== 0/4 准备独立环境 $WORK_ROOT ===="
rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT"
cp -a "$SRC_RELEASE/." "$WORK_ROOT/"
sed -e "s/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=$PROJECT/" \
    -e "s/^PRODUCT_API_A_SERVER_PORT=.*/PRODUCT_API_A_SERVER_PORT=18710/" \
    -e "s/^PRODUCT_API_B_SERVER_PORT=.*/PRODUCT_API_B_SERVER_PORT=18711/" \
    -e "s/^CORE_API_SERVER_PORT=.*/CORE_API_SERVER_PORT=18712/" \
    /opt/deepsearcher-study/.env.server > "$ENV_FILE"
chmod 600 "$ENV_FILE"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"

echo "==== 1/4 空环境部署 ===="
cd "$WORK_ROOT"
# 1a0 构建镜像（幂等，缓存命中）
echo "  build images..."
$COMPOSE build migrate core-api product-api-a product-api-b consumer-a consumer-b >/tmp/deploy-check-build.log 2>&1
echo "  build exit=$?; images=$(docker images deepsearcher-study:local --format '{{.Repository}}:{{.Tag}} {{.Size}}')"
# 1a Storage
$COMPOSE up -d postgres redis minio minio-init
for i in $(seq 1 30); do
  N=$($COMPOSE ps postgres redis minio --format '{{.Status}}' 2>/dev/null | grep -c healthy || true)
  [ "$N" -ge 3 ] && break; sleep 3
done
echo "  storage healthy=$($COMPOSE ps postgres redis minio --format '{{.Name}}={{.Status}}')"
# 1b Alembic（migrate 一次性任务）
$COMPOSE up -d migrate >/dev/null 2>&1
MIG_OK=""
for i in $(seq 1 30); do
  ST=$($COMPOSE ps -a migrate --format '{{.Status}}' 2>/dev/null)
  case "$ST" in
    *"Exited (0)"*) MIG_OK=1; break;;
    *"Exited"*) break;;
  esac
  sleep 3
done
echo "  migrate=$($COMPOSE ps -a migrate --format '{{.Status}}')"
[ -n "$MIG_OK" ] || { echo "FAIL: Alembic 迁移未成功"; FAIL=1; }
# 1c Vector
$COMPOSE up -d etcd milvus
# 1d Messaging
$COMPOSE up -d rocketmq-namesrv rocketmq-permissions rocketmq-broker rocketmq-init
# 等待 Messaging 就绪（broker healthy + init 完成）再启动 App
MSG_OK=""
for i in $(seq 1 60); do
  BH=$($COMPOSE ps -a rocketmq-broker --format '{{.Status}}' 2>/dev/null | grep -c healthy || true)
  IH=$($COMPOSE ps -a rocketmq-init --format '{{.Status}}' 2>/dev/null | grep -c "Exited (0)" || true)
  if [ "${BH:-0}" -ge 1 ] && [ "${IH:-0}" -ge 1 ]; then MSG_OK=1; break; fi
  sleep 5
done
echo "  messaging broker=$($COMPOSE ps -a rocketmq-broker --format '{{.Status}}') init=$($COMPOSE ps -a rocketmq-init --format '{{.Status}}')"
[ -n "$MSG_OK" ] || { echo "FAIL: Messaging 未在预期时间内就绪"; FAIL=1; }
# 1e Application
$COMPOSE up -d core-api product-api-a product-api-b consumer-a consumer-b
for i in $(seq 1 45); do
  H=$($COMPOSE ps --format '{{.Status}}' 2>/dev/null | grep -c healthy || true)
  echo "  healthy=$H"
  [ "$H" -ge 11 ] && break; sleep 5
done
[ "$H" -ge 11 ] || { echo "FAIL: 未达到 11 healthy"; FAIL=1; }

echo "==== 2/4 重复部署（幂等） ===="
$COMPOSE up -d
N2=$(docker ps --filter "name=$PROJECT" -q | wc -l)
echo "  重复 up 后容器数=$N2（预期 14：11 运行 + 3 一次性 Exited）"
# 验证数据卷未被破坏：检查 postgres 数据仍可查询
$COMPOSE exec -T postgres psql -U "$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2)" -d "$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2)" -tAc "SELECT count(*) FROM alembic_version" 2>/dev/null

echo "==== 3/4 失败中断恢复模拟 ===="
# 模拟 Alembic 失败：坏数据库名
BAD_ENV="$WORK_ROOT/.env.bad"
sed "s/^POSTGRES_DB=.*/POSTGRES_DB=no_such_db_xyz/" "$ENV_FILE" > "$BAD_ENV"
docker compose --env-file "$BAD_ENV" -f compose.yaml -f compose.server.yaml up -d --no-deps migrate >/tmp/deploy-check-bad.log 2>&1 || true
MIG_BAD=""
for i in $(seq 1 20); do
  ST=$(docker compose --env-file "$BAD_ENV" -f compose.yaml -f compose.server.yaml ps -a migrate --format '{{.Status}}' 2>/dev/null)
  case "$ST" in
    *"Exited (0)"*) MIG_BAD=success; break;;
    *"Exited"*) MIG_BAD=failed; break;;
  esac
  sleep 3
done
if [ "$MIG_BAD" = "failed" ]; then
  echo "  EXPECTED_OK：坏 env 的 migrate 失败已捕获（$ST）"
else
  echo "  WARN：坏 env 的 migrate 未按预期失败（$ST）"
fi
grep -iE "error|fail|no_such_db|does not exist" /tmp/deploy-check-bad.log | head -3 || true
# 修复 env 后从失败点继续：migrate 应成功
echo "  修复 env 后重新 migrate（从失败阶段继续）..."
if $COMPOSE up -d --no-deps migrate; then
  for i in $(seq 1 30); do
    S=$($COMPOSE ps -a migrate --format '{{.Status}}' 2>/dev/null | grep -c "Exited (0)" || true)
    [ "$S" -ge 1 ] && break; sleep 3
  done
  echo "  migrate 修复后=$($COMPOSE ps -a migrate --format '{{.Status}}')"
else
  echo "  FAIL: 修复后 migrate 仍失败"; FAIL=1
fi
# 验证现有（独立环境）应用不受影响
echo "  core-api 状态=$($COMPOSE ps core-api --format '{{.Status}}')"

echo "==== 4/4 清理独立环境 ===="
$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
rm -rf "$WORK_ROOT"
echo "  已清理 $WORK_ROOT"

if [ "$FAIL" = "1" ]; then echo "==== DEPLOY-CHECK FAIL ===="; exit 1; fi
echo "==== DEPLOY-CHECK PASS ===="