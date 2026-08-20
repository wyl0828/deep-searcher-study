# Shared resolver for deployment-node configuration.
# The tracked file contains no server identity. Use local.config.ps1 or
# DEEPSEARCHER_* environment variables for the current execution node.

function Resolve-DeepSearcherServerConfig {
    param(
        [string]$Server = "",
        [string]$KeyPath = "",
        [string]$RemoteRoot = ""
    )

    $localPath = Join-Path $PSScriptRoot "local.config.ps1"
    $localServer = ""
    $localKeyPath = ""
    $localRemoteRoot = ""
    if (Test-Path -LiteralPath $localPath) {
        . $localPath
        $localServer = [string]$DeepSearcherServer
        $localKeyPath = [string]$DeepSearcherSshKey
        $localRemoteRoot = [string]$DeepSearcherRemoteRoot
    }

    $resolvedServer = if ($Server) { $Server } elseif ($env:DEEPSEARCHER_SERVER) {
        $env:DEEPSEARCHER_SERVER
    } else { $localServer }
    $resolvedKeyPath = if ($KeyPath) { $KeyPath } elseif ($env:DEEPSEARCHER_SSH_KEY) {
        $env:DEEPSEARCHER_SSH_KEY
    } else { $localKeyPath }
    $resolvedRemoteRoot = if ($RemoteRoot) { $RemoteRoot } elseif ($env:DEEPSEARCHER_REMOTE_ROOT) {
        $env:DEEPSEARCHER_REMOTE_ROOT
    } else { $localRemoteRoot }

    $missing = @()
    if (-not $resolvedServer) { $missing += "DEEPSEARCHER_SERVER" }
    if (-not $resolvedKeyPath) { $missing += "DEEPSEARCHER_SSH_KEY" }
    if (-not $resolvedRemoteRoot) { $missing += "DEEPSEARCHER_REMOTE_ROOT" }
    if ($missing.Count -gt 0) {
        throw "缺少部署节点配置：$($missing -join ', ')。请使用命令行参数、环境变量或 deploy/server/local.config.ps1。"
    }

    return [pscustomobject]@{
        Server = $resolvedServer
        KeyPath = $resolvedKeyPath
        RemoteRoot = $resolvedRemoteRoot
    }
}
