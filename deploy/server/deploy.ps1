<#
.SYNOPSIS
    本地 Codex + SSH 方式部署 deep-searcher-study 到阿里云 ECS。
.DESCRIPTION
    动作：
      package  从已验证提交（默认 HEAD）生成干净 tar.gz 部署包到 tmp/deploy
      upload   将部署包上传到 /opt/deepsearcher-study/releases/<release> 并解压
      prepare  创建目录；首次生成私有 .env.server（随机密钥 + 可选的 Provider 密钥），不输出密钥值
      build    在服务器构建应用镜像，构建后清理无用构建缓存
      deploy   等价 package + upload + prepare + build
      validate 在服务器校验 docker compose config
      start    按分组启动（storage/vector/messaging/app/full）
      stop     停止分组容器（保留容器）
      down     移除项目容器与网络（保留命名卷）
      status   容器状态、资源、磁盘、日志体积
      logs     查看分组日志
.PARAMETER Action
    要执行的动作，默认 status。
.PARAMETER Release
    版本目录名，例如 20260815-01，默认按当天日期 + 序号。
.PARAMETER Server
    SSH 目标，默认 root@47.96.40.156。
.PARAMETER KeyPath
    SSH 私钥，默认 D:\code\ecs_key.pem。
.PARAMETER RemoteRoot
    服务器项目根，默认 /opt/deepsearcher-study。
.PARAMETER Group
    start/stop/logs 使用的分组。
.PARAMETER ProviderEnvFile
    本地 Provider 环境文件（含 DEEPSEEK_API_KEY/OPENAI_API_KEY），prepare 时合并到服务器 .env.server。
.PARAMETER Commit
    package 使用的提交，默认 HEAD。
#>
[CmdletBinding()]
param(
    [ValidateSet("package", "upload", "prepare", "build", "deploy", "validate", "start", "stop", "down", "status", "logs")]
    [string]$Action = "status",
    [string]$Release = "",
    [string]$Server = "root@47.96.40.156",
    [string]$KeyPath = "D:\code\ecs_key.pem",
    [string]$RemoteRoot = "/opt/deepsearcher-study",
    [ValidateSet("storage", "vector", "messaging", "app", "full")]
    [string]$Group = "full",
    [string]$ProviderEnvFile = "",
    [string]$Commit = "HEAD"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Release) {
    $today = Get-Date -Format "yyyyMMdd"
    $Release = "$today-01"
}
$SshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$ScpArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")

function Invoke-Ssh {
    param([string]$Command)
    Write-Host "[ssh] $Command"
    & ssh @SshArgs $Command
    if ($LASTEXITCODE -ne 0) { throw "SSH 命令失败（退出码 $LASTEXITCODE）：$Command" }
}

$envFileRemote = "$RemoteRoot/.env.server"
$releaseRemote = "$RemoteRoot/releases/$Release"
$compose = "docker compose --env-file $envFileRemote -f compose.yaml -f compose.server.yaml"

$groups = @{
    storage   = @("postgres", "redis", "minio", "minio-init")
    vector    = @("minio", "minio-init", "etcd", "milvus")
    messaging = @("rocketmq-namesrv", "rocketmq-permissions", "rocketmq-broker", "rocketmq-init")
    app       = @("migrate", "core-api", "product-api-a", "product-api-b", "consumer-a", "consumer-b")
    full      = @()
}

function New-RandomSecret {
    param([int]$Length = 48)
    $hex = [System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(($Length + 1) / 2))
    return $hex.Substring(0, [Math]::Min($Length, $hex.Length)).ToLowerInvariant()
}

