<#
.SYNOPSIS
    在服务器执行 deep-searcher-study 数据备份并打包，可选下载到本地。
.DESCRIPTION
    备份范围（对应计划第五阶段）：
      - PostgreSQL 业务数据（pg_dump 自定义格式）
      - MinIO 文档对象（mc mirror 到备份目录）
      - 关键服务器配置：.env.server、compose 文件、Nginx 项目配置、容器状态、版本信息
    产物：/opt/deepsearcher-study/backups/backup-<TS>.tar.gz
    -Download 时同时下载到本地 OutDir。
.PARAMETER Release
    版本目录名，默认 20260815-01。
.PARAMETER Download
    是否将备份包下载到本地，默认否。
.PARAMETER OutDir
    本地下载目录，默认 <项目根>/tmp/backups。
#>
[CmdletBinding()]
param(
    [string]$Release = "20260815-01",
    [string]$Server = "root@47.96.40.156",
    [string]$KeyPath = "D:\code\ecs_key.pem",
    [string]$RemoteRoot = "/opt/deepsearcher-study",
    [switch]$Download,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$scpArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")
$releaseRemote = "$RemoteRoot/releases/$Release"
$remoteScript = "$RemoteRoot/scripts/backup-run.sh"
$localScript = Join-Path $PSScriptRoot "backup.sh"

if (-not (Test-Path -LiteralPath $localScript)) { throw "缺少 backup.sh：$localScript" }

& ssh @sshArgs "mkdir -p $RemoteRoot/scripts"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir 失败" }
& scp @ScpArgs $localScript "$Server`:$remoteScript"
if ($LASTEXITCODE -ne 0) { throw "scp 备份脚本失败" }

$output = & ssh @sshArgs "bash $remoteScript $releaseRemote $RemoteRoot"
if ($LASTEXITCODE -ne 0) { throw "服务器备份脚本执行失败（退出码 $LASTEXITCODE）" }
$output | ForEach-Object { Write-Host $_ }

$m = $output | Select-String -Pattern "^BACKUP_FILE=(.+)$"
if (-not $m) { throw "未从服务器输出中识别到 BACKUP_FILE" }
$backupFile = $m.Matches[0].Groups[1].Value.Trim()
Write-Host ""
Write-Host "BACKUP  $backupFile"

if ($Download) {
    if (-not $OutDir) { $OutDir = Join-Path $projectRoot "tmp\backups" }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $name = Split-Path $backupFile -Leaf
    $local = Join-Path $OutDir $name
    & scp @ScpArgs "$Server`:$backupFile" $local
    if ($LASTEXITCODE -ne 0) { throw "下载备份包失败" }
    $size = (Get-Item -LiteralPath $local).Length
    Write-Host "LOCAL   $local ($([Math]::Round($size / 1MB, 2)) MiB)"
}