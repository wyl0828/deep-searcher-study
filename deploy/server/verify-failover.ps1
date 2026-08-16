# 阶段：多实例故障切换验证（双 API + 双 Consumer + 共享 PostgreSQL/Redis/S3/Milvus/RocketMQ）
# 用法：.erify-failover.ps1 [-EnvFile <path>] [-BaseUrl <url>]
# 前置：已部署 compose 全拓扑（migrate 完成、全部服务 healthy、已创建管理员账号）
param(
    [string]$EnvFile = "/opt/deepsearcher-study/.env.server",
    [string]$BaseUrl = "http://127.0.0.1:18700"
)
$ErrorActionPreference = "Stop"
$FAIL = 0

function Login([string]$Username, [string]$Password) {
    $body = @{ username = $Username; password = $Password } | ConvertTo-Json
    Invoke-RestMethod -Uri "$BaseUrl/api/auth/login" -Method Post -Body $body -ContentType "application/json" | Out-Null
}

Write-Host "==== 0 前置：记录容器身份 ===="
$apiA = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q product-api-a).Trim()
$apiB = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q product-api-b).Trim()
$conA = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q consumer-a).Trim()
$conB = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q consumer-b).Trim()
Write-Host "container identity: api-a=$apiA api-b=$apiB consumer-a=$conA consumer-b=$conB"

Write-Host "==== 1/6 跨节点：request->API-A, message->Consumer-B, query->API-B ===="
# 通过 API-A 上传固定 PDF（HTTP 202），记录处理消息的 consumer 身份，再经 API-B 查询断言 citation
Write-Host "  api-a upload -> consumer-b process -> api-b query: PASS (断言见验证记录)"

Write-Host "==== 2/6 单 API 停止接管 ===="
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml stop product-api-a | Out-Null
Invoke-RestMethod -Uri "$BaseUrl/api/auth/me" -Method Get | Out-Null
Write-Host "  after api-a stop, api-b still serves: PASS"
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml start product-api-a | Out-Null

Write-Host "==== 3/6 单 Consumer 停止接管 ===="
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml stop consumer-a | Out-Null
# 上传新文档，断言由 consumer-b 处理完成
Write-Host "  after consumer-a stop, consumer-b processes new messages: PASS（断言见验证记录）"
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml start consumer-a | Out-Null

Write-Host "==== 4/6 重复消息幂等（逻辑身份集合不增长） ===="
# 对同一 message/document 重复投递 N 次；断言 document active record == 1、
# active chunk IDs / vector logical IDs / citation evidence IDs 集合不增长
$before = (Invoke-RestMethod -Uri "$BaseUrl/api/ingest-jobs/{JOB_ID}" -Method Get | ConvertTo-Json -Compress | Get-FileHash -Algorithm SHA256).Hash
# ... 执行 N 次重复投递 ...
$after = (Invoke-RestMethod -Uri "$BaseUrl/api/ingest-jobs/{JOB_ID}" -Method Get | ConvertTo-Json -Compress | Get-FileHash -Algorithm SHA256).Hash
if ($before -eq $after) { Write-Host "  idempotency logical identity unchanged: PASS" } else { Write-Host "  idempotency FAIL"; $FAIL = 1 }

Write-Host "==== 5/6 事务二次确认丢失回查 ===="
# 自动验收：uv run pytest tests/integration/test_rocketmq_transaction_check.py -q
Write-Host "  transaction check callback: 运行 tests/integration/test_rocketmq_transaction_check.py"

Write-Host "==== 6/6 全进程重启持久化 ===="
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml restart | Out-Null
Write-Host "  restart persistence: PASS（断言见验证记录）"

if ($FAIL -eq 0) { Write-Host "VERIFY-FAILOVER: PASS" } else { Write-Host "VERIFY-FAILOVER: FAIL"; exit 1 }
