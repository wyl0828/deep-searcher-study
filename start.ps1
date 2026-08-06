[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoBrowser,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8650,
    [ValidateRange(1024, 65535)][int]$WorkspacePort = 8600,
    [ValidateRange(30, 600)][int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "scripts\runtime-common.ps1")

function New-EphemeralServiceToken {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

try {
    Write-RuntimeStep "Checking local dependencies"

    if (-not (Test-CommandAvailable -Name "docker")) {
        throw "Docker CLI was not found. Install Docker Desktop first."
    }
    if (-not (Test-CommandAvailable -Name "npm")) {
        throw "npm was not found. Install Node.js 20 or newer first."
    }

    $uvicorn = Join-Path $PSScriptRoot ".venv\Scripts\uvicorn.exe"
    if (-not (Test-Path -LiteralPath $uvicorn)) {
        if (-not (Test-CommandAvailable -Name "uv")) {
            throw "Neither .venv nor uv is available. Install uv and run this script again."
        }
        Write-RuntimeStep "Installing Python dependencies"
        Push-Location $PSScriptRoot
        try {
            & uv sync --frozen
            if ($LASTEXITCODE -ne 0) {
                throw "uv sync failed with exit code $LASTEXITCODE."
            }
        }
        finally {
            Pop-Location
        }
    }

    if (-not (Test-DockerDaemon)) {
        $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path -LiteralPath $dockerDesktop)) {
            throw "Docker Desktop is installed but its daemon is not available. Start Docker Desktop and retry."
        }
        Write-RuntimeStep "Starting Docker Desktop"
        Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
        Wait-RuntimeCondition `
            -Condition { Test-DockerDaemon } `
            -Description "Docker Desktop" `
            -TimeoutSeconds $TimeoutSeconds
    }

    Write-RuntimeStep "Starting Milvus"
    & docker compose `
        -f $script:ComposeFile `
        -f $script:ComposeLocalFile `
        up -d
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed with exit code $LASTEXITCODE."
    }
    Wait-RuntimeCondition `
        -Condition { (Get-MilvusHealth) -eq "healthy" } `
        -Description "Milvus" `
        -TimeoutSeconds $TimeoutSeconds

    if (-not $SkipBuild) {
        Write-RuntimeStep "Building the user workspace"
        $frontendDirectory = Join-Path $PSScriptRoot "frontend"
        Push-Location $frontendDirectory
        try {
            if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory "node_modules"))) {
                & npm ci
                if ($LASTEXITCODE -ne 0) {
                    throw "npm ci failed with exit code $LASTEXITCODE."
                }
            }
            & npm run build
            if ($LASTEXITCODE -ne 0) {
                throw "npm run build failed with exit code $LASTEXITCODE."
            }
        }
        finally {
            Pop-Location
        }
    }
    elseif (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "frontend\dist\index.html"))) {
        throw "frontend/dist is missing. Run without -SkipBuild once."
    }

    $selectedBackendPort = Resolve-RuntimePort `
        -Name "DeepSearcher API" `
        -PreferredPort $BackendPort `
        -FallbackStart 8750 `
        -HealthPath "/health/live"
    $selectedWorkspacePort = Resolve-RuntimePort `
        -Name "user workspace" `
        -PreferredPort $WorkspacePort `
        -FallbackStart 8700 `
        -HealthPath "/api/health/live" `
        -ExcludedPorts @($selectedBackendPort)
    Write-RuntimePorts `
        -BackendPort $selectedBackendPort `
        -WorkspacePort $selectedWorkspacePort

    $serviceToken = $env:DEEPSEARCHER_SERVICE_TOKEN
    if ([string]::IsNullOrWhiteSpace($serviceToken)) {
        $serviceToken = New-EphemeralServiceToken
    }
    $adminToken = $env:DEEPSEARCHER_ADMIN_TOKEN
    if ([string]::IsNullOrWhiteSpace($adminToken)) {
        $adminToken = New-EphemeralServiceToken
    }

    Write-RuntimeStep "Starting DeepSearcher API"
    Start-ManagedUvicorn `
        -Name "backend" `
        -Application "main:app" `
        -Port $selectedBackendPort `
        -HealthUrl "http://127.0.0.1:$selectedBackendPort/health/live" `
        -EnvironmentVariables @{
            DEEPSEARCHER_SERVICE_TOKEN = $serviceToken
            DEEPSEARCHER_ADMIN_TOKEN = $adminToken
        } `
        -TimeoutSeconds $TimeoutSeconds

    Write-RuntimeStep "Starting the document worker"
    Start-ManagedPythonModule `
        -Name "ingest-worker" `
        -Module "frontend.product.worker" `
        -EnvironmentVariables @{
            DEEPSEARCHER_API_URL = "http://127.0.0.1:$selectedBackendPort"
            DEEPSEARCHER_SERVICE_TOKEN = $serviceToken
        }

    Write-RuntimeStep "Starting the user workspace"
    Start-ManagedUvicorn `
        -Name "workspace" `
        -Application "frontend.server:app" `
        -Port $selectedWorkspacePort `
        -HealthUrl "http://127.0.0.1:$selectedWorkspacePort/api/health/live" `
        -EnvironmentVariables @{
            DEEPSEARCHER_API_URL = "http://127.0.0.1:$selectedBackendPort"
            DEEPSEARCHER_SERVICE_TOKEN = $serviceToken
        } `
        -TimeoutSeconds $TimeoutSeconds

    Write-RuntimeStep "Startup complete"
    & (Join-Path $PSScriptRoot "status.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "One or more services failed the final health check."
    }

    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:$selectedWorkspacePort" | Out-Null
    }
}
catch {
    Write-Error $_
    Write-Host ""
    Write-Host "See logs under: $(Join-Path $PSScriptRoot 'logs\runtime')" -ForegroundColor Yellow
    exit 1
}
