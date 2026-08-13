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
from statistics import mean
from typing import Any, Mapping, Sequence

from deepsearcher.collection_manifest import EmbeddingProfile
from deepsearcher.configuration import Configuration, ModuleFactory
from deepsearcher.offline_loading import load_from_local_files
from deepsearcher.query_context import contextualize_query
from deepsearcher.query_planner import (
    QUERY_PLAN_ORIGINAL_ANCHORS,
    QUERY_PLAN_RRF_K,
    QUERY_PLAN_TOPIC_ANCHORS,
    QueryPlan,
    merge_ranked_results,
    plan_queries,
)
from evaluation.benchmark import (
    DEFAULT_CONFIG,
    DEFAULT_DATASET,
    ROOT,
    CountingLLM,
    load_env_file,
    select_samples,
    verify_sources,
)
from evaluation.dataset import EvalDataset, load_dataset
from evaluation.metrics import (
    METRIC_VERSION,
    aggregate,
    evaluate_sample,
    matches_document,
    matches_evidence,
    percentile,
    result_view,
)

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
    summary["search_latency_ms"] = _latency_summary(search_latencies)
    summary["embedding_latency_ms"] = _latency_summary(embedding_latencies)
    for field in (
        "query_plan_stability_rate",
        "retrieval_given_plan_stability_rate",
        "end_to_end_ranking_stability_rate",
        "ranking_stability_rate",
    ):
        values = [float(row[field]) for row in rows if row.get(field) is not None]
        summary[field] = round(mean(values), 4) if values else None
    return summary


