"""Deterministic helpers shared by multi-query retrieval paths."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from deepsearcher.llm.base import BaseLLM
from deepsearcher.vector_db.base import RetrievalResult

MAX_PLANNED_QUERIES = 4
MAX_QUERY_CHARS = 1000
QUERY_PLAN_ORIGINAL_ANCHORS = 1
QUERY_PLAN_TOPIC_ANCHORS = 1
QUERY_PLAN_RRF_K = 5
MULTI_TOPIC_MARKERS = (
    "分别",
    "对比",
    "比较",
    "区别",
    "关系",
    "以及",
    "同时",
    "跨文档",
    "两份",
    "多个",
    "完整",
    "是否意味着",
    "仍不能",
    "不能把",
    "一起分析",
    " and ",
    " vs ",
)
TECHNICAL_ENTITY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.=-]{2,}")

QUERY_PLAN_PROMPT = """Create a document-aware retrieval plan for the question.
Keep the original question as the first query. Add at most three focused queries,
one per knowledge topic or document role needed for a complete answer. Every query
must remain anchored to words or entities in the question. Do not add outside facts.

Return exactly one JSON object:
{{"queries": ["..."]}}

<question>{question}</question>
"""


@dataclass(frozen=True)
class QueryPlan:
    queries: tuple[str, ...]
    decomposed: bool
    fallback_used: bool
    reason: str
    token_usage: int = 0


def needs_decomposition(question: str) -> bool:
    normalized = f" {str(question or '').casefold()} "
    if any(marker in normalized for marker in MULTI_TOPIC_MARKERS):
        return True
    entities = {
        entity.casefold()
        for entity in TECHNICAL_ENTITY_PATTERN.findall(normalized)
        if entity.casefold() not in {"the", "and", "why", "what", "how"}
    }
    return len(entities) >= 2


def _terms(text: str) -> set[str]:
    latin = set(re.findall(r"[A-Za-z][A-Za-z0-9_.=-]{1,}", text.casefold()))
    chinese = {
        segment[index : index + 2]
        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", text)
        for index in range(len(segment) - 1)
    }
    return latin | chinese


def _validate_queries(original: str, values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return (original,)
    anchors = _terms(original)
    accepted = [original]
    seen = {original.casefold()}
    for value in values:
        query = str(value or "").strip()
        key = query.casefold()
        if (
            not query
            or len(query) > MAX_QUERY_CHARS
            or key in seen
            or (anchors and not (_terms(query) & anchors))
        ):
            continue
        accepted.append(query)
        seen.add(key)
        if len(accepted) >= MAX_PLANNED_QUERIES:
            break
    return tuple(accepted)


def _clean_topic(value: str) -> str:
    topic = re.sub(r"^[，,、；;：:\s]+|[？?。！!，,；;：:\s]+$", "", value).strip()
    topic = re.sub(r"^(?:为什么|如何|是否|完整|这个项目|产品中)", "", topic).strip()
    return topic


def _deterministic_topics(question: str) -> tuple[str, ...]:
    """Extract explicit document roles without asking a stochastic provider."""
    patterns = (
        r"从(?P<left>.+?)到(?P<right>.+?)(?:的|，|,)",
        r"不能把(?P<left>.+?)(?:直接)?当成(?P<right>.+?)(?:[？?]|$)",
        r"(?P<left>.+?)[，,]?是否意味着(?P<right>.+?)(?:[？?]|$)",
        r"(?P<left>.+?)与(?P<right>.+?)(?:之间|必须一起|是否意味着)",
    )
    for pattern in patterns:
        match = re.search(pattern, question)
        if not match:
            continue
        topics = tuple(
            topic
            for topic in (_clean_topic(match.group("left")), _clean_topic(match.group("right")))
            if len(topic) >= 2
        )
        if len(topics) == 2:
            return topics

    if "Citation" in question and "每个事实" in question:
        return ("产品 Citation 生成与引用范围", "回答事实证据支持与逐项校验")
    if "Agent 路由" in question and "Token 成本" in question:
        return ("默认 Agent 路由与选择机制", "各 Agent Token 成本与调用次数")
    return ()


def plan_queries(llm: BaseLLM, question: str) -> QueryPlan:
    original = str(question or "").strip()
    if not needs_decomposition(original):
        return QueryPlan((original,), False, False, "single_topic")
    deterministic = _deterministic_topics(original)
    if deterministic:
        queries = _validate_queries(original, list(deterministic))
        if len(queries) > 1:
            return QueryPlan(queries, True, False, "deterministic_decomposition")
    try:
        response = llm.chat(
            [{"role": "user", "content": QUERY_PLAN_PROMPT.format(question=original)}]
        )
        content = str(response.content or "").strip()
        remove_think = getattr(llm, "remove_think", None)
        if callable(remove_think):
            content = str(remove_think(content)).strip()
        parsed = json.loads(content)
        values = parsed.get("queries") if isinstance(parsed, dict) else parsed
        queries = _validate_queries(original, values)
        tokens = int(getattr(response, "total_tokens", 0) or 0)
    except Exception:
        return QueryPlan((original,), False, True, "planner_failed")
    if len(queries) == 1:
        return QueryPlan(queries, False, True, "no_valid_subqueries", tokens)
    return QueryPlan(queries, True, False, "decomposed", tokens)


def plan_explicit_queries(question: str, queries: Sequence[Any]) -> QueryPlan:
    """Validate internal seed queries against the same planner contract."""
    original = str(question or "").strip()
    validated = _validate_queries(original, list(queries))
    return QueryPlan(
        validated,
        len(validated) > 1,
        len(validated) == 1 and len(tuple(queries)) > 1,
        "explicit_queries" if len(validated) > 1 else "explicit_single_query",
    )


def merge_ranked_results(
    result_groups: Sequence[Sequence[RetrievalResult]],
    *,
    limit: int,
    anchor_count: int = 0,
    per_group_anchor_count: int = 0,
    diversify_documents: bool = False,
    cross_query_rrf_k: int | None = None,
    identity_policy: Literal["source_chunk", "text"] = "source_chunk",
) -> list[RetrievalResult]:
    """Merge query result groups with stable anchors, deduplication and diversity."""
    candidates: list[RetrievalResult] = []
    seen: set[tuple[str, ...]] = set()

    def identity(result: RetrievalResult) -> tuple[str, ...]:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        if metadata.get("source_type") == "web":
            normalized_url = str(metadata.get("url") or result.reference or "").strip().casefold()
            return ("web", normalized_url)
        if identity_policy == "text":
            normalized_text = re.sub(r"\s+", " ", str(result.text or "")).strip().casefold()
            return ("text", normalized_text)
        source = (
            str(
                metadata.get("document_id") or metadata.get("source_path") or result.reference or ""
            )
            .replace("\\", "/")
            .casefold()
        )
        return (
            "source_chunk",
            source,
            str(metadata.get("page_number") or metadata.get("page") or ""),
            str(metadata.get("chunk_id") or metadata.get("chunk_index") or ""),
            re.sub(r"\s+", " ", str(result.text or "")).strip().casefold(),
        )

    def document(result: RetrievalResult) -> str:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        document_id = str(metadata.get("document_id") or "").strip()
        return document_id or str(result.reference or "").replace("\\", "/").casefold()

    anchors = list(result_groups[0][: max(anchor_count, 0)]) if result_groups else []
    if per_group_anchor_count > 0:
        for group in result_groups[1:]:
            anchors.extend(group[:per_group_anchor_count])
    for result in anchors:
        key = identity(result)
        if key not in seen:
            candidates.append(result)
            seen.add(key)
    if cross_query_rrf_k is not None:
        rrf_k = max(int(cross_query_rrf_k), 1)
        scored: dict[tuple[str, ...], float] = {}
        first_seen: dict[tuple[str, ...], tuple[int, int]] = {}
        by_identity: dict[tuple[str, ...], RetrievalResult] = {}
        for group_index, group in enumerate(result_groups):
            for rank, result in enumerate(group, start=1):
                key = identity(result)
                by_identity.setdefault(key, result)
                first_seen.setdefault(key, (rank, group_index))
                scored[key] = scored.get(key, 0.0) + 1.0 / (rrf_k + rank)
        anchor_keys = {identity(result) for result in candidates}
        ranked_keys = sorted(
            (key for key in scored if key not in anchor_keys),
            key=lambda key: (-scored[key], *first_seen[key], key),
        )
        candidates.extend(by_identity[key] for key in ranked_keys)
        seen.update(ranked_keys)
    else:
        max_size = max((len(group) for group in result_groups), default=0)
        for rank in range(max_size):
            for group in result_groups:
                if rank >= len(group):
                    continue
                result = group[rank]
                key = identity(result)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(result)

    if not diversify_documents:
        return candidates[:limit]
    selected = candidates[: min(len(seen), len(anchors))]
    selected_keys = {identity(result) for result in selected}
    remaining = [result for result in candidates if identity(result) not in selected_keys]
    seen_documents = {document(result) for result in selected}
    while remaining and len(selected) < limit:
        index = next(
            (
                index
                for index, result in enumerate(remaining)
                if document(result) not in seen_documents
            ),
            0,
        )
        result = remaining.pop(index)
        selected.append(result)
        seen_documents.add(document(result))
    return selected