switch ($Action) {
    "package" {
        if (-not (Test-Path -LiteralPath $KeyPath)) { throw "SSH 私钥不存在：$KeyPath" }
        $outDir = Join-Path $projectRoot "tmp\deploy"
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
        $tarball = Join-Path $outDir "$Release.tar.gz"
        if (Test-Path -LiteralPath $tarball) { Remove-Item -LiteralPath $tarball -Force }
        Push-Location $projectRoot
        try {
            $dirty = @(git status --porcelain)
            if ($dirty.Count -gt 0) {
                Write-Warning "工作树有未提交变更，部署包将只包含提交 $Commit 的内容：`n$($dirty -join "`n")"
            }
            & git archive --format=tar.gz --output=$tarball $Commit
            if ($LASTEXITCODE -ne 0) { throw "git archive 失败" }
        } finally {
            Pop-Location
        }
        $size = (Get-Item -LiteralPath $tarball).Length
        Write-Host "PACK  $tarball ($([Math]::Round($size / 1MB, 2)) MiB)"
    }
    "upload" {
        $tarball = Join-Path $projectRoot "tmp\deploy\$Release.tar.gz"
        if (-not (Test-Path -LiteralPath $tarball)) { throw "未找到部署包：$tarball，请先执行 package" }
        Invoke-Ssh "mkdir -p $releaseRemote"
        Write-Host "[scp] $tarball -> $Server`:$releaseRemote/"
        & scp @ScpArgs $tarball "$Server`:$releaseRemote/"
        if ($LASTEXITCODE -ne 0) { throw "scp 失败" }
        Invoke-Ssh "tar -xzf $releaseRemote/$Release.tar.gz -C $releaseRemote && chmod -R u+rwX,go+rX $releaseRemote && rm -f $releaseRemote/$Release.tar.gz"
        Invoke-Ssh "ls -la $releaseRemote | head -30"
    }
    "prepare" {
        Invoke-Ssh "mkdir -p $RemoteRoot/releases $RemoteRoot/shared $RemoteRoot/backups"
        $exists = (& ssh @SshArgs "test -f $envFileRemote && echo YES || echo NO" | Select-Object -Last 1).Trim()
        if ($exists -eq "YES") {
            Write-Host "ENV   已存在 $envFileRemote，跳过生成"
        } else {
            $example = Join-Path $projectRoot ".env.server.example"
            if (-not (Test-Path -LiteralPath $example)) { throw "缺少本地模板：$example" }
            $lines = Get-Content -LiteralPath $example
            $replace = @{
                "change-this-postgres-password"          = New-RandomSecret 32
                "change-this-minio-password"             = New-RandomSecret 32
                "change-this-service-token"              = New-RandomSecret 48
                "change-this-admin-token"                = New-RandomSecret 48
                "change-this-session-secret-at-least-32-bytes" = New-RandomSecret 64
            }
            $rendered = foreach ($line in $lines) {
                $out = $line
                foreach ($k in $replace.Keys) {
                    if ($out -like "*$k*") { $out = $out.Replace($k, $replace[$k]) }
                }
                $out
            }
            $provider = @{}
            if ($ProviderEnvFile -and (Test-Path -LiteralPath $ProviderEnvFile)) {
                $canonical = @("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL")
                Get-Content -LiteralPath $ProviderEnvFile | ForEach-Object {
                    $t = $_.Trim()
                    if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
                        $n, $v = $t.Split("=", 2)
                        foreach ($canon in $canonical) {
                            if ($n.Trim() -ieq $canon) { $provider[$canon] = $v.Trim() }
                        }
                    }
                }
                $rendered = foreach ($line in $rendered) {
                    $out = $line
                    foreach ($n in $canonical) {
                        if ($provider.ContainsKey($n) -and $out -match "^$n=") { $out = "$n=$($provider[$n])" }
                    }
                    $out
                }
            }
            $temp = Join-Path $env:TEMP "deepsearcher-env-server-$PID"
            [System.IO.File]::WriteAllLines($temp, $rendered, (New-Object System.Text.UTF8Encoding($false)))
            try {
                & scp @ScpArgs $temp "$Server`:$envFileRemote"
                if ($LASTEXITCODE -ne 0) { throw "scp .env.server 失败" }
            } finally {
                Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
            }
            Write-Host "ENV   已生成 $envFileRemote"
        }
        $summaryCmd = @(
            "echo -n POSTGRES_PASSWORD=; grep -E '^POSTGRES_PASSWORD=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n MINIO_ROOT_PASSWORD=; grep -E '^MINIO_ROOT_PASSWORD=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n DEEPSEARCHER_SERVICE_TOKEN=; grep -E '^DEEPSEARCHER_SERVICE_TOKEN=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n DEEPSEARCHER_ADMIN_TOKEN=; grep -E '^DEEPSEARCHER_ADMIN_TOKEN=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n DEEPSEARCHER_SESSION_SECRET=; grep -E '^DEEPSEARCHER_SESSION_SECRET=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n DEEPSEEK_API_KEY=; grep -E '^DEEPSEEK_API_KEY=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c;"
            "echo -n OPENAI_API_KEY=; grep -E '^OPENAI_API_KEY=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]' | wc -c"
        ) -join " "
        Write-Host "ENV   密钥项（键=长度，不显示值）："
        (& ssh @SshArgs $summaryCmd) | ForEach-Object { Write-Host "      $_" }
        $missing = @()
        foreach ($k in @("DEEPSEEK_API_KEY", "OPENAI_API_KEY")) {
            $val = (& ssh @SshArgs "grep -E '^$k=' $envFileRemote | head -1 | cut -d= -f2- | tr -d '[:space:]'")
            if (-not $val) { $missing += $k }
        }
        if ($missing.Count -gt 0) {
            Write-Warning "Provider 密钥为空：$($missing -join ', ')。完整业务链路前请在服务器编辑 $envFileRemote 填入。"
        }
    }
    "build" {
        Invoke-Ssh "df -h / | tail -1"
        $buildTargets = @("migrate", "core-api", "product-api-a", "product-api-b", "consumer-a", "consumer-b")
        Invoke-Ssh "cd $releaseRemote && $compose build $($buildTargets -join ' ')"
        Invoke-Ssh "docker builder prune -f >/dev/null 2>&1 || true"
        Invoke-Ssh "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep deepsearcher-study"
        Invoke-Ssh "df -h / | tail -1"
    }
    "deploy" {
        & $MyInvocation.MyCommand -Action package -Release $Release -Server $Server -KeyPath $KeyPath -RemoteRoot $RemoteRoot
        & $MyInvocation.MyCommand -Action upload -Release $Release -Server $Server -KeyPath $KeyPath -RemoteRoot $RemoteRoot
        & $MyInvocation.MyCommand -Action prepare -Release $Release -Server $Server -KeyPath $KeyPath -RemoteRoot $RemoteRoot -ProviderEnvFile $ProviderEnvFile
        & $MyInvocation.MyCommand -Action build -Release $Release -Server $Server -KeyPath $KeyPath -RemoteRoot $RemoteRoot
    }
    "validate" {
        Invoke-Ssh "cd $releaseRemote && $compose config --quiet"
        Write-Host "VALID 服务器 docker compose config 通过"
    }
    "start" {
        $targets = if ($Group -eq "full") { "" } else { $groups[$Group] -join " " }
        Invoke-Ssh "cd $releaseRemote && $compose up -d $targets"
        Invoke-Ssh "cd $releaseRemote && $compose ps --all"
    }
    "stop" {
        $targets = if ($Group -eq "full") { "" } else { $groups[$Group] -join " " }
        Invoke-Ssh "cd $releaseRemote && $compose stop $targets"
    }
    "down" {
        Invoke-Ssh "cd $releaseRemote && $compose down --remove-orphans"
        Write-Host "DOWN  已移除项目容器与网络（命名卷保留）"
    }
    "status" {
        Invoke-Ssh "cd $releaseRemote && $compose ps --all"
        Write-Host "--- docker stats ---"
        Invoke-Ssh "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'deepsearcher|NAME' || true"
        Write-Host "--- 磁盘 / ---"
        Invoke-Ssh "df -h /"
        Write-Host "--- 内存 / Swap ---"
        Invoke-Ssh "free -h && swapon --show || true"
        Write-Host "--- Docker 日志体积 ---"
        Invoke-Ssh "find /var/lib/docker/containers -name '*-json.log' -exec du -ch {} + 2>/dev/null | tail -1"
    }
    "logs" {
        $targets = if ($Group -eq "full") { "" } else { $groups[$Group] -join " " }
        Invoke-Ssh "cd $releaseRemote && $compose logs --tail 200 $targets"
    }
}