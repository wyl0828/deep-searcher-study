from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from deepsearcher.collection_manifest import EmbeddingProfile
from deepsearcher.configuration import Configuration, ModuleFactory
from deepsearcher.offline_loading import load_from_local_files
from evaluation.benchmark import (
    DEFAULT_CONFIG,
    DEFAULT_DATASET,
    ROOT,
    load_env_file,
    select_samples,
    verify_source,
)
from evaluation.dataset import EvalDataset, load_dataset
from evaluation.metrics import aggregate, evaluate_sample, percentile, result_view

RETRIEVAL_MODES = ("dense", "bm25", "hybrid")
EVALUATION_COLLECTION_PATTERN = re.compile(r"eval_[A-Za-z0-9_]{1,58}")


@dataclass(frozen=True)
class DecisionThresholds:
    min_quality_gain: float = 0.01
    max_quality_regression: float = 0.01
    max_search_p95_ratio: float = 2.0
    max_search_p95_overhead_ms: float = 10.0
    min_answerable_samples: int = 20


def validate_evaluation_collection(value: str) -> str:
    collection = str(value or "").strip()
    if EVALUATION_COLLECTION_PATTERN.fullmatch(collection) is None:
        raise ValueError(
            "evaluation collection must start with eval_ and contain at most "
            "63 letters, digits or underscores"
        )
    return collection


def parse_modes(value: str) -> tuple[str, ...]:
    modes = tuple(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))
    invalid = sorted(set(modes) - set(RETRIEVAL_MODES))
    if not modes or invalid:
        raise ValueError(f"invalid retrieval modes: {invalid or 'empty selection'}")
    return modes


def _safe_error(exc: Exception) -> str:
    return type(exc).__name__


def _result_signature(results: Sequence[Any]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (view.document, view.page, view.chunk, view.text)
        for view in (result_view(result) for result in results)
    )


def _latency_summary(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "average": round(mean(values), 3) if values else None,
        "p50": round(float(percentile(values, 0.5)), 3) if values else None,
        "p95": round(float(percentile(values, 0.95)), 3) if values else None,
    }


