[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "scripts\runtime-common.ps1")

$dockerReady = Test-DockerDaemon
$milvusHealth = Get-MilvusHealth
$milvusReady = $milvusHealth -eq "healthy" -and (Test-TcpPort -HostName "127.0.0.1" -Port 19530)
$runtimePorts = Get-RuntimePorts
$backendUrl = "http://127.0.0.1:$($runtimePorts.BackendPort)"
$workspaceUrl = "http://127.0.0.1:$($runtimePorts.WorkspacePort)"
$backendHealth = Get-HttpHealthResult -Url "$backendUrl/health/ready"
$workspaceHealth = Get-HttpHealthResult -Url "$workspaceUrl/api/health"
$backendReady = $backendHealth.Status -eq "ready"
$workspaceReady = $workspaceHealth.Status -eq "ready"
$workerProcessReady = Test-ManagedPythonModule `
    -Name "ingest-worker" `
    -Module "frontend.product.worker"
$workerHeartbeatReady = $false
if ($null -ne $workspaceHealth.Payload) {
    try {
        $workerHeartbeatReady = `
            [string]$workspaceHealth.Payload.services.ingest_worker.state -eq "ready"
    }
    catch {
        $workerHeartbeatReady = $false
    }
}
$workerReady = $workerProcessReady -and $workerHeartbeatReady
$workerState = if ($workerReady) {
    "ready"
}
elseif (-not $workerProcessReady) {
    "stopped"
}
elseif ($workspaceHealth.Status -eq "unreachable") {
    "unknown"
}
else {
    "not_ready"
}

$rows = @(
    [pscustomobject]@{
        Component = "Docker"
        State = $(if ($dockerReady) { "ready" } else { "stopped" })
        Detail = $(if ($dockerReady) { "daemon available" } else { "daemon unavailable" })
    }
    [pscustomobject]@{
        Component = "Milvus"
        State = $(if ($milvusReady) { "ready" } else { "stopped" })
        Detail = "$milvusHealth; 127.0.0.1:19530"
    }
    [pscustomobject]@{
        Component = "DeepSearcher API"
        State = $backendHealth.Status
        Detail = $backendUrl
    }
    [pscustomobject]@{
        Component = "Document worker"
        State = $workerState
        Detail = $(if ($workerHeartbeatReady) { "heartbeat ready" } else { "heartbeat unavailable" })
    }
    [pscustomobject]@{
        Component = "User workspace"
        State = $workspaceHealth.Status
        Detail = $workspaceUrl
    }
)

Write-Host ""
$rows | Format-Table -AutoSize

if ($dockerReady -and $milvusReady -and $backendReady -and $workerReady -and $workspaceReady) {
    Write-Host "All services are ready." -ForegroundColor Green
    exit 0
}

Write-Host "One or more services are not ready." -ForegroundColor Yellow
exit 1
