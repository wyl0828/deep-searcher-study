[CmdletBinding()]
param(
    [ValidateSet("Fast", "Live", "Full")]
    [string]$Mode = "Fast",
    [string]$OutputDir = "tmp/quality-gate/latest",
    [switch]$InstallDependencies,
    [switch]$SkipDocs,
    [string]$ApplicationCommit = "",
    [string]$CorpusCommit = "",
    [string]$ExecutionRole = "",
    [string]$ExecutionNode = ""
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$qualityGateArgs = @(
    "run",
    "--frozen",
    "python",
    "scripts/quality_gate.py",
    "--mode",
    $Mode.ToLowerInvariant(),
    "--output-dir",
    $OutputDir
)
if ($InstallDependencies) {
    $qualityGateArgs += "--install-dependencies"
}
if ($SkipDocs) {
    $qualityGateArgs += "--skip-docs"
}
if ($ApplicationCommit) {
    $qualityGateArgs += @("--application-commit", $ApplicationCommit)
}
if ($CorpusCommit) {
    $qualityGateArgs += @("--corpus-commit", $CorpusCommit)
}
if ($ExecutionRole) {
    $qualityGateArgs += @("--execution-role", $ExecutionRole)
}
if ($ExecutionNode) {
    $qualityGateArgs += @("--execution-node", $ExecutionNode)
}

Push-Location $projectRoot
try {
    & uv @qualityGateArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
