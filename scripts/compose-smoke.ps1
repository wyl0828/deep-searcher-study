[CmdletBinding()]
param(
    [string]$EnvFile = ".env.compose",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $projectRoot $EnvFile }
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "未找到环境文件：$envPath"
}

$values = @{}
Get-Content -LiteralPath $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $name, $value = $line.Split("=", 2)
        $values[$name.Trim()] = $value.Trim()
    }
}

$apiAPort = if ($values["PRODUCT_API_A_PORT"]) { $values["PRODUCT_API_A_PORT"] } else { "8600" }
$apiBPort = if ($values["PRODUCT_API_B_PORT"]) { $values["PRODUCT_API_B_PORT"] } else { "8601" }
$corePort = if ($values["CORE_API_PORT"]) { $values["CORE_API_PORT"] } else { "8500" }
$targets = @(
    "http://127.0.0.1:$corePort/health/ready",
    "http://127.0.0.1:$apiAPort/api/health/live",
    "http://127.0.0.1:$apiBPort/api/health/live"
)

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$pending = [System.Collections.Generic.HashSet[string]]::new([string[]]$targets)
while ($pending.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
    foreach ($target in @($pending)) {
        try {
            $response = Invoke-WebRequest -Uri $target -TimeoutSec 5 -UseBasicParsing
            if ($response.StatusCode -eq 200) {
                Write-Host "PASS $target"
                [void]$pending.Remove($target)
            }
        } catch {
            # 服务启动期间允许短暂失败，直到统一截止时间。
        }
    }
    if ($pending.Count -gt 0) { Start-Sleep -Seconds 2 }
}

if ($pending.Count -gt 0) {
    throw "以下健康检查未在 ${TimeoutSeconds}s 内通过：$($pending -join ', ')"
}

Write-Host "Compose API 冒烟检查通过。"
