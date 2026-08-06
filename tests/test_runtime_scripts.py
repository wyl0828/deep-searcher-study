from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PowerShell runtime scripts are Windows-only"
)
ROOT = Path(__file__).resolve().parents[1]
COMMON_SCRIPT = ROOT / "scripts" / "runtime-common.ps1"


def run_powershell(script: str) -> str:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def run_powershell_with_file_capture(script: str, output_directory: Path) -> str:
    stdout_path = output_directory / "powershell.stdout.log"
    stderr_path = output_directory / "powershell.stderr.log"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            timeout=30,
            check=False,
        )
    captured_stdout = stdout_path.read_text(encoding="utf-8").strip()
    captured_stderr = stderr_path.read_text(encoding="utf-8").strip()
    assert result.returncode == 0, captured_stderr
    return captured_stdout


def test_runtime_port_record_round_trips(tmp_path):
    runtime_directory = tmp_path.as_posix()
    output = run_powershell(
        f"""
. '{COMMON_SCRIPT.as_posix()}'
$script:RuntimeDirectory = '{runtime_directory}'
$script:RuntimePortsPath = Join-Path $script:RuntimeDirectory 'ports.json'
Write-RuntimePorts -BackendPort 8750 -WorkspacePort 8700
$ports = Get-RuntimePorts
Write-Output \"$($ports.BackendPort),$($ports.WorkspacePort)\"
"""
    )

    assert output == "8750,8700"


def test_local_port_probe_distinguishes_busy_and_available_ports():
    output = run_powershell(
        f"""
. '{COMMON_SCRIPT.as_posix()}'
$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$busy = Test-LocalPortBindable -Port $port
$listener.Stop()
$available = Test-LocalPortBindable -Port $port
Write-Output \"$busy,$available\"
"""
    )

    assert output == "False,True"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"status":"ready"}', "ready,True"),
        ('{"status":"degraded"}', "degraded,True"),
        ('{"status":"unexpected"}', "invalid,False"),
    ],
)
def test_http_health_result_preserves_json_health_semantics(payload, expected):
    escaped_payload = payload.replace("'", "''")
    output = run_powershell(
        f"""
. '{COMMON_SCRIPT.as_posix()}'
function Invoke-WebRequest {{
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec, $ErrorAction)
    return [pscustomobject]@{{
        StatusCode = 200
        Content = '{escaped_payload}'
    }}
}}
$result = Get-HttpHealthResult -Url 'http://health.test/status'
Write-Output "$($result.Status),$($null -ne $result.Payload)"
"""
    )

    assert output == expected


def test_managed_document_worker_starts_is_detected_and_stops(tmp_path):
    runtime_directory = (tmp_path / "runtime").as_posix()
    database_path = (tmp_path / "worker.db").as_posix()
    data_directory = (tmp_path / "data").as_posix()
    output = run_powershell_with_file_capture(
        f"""
. '{COMMON_SCRIPT.as_posix()}'
$script:RuntimeDirectory = '{runtime_directory}'
Start-ManagedPythonModule `
    -Name 'test-ingest-worker' `
    -Module 'frontend.product.worker' `
    -EnvironmentVariables @{{
        DEEPSEARCHER_DATABASE_URL = 'sqlite:///{database_path}'
        DEEPSEARCHER_DATA_DIR = '{data_directory}'
    }}
$running = Test-ManagedPythonModule `
    -Name 'test-ingest-worker' `
    -Module 'frontend.product.worker'
$metadataPath = Join-Path $script:RuntimeDirectory 'test-ingest-worker.process.json'
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
$metadata.module = 'unexpected.module'
[System.IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json))
$duplicateBlocked = $false
try {{
    Start-ManagedPythonModule `
        -Name 'test-ingest-worker' `
        -Module 'frontend.product.worker'
}}
catch {{
    $duplicateBlocked = $true
}}
$metadata.module = 'frontend.product.worker'
[System.IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json))
Stop-ManagedPythonModule `
    -Name 'test-ingest-worker' `
    -Module 'frontend.product.worker'
$stopped = -not (Test-ManagedPythonModule `
    -Name 'test-ingest-worker' `
    -Module 'frontend.product.worker')
Write-Output "worker-state=$running,$stopped,$duplicateBlocked"
""",
        tmp_path,
    )

    assert "worker-state=True,True,True" in output
