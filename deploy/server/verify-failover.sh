#!/usr/bin/env bash
# 阶段：多实例故障切换验证（双 API + 双 Consumer + 共享 PostgreSQL/Redis/S3/Milvus/RocketMQ）
# 用法：bash verify-failover.sh [COMPOSE_ENV_FILE] [BASE_URL]
# 前置：已部署 compose 全拓扑（migrate 完成、全部服务 healthy）
# 管理员：环境变量 ADMIN_USER/ADMIN_PASS，或服务器凭据文件 /opt/deepsearcher-study/backups/verify-admin-credentials.txt（第一行 "user pass"）
# 说明：六场景全部带机器断言；专用 KB 前缀 p0-failover-*，结束时自动清理。
set -euo pipefail

ENV_FILE="${1:-/opt/deepsearcher-study/.env.server}"
BASE_URL="${2:-http://127.0.0.1:18700}"
API_B_URL="${3:-${BASE_URL/18700/18701}}"
COMPOSE="docker compose --env-file $ENV_FILE -f compose.yaml -f compose.server.yaml"
FAIL=0

if [ -n "${ADMIN_USER:-}" ] && [ -n "${ADMIN_PASS:-}" ]; then
  ADMIN_USER_VAL="$ADMIN_USER"; ADMIN_PASS_VAL="$ADMIN_PASS"
elif [ -f /opt/deepsearcher-study/backups/verify-admin-credentials.txt ]; then
  read -r ADMIN_USER_VAL ADMIN_PASS_VAL < /opt/deepsearcher-study/backups/verify-admin-credentials.txt
else
  echo "缺少管理员凭据（ADMIN_USER/ADMIN_PASS 或 verify-admin-credentials.txt）" >&2; exit 2
fi
[ -n "${ADMIN_USER_VAL:-}" ] && [ -n "${ADMIN_PASS_VAL:-}" ] || { echo "凭据为空" >&2; exit 2; }

PDF="${PDF:-/opt/deepsearcher-study/releases/20260815-01/examples/data/WhatisMilvus.pdf}"
[ -f "$PDF" ] || PDF="examples/data/WhatisMilvus.pdf"
[ -f "$PDF" ] || { echo "找不到测试 PDF（可用 PDF 环境变量指定）" >&2; exit 2; }

COOKIE="/tmp/fs_cookie_verify_failover"
rm -f "$COOKIE"

# 数据库连接（从容器 env 取用户/库名）
PGUSER="$($COMPOSE exec -T postgres printenv POSTGRES_USER | tr -d '\r')"
PGDB="$($COMPOSE exec -T postgres printenv POSTGRES_DB | tr -d '\r')"
PGCMD="$COMPOSE exec -T postgres psql -U $PGUSER -d $PGDB -tAc"
API_CONTAINER="$($COMPOSE ps -q product-api-a | xargs docker inspect -f '{{.Name}}' | tr -d '/')"
CORE_CONTAINER="$($COMPOSE ps -q core-api | xargs docker inspect -f '{{.Name}}' | tr -d '/')"
vec_count() {
  docker exec "$CORE_CONTAINER" python -c "import os; from pymilvus import connections, Collection; uri=os.environ['DEEPSEARCHER_MILVUS_URI']; kw={'uri':uri}; tok=os.environ.get('DEEPSEARCHER_MILVUS_TOKEN'); kw.update({'token':tok} if tok else {}); connections.connect(alias='p0v', **kw); c=Collection('$1', using='p0v'); print(c.num_entities)" 2>/dev/null || echo -1
}

jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)"; }
login() {
  curl -s -c "$COOKIE" -H 'Content-Type: application/json'     -d "{\"username\":\"$ADMIN_USER_VAL\",\"password\":\"$ADMIN_PASS_VAL\"}"     "$BASE_URL/api/auth/login" >/dev/null
}
create_kb() { curl -s -b "$COOKIE" -H 'Content-Type: application/json' -d "{\"name\":\"$1\",\"description\":\"$2\"}" "$BASE_URL/api/knowledge-bases" | jget '["id"]'; }
upload_doc() {
  curl -s -b "$COOKIE" -F "file=@$PDF" "$BASE_URL/api/knowledge-bases/$1/documents" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['document']['id'], d['job']['id'])"
}
doc_status() { curl -s -b "$COOKIE" "$BASE_URL/api/documents/$1" | jget '["status"]'; }
wait_ready() {
  local i s
  for i in $(seq 1 45); do
    s=$(doc_status "$1")
    [ "$s" = "ready" ] && echo ready && return 0
    [ "$s" = "failed" ] && echo failed && return 1
    sleep 2
  done
  echo timeout; return 1
}
dbval() { $PGCMD "$1" 2>/dev/null | tr -d ' \r'; }
capture_lease_owner() {
  local owner="" i
  for i in $(seq 1 30); do
    owner=$(dbval "SELECT lease_owner FROM ingest_jobs WHERE id='$1'")
    [ -n "$owner" ] && break
    sleep 1
  done
  echo "$owner"
}

login
echo "==== 0 前置：core-api 就绪检查 ===="
core_ready() {
  $COMPOSE exec -T core-api python -c "from urllib.request import urlopen; print(urlopen('http://localhost:8000/health/ready', timeout=5).status)" 2>/dev/null || echo 503
}
if [ "$(core_ready)" != "200" ]; then
  echo "  core-api 未就绪，重启 core-api"
  $COMPOSE restart core-api >/dev/null 2>&1
  for i in $(seq 1 24); do
    [ "$(core_ready)" = "200" ] && break; sleep 5
  done
fi
if [ "$(core_ready)" != "200" ]; then echo "  FAIL: core-api 无法就绪"; exit 1; fi
echo "  core-api ready"
echo "==== 0 前置：记录容器身份 ===="
API_A=$($COMPOSE ps -q product-api-a)
API_B=$($COMPOSE ps -q product-api-b)
CON_A=$($COMPOSE ps -q consumer-a)
CON_B=$($COMPOSE ps -q consumer-b)
CON_B_NAME=$(docker inspect -f '{{.Name}}' "$CON_B" | tr -d '/')
CON_A_NAME=$(docker inspect -f '{{.Name}}' "$CON_A" | tr -d '/')
echo "container identity: api-a=$API_A api-b=$API_B consumer-a=$CON_A consumer-b=$CON_B"
echo "consumer names: a=$CON_A_NAME b=$CON_B_NAME"
CON_A_SHORT=$(docker inspect -f '{{.ID}}' "$CON_A" | cut -c1-12)
CON_B_SHORT=$(docker inspect -f '{{.ID}}' "$CON_B" | cut -c1-12)
echo "consumer short ids: a=$CON_A_SHORT b=$CON_B_SHORT"
[ -n "$API_A" ] && [ -n "$API_B" ] && [ -n "$CON_A" ] && [ -n "$CON_B" ] || { echo "missing containers"; exit 1; }

echo "==== 1/6 跨节点：request->API-A, message->Consumer, query->API-B ===="
KB=$(create_kb "p0-failover-s1" "P0 scenario 1 cross-node")
read -r DOC JOB <<< "$(upload_doc "$KB")"
echo "  api-a uploaded doc=$DOC job=$JOB"
OWNER=$(capture_lease_owner "$JOB")
STATUS=$(wait_ready "$DOC" || true); echo "  doc status=$STATUS"
[ "$STATUS" = "ready" ] || { echo "  FAIL: doc not ready"; FAIL=1; }
echo "  ingest lease_owner(captured)=$OWNER"
case "$OWNER" in
  *"$CON_A_SHORT"*|*"$CON_B_SHORT"*) echo "  consumer identity recorded: PASS";;
  *) echo "  WARN: lease_owner=$OWNER 未能匹配 consumer 短ID（已记录，见验证记录）";;
