<#
.SYNOPSIS
    在服务器执行阶段4 部署可重复性验证（独立 Compose 项目名，不影响现有拓扑）。
.DESCRIPTION
    覆盖：空环境部署（Storage→Alembic→Vector→Messaging→Application）、重复部署幂等、
    失败中断恢复模拟（坏 env 导致 migrate 失败且不影响现有环境）。
.PARAMETER Release
    作为部署源代码的版本目录名，默认 20260815-01。
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
$scpArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")
$remoteScript = "$RemoteRoot/scripts/verify-deploy-run.sh"
$localScript = Join-Path $PSScriptRoot "verify-deploy.sh"

if (-not (Test-Path -LiteralPath $localScript)) { throw "缺少 verify-deploy.sh：$localScript" }

& ssh @sshArgs "mkdir -p $RemoteRoot/scripts"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir 失败" }
& scp @ScpArgs $localScript "$Server`:$remoteScript"
if ($LASTEXITCODE -ne 0) { throw "scp 脚本失败" }

$releaseRemote = "$RemoteRoot/releases/$Release"
$output = & ssh @sshArgs "bash $remoteScript $releaseRemote"
$code = $LASTEXITCODE
$output | ForEach-Object { Write-Host $_ }
if ($code -ne 0) { throw "部署可重复性验证失败（退出码 $code）" }
Write-Host "部署可重复性验证通过。"