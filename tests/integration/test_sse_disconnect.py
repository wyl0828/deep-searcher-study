from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until_ready(base_url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 15
    last_response = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"SSE probe server exited early\nstdout={stdout}\nstderr={stderr}")
        try:
            last_response = httpx.get(
                f"{base_url}/health/live",
                timeout=0.5,
                trust_env=False,
            )
            if last_response.status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    details = (
        f"status={last_response.status_code} body={last_response.text}"
        if last_response is not None
        else "no HTTP response"
    )
    raise AssertionError(f"SSE probe server did not become ready: {details}")


def stop_and_close_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    pipes_are_open = any(
        stream is not None and not stream.closed for stream in (process.stdout, process.stderr)
    )
    try:
        if pipes_are_open:
            process.communicate(timeout=10)
        else:
            process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        if pipes_are_open:
            process.communicate(timeout=5)
        else:
            process.wait(timeout=5)
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def test_live_client_disconnect_cancels_query_and_releases_runtime():
    root = Path(__file__).resolve().parents[2]
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.sse_probe_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_until_ready(base_url, process)
        with httpx.stream(
            "POST",
            f"{base_url}/query/stream",
            headers={"X-DeepSearcher-Service-Token": "probe-service-token"},
            json={"original_query": "disconnect probe", "max_iter": 3},
            timeout=5,
            trust_env=False,
        ) as response:
            assert response.status_code == 200
            assert response.headers["x-trace-retention"] == "transient"
            assert next(line for line in response.iter_lines() if line).startswith("event: started")

        deadline = time.monotonic() + 10
        state = {}
        while time.monotonic() < deadline:
            state = httpx.get(
                f"{base_url}/probe/state",
                timeout=1,
                trust_env=False,
            ).json()
            if (
                state["cancelled"]
                and state["finished"]
                and state["runtime_references"] == 0
                and state["cleanup_tasks"] == 0
            ):
                break
            time.sleep(0.1)

        assert state == {
            "started": True,
            "cancelled": True,
            "finished": True,
            "runtime_references": 0,
            "cleanup_tasks": 0,
        }
    finally:
        stop_and_close_process(process)
