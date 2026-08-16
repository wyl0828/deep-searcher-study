<#
.SYNOPSIS
    查看服务器 deep-searcher-study 部署状态：容器、资源、磁盘、日志体积。
.PARAMETER Release
    版本目录名，默认 20260815-01。
#>
[CmdletBinding()]
param(
    [string]$Release = "20260815-01",
    [string]$Server = "root@47.96.40.156",
    [string]$KeyPath = "D:\code\ecs_key.pem",
    [string]$RemoteRoot = "/opt/deepsearcher-study"
)

$ErrorActionPreference = "Stop"
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$releaseRemote = "$RemoteRoot/releases/$Release"
$envFileRemote = "$RemoteRoot/.env.server"
$compose = "docker compose --env-file $envFileRemote -f compose.yaml -f compose.server.yaml"

& ssh @sshArgs "cd $releaseRemote && $compose ps --all"
Write-Host ""
Write-Host "--- docker stats（本项目） ---"
& ssh @sshArgs "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'deepsearcher|NAME' || echo '(本项目容器未运行)'"
Write-Host ""
Write-Host "--- 磁盘 / ---"
& ssh @sshArgs "df -h /"
Write-Host ""
Write-Host "--- 内存 / Swap ---"
& ssh @sshArgs "free -h && swapon --show || true"
Write-Host ""
Write-Host "--- Docker 日志体积 ---"
& ssh @sshArgs "find /var/lib/docker/containers -name '*-json.log' -exec du -ch {} + 2>/dev/null | tail -1"