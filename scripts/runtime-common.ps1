Set-StrictMode -Version Latest

$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$script:RuntimeDirectory = Join-Path $script:ProjectRoot "logs\runtime"
$script:RuntimePortsPath = Join-Path $script:RuntimeDirectory "ports.json"
$script:ComposeFile = Join-Path $script:ProjectRoot "infra\milvus\docker-compose.yml"
$script:ComposeLocalFile = Join-Path $script:ProjectRoot "infra\milvus\docker-compose.local.yml"

function Write-RuntimeStep {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ("==> {0}" -f $Message) -ForegroundColor Cyan
}

function Test-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 3
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Get-HttpHealthResult {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 3
    )

    try {
        $response = Invoke-WebRequest `
            -Uri $Url `
            -UseBasicParsing `
            -TimeoutSec $TimeoutSeconds `
            -ErrorAction Stop
        $payload = $response.Content | ConvertFrom-Json -ErrorAction Stop
        $status = [string]$payload.status
        if ($status -notin @("alive", "ready", "degraded", "not_ready")) {
            $status = "invalid"
            $payload = $null
        }
        return [pscustomobject]@{
            Status = $status
            Payload = $payload
        }
    }
    catch {
        $status = "unreachable"
        try {
            if ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 503) {
                $status = "not_ready"
            }
        }
        catch {
            $status = "unreachable"
        }
        return [pscustomobject]@{
            Status = $status
            Payload = $null
        }
    }
}

function Get-ListenerProcessId {
    param([Parameter(Mandatory = $true)][int]$Port)

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection) {
        return $null
    }
    return [int]$connection.OwningProcess
}

