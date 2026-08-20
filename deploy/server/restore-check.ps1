<#
.SYNOPSIS
    在服务器验证最近（或指定）备份包可恢复：PostgreSQL 恢复检查、MinIO 恢复检查、Milvus 卷确认。
.DESCRIPTION
    - 从备份包解压 PostgreSQL dump，恢复到独立测试库 deepsearcher_restore_check，
      验证 11 张业务表存在且行数可查、alembic_version 存在，随后删除测试库。
    - 从备份包解压 MinIO 对象目录，恢复到测试 bucket deepsearcher-restore-check，
      对比对象数量，随后删除测试 bucket。
    - 确认 milvus-data 持久卷存在（向量丢失时的重建路径见项目文档）。
    不修改生产数据；只读取生产状态并在独立测试资源上验证。
.PARAMETER BackupName
    备份文件名 backup-YYYYMMDD-HHMMSS.tar.gz；缺省自动取服务器最新备份。
#>
[CmdletBinding()]
param(
    [string]$BackupName = "",
    [string]$Release = "20260815-01",
    [string]$Server = "",
    [string]$KeyPath = "",
    [string]$RemoteRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "config.ps1")
$serverConfig = Resolve-DeepSearcherServerConfig -Server $Server -KeyPath $KeyPath -RemoteRoot $RemoteRoot
$Server = $serverConfig.Server
$KeyPath = $serverConfig.KeyPath
$RemoteRoot = $serverConfig.RemoteRoot
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15", $Server)
$scpArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15")
$releaseRemote = "$RemoteRoot/releases/$Release"
$remoteScript = "$RemoteRoot/scripts/restore-check-run.sh"
$localScript = Join-Path $PSScriptRoot "restore-check.sh"

if (-not (Test-Path -LiteralPath $localScript)) { throw "缺少 restore-check.sh：$localScript" }

& ssh @sshArgs "mkdir -p $RemoteRoot/scripts"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir 失败" }
& scp @ScpArgs $localScript "$Server`:$remoteScript"
if ($LASTEXITCODE -ne 0) { throw "scp 恢复检查脚本失败" }

$cmd = "bash $remoteScript $releaseRemote $RemoteRoot"
if ($BackupName) {
    $cmd += " $RemoteRoot/backups/$BackupName"
}
$output = & ssh @sshArgs $cmd
$code = $LASTEXITCODE
$output | ForEach-Object { Write-Host $_ }
if ($code -ne 0) { throw "服务器恢复检查失败（退出码 $code）" }
Write-Host "恢复检查通过。"
