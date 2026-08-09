[CmdletBinding()]
param(
    [ValidateSet("Fast", "Full")]
    [string]$Mode = "Fast",
    [string]$OutputDir = "tmp/quality-gate/latest",
    [switch]$InstallDependencies,
    [switch]$SkipDocs
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

Push-Location $projectRoot
try {
    & uv @qualityGateArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
