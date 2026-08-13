"""Run a two-stage grid search for multi-document Hybrid retrieval settings."""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from evaluation.dataset import load_dataset
from evaluation.retrieval_compare import DEFAULT_DATASET, ROOT

DEFAULT_CANDIDATES = (1, 2, 3, 4)
DEFAULT_RRF_K = (5, 20, 60)
DEFAULT_WEIGHTS = ((1.0, 1.0), (1.5, 1.0), (2.0, 1.0))
DEFAULT_ANCHORS = (0, 1, 2)


def multi_document_sample_ids(dataset_path: Path) -> tuple[str, ...]:
    dataset = load_dataset(dataset_path)
    return tuple(
        sample.id
        for sample in dataset.samples
        if len({target.document for target in sample.evidence}) > 1
    )


def parameter_grid() -> tuple[dict[str, int | float], ...]:
    return tuple(
        {
            "candidate_multiplier": candidate,
            "rrf_k": rrf_k,
            "dense_weight": dense_weight,
            "sparse_weight": sparse_weight,
            "dense_anchors": anchors,
            "diversity_tolerance": 0.1,
        }
        for candidate, rrf_k, (dense_weight, sparse_weight), anchors in itertools.product(
            DEFAULT_CANDIDATES,
            DEFAULT_RRF_K,
            DEFAULT_WEIGHTS,
            DEFAULT_ANCHORS,
        )
    )


def candidate_score(report: dict[str, Any]) -> tuple[float, float, float, float]:
    metrics = report["metrics"]["hybrid"]
    return (
        float(metrics.get("full_document_coverage_rate") or 0),
        float(metrics.get("retrieval_recall_at_k") or 0),
        float(metrics.get("mrr") or 0),
        -float(metrics.get("search_latency_ms", {}).get("p95") or 0),
    )


def _command(
    args: argparse.Namespace,
    settings: dict[str, int | float],
    output: Path,
    *,
    sample_ids: Sequence[str] | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "evaluation.retrieval_compare",
        "--dataset",
        str(args.dataset),
        "--config",
        str(args.config),
        "--env-file",
        str(args.env_file),
        "--collection",
        args.collection,
        "--modes",
        "dense,hybrid",
        "--top-k",
        str(args.top_k),
        "--repetitions",
        str(args.repetitions),
        "--candidate-multiplier",
        str(settings["candidate_multiplier"]),
        "--rrf-k",
        str(settings["rrf_k"]),
        "--dense-weight",
        str(settings["dense_weight"]),
        "--sparse-weight",
        str(settings["sparse_weight"]),
        "--dense-anchors",
        str(settings["dense_anchors"]),
        "--diversity-tolerance",
        str(settings["diversity_tolerance"]),
        "--output",
        str(output),
    ]
    if sample_ids:
        command.extend(("--sample-ids", ",".join(sample_ids)))
    return command


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--config", type=Path, default=ROOT / "deepsearcher" / "config.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--collection", default="eval_workspace_v2")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--full-verify", action="store_true")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    sample_ids = multi_document_sample_ids(args.dataset)
    candidates = parameter_grid()
    if args.max_candidates is not None:
        candidates = candidates[: args.max_candidates]
    results = []
    for index, settings in enumerate(candidates, start=1):
        run_dir = args.output / "screen" / f"candidate-{index:03d}"
        completed = subprocess.run(
            _command(args, settings, run_dir, sample_ids=sample_ids),
            cwd=ROOT,
            check=False,
        )
        report_path = run_dir / "report.json"
        if completed.returncode == 0 and report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            results.append({"settings": settings, "score": candidate_score(report)})
    results.sort(key=lambda item: tuple(item["score"]), reverse=True)
    selected = results[0] if results else None
    full_report = None
    if selected and args.full_verify:
        full_dir = args.output / "full"
        completed = subprocess.run(
            _command(args, selected["settings"], full_dir, sample_ids=None),
            cwd=ROOT,
            check=False,
        )
        if completed.returncode == 0 and (full_dir / "report.json").exists():
            full_report = str((full_dir / "report.json").resolve())
    summary = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "grid_size": len(candidates),
        "multi_document_sample_ids": list(sample_ids),
        "selected": selected,
        "full_report": full_report,
        "candidates": results,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if selected is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
