"""Identity and ownership helpers for the canonical evaluator inputs."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_COLLECTION = "eval_workspace_v2"
DEFAULT_DATASET_PATH = ROOT / "evaluation" / "datasets" / "workspace_v2.json"
DEFAULT_QUALITY_GATE_PATH = ROOT / "evaluation" / "quality_gate.json"
DEFAULT_RUNTIME_CONFIG_PATH = ROOT / "deepsearcher" / "config.yaml"


def assert_evaluator_collection(collection: str) -> str:
    """Require the exact collection owned by the quality evaluator."""
    normalized = str(collection or "").strip()
    if normalized != EVALUATOR_COLLECTION:
        raise ValueError(
            "quality evaluator prepare is restricted to the exact owned collection "
            f"{EVALUATOR_COLLECTION!r}; got {normalized!r}"
        )
    return normalized


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _identity(explicit: str | None, env_name: str, fallback: str) -> str:
    return str(explicit or os.environ.get(env_name) or fallback).strip() or fallback


def _source_paths(dataset_path: Path, root: Path) -> list[Path]:
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    raw_sources = raw.get("sources")
    if raw_sources is None:
        raw_sources = [raw.get("source")]
    paths: list[Path] = []
    for item in raw_sources:
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError(f"invalid evaluator source in {dataset_path}")
        source = Path(str(item["path"]))
        resolved = source if source.is_absolute() else root / source
        if not resolved.is_file():
            raise FileNotFoundError(f"evaluator source not found: {resolved}")
        paths.append(resolved.resolve())
    return paths


def build_evaluator_manifest(
    *,
    root: Path = ROOT,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    quality_gate_path: Path = DEFAULT_QUALITY_GATE_PATH,
    runtime_config_path: Path = DEFAULT_RUNTIME_CONFIG_PATH,
    application_commit: str | None = None,
    corpus_commit: str | None = None,
    collection: str = EVALUATOR_COLLECTION,
    execution_role: str | None = None,
    execution_node: str | None = None,
    mode: str = "live",
) -> dict[str, Any]:
    assert_evaluator_collection(collection)
    root = root.resolve()
    dataset_path = dataset_path.resolve()
    quality_gate_path = quality_gate_path.resolve()
    runtime_config_path = runtime_config_path.resolve()
    for path in (dataset_path, quality_gate_path, runtime_config_path):
        if not path.is_file():
            raise FileNotFoundError(f"evaluator input not found: {path}")

    current_commit = _git_commit(root)
    dataset_entries = [
        {
            "path": _relative_path(dataset_path, root),
            "sha256": sha256_file(dataset_path),
        }
    ]
    config_entries = [
        {
            "path": _relative_path(quality_gate_path, root),
            "sha256": sha256_file(quality_gate_path),
        },
        {
            "path": _relative_path(runtime_config_path, root),
            "sha256": sha256_file(runtime_config_path),
        },
    ]
    corpus_entries = [
        {
            "path": _relative_path(path, root),
            "sha256": sha256_file(path),
        }
        for path in _source_paths(dataset_path, root)
    ]
    return {
        "schema_version": 1,
        "application_commit": _identity(
            application_commit,
            "DEEPSEARCHER_APPLICATION_COMMIT",
            current_commit,
        ),
        "corpus_commit": _identity(corpus_commit, "DEEPSEARCHER_CORPUS_COMMIT", current_commit),
        "collection": EVALUATOR_COLLECTION,
        "dataset": dataset_entries,
        "configs": config_entries,
        "files": corpus_entries,
        "execution": {
            "mode": mode,
            "role": _identity(
                execution_role,
                "DEEPSEARCHER_EXECUTION_ROLE",
                "local_canonical",
            ),
            "node": _identity(
                execution_node,
                "DEEPSEARCHER_EXECUTION_NODE",
                socket.gethostname(),
            ),
            "prepare": "reset_owned_collection",
        },
    }


def write_evaluator_manifest(output_dir: Path, **kwargs: Any) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "evaluator-manifest.json"
    payload = build_evaluator_manifest(**kwargs)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
