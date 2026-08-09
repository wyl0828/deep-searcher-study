from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from deepsearcher.agent import ChainOfRAG, DeepSearch, NaiveRAG
from deepsearcher.configuration import Configuration, RuntimeComponents, build_runtime
from deepsearcher.query_context import contextualize_query
from deepsearcher.trace import TraceCollector
from evaluation.dataset import EvalDataset, load_dataset
from evaluation.metrics import METRIC_VERSION, aggregate, evaluate_sample

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "workspace_v2.json"
DEFAULT_CONFIG = ROOT / "deepsearcher" / "config.yaml"
AGENT_NAMES = ("naive", "deep_search", "chain_of_rag")
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ANSWER_QUALITY_WEIGHTS = {
    "answer_criteria_coverage": 0.30,
    "grounded_criteria_coverage": 0.10,
    "claim_support_rate": 0.20,
    "claim_citation_precision": 0.10,
    "claim_citation_recall": 0.10,
    "refusal_accuracy": 0.15,
    "citation_validity": 0.05,
}
CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_WIDENABLE_TIMEOUTS = (
    "llm_timeout_seconds",
    "external_call_timeout_seconds",
    "request_timeout_seconds",
)


class CountingLLM:
    """Transparent LLM proxy used only to measure calls per evaluation sample."""

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.calls = 0

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.delegate.chat(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


def load_env_file(path: Path | None) -> None:
    """Load simple dotenv entries without logging values or overriding the process."""
    if path is None or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if ENV_NAME_PATTERN.fullmatch(name) is None or name in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[name] = value


def verify_sources(dataset: EvalDataset, root: Path = ROOT) -> tuple[Path, ...]:
    resolved_sources: list[Path] = []
    for source in dataset.sources:
        source_path = Path(source.path)
        resolved = source_path if source_path.is_absolute() else root / source_path
        if not resolved.is_file():
            raise FileNotFoundError(f"evaluation source not found: {resolved}")
        actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual_sha256 != source.sha256:
            raise ValueError(
                f"evaluation source SHA-256 mismatch for {source.document}: "
                f"expected {source.sha256}, got {actual_sha256}"
            )
        resolved_sources.append(resolved.resolve())
    return tuple(resolved_sources)


def verify_source(dataset: EvalDataset, root: Path = ROOT) -> Path:
    """Backward-compatible single-source verifier used by existing integrations."""
    return verify_sources(dataset, root)[0]


def parse_source_aliases(
    dataset: EvalDataset,
    values: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, list[str]] = {source.document: [] for source in dataset.sources}
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        if "=" in value:
            document, alias = (part.strip() for part in value.split("=", 1))
            if document not in aliases:
                raise ValueError(f"unknown source document in alias: {document}")
        elif len(dataset.sources) == 1:
            document = dataset.sources[0].document
            alias = value
        else:
            raise ValueError("multi-source datasets require --source-alias DOCUMENT=INGESTED_NAME")
        if not alias:
            raise ValueError(f"empty source alias for {document}")
        if alias not in aliases[document]:
            aliases[document].append(alias)
    return {document: tuple(items) for document, items in aliases.items()}


def create_agents(
    runtime: RuntimeComponents,
    max_iter: int,
    *,
    deep_search_concurrency: int = 4,
    external_call_timeout_seconds: float = 60.0,
    request_timeout_seconds: float = 300.0,
    chain_min_evidence_for_stop: int = 2,
) -> dict[str, Any]:
    llm = CountingLLM(runtime.llm)
    shared = {
        "llm": llm,
        "embedding_model": runtime.embedding_model,
        "vector_db": runtime.vector_db,
        "route_collection": True,
        "text_window_splitter": True,
    }
    return {
        "naive": NaiveRAG(**shared),
        "deep_search": DeepSearch(
            **shared,
            max_iter=max_iter,
            retrieval_concurrency=deep_search_concurrency,
            external_call_timeout_seconds=external_call_timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
        ),
        "chain_of_rag": ChainOfRAG(
            **shared,
            max_iter=max_iter,
            early_stopping=True,
            min_evidence_for_stop=chain_min_evidence_for_stop,
        ),
    }


def apply_llm_timeout_override(runtime: RuntimeComponents, timeout_seconds: float | None) -> None:
    """Widen an OpenAI-compatible client's timeout for a benchmark run only."""
    if timeout_seconds is None:
        return
    client = getattr(runtime.llm, "client", None)
    with_options = getattr(client, "with_options", None)
    if not callable(with_options):
        raise ValueError("configured LLM client does not support a timeout override")
    runtime.llm.client = with_options(timeout=float(timeout_seconds))


def _safe_error(exc: Exception) -> str:
    return type(exc).__name__


def select_samples(
    dataset: EvalDataset,
    *,
    limit: int | None = None,
    sample_ids: Sequence[str] | None = None,
) -> tuple[Any, ...]:
    if sample_ids:
        by_id = {sample.id: sample for sample in dataset.samples}
        missing = [sample_id for sample_id in sample_ids if sample_id not in by_id]
        if missing:
            raise ValueError(f"unknown sample ids: {missing}")
        return tuple(by_id[sample_id] for sample_id in sample_ids)
    return dataset.samples[:limit] if limit else dataset.samples


def summarize_sample_selection(samples: Sequence[Any]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "answerable": sum(bool(sample.answerable) for sample in samples),
        "unanswerable": sum(not sample.answerable for sample in samples),
        "history": sum(bool(sample.history) for sample in samples),
        "multi_document": sum(
            len({target.document for target in sample.evidence}) > 1 for sample in samples
        ),
        "difficulty": dict(sorted(Counter(sample.difficulty for sample in samples).items())),
    }


def checkpoint_signature(payload: Mapping[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "run": dict(payload),
    }


def _is_compatible_timeout_widening(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    if observed.get("schema_version") != expected.get("schema_version"):
        return False
    old_run = observed.get("run")
    new_run = expected.get("run")
    if not isinstance(old_run, dict) or not isinstance(new_run, dict):
        return False
    old_keys = set(old_run)
    new_keys = set(new_run)
    added_keys = new_keys - old_keys
    if old_keys - new_keys or added_keys - {"llm_timeout_seconds"}:
        return False
    changed = bool(added_keys)
    if "llm_timeout_seconds" in added_keys and new_run["llm_timeout_seconds"] is None:
        return False
    for key in old_run:
        if old_run[key] == new_run[key]:
            continue
        if key not in CHECKPOINT_WIDENABLE_TIMEOUTS:
            return False
        try:
            if float(new_run[key]) < float(old_run[key]):
                return False
        except (TypeError, ValueError):
            return False
        changed = True
    return changed


def prepare_checkpoint(
    output_dir: Path,
    signature: Mapping[str, Any],
) -> tuple[Path, dict[tuple[str, str], dict[str, Any]]]:
    """Validate a resumable JSONL checkpoint and return its latest rows."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "checkpoint.meta.json"
    rows_path = output_dir / "checkpoint.jsonl"
    expected = checkpoint_signature(signature)
    if metadata_path.exists():
        try:
            observed = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("checkpoint metadata is unreadable") from exc
        if observed != expected:
            if not _is_compatible_timeout_widening(observed, expected):
                raise ValueError("checkpoint signature does not match this evaluation run")
            append_checkpoint_row(
                output_dir / "checkpoint.history.jsonl",
                {
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": "timeout_widened_after_observed_failures",
                    "previous_sha256": observed.get("sha256"),
                    "new_sha256": expected.get("sha256"),
                    "previous_timeouts": {
                        key: observed["run"].get(key) for key in CHECKPOINT_WIDENABLE_TIMEOUTS
                    },
                    "new_timeouts": {
                        key: expected["run"].get(key) for key in CHECKPOINT_WIDENABLE_TIMEOUTS
                    },
                },
            )
            temporary = metadata_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(expected, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(metadata_path)
    else:
        if rows_path.exists() and rows_path.stat().st_size:
            raise ValueError("checkpoint rows exist without matching metadata")
        temporary = metadata_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(expected, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(metadata_path)

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    if not rows_path.exists():
        return rows_path, rows
    lines = rows_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            if index == len(lines) - 1:
                break
            raise ValueError(f"checkpoint row {index + 1} is invalid") from exc
        if not isinstance(row, dict):
            raise ValueError(f"checkpoint row {index + 1} is not an object")
        agent = str(row.get("agent") or "")
        sample_id = str(row.get("sample_id") or "")
        if not agent or not sample_id:
            raise ValueError(f"checkpoint row {index + 1} has no agent/sample_id")
        rows[(agent, sample_id)] = row
    return rows_path, rows


def append_checkpoint_row(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def recommend_answer_agent(
    summaries: Mapping[str, Mapping[str, Any]],
    *,
    max_error_rate: float = 0.0,
    min_claim_support_rate: float = 0.65,
    min_refusal_accuracy: float = 0.75,
    max_invalid_claim_citation_rate: float = 0.10,
) -> dict[str, Any]:
    scorecards: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    for agent, summary in summaries.items():
        rejection_reasons: list[str] = []
        error_rate = float(summary.get("error_rate") or 0.0)
        claim_support = summary.get("claim_support_rate")
        refusal_accuracy = summary.get("refusal_accuracy")
        invalid_citation_rate = summary.get("invalid_claim_citation_rate")
        if error_rate > max_error_rate:
            rejection_reasons.append("ERROR_RATE_ABOVE_THRESHOLD")
        if claim_support is None or float(claim_support) < min_claim_support_rate:
            rejection_reasons.append("CLAIM_SUPPORT_BELOW_THRESHOLD")
        if refusal_accuracy is None:
            rejection_reasons.append("REFUSAL_NOT_MEASURED")
        elif float(refusal_accuracy) < min_refusal_accuracy:
            rejection_reasons.append("REFUSAL_ACCURACY_BELOW_THRESHOLD")
        if (
            invalid_citation_rate is None
            or float(invalid_citation_rate) > max_invalid_claim_citation_rate
        ):
            rejection_reasons.append("INVALID_CITATION_RATE_ABOVE_THRESHOLD")

        values = {
            field: (
                1.0 - float(invalid_citation_rate)
                if field == "citation_validity" and invalid_citation_rate is not None
                else float(summary.get(field) or 0.0)
            )
            for field in ANSWER_QUALITY_WEIGHTS
        }
        quality_score = sum(
            ANSWER_QUALITY_WEIGHTS[field] * values[field] for field in ANSWER_QUALITY_WEIGHTS
        )
        latency = summary.get("latency_ms") or {}
        tokens = summary.get("tokens") or {}
        scorecards[agent] = {
            "eligible": not rejection_reasons,
            "rejection_reasons": rejection_reasons,
            "quality_score": round(quality_score, 6),
            "quality_values": {field: round(value, 6) for field, value in values.items()},
            "average_tokens": tokens.get("average"),
            "p95_latency_ms": latency.get("p95"),
        }
        if not rejection_reasons:
            eligible.append(agent)

    ranked = sorted(
        eligible,
        key=lambda agent: (
            -float(scorecards[agent]["quality_score"]),
            float(scorecards[agent]["average_tokens"] or float("inf")),
            float(scorecards[agent]["p95_latency_ms"] or float("inf")),
            agent,
        ),
    )
    recommended = ranked[0] if ranked else None
    return {
        "recommended_default": recommended,
        "reasons": (
            ["HIGHEST_ELIGIBLE_ANSWER_QUALITY_SCORE"]
            if recommended is not None
            else ["NO_AGENT_MET_ANSWER_QUALITY_GATE"]
        ),
        "ranking": ranked,
        "quality_weights": ANSWER_QUALITY_WEIGHTS,
        "thresholds": {
            "max_error_rate": max_error_rate,
            "min_claim_support_rate": min_claim_support_rate,
            "min_refusal_accuracy": min_refusal_accuracy,
            "max_invalid_claim_citation_rate": max_invalid_claim_citation_rate,
        },
        "scorecards": scorecards,
        "tie_breakers": ["average_tokens", "p95_latency_ms", "agent_name"],
    }


def evaluate_agent(
    agent_name: str,
    agent: Any,
    dataset: EvalDataset,
    *,
    collection: str,
    mode: str,
    top_k: int,
    limit: int | None = None,
    source_aliases: Mapping[str, Sequence[str]] | None = None,
    sample_ids: Sequence[str] | None = None,
    existing_rows: Mapping[str, Mapping[str, Any]] | None = None,
    on_row: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    samples = select_samples(dataset, limit=limit, sample_ids=sample_ids)
    llm = getattr(agent, "llm", None)
    for index, sample in enumerate(samples, start=1):
        existing = existing_rows.get(sample.id) if existing_rows is not None else None
        if existing is not None and not existing.get("error"):
            row = dict(existing)
            rows.append(row)
            print(f"[{agent_name}] {index}/{len(samples)} {sample.id} resumed")
            continue
        call_count_before = int(getattr(llm, "calls", 0))
        started = time.perf_counter()
        results: Sequence[Any] = ()
        answer: str | None = None
        grounding: dict[str, Any] | None = None
        tokens = 0
        error = None
        contextualization = None
        effective_query = sample.question
        try:
            if sample.history:
                contextualization = contextualize_query(
                    llm,
                    sample.question,
                    [
                        {
                            "role": role,
                            "content": content,
                            "grounded": role == "assistant",
                        }
                        for role, content in sample.history
                    ],
                )
                effective_query = contextualization.query
                tokens += contextualization.token_usage
            kwargs = {
                "collection_names": [collection],
                "allowed_collections": [collection],
                "top_k": top_k,
            }
            if mode == "answer":
                collector = TraceCollector(sample.question)
                if contextualization is not None:
                    collector.record_contextualization(
                        depends_on_history=contextualization.depends_on_history,
                        history_turn_count=contextualization.history_turn_count,
                        fallback_used=contextualization.fallback_used,
                        reason=contextualization.reason,
                        token_usage=contextualization.token_usage,
                    )
                answer, results, agent_tokens = agent.query(
                    effective_query,
                    trace_collector=collector,
                    **kwargs,
                )
                tokens += int(agent_tokens or 0)
                grounding = collector.build(
                    total_tokens=tokens,
                    final_results=results,
                    answer=answer,
                ).get("grounding")
            else:
                results, agent_tokens, _ = agent.retrieve(effective_query, **kwargs)
                tokens += int(agent_tokens or 0)
        except Exception as exc:
            error = _safe_error(exc)
        latency_ms = (time.perf_counter() - started) * 1000
        llm_calls = int(getattr(llm, "calls", 0)) - call_count_before
        row = evaluate_sample(
            sample,
            results,
            answer=answer,
            top_k=top_k,
            latency_ms=latency_ms,
            tokens=tokens,
            llm_calls=llm_calls,
            error=error,
            source_aliases=source_aliases,
            grounding=grounding,
        )
        row["agent"] = agent_name
        row["context_expected_dependency"] = sample.context_dependent if sample.history else None
        row["context_predicted_dependency"] = (
            contextualization.depends_on_history if contextualization is not None else None
        )
        row["context_dependency_correct"] = (
            contextualization.depends_on_history == sample.context_dependent
            if contextualization is not None
            else None
        )
        row["context_query_match"] = (
            re.sub(r"\s+", " ", effective_query.casefold()).strip()
            == re.sub(r"\s+", " ", (sample.standalone_question or "").casefold()).strip()
            if contextualization is not None
            else None
        )
        row["context_fallback_used"] = (
            contextualization.fallback_used if contextualization is not None else None
        )
        row["context_tokens"] = (
            contextualization.token_usage if contextualization is not None else 0
        )
        rows.append(row)
        if on_row is not None:
            on_row(row)
        print(
            f"[{agent_name}] {index}/{len(samples)} {sample.id} "
            f"hit={row['retrieval_hit']} error={error or '-'} "
            f"latency={latency_ms:.0f}ms"
        )
    return rows, aggregate(rows)


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


def _runtime_metadata(config: Configuration) -> dict[str, Any]:
    providers = {}
    for feature in ("llm", "embedding", "vector_db"):
        setting = config.get_provider_config(feature)
        model = setting.get("config", {}).get("model")
        providers[feature] = {
            "provider": setting.get("provider"),
            **({"model": model} if model else {}),
        }
    return providers


def build_report(
    dataset: EvalDataset,
    config: Configuration,
    *,
    collection: str,
    mode: str,
    top_k: int,
    max_iter: int,
    summaries: Mapping[str, dict[str, Any]],
    rows: Sequence[dict[str, Any]],
    config_path: Path,
    source_aliases: Mapping[str, Sequence[str]],
    deep_search_concurrency: int,
    llm_timeout_seconds: float | None,
    external_call_timeout_seconds: float,
    request_timeout_seconds: float,
    chain_min_evidence_for_stop: int,
    selected_samples: Sequence[Any],
    sample_profile: str | None,
) -> dict[str, Any]:
    metrics_by_tag = {
        agent: {
            tag: aggregate(
                [row for row in rows if row.get("agent") == agent and tag in row.get("tags", [])]
            )
            for tag in sorted({tag for row in rows for tag in row.get("tags", [])})
        }
        for agent in summaries
    }
    metrics_by_difficulty = {
        agent: {
            difficulty: aggregate(
                [
                    row
                    for row in rows
                    if row.get("agent") == agent and row.get("difficulty") == difficulty
                ]
            )
            for difficulty in sorted({str(row.get("difficulty")) for row in rows})
        }
        for agent in summaries
    }
    report = {
        "report_schema_version": 2,
        "metric_version": METRIC_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "sources": [source.as_dict() for source in dataset.sources],
            "sample_count": len(rows) // max(len(summaries), 1),
        },
        "run": {
            "mode": mode,
            "collection": collection,
            "top_k": top_k,
            "max_iter": max_iter,
            "deep_search_concurrency": deep_search_concurrency,
            "llm_timeout_seconds": llm_timeout_seconds,
            "external_call_timeout_seconds": external_call_timeout_seconds,
            "request_timeout_seconds": request_timeout_seconds,
            "chain_early_stopping": True,
            "chain_min_evidence_for_stop": chain_min_evidence_for_stop,
            "agents": list(summaries),
            "selection": {
                "profile": sample_profile,
                "sample_ids": [sample.id for sample in selected_samples],
                "strata": summarize_sample_selection(selected_samples),
            },
            "source_aliases": dict(source_aliases),
            "config_path": str(config_path.resolve()),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "environment": {
            "python": platform.python_version(),
            "providers": _runtime_metadata(config),
            "git": _git_state(),
        },
        "metrics": dict(summaries),
        "metrics_by_tag": metrics_by_tag,
        "metrics_by_difficulty": metrics_by_difficulty,
        "details": list(rows),
        "metric_notes": {
            "retrieval_precision_at_k": "相关结果数除以固定 K；返回不足 K 条时，缺失位置按不相关计。",
            "answer_criteria_coverage": "标准答案要点在生成答案中的确定性字符串覆盖率。",
            "grounded_criteria_coverage": "标准答案要点在召回文本中的覆盖率，是证据充分度代理指标，不等同于 LLM 忠实度裁判。",
            "evidence_source_accuracy": "返回来源中至少一个命中标注文档与页码的比例。",
            "full_evidence_retrieval_rate": "回答所需的全部标注文档页都被召回的样本比例。",
            "full_document_coverage_rate": "跨文档问题所需的每份文档至少命中一条证据的样本比例。",
            "claim_support_rate": "生成答案中带有本次有效证据编号的声明比例。",
            "claim_citation_precision": "声明实际引用的证据中，命中金标文档与页码的比例。",
            "claim_citation_recall": "金标证据页中至少被一个声明引用的比例。",
            "context_dependency_accuracy": "有历史样本中，是否需要依赖历史的分类准确率。",
            "context_query_match_rate": "上下文改写结果与数据集标注独立问题的规范化精确匹配率。",
        },
    }
    if mode == "answer":
        report["decision"] = recommend_answer_agent(summaries)
    return report


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

    scalar_fields = [
        "agent",
        "sample_id",
        "question",
        "answerable",
        "difficulty",
        "history_turn_count",
        "context_expected_dependency",
        "context_predicted_dependency",
        "context_dependency_correct",
        "context_query_match",
        "context_fallback_used",
        "context_tokens",
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
        "answer_criteria_coverage",
        "grounded_criteria_coverage",
        "evidence_source_correct",
        "refusal_correct",
        "grounding_state",
        "claim_count",
        "supported_claim_count",
        "claim_support_rate",
        "ungrounded_claim_rate",
        "invalid_claim_citation_rate",
        "claim_citation_precision",
        "claim_citation_recall",
        "latency_ms",
        "tokens",
        "llm_calls",
        "error",
        "answer",
        "reference_answer",
    ]
    temporary_csv = details_path.with_suffix(".csv.tmp")
    with temporary_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_fields)
        writer.writeheader()
        for row in report["details"]:
            writer.writerow({field: row.get(field) for field in scalar_fields})
    temporary_csv.replace(details_path)
    return report_path, details_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepSearcher 可复现质量评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--collection", required=True)
    parser.add_argument(
        "--source-alias",
        action="append",
        default=[],
        help=(
            "入库后的来源文件名；单来源可直接传名称，多来源使用 "
            "DOCUMENT=INGESTED_NAME，可重复指定。"
        ),
    )
    parser.add_argument("--agents", default=",".join(AGENT_NAMES))
    parser.add_argument("--mode", choices=("retrieval", "answer"), default="retrieval")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-iter", type=int, default=1)
    parser.add_argument("--deep-search-concurrency", type=int, default=4)
    parser.add_argument("--llm-timeout-seconds", type=float)
    parser.add_argument("--external-call-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--chain-min-evidence-for-stop", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sample-ids",
        help="逗号分隔的题目 ID；指定后优先于 --limit。",
    )
    parser.add_argument("--sample-profile", help="记录到报告中的固定选样配置名称。")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    selected_agents = tuple(name.strip() for name in args.agents.split(",") if name.strip())
    invalid_agents = sorted(set(selected_agents) - set(AGENT_NAMES))
    if not selected_agents or invalid_agents:
        raise SystemExit(f"invalid agents: {invalid_agents or 'empty selection'}")
    positive_values = (
        args.top_k,
        args.max_iter,
        args.deep_search_concurrency,
        args.external_call_timeout_seconds,
        args.request_timeout_seconds,
        args.chain_min_evidence_for_stop,
    )
    if (
        any(value <= 0 for value in positive_values)
        or (args.llm_timeout_seconds is not None and args.llm_timeout_seconds <= 0)
        or (args.limit is not None and args.limit < 1)
    ):
        raise SystemExit("numeric limits and timeouts must be positive")

    load_env_file(args.env_file)
    dataset = load_dataset(args.dataset)
    verify_sources(dataset)
    try:
        source_aliases = parse_source_aliases(dataset, args.source_alias)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    sample_ids = (
        tuple(sample_id.strip() for sample_id in args.sample_ids.split(",") if sample_id.strip())
        if args.sample_ids
        else None
    )
    try:
        selected_samples = select_samples(dataset, limit=args.limit, sample_ids=sample_ids)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    config = Configuration(str(args.config))
    output_dir = args.output or (
        ROOT / "evaluation" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    signature = {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "dataset_sha256": dataset.sha256,
        "metric_version": METRIC_VERSION,
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "collection": args.collection,
        "mode": args.mode,
        "agents": list(selected_agents),
        "top_k": args.top_k,
        "max_iter": args.max_iter,
        "deep_search_concurrency": args.deep_search_concurrency,
        "llm_timeout_seconds": args.llm_timeout_seconds,
        "external_call_timeout_seconds": args.external_call_timeout_seconds,
        "request_timeout_seconds": args.request_timeout_seconds,
        "chain_min_evidence_for_stop": args.chain_min_evidence_for_stop,
        "sample_profile": args.sample_profile,
        "sample_ids": [sample.id for sample in selected_samples],
        "source_aliases": {key: list(value) for key, value in source_aliases.items()},
    }
    try:
        checkpoint_path, checkpoint_rows = prepare_checkpoint(output_dir, signature)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    runtime = build_runtime(config)
    try:
        apply_llm_timeout_override(runtime, args.llm_timeout_seconds)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    agents = create_agents(
        runtime,
        args.max_iter,
        deep_search_concurrency=args.deep_search_concurrency,
        external_call_timeout_seconds=args.external_call_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        chain_min_evidence_for_stop=args.chain_min_evidence_for_stop,
    )
    all_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for agent_name in selected_agents:
        existing_rows = {
            sample_id: row
            for (row_agent, sample_id), row in checkpoint_rows.items()
            if row_agent == agent_name
        }
        rows, summary = evaluate_agent(
            agent_name,
            agents[agent_name],
            dataset,
            collection=args.collection,
            mode=args.mode,
            top_k=args.top_k,
            limit=args.limit,
            source_aliases=source_aliases,
            sample_ids=sample_ids,
            existing_rows=existing_rows,
            on_row=lambda row: append_checkpoint_row(checkpoint_path, row),
        )
        all_rows.extend(rows)
        summaries[agent_name] = summary

    report = build_report(
        dataset,
        config,
        collection=args.collection,
        mode=args.mode,
        top_k=args.top_k,
        max_iter=args.max_iter,
        summaries=summaries,
        rows=all_rows,
        config_path=args.config,
        source_aliases=source_aliases,
        deep_search_concurrency=args.deep_search_concurrency,
        llm_timeout_seconds=args.llm_timeout_seconds,
        external_call_timeout_seconds=args.external_call_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        chain_min_evidence_for_stop=args.chain_min_evidence_for_stop,
        selected_samples=selected_samples,
        sample_profile=args.sample_profile,
    )
    report_path, details_path = save_report(report, output_dir)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"report: {report_path}")
    print(f"details: {details_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