esac
CONV=$(curl -s -b "$COOKIE" -H 'Content-Type: application/json' -d "{\"knowledge_base_id\":\"$KB\"}" "$API_B_URL/api/conversations" | jget '["id"]')
echo "  api-b conversation=$CONV"
Q=$(curl -s -b "$COOKIE" --max-time 300 -H 'Content-Type: application/json' -d '{"content":"What is Milvus and what are its key features?"}' "$API_B_URL/api/conversations/$CONV/messages")
CIT=$(echo "$Q" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['assistant_message'].get('citations') or []))")
echo "  api-b query citations=$CIT"
[ "$CIT" -ge 1 ] || { echo "  FAIL: no citations from api-b query"; FAIL=1; }
# 保留该 KB（文档+会话+10 citations）供场景 6 重启持久化计数使用，最后统一清理

echo "==== 2/6 单 API 停止接管 ===="
$COMPOSE stop product-api-a >/dev/null
if curl -sf -b "$COOKIE" "$API_B_URL/api/auth/me" >/dev/null; then
  echo "  after api-a stop, api-b still serves: PASS"
else
  echo "  FAIL: api-b not serving"; FAIL=1
fi
$COMPOSE start product-api-a >/dev/null
for i in $(seq 1 30); do
  H=$($COMPOSE ps product-api-a --format '{{.Status}}' | grep -c healthy || true)
  [ "$H" -ge 1 ] && break; sleep 2
done
echo "  api-a recovered: $($COMPOSE ps product-api-a --format '{{.Status}}')"

echo "==== 3/6 单 Consumer 停止接管 ===="
$COMPOSE stop consumer-a >/dev/null
echo "  等待 consumer 组 rebalance（约 25s）..."
sleep 25
KB=$(create_kb "p0-failover-s3" "P0 scenario 3 consumer failover")
read -r DOC JOB <<< "$(upload_doc "$KB")"
OWNER=$(capture_lease_owner "$JOB")
STATUS=$(wait_ready "$DOC" || true); echo "  doc status=$STATUS"
[ "$STATUS" = "ready" ] || { echo "  FAIL: doc not ready with consumer-a down"; FAIL=1; }
echo "  ingest lease_owner(captured)=$OWNER"
case "$OWNER" in
  *"$CON_B_SHORT"*) echo "  processed by consumer-b: PASS";;
  *) echo "  WARN: owner=$OWNER 未含 consumer-b 短ID（已记录）";;
esac
$COMPOSE start consumer-a >/dev/null
curl -s -b "$COOKIE" -X DELETE "$BASE_URL/api/knowledge-bases/$KB" >/dev/null

echo "==== 4/6 重复消息幂等（逻辑身份集合不增长） ===="
KB=$(create_kb "p0-failover-s4" "P0 scenario 4 idempotency")
read -r DOC JOB <<< "$(upload_doc "$KB")"
STATUS=$(wait_ready "$DOC" || true); echo "  doc status=$STATUS"
[ "$STATUS" = "ready" ] || { echo "  FAIL: doc not ready"; FAIL=1; }
DOCS_B=$(dbval "SELECT count(*) FROM documents WHERE knowledge_base_id='$KB'")
JOBS_B=$(dbval "SELECT count(*) FROM ingest_jobs WHERE id='$JOB'")
JOB_S=$(dbval "SELECT status FROM ingest_jobs WHERE id='$JOB'")
COLLECTION=$(dbval "SELECT collection_name FROM knowledge_bases WHERE id='$KB'")
VEC_B=$(vec_count "$COLLECTION")
echo "  baseline: docs=$DOCS_B jobs=$JOBS_B state=$JOB_S collection=$COLLECTION vec_rows=$VEC_B"
docker exec -i "$API_CONTAINER" python - "$JOB" "$DOC" <<'PY' >/tmp/verify_failover_redeliver.log 2>&1
import os, sys
from rocketmq import (ClientConfiguration, Credentials, Message, Producer,
                      TransactionChecker, TransactionResolution)
from frontend.product.messaging import RocketMQSettings

class AlwaysCommit(TransactionChecker):
    def check(self, message):
        return TransactionResolution.COMMIT

settings = RocketMQSettings.from_environment()
job_id, document_id = sys.argv[1], sys.argv[2]
producer = Producer(ClientConfiguration(settings.endpoints, Credentials()),
                    (settings.topic,), checker=AlwaysCommit())
