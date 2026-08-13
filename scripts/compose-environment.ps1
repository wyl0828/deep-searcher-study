[CmdletBinding()]
param(
    [ValidateSet("validate", "up", "stop", "status", "logs")]
    [string]$Action = "status",

    [ValidateSet("infra", "mq", "vector", "app", "full")]
    [string]$Group = "full",

    [string]$EnvFile = ".env.compose",

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
    mq     = @("rocketmq-namesrv", "rocketmq-broker", "rocketmq-init")
    vector = @("minio", "minio-init", "etcd", "milvus")
    app    = @("migrate", "core-api", "product-api-a", "product-api-b", "consumer-a", "consumer-b")
    full   = @()
}

Push-Location $projectRoot
try {
    switch ($Action) {
        "validate" {
            & docker @composeArgs config --quiet
        }
        "up" {
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