def summarize_mode(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summary = aggregate(rows)
    search_latencies = [float(row["search_latency_ms"]) for row in rows]
    embedding_latencies = [float(row["embedding_latency_ms"]) for row in rows]
    stability = [
        float(row["ranking_stability_rate"])
        for row in rows
        if row.get("ranking_stability_rate") is not None
    ]
    summary["search_latency_ms"] = _latency_summary(search_latencies)
    summary["embedding_latency_ms"] = _latency_summary(embedding_latencies)
    summary["ranking_stability_rate"] = round(mean(stability), 4) if stability else None
    return summary


def summarize_by_tag(
    rows_by_mode: Mapping[str, Sequence[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for mode, rows in rows_by_mode.items():
        tags = sorted({tag for row in rows for tag in row.get("tags", [])})
        result[mode] = {
            tag: summarize_mode([row for row in rows if tag in row.get("tags", [])])
            for tag in tags
        }
    return result


def evaluate_modes(
    dataset: EvalDataset,
    *,
    embedding_model: Any,
    vector_db: Any,
    collection: str,
    modes: Sequence[str],
    top_k: int,
    repetitions: int,
    limit: int | None = None,
    sample_ids: Sequence[str] | None = None,
    source_aliases: Mapping[str, Sequence[str]] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    samples = select_samples(dataset, limit=limit, sample_ids=sample_ids)
    rows_by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    for sample_index, sample in enumerate(samples):
        embedding_started = time.perf_counter()
        embedding_error: str | None = None
        try:
            vector = embedding_model.embed_query(sample.question)
        except Exception as exc:
            vector = []
            embedding_error = _safe_error(exc)
        embedding_latency_ms = (time.perf_counter() - embedding_started) * 1000

        mode_results: dict[str, list[Any]] = {mode: [] for mode in modes}
        mode_durations: dict[str, list[float]] = {mode: [] for mode in modes}
        mode_signatures: dict[str, list[tuple[tuple[Any, ...], ...]]] = {
            mode: [] for mode in modes
        }
        mode_errors: dict[str, str | None] = {mode: embedding_error for mode in modes}
        if embedding_error is None:
            for repetition in range(repetitions):
                offset = (sample_index + repetition) % len(modes)
                ordered_modes = tuple(modes[offset:]) + tuple(modes[:offset])
                for mode in ordered_modes:
                    started = time.perf_counter()
                    try:
                        results = vector_db.search_data(
                            collection=collection,
                            vector=vector,
                            query_text=sample.question,
                            retrieval_mode=mode,
                            top_k=top_k,
                        )
                    except Exception as exc:
                        mode_errors[mode] = _safe_error(exc)
                        continue
                    mode_durations[mode].append((time.perf_counter() - started) * 1000)
                    if not mode_results[mode]:
                        mode_results[mode] = list(results)
                    mode_signatures[mode].append(_result_signature(results))

        for mode in modes:
            durations = mode_durations[mode]
            search_latency_ms = median(durations) if durations else 0.0
            signatures = mode_signatures[mode]
            stability_rate = (
                mean(signature == signatures[0] for signature in signatures)
                if signatures
                else None
            )
            row = evaluate_sample(
                sample,
                mode_results[mode],
                answer=None,
                top_k=top_k,
                latency_ms=embedding_latency_ms + search_latency_ms,
                tokens=0,
                error=mode_errors[mode],
                source_aliases=source_aliases,
            )
            row.update(
                {
                    "retrieval_mode": mode,
                    "embedding_latency_ms": round(embedding_latency_ms, 3),
                    "search_latency_ms": round(search_latency_ms, 3),
                    "search_repetitions": len(durations),
                    "ranking_stability_rate": (
                        round(stability_rate, 4) if stability_rate is not None else None
                    ),
                }
            )
            rows_by_mode[mode].append(row)
        status = ", ".join(
            f"{mode}={'ok' if mode_errors[mode] is None else mode_errors[mode]}"
            for mode in modes
        )
        print(f"[{sample_index + 1}/{len(samples)}] {sample.id}: {status}")

    return rows_by_mode, {
        mode: summarize_mode(rows) for mode, rows in rows_by_mode.items()
    }


def _quality_score(summary: Mapping[str, Any]) -> float:
    fields = (
        "retrieval_recall_at_k",
        "mrr",
        "grounded_criteria_coverage",
    )
    values = [float(summary[field]) for field in fields if summary.get(field) is not None]
    return mean(values) if values else 0.0


def recommend_default(
    summaries: Mapping[str, Mapping[str, Any]],
    *,
    answerable_samples: int,
    thresholds: DecisionThresholds,
) -> dict[str, Any]:
    if "dense" not in summaries or "hybrid" not in summaries:
        return {
            "recommended_default": "dense",
            "promote_hybrid": False,
            "reasons": ["DENSE_OR_HYBRID_RESULT_MISSING"],
        }

    dense = summaries["dense"]
    hybrid = summaries["hybrid"]
    dense_quality = _quality_score(dense)
    hybrid_quality = _quality_score(hybrid)
    quality_gain = hybrid_quality - dense_quality
    protected_fields = ("retrieval_recall_at_k", "grounded_criteria_coverage")
    regressions = {
        field: float(hybrid[field]) - float(dense[field])
        for field in protected_fields
        if dense.get(field) is not None and hybrid.get(field) is not None
    }
    quality_safe = all(
        delta >= -thresholds.max_quality_regression for delta in regressions.values()
    )

    dense_p95 = float(dense["search_latency_ms"]["p95"] or 0.0)
    hybrid_p95 = float(hybrid["search_latency_ms"]["p95"] or 0.0)
    allowed_p95 = max(
        dense_p95 * thresholds.max_search_p95_ratio,
        dense_p95 + thresholds.max_search_p95_overhead_ms,
    )
    latency_safe = hybrid_p95 <= allowed_p95
    enough_samples = answerable_samples >= thresholds.min_answerable_samples
    no_errors = float(hybrid.get("error_rate") or 0.0) == 0.0
    quality_wins = quality_gain >= thresholds.min_quality_gain
    promote = all((quality_safe, quality_wins, latency_safe, enough_samples, no_errors))
    reasons = []
    if not quality_wins:
        reasons.append("QUALITY_GAIN_BELOW_THRESHOLD")
    if not quality_safe:
        reasons.append("QUALITY_REGRESSION")
    if not latency_safe:
        reasons.append("SEARCH_P95_OVERHEAD_TOO_HIGH")
    if not enough_samples:
        reasons.append("INSUFFICIENT_ANSWERABLE_SAMPLES")
    if not no_errors:
        reasons.append("HYBRID_ERRORS_PRESENT")
    if promote:
        reasons.append("HYBRID_MEETS_PROMOTION_GATE")
    return {
        "recommended_default": "hybrid" if promote else "dense",
        "promote_hybrid": promote,
        "reasons": reasons,
        "quality": {
            "composite_fields": [
                "retrieval_recall_at_k",
                "mrr",
                "grounded_criteria_coverage",
            ],
            "dense_score": round(dense_quality, 6),
            "hybrid_score": round(hybrid_quality, 6),
            "gain": round(quality_gain, 6),
            "protected_metric_deltas": {
                field: round(delta, 6) for field, delta in regressions.items()
            },
        },
        "latency": {
            "dense_search_p95_ms": round(dense_p95, 3),
            "hybrid_search_p95_ms": round(hybrid_p95, 3),
            "allowed_hybrid_search_p95_ms": round(allowed_p95, 3),
        },
        "thresholds": {
            "min_quality_gain": thresholds.min_quality_gain,
            "max_quality_regression": thresholds.max_quality_regression,
            "max_search_p95_ratio": thresholds.max_search_p95_ratio,
            "max_search_p95_overhead_ms": thresholds.max_search_p95_overhead_ms,
            "min_answerable_samples": thresholds.min_answerable_samples,
        },
    }


def _git_state() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip()

    status = run("status", "--porcelain")
    return {
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def save_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    details_path = output_dir / "details.csv"
    temporary_report = report_path.with_suffix(".json.tmp")
    temporary_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_report.replace(report_path)

    fields = [
        "retrieval_mode",
        "sample_id",
        "question",
        "answerable",
        "tags",
        "retrieved_count",
        "retrieval_hit",
        "retrieval_recall",
        "retrieval_precision",
        "reciprocal_rank",
        "empty_result",
        "duplicate_count",
        "duplicate_rate",
        "no_answer_false_positive",
        "grounded_criteria_coverage",
        "evidence_source_correct",
        "embedding_latency_ms",
        "search_latency_ms",
        "latency_ms",
        "search_repetitions",
        "ranking_stability_rate",
        "error",
    ]
    temporary_csv = details_path.with_suffix(".csv.tmp")
    with temporary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["details"]:
            serialized = {field: row.get(field) for field in fields}
            serialized["tags"] = ",".join(row.get("tags") or [])
            writer.writerow(serialized)
    temporary_csv.replace(details_path)
    return report_path, details_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在同一 Milvus 混合索引上比较 Dense、BM25 与 Hybrid/RRF"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--collection", default="eval_o06_milvus_v1")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--modes", default=",".join(RETRIEVAL_MODES))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-ids")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-quality-gain", type=float, default=0.01)
    parser.add_argument("--max-quality-regression", type=float, default=0.01)
    parser.add_argument("--max-search-p95-ratio", type=float, default=2.0)
    parser.add_argument("--max-search-p95-overhead-ms", type=float, default=10.0)
    parser.add_argument("--min-answerable-samples", type=int, default=20)
    return parser.parse_args(argv)


def _create_components(config: Configuration, *, rrf_k: int) -> tuple[Any, Any, Any]:
    vector_setting = config.get_provider_config("vector_db")
    if vector_setting.get("provider") != "Milvus":
        raise ValueError("retrieval comparison currently requires the Milvus provider")
    vector_config = dict(vector_setting.get("config") or {})
    vector_config.update({"hybrid": True, "rrf_k": rrf_k})
    config.set_provider_config("vector_db", "Milvus", vector_config)
    factory = ModuleFactory(config)
    return (
        factory.create_embedding(),
        factory.create_file_loader(),
        factory.create_vector_db(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        collection = validate_evaluation_collection(args.collection)
        modes = parse_modes(args.modes)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    positive_values = (
        args.top_k,
        args.repetitions,
        args.rrf_k,
        args.batch_size,
        args.max_search_p95_ratio,
        args.max_search_p95_overhead_ms,
        args.min_answerable_samples,
    )
    if any(value <= 0 for value in positive_values):
        raise SystemExit("positive limits, repetitions, RRF and latency thresholds are required")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("limit must be positive")
    if args.min_quality_gain < 0 or args.max_quality_regression < 0:
        raise SystemExit("quality thresholds must be non-negative")

    load_env_file(args.env_file)
    dataset = load_dataset(args.dataset)
    source_path = verify_source(dataset)
    sample_ids = (
        tuple(value.strip() for value in args.sample_ids.split(",") if value.strip())
        if args.sample_ids
        else None
    )
    try:
        samples = select_samples(dataset, limit=args.limit, sample_ids=sample_ids)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    config = Configuration(str(args.config))
    embedding_model, file_loader, vector_db = _create_components(config, rrf_k=args.rrf_k)
    report: dict[str, Any] | None = None
    cleanup_result: dict[str, Any] | None = None
    preparation: dict[str, Any] | None = None
    try:
        if args.prepare:
            if vector_db.collection_exists(collection):
                vector_db.delete_collection(collection)
            preparation = load_from_local_files(
                str(source_path),
                collection_name=collection,
                collection_description=f"O-06 evaluation: {dataset.dataset_id}@{dataset.version}",
                chunk_size=int(config.load_settings["chunk_size"]),
                chunk_overlap=int(config.load_settings["chunk_overlap"]),
                batch_size=args.batch_size,
                vector_db_instance=vector_db,
                embedding_model_instance=embedding_model,
                file_loader_instance=file_loader,
            )
            vector_db.client.flush(collection_name=collection, timeout=30)
        if not vector_db.collection_exists(collection):
            raise ValueError("evaluation collection is missing; run again with --prepare")

        embedding_profile = EmbeddingProfile.from_embedding(embedding_model)
        manifest = vector_db.assert_collection_compatible(collection, embedding_profile)
        index_profile = vector_db.describe_retrieval_profile(collection)
        missing_modes = sorted(set(modes) - set(index_profile["capabilities"]))
        if missing_modes:
            raise ValueError(f"collection does not support retrieval modes: {missing_modes}")

        source_document = str(dataset.source["document"])
        source_aliases = {source_document: (source_path.name,)}
        rows_by_mode, summaries = evaluate_modes(
            dataset,
            embedding_model=embedding_model,
            vector_db=vector_db,
            collection=collection,
            modes=modes,
            top_k=args.top_k,
            repetitions=args.repetitions,
            limit=args.limit,
            sample_ids=sample_ids,
            source_aliases=source_aliases,
        )
        thresholds = DecisionThresholds(
            min_quality_gain=args.min_quality_gain,
            max_quality_regression=args.max_quality_regression,
            max_search_p95_ratio=args.max_search_p95_ratio,
            max_search_p95_overhead_ms=args.max_search_p95_overhead_ms,
            min_answerable_samples=args.min_answerable_samples,
        )
        decision = recommend_default(
            summaries,
            answerable_samples=sum(sample.answerable for sample in samples),
            thresholds=thresholds,
        )
        config_path = args.config.resolve()
        vector_setting = config.get_provider_config("vector_db")
        report = {
            "report_schema_version": 1,
            "metric_version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "id": dataset.dataset_id,
                "version": dataset.version,
                "sha256": dataset.sha256,
                "source": dataset.source,
                "sample_count": len(samples),
                "answerable_sample_count": sum(sample.answerable for sample in samples),
            },
            "run": {
                "collection": collection,
                "prepared": bool(args.prepare),
                "prepared_chunk_count": (
                    int(preparation["chunk_count"]) if preparation is not None else None
                ),
                "cleanup_requested": bool(args.cleanup),
                "modes": list(modes),
                "top_k": args.top_k,
                "repetitions": args.repetitions,
                "embedding_batch_size": args.batch_size,
                "rrf": {"algorithm": "RRF", "k": args.rrf_k},
                "source_aliases": source_aliases,
                "config_path": str(config_path),
                "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            },
            "environment": {
                "python": platform.python_version(),
                "embedding": {
                    "provider": config.get_provider_config("embedding").get("provider"),
                    "model": embedding_profile.model,
                    "version": embedding_profile.version,
                    "dimension": embedding_profile.dimension,
                    "fingerprint": embedding_profile.fingerprint,
                },
                "vector_db": {"provider": vector_setting.get("provider")},
                "git": _git_state(),
            },
            "index": {
                "manifest": manifest.to_dict() if manifest is not None else None,
                "profile": index_profile,
                "resource_note": (
                    "Hybrid uses the same dense index plus one sparse vector field and one "
                    "SPARSE_INVERTED_INDEX; Milvus does not expose per-index storage bytes here."
                ),
            },
            "metrics": summaries,
            "metrics_by_tag": summarize_by_tag(rows_by_mode),
            "decision": decision,
            "details": [row for mode in modes for row in rows_by_mode[mode]],
            "metric_notes": {
                "latency_ms": "查询 Embedding 延迟加各模式检索延迟中位数。",
                "search_latency_ms": "同一问题重复检索后取中位数；模式执行顺序轮换。",
                "ranking_stability_rate": "重复检索 Top-K 文档、页码、Chunk 和文本顺序完全一致的比例。",
                "quality_score": "Recall@K、MRR 与召回答案要点覆盖率的等权平均，仅用于本报告门禁。",
            },
        }
        if args.cleanup:
            cleanup_result = vector_db.delete_collection(collection)
            report["run"]["cleanup"] = cleanup_result
        output_dir = args.output or (
            ROOT / "evaluation" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S-o06")
        )
        report_path, details_path = save_report(report, output_dir)
        print(json.dumps({"metrics": summaries, "decision": decision}, ensure_ascii=False, indent=2))
        print(f"report: {report_path}")
        print(f"details: {details_path}")
        return 0
    finally:
        if args.cleanup and cleanup_result is None:
            try:
                vector_db.delete_collection(collection)
            except Exception:
                pass
        close = getattr(vector_db.client, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