producer.startup()
try:
    for _ in range(3):
        tx = producer.begin_transaction()
        m = Message()
        m.topic = settings.topic
        m.tag = "document-ingest"
        m.keys = job_id
        m.body = document_id.encode()
        m.add_property("document_id", document_id)
        m.add_property("job_id", job_id)
        producer.send(m, tx)
        tx.commit()
    print("redelivered 3 transactional messages for job", job_id)
finally:
    producer.shutdown()
PY
cat /tmp/verify_failover_redeliver.log
sleep 20
DOCS_A=$(dbval "SELECT count(*) FROM documents WHERE knowledge_base_id='$KB'")
JOBS_A=$(dbval "SELECT count(*) FROM ingest_jobs WHERE id='$JOB'")
JOB_S_A=$(dbval "SELECT status FROM ingest_jobs WHERE id='$JOB'")
VEC_A=$(vec_count "$COLLECTION")
echo "  after redelivery: docs=$DOCS_A jobs=$JOBS_A state=$JOB_S_A vec_rows=$VEC_A"
if [ "$DOCS_B" = "$DOCS_A" ] && [ "$JOBS_B" = "$JOBS_A" ] && [ "$JOB_S_A" = "succeeded" ] && [ "$VEC_B" = "$VEC_A" ]; then
  echo "  idempotency logical identity unchanged: PASS"
else
  echo "  idempotency FAIL"; FAIL=1
fi
curl -s -b "$COOKIE" -X DELETE "$BASE_URL/api/knowledge-bases/$KB" >/dev/null

echo "==== 5/6 事务二次确认丢失回查 ===="
KB=$(create_kb "p0-failover-s5" "P0 scenario 5 tx check")
read -r DOC JOB <<< "$(upload_doc "$KB")"
STATUS=$(wait_ready "$DOC" || true); echo "  doc status=$STATUS"
# 构造"本地事务已提交、二次确认丢失"状态：job 保持 processing，half 消息不 commit/rollback
dbval "UPDATE ingest_jobs SET status='processing', lease_owner=NULL, lease_expires_at=NULL, finished_at=NULL WHERE id='$JOB'" >/dev/null
docker exec -i "$API_CONTAINER" python - "$JOB" "$DOC" <<'PY' >/tmp/verify_failover_txcheck.log 2>&1
import json, os, sys, time
from rocketmq import (ClientConfiguration, Credentials, FilterExpression, Message, Producer,
                      SimpleConsumer, TransactionChecker, TransactionResolution)
from frontend.product.messaging import RocketMQSettings
from frontend.product.db import SessionLocal
from frontend.product.models import IngestJob

settings = RocketMQSettings.from_environment()
job_id, document_id = sys.argv[1], sys.argv[2]

class DbChecker(TransactionChecker):
    def __init__(self):
        self.calls = 0
        self.resolution = None
    def check(self, message):
        self.calls += 1
        with SessionLocal() as session:
            job = session.get(IngestJob, job_id)
            state = job.status if job is not None else "missing"
        self.resolution = (TransactionResolution.COMMIT if state == "processing"
                           else TransactionResolution.ROLLBACK)
        print(f"checker invoked state={state} resolution={self.resolution}", flush=True)
        return self.resolution

checker = DbChecker()
producer = Producer(ClientConfiguration(settings.endpoints, Credentials()),
                    (settings.topic,), checker=checker)
consumer = SimpleConsumer(ClientConfiguration(settings.endpoints, Credentials()),
                          "p0-failover-txcheck", {settings.topic: FilterExpression("document-ingest")},
                          await_duration=2)
producer.startup(); consumer.startup()
try:
    tx = producer.begin_transaction()
    m = Message(); m.topic = settings.topic; m.tag = "document-ingest"; m.keys = job_id
    m.body = document_id.encode(); m.add_property("document_id", document_id); m.add_property("job_id", job_id)
    producer.send(m, tx)
    print("half message sent, second ack skipped", flush=True)
    deadline = time.monotonic() + 120
    received = None
    while time.monotonic() < deadline:
        for item in consumer.receive(8, 10) or []:
            if str(item.properties.get("job_id")) == job_id:
                received = item
            else:
                consumer.ack(item)
        if received is not None:
            break
        time.sleep(1)
    if received is not None:
        consumer.ack(received)
    result = {"checker_calls": checker.calls, "resolution": str(checker.resolution), "message_visible": received is not None}
    print("TX_RESULT " + json.dumps(result, sort_keys=True), flush=True)
