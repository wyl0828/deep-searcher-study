[CmdletBinding()]
param(
    [ValidateSet("validate", "up", "stop", "status", "logs")]
    [string]$Action = "status",

    [ValidateSet("infra", "mq", "vector", "app", "full")]
    [string]$Group = "full",

    [string]$EnvFile = ".env.compose",

    [string]$ProviderEnvFile = ".env",

    [switch]$Build
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile
} else {
    Join-Path $projectRoot $EnvFile
}

if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "未找到环境文件：$envPath。请先复制 env.compose.example 为 .env.compose 并修改密码与密钥。"
}

$composeArgs = @(
    "compose",
    "--env-file", $envPath,
    "-f", (Join-Path $projectRoot "compose.yaml"),
    "-f", (Join-Path $projectRoot "compose.local.yaml")
)

$services = @{
    infra  = @("postgres", "redis", "minio", "minio-init")
    mq     = @("rocketmq-namesrv", "rocketmq-permissions", "rocketmq-broker", "rocketmq-init")
    vector = @("minio", "minio-init", "etcd", "milvus")
    app    = @("migrate", "core-api", "product-api-a", "product-api-b", "consumer-a", "consumer-b")
    full   = @()
}

function Import-ProviderEnvironment {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "未找到 Provider 环境文件：$Path。app/full 模式需要有效的模型与 Embedding 凭据。"
    }
    $values = @{}
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $name, $value = $line.Split("=", 2)
            $values[$name.Trim()] = $value.Trim()
        }
    }
    foreach ($required in @("DEEPSEEK_API_KEY", "OPENAI_API_KEY")) {
        $value = [string]$values[$required]
        if (-not $value -or $value -match "^(your|change-this|replace|example|sk-xxxx|xxxx)") {
            throw "$required 未配置或仍是示例占位符，拒绝启动 app/full 模式。"
        }
    }
    foreach ($name in @("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL")) {
        if ($values.ContainsKey($name) -and $values[$name]) {
            Set-Item -Path "Env:$name" -Value $values[$name]
        }
    }
}

Push-Location $projectRoot
try {
    switch ($Action) {
        "validate" {
            & docker @composeArgs config --quiet
        }
        "up" {
            if ($Group -in @("app", "full")) {
                $providerPath = if ([System.IO.Path]::IsPathRooted($ProviderEnvFile)) {
                    $ProviderEnvFile
                } else {
                    Join-Path $projectRoot $ProviderEnvFile
                }
                Import-ProviderEnvironment -Path $providerPath
            }
            $upArgs = @("up", "-d")
            if ($Build) { $upArgs += "--build" }
            $upArgs += $services[$Group]
            & docker @composeArgs @upArgs
        }
        "stop" {
            if ($Group -eq "full") {
                & docker @composeArgs down --remove-orphans
            } else {
                & docker @composeArgs stop @($services[$Group])
                & docker @composeArgs rm -f @($services[$Group])
            }
        }
        "status" {
            & docker @composeArgs ps --all @($services[$Group])
        }
        "logs" {
            & docker @composeArgs logs --tail 200 @($services[$Group])
        }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 执行失败，退出码：$LASTEXITCODE"
    }
} finally {
    Pop-Location
}
