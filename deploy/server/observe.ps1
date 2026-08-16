<#
.SYNOPSIS
    管理服务器 deep-searcher-study 长时间观察监控（阶段一/七）。
.DESCRIPTION
    Action：
      start  启动后台观察（默认 240 分钟、每 60s 采样一次），追加到 observation.log；
             可再次 start 补足缺失观察时间。
      status 查看监控进程与日志覆盖范围（样本数/首末时间/告警数）。
      stop   停止监控进程。
      clean  清空 observation.log 与进程文件。
    观察日志：/opt/deepsearcher-study/backups/observation.log
.PARAMETER Action
    必填：start|status|stop|clean。
.PARAMETER DurationMinutes
    start 时的观察时长（分钟），默认 240。
.PARAMETER IntervalSeconds
    采样间隔（秒），默认 60。
.PARAMETER DownloadLog
    在 status 时把 observation.log 下载到本地 OutDir。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "status", "stop", "clean")]
    [string]$Action,
    [int]$DurationMinutes = 240,
    [int]$IntervalSeconds = 60,
    [string]$Server = "root@47.96.40.156",
    [string]$KeyPath = "D:\code\ecs_key.pem",
    [string]$RemoteRoot = "/opt/deepsearcher-study",
    [switch]$DownloadLog,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$scpArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")
$remoteScript = "$RemoteRoot/scripts/observe-run.sh"
$localScript = Join-Path $PSScriptRoot "observe.sh"

if (-not (Test-Path -LiteralPath $localScript)) { throw "缺少 observe.sh：$localScript" }

& ssh @sshArgs "mkdir -p $RemoteRoot/scripts"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir 失败" }
& scp @ScpArgs $localScript "$Server`:$remoteScript"
if ($LASTEXITCODE -ne 0) { throw "scp 观察脚本失败" }

$cmd = "bash $remoteScript $RemoteRoot $Action"
if ($Action -eq "start") { $cmd += " $DurationMinutes $IntervalSeconds" }
$output = & ssh @sshArgs $cmd
$code = $LASTEXITCODE
$output | ForEach-Object { Write-Host $_ }
if ($code -ne 0) { throw "观察命令失败（退出码 $code）" }

if ($DownloadLog) {
    if (-not $OutDir) { $OutDir = Join-Path $projectRoot "tmp\backups" }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $local = Join-Path $OutDir "observation.log"
    & scp @ScpArgs "$Server`:$RemoteRoot/backups/observation.log" $local
    if ($LASTEXITCODE -ne 0) { throw "下载 observation.log 失败" }
    Write-Host "LOCAL   $local"
}