def summarize_by_tag(
    rows_by_mode: Mapping[str, Sequence[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for mode, rows in rows_by_mode.items():
        tags = sorted({tag for row in rows for tag in row.get("tags", [])})
        result[mode] = {
            tag: summarize_mode([row for row in rows if tag in row.get("tags", [])]) for tag in tags
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
    contextualizer_llm: Any | None = None,
    query_decomposition_enabled: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    samples = select_samples(dataset, limit=limit, sample_ids=sample_ids)
    if contextualizer_llm is None and (
        query_decomposition_enabled or any(sample.history for sample in samples)
    ):
        raise ValueError("contextualizer_llm is required for context or query decomposition")
    rows_by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    for sample_index, sample in enumerate(samples):
        repetition_rows: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
        plan_signatures: list[tuple[str, ...]] = []
        plan_outcome_signatures: list[tuple[Any, ...]] = []
        result_signatures: dict[str, list[tuple[tuple[Any, ...], ...]]] = {
            mode: [] for mode in modes
        }
        for repetition in range(repetitions):
            planning_started = time.perf_counter()
            calls_before = int(getattr(contextualizer_llm, "calls", 0))
            contextualization = None
            effective_query = sample.question
            query_plan = QueryPlan((effective_query,), False, False, "disabled")
            if sample.history:
                contextualization = contextualize_query(
                    contextualizer_llm,
                    sample.question,
                    [
                        {"role": role, "content": content, "grounded": role == "assistant"}
                        for role, content in sample.history
                    ],
                )
                effective_query = contextualization.query
                query_plan = QueryPlan(
                    contextualization.retrieval_queries,
                    len(contextualization.retrieval_queries) > 1,
                    contextualization.fallback_used,
                    f"context:{contextualization.reason}",
                    contextualization.token_usage,
                )
            elif query_decomposition_enabled:
                query_plan = plan_queries(contextualizer_llm, effective_query)
            planning_latency_ms = (time.perf_counter() - planning_started) * 1000
            context_calls = int(getattr(contextualizer_llm, "calls", 0)) - calls_before
            plan_signatures.append(query_plan.queries)
            plan_outcome_signatures.append(
                (
                    query_plan.decomposed,
                    query_plan.fallback_used,
                    query_plan.reason,
                    len(query_plan.queries),
                    contextualization.dependency_status if contextualization else None,
                )
            )

            embedding_started = time.perf_counter()
            embedding_error = None
            try:
                vectors = {
                    query: embedding_model.embed_query(query) for query in query_plan.queries
                }
            except Exception as exc:
                vectors = {}
                embedding_error = _safe_error(exc)
            embedding_latency_ms = (time.perf_counter() - embedding_started) * 1000
            offset = (sample_index + repetition) % len(modes)
            ordered_modes = tuple(modes[offset:]) + tuple(modes[:offset])
            for mode in ordered_modes:
                started = time.perf_counter()
                results: list[Any] = []
                error = embedding_error
                query_diagnostics = []
                if error is None:
                    try:
                        groups = []
                        for planned_query in query_plan.queries:
                            diagnostics: dict[str, Any] = {}
                            query_results = list(
                                vector_db.search_data(
                                    collection=collection,
                                    vector=vectors[planned_query],
                                    query_text=planned_query,
                                    retrieval_mode=mode,
                                    top_k=top_k,
                                    retrieval_diagnostics=diagnostics,
                                )
                            )
                            groups.append(query_results)
                            query_diagnostics.append(
                                {
                                    "query": planned_query,
                                    "diagnostics": diagnostics or None,
                                    "ranked_results": [
                                        {
                                            "rank": rank,
                                            "document": result_view(result).document,
                                            "page": result_view(result).page,
                                            "matches_gold": any(
                                                matches_evidence(
                                                    result_view(result), target, source_aliases
                                                )
                                                for target in sample.evidence
                                            ),
                                        }
                                        for rank, result in enumerate(query_results, start=1)
                                    ],
                                }
                            )
                        merged_candidates = merge_ranked_results(
                            groups,
                            limit=sum(len(group) for group in groups),
                            anchor_count=(
                                QUERY_PLAN_ORIGINAL_ANCHORS if query_plan.decomposed else 0
                            ),
                            per_group_anchor_count=(
                                QUERY_PLAN_TOPIC_ANCHORS if query_plan.decomposed else 0
                            ),
                            cross_query_rrf_k=(QUERY_PLAN_RRF_K if query_plan.decomposed else None),
                        )
                        results = merged_candidates[:top_k]
                        missing_document_diagnostics = []
                        for document in sorted({target.document for target in sample.evidence}):
                            target = next(
                                target for target in sample.evidence if target.document == document
                            )
                            raw_ranks = [
                                {
                                    "query": planned_query,
                                    "rank": rank,
                                }
                                for planned_query, group in zip(
                                    query_plan.queries, groups, strict=True
                                )
                                for rank, candidate in enumerate(group, start=1)
                                if matches_document(result_view(candidate), target, source_aliases)
                            ]
                            final_rank = next(
                                (
                                    rank
                                    for rank, candidate in enumerate(merged_candidates, start=1)
                                    if matches_document(
                                        result_view(candidate), target, source_aliases
                                    )
                                ),
                                None,
                            )
                            if final_rank is None or final_rank > top_k:
                                missing_document_diagnostics.append(
                                    {
                                        "document": document,
                                        "classification": (
                                            "fusion_lost" if raw_ranks else "not_recalled"
                                        ),
                                        "raw_ranks": raw_ranks,
                                        "fusion_rank": final_rank,
                                        "candidate_truncation_position": top_k,
                                    }
                                )
                    except Exception as exc:
                        error = _safe_error(exc)
                search_latency_ms = (time.perf_counter() - started) * 1000
                row = evaluate_sample(
                    sample,
                    results,
                    answer=None,
                    top_k=top_k,
                    latency_ms=planning_latency_ms + embedding_latency_ms + search_latency_ms,
                    tokens=query_plan.token_usage,
                    llm_calls=context_calls,
                    error=error,
                    source_aliases=source_aliases,
                )
                row.update(
                    {
                        "retrieval_mode": mode,
                        "repetition": repetition + 1,
                        "context_expected_dependency": sample.context_dependent
                        if sample.history
                        else None,
                        "context_predicted_dependency": contextualization.depends_on_history
                        if contextualization
                        else None,
                        "context_dependency_correct": (
                            contextualization.depends_on_history == sample.context_dependent
                            if contextualization
                            else None
                        ),
                        "context_query_match": (
                            re.sub(
                                r"\s+",
                                " ",
                                str(
                                    contextualization.primary_rewrite or effective_query
                                ).casefold(),
                            ).strip()
                            == re.sub(
                                r"\s+", " ", (sample.standalone_question or "").casefold()
                            ).strip()
                            if contextualization
                            else None
                        ),
                        "context_fallback_used": contextualization.fallback_used
                        if contextualization
                        else None,
                        "context_dependency_status": contextualization.dependency_status
                        if contextualization
                        else None,
                        "context_retrieval_queries": list(query_plan.queries),
                        "query_plan": {
                            "queries": list(query_plan.queries),
                            "decomposed": query_plan.decomposed,
                            "fallback_used": query_plan.fallback_used,
                            "reason": query_plan.reason,
                            "token_usage": query_plan.token_usage,
                        },
                        "query_planning_latency_ms": round(planning_latency_ms, 3),
                        "embedding_latency_ms": round(embedding_latency_ms, 3),
                        "search_latency_ms": round(search_latency_ms, 3),
                        "retrieval_diagnostics": {
                            "queries": query_diagnostics,
                            "per_query_top_k": top_k,
                            "anchor_count": (
                                QUERY_PLAN_ORIGINAL_ANCHORS if query_plan.decomposed else 0
                            ),
                            "per_query_anchor_count": (
                                QUERY_PLAN_TOPIC_ANCHORS if query_plan.decomposed else 0
                            ),
                            "cross_query_rrf_k": (
                                QUERY_PLAN_RRF_K if query_plan.decomposed else None
                            ),
                            "missing_documents": missing_document_diagnostics,
                        },
                    }
                )
                repetition_rows[mode].append(row)
                result_signatures[mode].append(_result_signature(results))

        planner_stability = mean(
            outcome == plan_outcome_signatures[0] for outcome in plan_outcome_signatures
        )
        averaged_fields = (
            "retrieval_hit",
            "retrieval_recall",
            "retrieval_precision",
            "reciprocal_rank",
            "empty_result",
            "duplicate_rate",
            "grounded_criteria_coverage",
            "evidence_source_correct",
            "full_evidence_retrieved",
            "full_document_coverage",
            "context_dependency_correct",
            "context_query_match",
            "context_fallback_used",
        )
        for mode in modes:
            rep_rows = repetition_rows[mode]
            row = dict(rep_rows[0])
            for field in averaged_fields:
                values = [item[field] for item in rep_rows if item.get(field) is not None]
                if values:
                    row[field] = mean(float(value) for value in values)
            row["latency_ms"] = mean(float(item["latency_ms"]) for item in rep_rows)
            row["embedding_latency_ms"] = mean(
                float(item["embedding_latency_ms"]) for item in rep_rows
            )
            row["search_latency_ms"] = mean(float(item["search_latency_ms"]) for item in rep_rows)
            row["query_planning_latency_ms"] = mean(
                float(item["query_planning_latency_ms"]) for item in rep_rows
            )
            row["tokens"] = sum(int(item.get("tokens") or 0) for item in rep_rows)
            row["llm_calls"] = sum(int(item.get("llm_calls") or 0) for item in rep_rows)
            errors = [str(item["error"]) for item in rep_rows if item.get("error")]
            row["error"] = errors[0] if errors else None
            signatures = result_signatures[mode]
            end_to_end = mean(signature == signatures[0] for signature in signatures)
            fixed_plan_groups: dict[tuple[str, ...], list[tuple[tuple[Any, ...], ...]]] = {}
            for plan, signature in zip(plan_signatures, signatures, strict=True):
                fixed_plan_groups.setdefault(plan, []).append(signature)
            comparable = [items for items in fixed_plan_groups.values() if len(items) > 1]
            fixed_plan_stability = (
                mean(signature == items[0] for items in comparable for signature in items)
                if comparable
                else None
            )
            row.update(
                {
                    "search_repetitions": repetitions,
                    "query_plan_stability_rate": round(planner_stability, 4),
                    "retrieval_given_plan_stability_rate": (
                        round(fixed_plan_stability, 4) if fixed_plan_stability is not None else None
                    ),
                    "end_to_end_ranking_stability_rate": round(end_to_end, 4),
                    "ranking_stability_rate": round(end_to_end, 4),
                    "repetitions": rep_rows,
                    "retrieval_diagnostics": [item["retrieval_diagnostics"] for item in rep_rows],
                }
            )
            rows_by_mode[mode].append(row)
        status = ", ".join(
            f"{mode}={'ok' if not any(item.get('error') for item in repetition_rows[mode]) else 'error'}"
            for mode in modes
        )
        print(f"[{sample_index + 1}/{len(samples)}] {sample.id}: {status}")

    return rows_by_mode, {mode: summarize_mode(rows) for mode, rows in rows_by_mode.items()}


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
        "difficulty",
        "history_turn_count",
        "context_expected_dependency",
        "context_predicted_dependency",
        "context_dependency_correct",
        "context_query_match",
        "context_fallback_used",
        "context_primary_rewrite",
        "context_safe_query",
        "context_retrieval_queries",
        "context_validation_reason",
        "query_plan",
        "query_planning_latency_ms",
        "contextualization_latency_ms",
        "multi_document",
        "required_evidence_count",
        "matched_evidence_count",
        "required_document_count",
        "matched_document_count",
        "full_evidence_retrieved",
        "full_document_coverage",
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
        "query_plan_stability_rate",
        "retrieval_given_plan_stability_rate",
        "end_to_end_ranking_stability_rate",
        "ranking_stability_rate",
        "error",
        "missing_documents",
        "retrieval_diagnostics",
        "repetitions",
    ]
    temporary_csv = details_path.with_suffix(".csv.tmp")
    with temporary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["details"]:
            serialized = {field: row.get(field) for field in fields}
            serialized["tags"] = ",".join(row.get("tags") or [])
            serialized["missing_documents"] = ",".join(row.get("missing_documents") or [])
            serialized["retrieval_diagnostics"] = json.dumps(
                row.get("retrieval_diagnostics"), ensure_ascii=False, separators=(",", ":")
            )
            serialized["context_retrieval_queries"] = json.dumps(
                row.get("context_retrieval_queries"), ensure_ascii=False, separators=(",", ":")
            )
            serialized["query_plan"] = json.dumps(
                row.get("query_plan"), ensure_ascii=False, separators=(",", ":")
            )
            serialized["repetitions"] = json.dumps(
                row.get("repetitions"), ensure_ascii=False, separators=(",", ":")
            )
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
    parser.add_argument("--rrf-k", type=int, default=5)
    parser.add_argument(
        "--hybrid-ranker",
        choices=("rrf", "weighted", "weighted_rrf"),
        default="weighted_rrf",
    )
    parser.add_argument("--dense-weight", type=float, default=1.5)
    parser.add_argument("--sparse-weight", type=float, default=1.0)
    parser.add_argument("--candidate-multiplier", type=int, default=1)
    parser.add_argument("--dense-anchors", type=int, default=2)
    parser.add_argument("--diversity-tolerance", type=float, default=0.0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-ids")
    parser.add_argument("--query-decomposition", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-quality-gain", type=float, default=0.01)
    parser.add_argument("--max-quality-regression", type=float, default=0.01)
    parser.add_argument("--max-search-p95-ratio", type=float, default=2.0)
    parser.add_argument("--max-search-p95-overhead-ms", type=float, default=10.0)
    parser.add_argument("--min-answerable-samples", type=int, default=20)
    return parser.parse_args(argv)


def _create_components(
    config: Configuration,
    *,
    rrf_k: int,
    hybrid_ranker: str,
    dense_weight: float,
    sparse_weight: float,
    candidate_multiplier: int,
    dense_anchors: int,
    diversity_tolerance: float,
) -> tuple[Any, Any, Any, Any]:
    vector_setting = config.get_provider_config("vector_db")
    if vector_setting.get("provider") != "Milvus":
        raise ValueError("retrieval comparison currently requires the Milvus provider")
    vector_config = dict(vector_setting.get("config") or {})
    vector_config.update(
        {
            "hybrid": True,
            "rrf_k": rrf_k,
            "hybrid_ranker": hybrid_ranker,
            "hybrid_dense_weight": dense_weight,
            "hybrid_sparse_weight": sparse_weight,
            "hybrid_candidate_multiplier": candidate_multiplier,
            "hybrid_dense_anchor_count": dense_anchors,
            "hybrid_diversity_tolerance": diversity_tolerance,
        }
    )
    config.set_provider_config("vector_db", "Milvus", vector_config)
    factory = ModuleFactory(config)
    return (
        CountingLLM(factory.create_llm()),
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
        args.dense_weight,
        args.sparse_weight,
        args.candidate_multiplier,
        args.batch_size,
        args.max_search_p95_ratio,
        args.max_search_p95_overhead_ms,
        args.min_answerable_samples,
    )
    if any(value <= 0 for value in positive_values):
        raise SystemExit("positive limits, repetitions, RRF and latency thresholds are required")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("limit must be positive")
    if args.dense_anchors < 0:
        raise SystemExit("dense anchors must be non-negative")
    if not 0 <= args.diversity_tolerance <= 1:
        raise SystemExit("diversity tolerance must be between 0 and 1")
    if args.min_quality_gain < 0 or args.max_quality_regression < 0:
        raise SystemExit("quality thresholds must be non-negative")

    load_env_file(args.env_file)
    dataset = load_dataset(args.dataset)
    source_paths = verify_sources(dataset)
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
    contextualizer_llm, embedding_model, file_loader, vector_db = _create_components(
        config,
        rrf_k=args.rrf_k,
        hybrid_ranker=args.hybrid_ranker,
        dense_weight=args.dense_weight,
        sparse_weight=args.sparse_weight,
        candidate_multiplier=args.candidate_multiplier,
        dense_anchors=args.dense_anchors,
        diversity_tolerance=args.diversity_tolerance,
    )
    report: dict[str, Any] | None = None
    cleanup_result: dict[str, Any] | None = None
    preparation: dict[str, Any] | None = None
    try:
        if args.prepare:
            if vector_db.collection_exists(collection):
                vector_db.delete_collection(collection)
            preparation = load_from_local_files(
                [str(path) for path in source_paths],
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

        source_aliases = {
            source.document: (source_path.name,)
            for source, source_path in zip(dataset.sources, source_paths, strict=True)
        }
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
            contextualizer_llm=contextualizer_llm,
            query_decomposition_enabled=args.query_decomposition,
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
            "report_schema_version": 2,
            "metric_version": METRIC_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "id": dataset.dataset_id,
                "version": dataset.version,
                "sha256": dataset.sha256,
                "sources": [source.as_dict() for source in dataset.sources],
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
                "query_decomposition_enabled": args.query_decomposition,
                "embedding_batch_size": args.batch_size,
                "fusion": {
                    "algorithm": args.hybrid_ranker,
                    "rrf_k": args.rrf_k,
                    "dense_weight": args.dense_weight,
                    "sparse_weight": args.sparse_weight,
                    "candidate_multiplier": args.candidate_multiplier,
                    "dense_anchor_count": args.dense_anchors,
                    "diversity_tolerance": args.diversity_tolerance,
                },
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
                "llm": {
                    "provider": config.get_provider_config("llm").get("provider"),
                    "model": config.get_provider_config("llm").get("config", {}).get("model"),
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
                "query_plan_stability_rate": "各 repetition 生成完全相同查询计划的比例。",
                "retrieval_given_plan_stability_rate": "查询计划相同时最终 Top-K 排名完全一致的比例。",
                "end_to_end_ranking_stability_rate": "从 Planner 到最终 Top-K 的端到端完全一致比例。",
                "quality_score": "Recall@K、MRR 与召回答案要点覆盖率的等权平均，仅用于本报告门禁。",
                "context_dependency_accuracy": "有历史样本中，是否需要依赖历史的分类准确率。",
                "context_query_match_rate": "改写结果与金标独立问题的规范化精确匹配率。",
            },
        }
        if args.cleanup:
            cleanup_result = vector_db.delete_collection(collection)
            report["run"]["cleanup"] = cleanup_result
        output_dir = args.output or (
            ROOT / "evaluation" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S-o06")
        )
        report_path, details_path = save_report(report, output_dir)
        print(
            json.dumps({"metrics": summaries, "decision": decision}, ensure_ascii=False, indent=2)
        )
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