function Test-LocalPortBindable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            $Port
        )
        $listener.Start()
        return $true
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Resolve-RuntimePort {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$PreferredPort,
        [Parameter(Mandatory = $true)][int]$FallbackStart,
        [Parameter(Mandatory = $true)][string]$HealthPath,
        [int[]]$ExcludedPorts = @()
    )

    $candidates = @(
        @($PreferredPort) + ($FallbackStart..([Math]::Min($FallbackStart + 99, 65535))) |
            Select-Object -Unique
    )
    foreach ($candidate in $candidates) {
        if ($ExcludedPorts -contains $candidate) {
            continue
        }
        $healthUrl = "http://127.0.0.1:$candidate$HealthPath"
        $bindable = Test-LocalPortBindable -Port $candidate
        $managedServiceReady = $false
        if (-not $bindable -and $null -ne (Get-ListenerProcessId -Port $candidate)) {
            $managedServiceReady = Test-HttpEndpoint -Url $healthUrl
        }
        if ($bindable -or $managedServiceReady) {
            if ($candidate -ne $PreferredPort) {
                Write-Warning (
                    "Port {0} is unavailable for {1}; using {2} instead." -f `
                        $PreferredPort, $Name, $candidate
                )
            }
            return [int]$candidate
        }
    }
    throw "No available local port was found for $Name."
}

function Write-RuntimePorts {
    param(
        [Parameter(Mandatory = $true)][int]$BackendPort,
        [Parameter(Mandatory = $true)][int]$WorkspacePort
    )

    if (-not (Test-Path -LiteralPath $script:RuntimeDirectory)) {
        New-Item -ItemType Directory -Path $script:RuntimeDirectory -Force | Out-Null
    }
    $payload = [ordered]@{
        backend_port = $BackendPort
        workspace_port = $WorkspacePort
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($script:RuntimePortsPath, $payload)
}

function Get-RuntimePorts {
    $backendPort = 8650
    $workspacePort = 8600
    if (Test-Path -LiteralPath $script:RuntimePortsPath) {
        try {
            $payload = Get-Content -LiteralPath $script:RuntimePortsPath -Raw | ConvertFrom-Json
            if ([int]$payload.backend_port -ge 1024 -and [int]$payload.backend_port -le 65535) {
                $backendPort = [int]$payload.backend_port
            }
            if ([int]$payload.workspace_port -ge 1024 -and [int]$payload.workspace_port -le 65535) {
                $workspacePort = [int]$payload.workspace_port
            }
        }
        catch {
            Write-Warning "Runtime port record is invalid; using default ports."
        }
    }
    return [pscustomobject]@{
        BackendPort = $backendPort
        WorkspacePort = $workspacePort
    }
}

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMilliseconds = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-RuntimeCondition {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Condition,
        [Parameter(Mandatory = $true)][string]$Description,
        [int]$TimeoutSeconds = 120
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) {
            return
        }
        Start-Sleep -Milliseconds 800
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Description after $TimeoutSeconds seconds."
}

function Test-DockerDaemon {
    if (-not (Test-CommandAvailable -Name "docker")) {
        return $false
    }

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & docker info 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Get-MilvusHealth {
    if (-not (Test-DockerDaemon)) {
        return "docker-unavailable"
    }

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $health = (& docker inspect --format "{{.State.Health.Status}}" milvus-standalone 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($health)) {
            return "not-created"
        }
        return $health
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Start-ManagedUvicorn {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Application,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$HealthUrl,
        [hashtable]$EnvironmentVariables = @{},
        [int]$TimeoutSeconds = 120
    )

    $listenerId = Get-ListenerProcessId -Port $Port
    if ($null -ne $listenerId) {
        if (Test-HttpEndpoint -Url $HealthUrl) {
            Write-Host ("{0} is already ready on port {1} (PID {2})." -f $Name, $Port, $listenerId)
            return
        }
        throw "Port $Port is already used by PID $listenerId, but $Name is not healthy. Stop that process or choose another port."
    }

    if (-not (Test-Path -LiteralPath $script:RuntimeDirectory)) {
        New-Item -ItemType Directory -Path $script:RuntimeDirectory -Force | Out-Null
    }

    $uvicorn = Join-Path $script:ProjectRoot ".venv\Scripts\uvicorn.exe"
    if (-not (Test-Path -LiteralPath $uvicorn)) {
        throw "Python environment is incomplete: $uvicorn was not found."
    }

    $safeName = $Name.ToLowerInvariant().Replace(" ", "-")
    $stdoutPath = Join-Path $script:RuntimeDirectory "$safeName.stdout.log"
    $stderrPath = Join-Path $script:RuntimeDirectory "$safeName.stderr.log"
    $pidPath = Join-Path $script:RuntimeDirectory "$safeName.pid"

    $originalEnvironment = @{}
    foreach ($key in $EnvironmentVariables.Keys) {
        $originalEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$EnvironmentVariables[$key], "Process")
    }

    try {
        $process = Start-Process `
            -FilePath $uvicorn `
            -ArgumentList @($Application, "--host", "127.0.0.1", "--port", $Port.ToString()) `
            -WorkingDirectory $script:ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
    }
    finally {
        foreach ($key in $EnvironmentVariables.Keys) {
            [Environment]::SetEnvironmentVariable($key, $originalEnvironment[$key], "Process")
        }
    }

    [System.IO.File]::WriteAllText($pidPath, $process.Id.ToString())

    try {
        Wait-RuntimeCondition `
            -Condition {
                if ($process.HasExited) {
                    throw "$Name exited before its health endpoint became ready."
                }
                Test-HttpEndpoint -Url $HealthUrl
            } `
            -Description "$Name health endpoint" `
            -TimeoutSeconds $TimeoutSeconds
    }
    catch {
        $tail = ""
        if (Test-Path -LiteralPath $stderrPath) {
            $tail = (Get-Content -LiteralPath $stderrPath -Tail 20 -ErrorAction SilentlyContinue | Out-String).Trim()
        }
        if (-not [string]::IsNullOrWhiteSpace($tail)) {
            throw "$($_.Exception.Message)`n$tail"
        }
        throw
    }

    Write-Host ("{0} is ready on port {1} (PID {2})." -f $Name, $Port, $process.Id)
}

function Start-ManagedPythonModule {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module,
        [hashtable]$EnvironmentVariables = @{}
    )

    if (-not (Test-Path -LiteralPath $script:RuntimeDirectory)) {
        New-Item -ItemType Directory -Path $script:RuntimeDirectory -Force | Out-Null
    }
    $python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python environment is incomplete: $python was not found."
    }
    $safeName = $Name.ToLowerInvariant().Replace(" ", "-")
    $stdoutPath = Join-Path $script:RuntimeDirectory "$safeName.stdout.log"
    $stderrPath = Join-Path $script:RuntimeDirectory "$safeName.stderr.log"
    $pidPath = Join-Path $script:RuntimeDirectory "$safeName.pid"
    $metadataPath = Join-Path $script:RuntimeDirectory "$safeName.process.json"

    if (Test-ManagedPythonModule -Name $Name -Module $Module) {
        $runningId = [int](Get-Content -LiteralPath $pidPath -Raw)
        Write-Host ("{0} is already running (PID {1})." -f $Name, $runningId)
        return
    }
    if (Test-Path -LiteralPath $pidPath) {
        $unverifiedId = 0
        $hasUnverifiedProcess = (
            [int]::TryParse(
                (Get-Content -LiteralPath $pidPath -Raw).Trim(),
                [ref]$unverifiedId
            ) -and
            $null -ne (Get-Process -Id $unverifiedId -ErrorAction SilentlyContinue)
        )
        if ($hasUnverifiedProcess) {
            throw "$Name has a live but unverified PID record; refusing to start a duplicate process."
        }
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $metadataPath -Force -ErrorAction SilentlyContinue
    }

    $originalEnvironment = @{}
    foreach ($key in $EnvironmentVariables.Keys) {
        $originalEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, [string]$EnvironmentVariables[$key], "Process")
    }
    try {
        $process = Start-Process `
            -FilePath $python `
            -ArgumentList @("-m", $Module) `
            -WorkingDirectory $script:ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
    }
    finally {
        foreach ($key in $EnvironmentVariables.Keys) {
            [Environment]::SetEnvironmentVariable($key, $originalEnvironment[$key], "Process")
        }
    }
    [System.IO.File]::WriteAllText($pidPath, $process.Id.ToString())
    $process.Refresh()
    $processMetadata = [ordered]@{
        version = 1
        pid = $process.Id
        module = $Module
        project_root = $script:ProjectRoot
        executable_path = $python
        started_at_utc = $process.StartTime.ToUniversalTime().ToString("o")
        started_at_utc_ticks = $process.StartTime.ToUniversalTime().Ticks
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($metadataPath, $processMetadata)
    Start-Sleep -Milliseconds 800
    if ($process.HasExited) {
        $tail = (Get-Content -LiteralPath $stderrPath -Tail 20 -ErrorAction SilentlyContinue | Out-String).Trim()
        throw "$Name exited during startup.`n$tail"
    }
    Write-Host ("{0} is running (PID {1})." -f $Name, $process.Id)
}

