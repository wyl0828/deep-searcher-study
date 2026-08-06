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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepsearcher.agent import ChainOfRAG, DeepSearch, NaiveRAG
from deepsearcher.configuration import Configuration, RuntimeComponents, build_runtime
from evaluation.dataset import EvalDataset, load_dataset
from evaluation.metrics import aggregate, evaluate_sample

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "milvus_v1.json"
DEFAULT_CONFIG = ROOT / "deepsearcher" / "config.yaml"
AGENT_NAMES = ("naive", "deep_search", "chain_of_rag")
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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


def verify_source(dataset: EvalDataset, root: Path = ROOT) -> Path:
    source_path = Path(str(dataset.source.get("path", "")))
    resolved = source_path if source_path.is_absolute() else root / source_path
    if not resolved.is_file():
        raise FileNotFoundError(f"evaluation source not found: {resolved}")
    actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if actual_sha256 != dataset.source["sha256"]:
        raise ValueError(
            "evaluation source SHA-256 mismatch: "
            f"expected {dataset.source['sha256']}, got {actual_sha256}"
        )
    return resolved.resolve()


def create_agents(
    runtime: RuntimeComponents,
    max_iter: int,
    *,
    deep_search_concurrency: int = 4,
    external_call_timeout_seconds: float = 30.0,
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    samples = select_samples(dataset, limit=limit, sample_ids=sample_ids)
    llm = getattr(agent, "llm", None)
    for index, sample in enumerate(samples, start=1):
        call_count_before = int(getattr(llm, "calls", 0))
        started = time.perf_counter()
        results: Sequence[Any] = ()
        answer: str | None = None
        tokens = 0
        error = None
        try:
            kwargs = {
                "collection_names": [collection],
                "allowed_collections": [collection],
                "top_k": top_k,
            }
            if mode == "answer":
                answer, results, tokens = agent.query(sample.question, **kwargs)
            else:
                results, tokens, _ = agent.retrieve(sample.question, **kwargs)
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
        )
        row["agent"] = agent_name
        rows.append(row)
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
    external_call_timeout_seconds: float,
    request_timeout_seconds: float,
    chain_min_evidence_for_stop: int,
) -> dict[str, Any]:
    return {
        "report_schema_version": 1,
        "metric_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "source": dataset.source,
            "sample_count": len(rows) // max(len(summaries), 1),
        },
        "run": {
            "mode": mode,
            "collection": collection,
            "top_k": top_k,
            "max_iter": max_iter,
            "deep_search_concurrency": deep_search_concurrency,
            "external_call_timeout_seconds": external_call_timeout_seconds,
            "request_timeout_seconds": request_timeout_seconds,
            "chain_early_stopping": True,
            "chain_min_evidence_for_stop": chain_min_evidence_for_stop,
            "agents": list(summaries),
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
        "details": list(rows),
        "metric_notes": {
            "retrieval_precision_at_k": "相关结果数除以固定 K；返回不足 K 条时，缺失位置按不相关计。",
            "answer_criteria_coverage": "标准答案要点在生成答案中的确定性字符串覆盖率。",
            "grounded_criteria_coverage": "标准答案要点在召回文本中的覆盖率，是证据充分度代理指标，不等同于 LLM 忠实度裁判。",
            "evidence_source_accuracy": "返回来源中至少一个命中标注文档与页码的比例。",
        },
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

    scalar_fields = [
        "agent",
        "sample_id",
        "question",
        "answerable",
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
        help="入库后的来源文件名；可重复指定，映射到数据集原始文档。",
    )
    parser.add_argument("--agents", default=",".join(AGENT_NAMES))
    parser.add_argument("--mode", choices=("retrieval", "answer"), default="retrieval")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-iter", type=int, default=1)
    parser.add_argument("--deep-search-concurrency", type=int, default=4)
    parser.add_argument("--external-call-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--chain-min-evidence-for-stop", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sample-ids",
        help="逗号分隔的题目 ID；指定后优先于 --limit。",
    )
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
    if any(value <= 0 for value in positive_values) or (args.limit is not None and args.limit < 1):
        raise SystemExit("numeric limits and timeouts must be positive")

    load_env_file(args.env_file)
    dataset = load_dataset(args.dataset)
    verify_source(dataset)
    source_document = str(dataset.source["document"])
    source_aliases = {source_document: tuple(args.source_alias)}
    sample_ids = (
        tuple(sample_id.strip() for sample_id in args.sample_ids.split(",") if sample_id.strip())
        if args.sample_ids
        else None
    )
    try:
        select_samples(dataset, limit=args.limit, sample_ids=sample_ids)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    config = Configuration(str(args.config))
    runtime = build_runtime(config)
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
        external_call_timeout_seconds=args.external_call_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        chain_min_evidence_for_stop=args.chain_min_evidence_for_stop,
    )
    output_dir = args.output or (
        ROOT / "evaluation" / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    report_path, details_path = save_report(report, output_dir)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"report: {report_path}")
    print(f"details: {details_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
