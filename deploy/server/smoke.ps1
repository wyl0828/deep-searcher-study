<#
.SYNOPSIS
    对服务器回环端口执行 API 健康检查冒烟。
.PARAMETER Release
    版本目录名，默认 20260815-01。
.PARAMETER TimeoutSeconds
    总等待时间，默认 180 秒。
#>
[CmdletBinding()]
param(
    [string]$Release = "20260815-01",
    [string]$Server = "root@118.178.234.18",
    [string]$KeyPath = "D:\code\ecs_key.pem",
    [string]$RemoteRoot = "/opt/deepsearcher-study",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$envFileRemote = "$RemoteRoot/.env.server"

$corePort = (& ssh @sshArgs "grep -E '^CORE_API_SERVER_PORT=' $envFileRemote | head -1 | cut -d= -f2 | tr -d '[:space:]'").Trim()
$apiAPort = (& ssh @sshArgs "grep -E '^PRODUCT_API_A_SERVER_PORT=' $envFileRemote | head -1 | cut -d= -f2 | tr -d '[:space:]'").Trim()
$apiBPort = (& ssh @sshArgs "grep -E '^PRODUCT_API_B_SERVER_PORT=' $envFileRemote | head -1 | cut -d= -f2 | tr -d '[:space:]'").Trim()
if (-not $corePort) { $corePort = "18702" }
if (-not $apiAPort) { $apiAPort = "18700" }
if (-not $apiBPort) { $apiBPort = "18701" }

$targets = @(
    "http://127.0.0.1:$corePort/health/ready",
    "http://127.0.0.1:$apiAPort/api/health/live",
    "http://127.0.0.1:$apiBPort/api/health/live"
)

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$pending = [System.Collections.Generic.HashSet[string]]::new([string[]]$targets)
while ($pending.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
    foreach ($target in @($pending)) {
        $code = (& ssh @sshArgs "curl -s -o /dev/null -w '%{http_code}' --max-time 5 $target" | Select-Object -Last 1).Trim()
        if ($code -eq "200") {
            Write-Host "PASS $target"
            [void]$pending.Remove($target)
        }
    }
    if ($pending.Count -gt 0) { Start-Sleep -Seconds 2 }
}

if ($pending.Count -gt 0) {
    throw "以下健康检查未在 ${TimeoutSeconds}s 内通过：$($pending -join ', ')"
}
Write-Host "服务器 API 冒烟检查通过。"