function Test-ManagedPythonModule {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module
    )

    $safeName = $Name.ToLowerInvariant().Replace(" ", "-")
    $pidPath = Join-Path $script:RuntimeDirectory "$safeName.pid"
    $metadataPath = Join-Path $script:RuntimeDirectory "$safeName.process.json"
    if (-not (Test-Path -LiteralPath $pidPath)) {
        return $false
    }
    $processId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$processId)) {
        return $false
    }
    $processInfo = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $processInfo -or $processInfo.ProcessName -notmatch "^python$") {
        return $false
    }
    $expectedPython = [System.IO.Path]::GetFullPath(
        (Join-Path $script:ProjectRoot ".venv\Scripts\python.exe")
    )
    try {
        $actualPython = [System.IO.Path]::GetFullPath($processInfo.Path)
        if (-not $actualPython.Equals($expectedPython, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
        if (Test-Path -LiteralPath $metadataPath) {
            $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
            if ($metadata.PSObject.Properties.Name -contains "started_at_utc_ticks") {
                $metadataStartTicks = [long]$metadata.started_at_utc_ticks
            }
            else {
                $metadataStartValue = $metadata.started_at_utc
                if ($metadataStartValue -is [datetime]) {
                    $metadataStart = [datetime]::SpecifyKind(
                        [datetime]$metadataStartValue,
                        [System.DateTimeKind]::Utc
                    )
                }
                else {
                    $metadataStart = [datetime]::Parse(
                        [string]$metadataStartValue,
                        [System.Globalization.CultureInfo]::InvariantCulture,
                        [System.Globalization.DateTimeStyles]::RoundtripKind
                    ).ToUniversalTime()
                }
                $metadataStartTicks = $metadataStart.Ticks
            }
            $metadataExecutable = [System.IO.Path]::GetFullPath(
                [string]$metadata.executable_path
            )
            return (
                [int]$metadata.version -eq 1 -and
                [int]$metadata.pid -eq $processId -and
                [string]$metadata.module -eq $Module -and
                ([System.IO.Path]::GetFullPath([string]$metadata.project_root)).Equals(
                    [System.IO.Path]::GetFullPath($script:ProjectRoot),
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                $metadataExecutable.Equals(
                    $expectedPython,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                [Math]::Abs(
                    $processInfo.StartTime.ToUniversalTime().Ticks - $metadataStartTicks
                ) -le (2 * [TimeSpan]::TicksPerSecond)
            )
        }

        # Backward-compatible validation for a PID file written before process metadata existed.
        $pidWrittenAt = (Get-Item -LiteralPath $pidPath).LastWriteTimeUtc
        return [Math]::Abs(
            ($processInfo.StartTime.ToUniversalTime() - $pidWrittenAt).TotalSeconds
        ) -le 10
    }
    catch {
        return $false
    }
}

function Stop-ManagedPythonModule {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module
    )

    $safeName = $Name.ToLowerInvariant().Replace(" ", "-")
    $pidPath = Join-Path $script:RuntimeDirectory "$safeName.pid"
    $metadataPath = Join-Path $script:RuntimeDirectory "$safeName.process.json"
    if (-not (Test-ManagedPythonModule -Name $Name -Module $Module)) {
        $unverifiedId = 0
        $hasUnverifiedProcess = (
            (Test-Path -LiteralPath $pidPath) -and
            [int]::TryParse(
                (Get-Content -LiteralPath $pidPath -Raw).Trim(),
                [ref]$unverifiedId
            ) -and
            $null -ne (Get-Process -Id $unverifiedId -ErrorAction SilentlyContinue)
        )
        if ($hasUnverifiedProcess) {
            throw "$Name has a live but unverified PID record; refusing to stop an unknown process."
        }
        if (Test-Path -LiteralPath $pidPath) {
            Remove-Item -LiteralPath $pidPath -Force
        }
        Remove-Item -LiteralPath $metadataPath -Force -ErrorAction SilentlyContinue
        Write-Host ("{0} was not running." -f $Name)
        return
    }
    $processId = [int](Get-Content -LiteralPath $pidPath -Raw)
    Stop-Process -Id $processId -ErrorAction Stop
    try {
        Wait-RuntimeCondition `
            -Condition { $null -eq (Get-Process -Id $processId -ErrorAction SilentlyContinue) } `
            -Description "$Name process to stop" `
            -TimeoutSeconds 10
    }
    catch {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $metadataPath -Force -ErrorAction SilentlyContinue
    Write-Host ("Stopped {0} (PID {1})." -f $Name, $processId)
}

function Stop-ManagedUvicorn {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ExpectedApplication
    )

    $safeName = $Name.ToLowerInvariant().Replace(" ", "-")
    $pidPath = Join-Path $script:RuntimeDirectory "$safeName.pid"
    $candidateIds = New-Object System.Collections.Generic.List[int]

    if (Test-Path -LiteralPath $pidPath) {
        $storedId = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$storedId)) {
            $candidateIds.Add($storedId)
        }
    }

    $listenerId = Get-ListenerProcessId -Port $Port
    if ($null -ne $listenerId -and -not $candidateIds.Contains($listenerId)) {
        $candidateIds.Add($listenerId)
    }

    $stopped = $false
    foreach ($candidateId in $candidateIds) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $candidateId" -ErrorAction SilentlyContinue
        if ($null -eq $processInfo) {
            continue
        }

        $commandLine = [string]$processInfo.CommandLine
        $expectedUvicorn = Join-Path $script:ProjectRoot ".venv\Scripts\uvicorn.exe"
        $isUvicornLauncher = $processInfo.Name -match "^uvicorn(\.exe)?$"
        $isPythonUvicornHost = (
            $processInfo.Name -match "^python(\.exe)?$" -and
            $commandLine -like "*$expectedUvicorn*"
        )
        $isExpectedApplication = (
            $commandLine -like "*$ExpectedApplication*" -and
            $commandLine -like "*$script:ProjectRoot*"
        )
        if ((-not $isUvicornLauncher -and -not $isPythonUvicornHost) -or -not $isExpectedApplication) {
            Write-Warning ("Skipped PID {0}; it does not look like the expected {1} process." -f $candidateId, $Name)
            continue
        }

        Stop-Process -Id $candidateId -ErrorAction Stop
        try {
            Wait-RuntimeCondition `
                -Condition { $null -eq (Get-Process -Id $candidateId -ErrorAction SilentlyContinue) } `
                -Description "$Name process to stop" `
                -TimeoutSeconds 10
        }
        catch {
            Stop-Process -Id $candidateId -Force -ErrorAction SilentlyContinue
        }
        Write-Host ("Stopped {0} (PID {1})." -f $Name, $candidateId)
        $stopped = $true
    }

    if (Test-Path -LiteralPath $pidPath) {
        Remove-Item -LiteralPath $pidPath -Force
    }
    if (-not $stopped) {
        Write-Host ("{0} was not running." -f $Name)
    }
}
