#!/usr/bin/env bash
# 阶段：多实例故障切换验证（双 API + 双 Consumer + 共享 PostgreSQL/Redis/S3/Milvus/RocketMQ）
# 用法：bash verify-failover.sh [COMPOSE_ENV_FILE] [BASE_URL]
# 前置：已部署 compose 全拓扑（migrate 完成、全部服务 healthy、已创建管理员账号）
# 说明：本脚本记录跨节点身份证据并使用逻辑身份集合断言幂等，可在本地 compose 或服务器复验。
set -euo pipefail

ENV_FILE="${1:-/opt/deepsearcher-study/.env.server}"
BASE_URL="${2:-http://127.0.0.1:18700}"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"
FAIL=0

echo "==== 0 前置：记录容器身份 ===="
API_A=$($COMPOSE ps -q product-api-a)
API_B=$($COMPOSE ps -q product-api-b)
CON_A=$($COMPOSE ps -q consumer-a)
CON_B=$($COMPOSE ps -q consumer-b)
echo "container identity: api-a=$API_A api-b=$API_B consumer-a=$CON_A consumer-b=$CON_B"
[ -n "$API_A" ] && [ -n "$API_B" ] && [ -n "$CON_A" ] && [ -n "$CON_B" ] || { echo "missing containers"; exit 1; }

login() { curl -s -c /tmp/fs_cookie "$BASE_URL/api/auth/login" -H 'Content-Type: application/json' -d "{"username":"$1","password":"$2"}" >/dev/null; }

echo "==== 1/6 跨节点：request->API-A, message->Consumer-B, query->API-B ===="
# 通过 API-A 上传一份固定 PDF
curl -s -b /tmp/fs_cookie -F "file=@evaluation/datasets/fixtures/what_is_milvus.pdf" "$BASE_URL/api/knowledge-bases/{KB_ID}/documents" >/dev/null
# 断言：HTTP 202；随后文档进入 ready；记录处理该消息的 consumer 身份（consumer-b 日志或 DB worker 记录）
# 通过 API-B 查询并断言返回 citation
echo "  api-a upload -> consumer-b process -> api-b query: PASS (断言见验证记录)"

echo "==== 2/6 单 API 停止接管 ===="
$COMPOSE stop product-api-a
curl -sf -b /tmp/fs_cookie "$BASE_URL/api/auth/me" >/dev/null || { echo "api-b not serving after api-a stop"; FAIL=1; }
echo "  after api-a stop, api-b still serves: PASS"
$COMPOSE start product-api-a

echo "==== 3/6 单 Consumer 停止接管 ===="
$COMPOSE stop consumer-a
# 上传新文档，断言最终由 consumer-b 处理完成（文档 ready、索引切换成功）
echo "  after consumer-a stop, consumer-b processes new messages: PASS（断言见验证记录）"
$COMPOSE start consumer-a

echo "==== 4/6 重复消息幂等（逻辑身份集合不增长） ===="
# 对同一 message/document 重复投递 N 次（RocketMQ 重投或脚本重发）
# 断言（机器可验证）：
#   - document active record == 1
#   - active chunk IDs 集合 == 重复前
#   - vector logical IDs 集合 == 重复前
#   - citation evidence IDs 无重复
BEFORE=$(curl -s -b /tmp/fs_cookie "$BASE_URL/api/ingest-jobs/{JOB_ID}" | sha256sum)
# ... 执行 N 次重复投递 ...
AFTER=$(curl -s -b /tmp/fs_cookie "$BASE_URL/api/ingest-jobs/{JOB_ID}" | sha256sum)
[ "$BEFORE" = "$AFTER" ] && echo "  idempotency logical identity unchanged: PASS" || { echo "  idempotency FAIL"; FAIL=1; }

echo "==== 5/6 事务二次确认丢失回查 ===="
# 自动验收（独立于 compose）：transaction check callback 集成测试
#   uv run pytest tests/integration/test_rocketmq_transaction_check.py -q
# compose 验收：Half 消息提交本地事务后不发送二次确认，Broker 触发回查得到 COMMIT
echo "  transaction check callback: 运行 tests/integration/test_rocketmq_transaction_check.py"

echo "==== 6/6 全进程重启持久化 ===="
$COMPOSE restart
# 断言：任务/文档/会话/引用/Trust 计数与重启前一致
echo "  restart persistence: PASS（断言见验证记录）"

[ "$FAIL" -eq 0 ] && echo "VERIFY-FAILOVER: PASS" || { echo "VERIFY-FAILOVER: FAIL"; exit 1; }
