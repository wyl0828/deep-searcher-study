[CmdletBinding()]
param(
    [switch]$KeepMilvus
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "scripts\runtime-common.ps1")

try {
    $runtimePorts = Get-RuntimePorts
    Write-RuntimeStep "Stopping user-facing services"
    Stop-ManagedPythonModule `
        -Name "ingest-worker" `
        -Module "frontend.product.worker"
    Stop-ManagedUvicorn `
        -Name "workspace" `
        -Port $runtimePorts.WorkspacePort `
        -ExpectedApplication "frontend.server:app"
    Stop-ManagedUvicorn `
        -Name "backend" `
        -Port $runtimePorts.BackendPort `
        -ExpectedApplication "main:app"

    if (-not $KeepMilvus) {
        if (Test-DockerDaemon) {
            Write-RuntimeStep "Stopping Milvus"
            & docker compose `
                -f $script:ComposeFile `
                -f $script:ComposeLocalFile `
                stop
            if ($LASTEXITCODE -ne 0) {
                throw "Docker Compose stop failed with exit code $LASTEXITCODE."
            }
        }
        else {
            Write-Warning "Docker is unavailable; Milvus could not be stopped."
        }
    }

    Write-RuntimeStep "Stop complete"
}
catch {
    Write-Error $_
    exit 1
}
