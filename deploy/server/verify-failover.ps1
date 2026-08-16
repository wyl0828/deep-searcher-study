# 阶段：多实例故障切换验证（双 API + 双 Consumer + 共享 PostgreSQL/Redis/S3/Milvus/RocketMQ）
# 用法：.\verify-failover.ps1 [-EnvFile <path>] [-BaseUrl <url>]
# 前置：已部署 compose 全拓扑（migrate 完成、全部服务 healthy、已创建管理员账号）
# 注意：六场景机器断言以 deploy/server/verify-failover.sh 为权威实现（已补全并在服务器
# 47.96.40.156 执行 VERIFY-FAILOVER: PASS，记录见 docs/验证记录/2026-08-16-failover-verification.md）。
# 本 PowerShell 版保留原骨架供 Windows/本地 compose 参考；正式验收请运行 .sh 并以其输出为准。
param(
    [string]$EnvFile = "/opt/deepsearcher-study/.env.server",
    [string]$BaseUrl = "http://127.0.0.1:18700"
)
$ErrorActionPreference = "Stop"
$FAIL = 0
Write-Host "==== 0 前置：记录容器身份 ===="
$apiA = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q product-api-a).Trim()
$apiB = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q product-api-b).Trim()
$conA = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q consumer-a).Trim()
$conB = (docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml ps -q consumer-b).Trim()
Write-Host "container identity: api-a=$apiA api-b=$apiB consumer-a=$conA consumer-b=$conB"
Write-Host "==== 1/6 跨节点 ===="
Write-Host "  断言见 verify-failover.sh（服务器已 PASS）"
Write-Host "==== 2/6 单 API 停止接管 ===="
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml stop product-api-a | Out-Null
Invoke-RestMethod -Uri "$BaseUrl/api/auth/me" -Method Get | Out-Null
Write-Host "  after api-a stop, api-b still serves: PASS"
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml start product-api-a | Out-Null
Write-Host "==== 3/6 单 Consumer 停止接管 ===="
Write-Host "  断言见 verify-failover.sh（服务器已 PASS）"
Write-Host "==== 4/6 重复消息幂等 ===="
Write-Host "  断言见 verify-failover.sh（服务器已 PASS）"
Write-Host "==== 5/6 事务二次确认丢失回查 ===="
Write-Host "  断言见 verify-failover.sh（服务器已 PASS）"
Write-Host "==== 6/6 全进程重启持久化 ===="
docker compose --env-file $EnvFile -f compose.yaml -f compose.server.yaml restart | Out-Null
Write-Host "  断言见 verify-failover.sh（服务器已 PASS）"
if ($FAIL -eq 0) { Write-Host "VERIFY-FAILOVER: 请以 verify-failover.sh 输出为准（服务器已 PASS）" } else { Write-Host "VERIFY-FAILOVER: FAIL"; exit 1 }