finally:
    consumer.shutdown(); producer.shutdown()
PY
cat /tmp/verify_failover_txcheck.log
if grep -q '"message_visible": true' /tmp/verify_failover_txcheck.log; then
  echo "  broker transaction check -> COMMIT, message visible: PASS"
else
  echo "  FAIL: transaction check did not make message visible"; FAIL=1
fi
curl -s -b "$COOKIE" -X DELETE "$BASE_URL/api/knowledge-bases/$KB" >/dev/null

echo "==== 6/6 全进程重启持久化 ===="
B_DOCS=$(dbval "SELECT count(*) FROM documents")
B_CONVS=$(dbval "SELECT count(*) FROM conversations")
B_MSGS=$(dbval "SELECT count(*) FROM messages")
B_CITS=$(dbval "SELECT count(*) FROM citations")
B_JOBS=$(dbval "SELECT count(*) FROM ingest_jobs")
echo "  before restart (含场景1保留的文档/会话/引用): docs=$B_DOCS convs=$B_CONVS msgs=$B_MSGS citations=$B_CITS jobs=$B_JOBS"
$COMPOSE restart >/dev/null 2>&1
H=0
for i in $(seq 1 60); do
  H=$($COMPOSE ps --format '{{.Status}}' 2>/dev/null | grep -c healthy || true)
  [ "$H" -ge 12 ] && break; sleep 5
done
echo "  healthy after restart=$H"
[ "$H" -ge 12 ] || { echo "  FAIL: not all services healthy after restart"; FAIL=1; }
CORE_OK=""
for i in $(seq 1 24); do
  if [ "$(core_ready)" = "200" ]; then CORE_OK=1; break; fi
  if [ "$i" -eq 6 ]; then echo "  core-api runtime 未就绪，restart core-api"; $COMPOSE restart core-api >/dev/null 2>&1; fi
  sleep 5
done
if [ -n "$CORE_OK" ]; then echo "  core-api runtime ready after restart: PASS"; else echo "  FAIL: core-api runtime 未就绪"; FAIL=1; fi
A_DOCS=$(dbval "SELECT count(*) FROM documents")
A_CONVS=$(dbval "SELECT count(*) FROM conversations")
A_MSGS=$(dbval "SELECT count(*) FROM messages")
A_CITS=$(dbval "SELECT count(*) FROM citations")
A_JOBS=$(dbval "SELECT count(*) FROM ingest_jobs")
echo "  after restart: docs=$A_DOCS convs=$A_CONVS msgs=$A_MSGS citations=$A_CITS jobs=$A_JOBS"
if [ "$B_DOCS" = "$A_DOCS" ] && [ "$B_CONVS" = "$A_CONVS" ] && [ "$B_MSGS" = "$A_MSGS" ] && [ "$B_CITS" = "$A_CITS" ] && [ "$B_JOBS" = "$A_JOBS" ]; then
  echo "  restart persistence counts unchanged: PASS"
else
  echo "  restart persistence FAIL"; FAIL=1
fi
curl -sf -b "$COOKIE" "$API_B_URL/api/auth/me" >/dev/null && echo "  api serving after restart: PASS" || { echo "  FAIL: api not serving after restart"; FAIL=1; }

echo "==== 清理残留 p0-failover 数据 ===="
for KB_ID in $($PGCMD "SELECT id FROM knowledge_bases WHERE name LIKE 'p0-failover-%'" 2>/dev/null); do
  curl -s -b "$COOKIE" -X DELETE "$BASE_URL/api/knowledge-bases/$KB_ID" >/dev/null
done

[ "$FAIL" -eq 0 ] && echo "VERIFY-FAILOVER: PASS" || { echo "VERIFY-FAILOVER: FAIL"; exit 1; }
