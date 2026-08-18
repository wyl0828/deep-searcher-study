"""Ingestion pipeline engine (aligned with ragent IngestionEngine).

Python equivalent of the ragent ingestion engine without a full DAG:
- IngestionEngine: validate_pipeline / find_start_nodes / execute_chain / execute_node
- IngestionNode + NodeConfig: node_id / node_type / settings / enabled / next_node_id
- NodeResult: ok / skip / fail / terminate factories
- IngestionContext: shared state (document / job / knowledge_base / steps / manifest)

P2-B contract:
- IndexNode is the mandatory terminal node: exactly one, enabled, no next node;
  every other node must eventually reach it.
- enabled=false only skips LOCAL validation / parameter normalization. The
  backend /load-files/ call is atomic and is never split by these nodes.
- Only process_* owns _finish_ingest_job; nodes never mutate the final
  job/document status (no double-finish across sync/MQ paths).
- Every node exception is normalized to NodeResult.fail with a NodeFailure
  {node_id, node_type, code, message}.
- terminate() stops the chain; lifecycle success additionally requires
  context.manifest to be produced (a terminated chain without a manifest is not
  a successful ingestion).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from frontend.product.errors import ProductError

NODE_PARSE = "parse"
NODE_CHUNK = "chunk"
NODE_EMBED = "embed"
NODE_INDEX = "index"
REGISTERED_NODE_TYPES = frozenset({NODE_PARSE, NODE_CHUNK, NODE_EMBED, NODE_INDEX})

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TERMINATED = "terminated"

# Chunk size sentinel for "do not split the document" (ragent WHOLE_DOCUMENT_SENTINEL).
CHUNK_SIZE_WHOLE_DOCUMENT = -1


@dataclass(frozen=True)
class NodeFailure:
    """Structured failure kept on the context for diagnostics."""

    node_id: str
    node_type: str
    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class NodeResult:
    """One node's execution result (ragent NodeResult)."""

    success: bool
    should_continue: bool
    message: str | None = None
    failure: NodeFailure | None = None

    @classmethod
    def ok(cls, message: str | None = None) -> "NodeResult":
        return cls(success=True, should_continue=True, message=message)

    @classmethod
    def skip(cls, reason: str) -> "NodeResult":
        return cls(success=True, should_continue=True, message=f"Skipped: {reason}")

    @classmethod
    def fail(cls, failure: NodeFailure) -> "NodeResult":
        return cls(success=False, should_continue=False, failure=failure, message=failure.message)

    @classmethod
    def terminate(cls, reason: str) -> "NodeResult":
        return cls(success=True, should_continue=False, message=reason)


@dataclass
class NodeConfig:
    """One pipeline node's configuration (ragent NodeConfig)."""

    node_id: str
    node_type: str
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    next_node_id: str | None = None


@dataclass
class IngestionContext:
    """Shared state across the pipeline (ragent IngestionContext)."""

    job: Any
    document: Any
    knowledge_base: Any
    steps: list[dict[str, Any]] = field(default_factory=list)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_params: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] | None = None
    failure: NodeFailure | None = None
    status: str = STATUS_PENDING
    node_logs: list[dict[str, Any]] = field(default_factory=list)


def default_pipeline_steps() -> list[dict[str, Any]]:
    """Default four-node chain (parse -> chunk -> embed -> index)."""
    return [
        {
            "node_id": "parse",
            "node_type": NODE_PARSE,
            "enabled": True,
            "settings": {},
            "next_node_id": "chunk",
        },
        {
            "node_id": "chunk",
            "node_type": NODE_CHUNK,
            "enabled": True,
            "settings": {},
            "next_node_id": "embed",
        },
        {
            "node_id": "embed",
            "node_type": NODE_EMBED,
            "enabled": True,
            "settings": {},
            "next_node_id": "index",
        },
        {
            "node_id": "index",
            "node_type": NODE_INDEX,
            "enabled": True,
            "settings": {},
            "next_node_id": None,
        },
    ]


def _normalize_steps(steps: list[dict[str, Any]]) -> list[NodeConfig]:
    return [
        NodeConfig(
            node_id=str(item.get("node_id") or ""),
            node_type=str(item.get("node_type") or ""),
            settings=item.get("settings"),
            enabled=bool(item.get("enabled", True)),
            next_node_id=item.get("next_node_id"),
        )
        for item in steps
    ]


def _raise_invalid(message: str) -> None:
    raise ProductError("INGESTION_PIPELINE_INVALID", message, status_code=400)


def validate_pipeline(steps: list[dict[str, Any]]) -> list[NodeConfig]:
    """Full structural validation (ragent validatePipeline + findStartNodes).

    Rules: non-empty; node_id unique; node_type registered; settings object;
    exactly one enabled terminal IndexNode; a single start node; every next
    reference exists; no cycles; every node reachable from the start.
    """
    if not steps:
        _raise_invalid("入库流水线不能为空")
    nodes = _normalize_steps(steps)

    node_ids = [node.node_id for node in nodes]
    if any(not node_id for node_id in node_ids):
        _raise_invalid("流水线节点缺少 node_id")
    if len(set(node_ids)) != len(node_ids):
        _raise_invalid("流水线节点 node_id 必须唯一")

    for node in nodes:
        if node.node_type not in REGISTERED_NODE_TYPES:
            _raise_invalid(f"未知节点类型: {node.node_type}")
        if node.settings is None or not isinstance(node.settings, dict):
            _raise_invalid(f"节点 {node.node_id} 的 settings 必须是对象")

    index_nodes = [node for node in nodes if node.node_type == NODE_INDEX]
    if len(index_nodes) != 1:
        _raise_invalid("流水线必须恰好包含一个 index 节点")
    index = index_nodes[0]
    if not index.enabled:
        _raise_invalid("index 节点不可禁用")
    if index.next_node_id is not None:
        _raise_invalid("index 节点必须是终结节点")

    by_id = {node.node_id: node for node in nodes}
    referenced = {node.next_node_id for node in nodes if node.next_node_id}
    missing = referenced - set(by_id)
    if missing:
        _raise_invalid("存在指向不存在节点的 next_node_id: " + ", ".join(sorted(missing)))

    # A start node has no incoming edge.
    incoming = set()
    for node in nodes:
        if node.next_node_id:
            incoming.add(node.next_node_id)
    starts = [node for node in nodes if node.node_id not in incoming]
    if len(starts) != 1:
        _raise_invalid(f"流水线必须恰好有一个起始节点，当前 {len(starts)} 个")

    start = starts[0]
    # Reachability from the single start.
    visited: set[str] = set()
    stack = [start.node_id]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = by_id[node_id]
        if node.next_node_id:
            stack.append(node.next_node_id)
    unreachable = set(by_id) - visited
    if unreachable:
        _raise_invalid("以下节点无法从起始节点到达: " + ", ".join(sorted(unreachable)))

    # Cycle detection: walk each node's next chain, stop when a node repeats.
    for node in nodes:
        seen: set[str] = set()
        current = node.node_id
        while current:
            if current in seen:
                _raise_invalid(f"流水线存在环: {current}")
            seen.add(current)
            current = by_id[current].next_node_id
    return nodes


def _next_node(config: NodeConfig, nodes_by_id: dict[str, NodeConfig]) -> NodeConfig | None:
    if not config.next_node_id:
        return None
    return nodes_by_id[config.next_node_id]


async def execute_chain(context: IngestionContext) -> IngestionContext:
    """Run the validated chain from its single start node (ragent executeChain)."""
    nodes = validate_pipeline(context.steps)
    by_id = {node.node_id: node for node in nodes}
    incoming = set()
    for node in nodes:
        if node.next_node_id:
            incoming.add(node.next_node_id)
    start = next(node for node in nodes if node.node_id not in incoming)

    context.status = STATUS_RUNNING
    current = start
    executed = 0
    while current is not None:
        executed += 1
        if executed > len(nodes):
            failure = NodeFailure(
                node_id=current.node_id,
                node_type=current.node_type,
                code=f"NODE_{current.node_type.upper()}_FAILED",
                message="执行节点数超过上限，可能存在死循环",
                retryable=False,
            )
            context.failure = failure
            context.status = STATUS_FAILED
            return context
        result = await execute_node(context, current)
        context.node_logs.append(
            {
                "node_id": current.node_id,
                "node_type": current.node_type,
                "message": result.message,
                "success": result.success,
            }
        )
        if not result.success:
            context.failure = result.failure
            context.status = STATUS_FAILED
            return context
        if not result.should_continue:
            context.status = STATUS_TERMINATED
            return context
        current = _next_node(current, by_id)
    context.status = STATUS_COMPLETED
    return context


async def execute_node(context: IngestionContext, config: NodeConfig) -> NodeResult:
    """Run one node; every exception is normalized to NodeResult.fail."""
    if not config.enabled:
        return NodeResult.skip("节点未启用")
    node = _node_for(config.node_type)
    try:
        return await node.execute(context, config)
    except Exception as exc:  # noqa: BLE001 - normalized into NodeResult.fail
        return NodeResult.fail(_normalize_failure(config, exc))


def _normalize_failure(config: NodeConfig, exc: Exception) -> NodeFailure:
    code = f"NODE_{config.node_type.upper()}_FAILED"
    retryable = False
    if isinstance(exc, ProductError):
        code = getattr(exc, "code", code)
    else:
        code = getattr(exc, "code", code)
        retryable = bool(getattr(exc, "retryable", False))
    return NodeFailure(
        node_id=config.node_id,
        node_type=config.node_type,
        code=code,
        message=str(exc),
        retryable=retryable,
    )


class _IngestionNode:
    node_type: str = ""

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        raise NotImplementedError


class ParseNode(_IngestionNode):
    """Local loader-routing validation (ragent ParserNode + ParserRegistry).

    enabled=false only skips this local check; the backend /load-files/ still
    parses the document with its default configuration.
    """

    node_type = NODE_PARSE

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        from deepsearcher.loader.file_loader.mime_type import normalize_extension
        from deepsearcher.loader.file_loader.registry import LoaderRegistry

        display_name = getattr(context.document, "display_name", "") or ""
        extension = normalize_extension(display_name)
        registry = LoaderRegistry()
        try:
            loader = registry.loader_for(display_name)
        except Exception as exc:
            return NodeResult.fail(_normalize_failure(config, exc))
        if loader is None:
            return NodeResult.fail(
                NodeFailure(
                    node_id=config.node_id,
                    node_type=config.node_type,
                    code="NODE_PARSE_UNSUPPORTED_TYPE",
                    message=f"不支持的文件类型: .{extension or 'unknown'}",
                    retryable=False,
                )
            )
        context.node_outputs[config.node_id] = {
            "extension": extension,
            "loader": loader.__class__.__name__,
        }
        return NodeResult.ok(f"路由到解析器 {loader.__class__.__name__}")


class ChunkNode(_IngestionNode):
    """Local chunk-settings normalization (ragent ChunkerNode budget).

    chunk_size=-1 means "do not split"; normalization is recorded locally. The
    atomic backend call keeps its default chunking unless the endpoint contract
    is confirmed to accept these overrides (out of scope for P2-B).
    """

    node_type = NODE_CHUNK

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        settings = config.settings or {}
        chunk_size = settings.get("chunk_size")
        if chunk_size is not None:
            try:
                chunk_size = int(chunk_size)
            except (TypeError, ValueError):
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_CHUNK_INVALID_SETTINGS",
                        message=f"chunk_size 必须是整数: {chunk_size!r}",
                        retryable=False,
                    )
                )
            if chunk_size != CHUNK_SIZE_WHOLE_DOCUMENT and chunk_size <= 0:
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_CHUNK_INVALID_SETTINGS",
                        message=f"chunk_size 必须 > 0 或等于 {CHUNK_SIZE_WHOLE_DOCUMENT}",
                        retryable=False,
                    )
                )
        chunk_overlap = settings.get("chunk_overlap")
        if chunk_overlap is not None:
            try:
                chunk_overlap = int(chunk_overlap)
            except (TypeError, ValueError):
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_CHUNK_INVALID_SETTINGS",
                        message=f"chunk_overlap 必须是整数: {chunk_overlap!r}",
                        retryable=False,
                    )
                )
            if chunk_overlap < 0:
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_CHUNK_INVALID_SETTINGS",
                        message="chunk_overlap 不能为负数",
                        retryable=False,
                    )
                )
        context.node_outputs[config.node_id] = {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        }
        return NodeResult.ok("分块参数已归一化")


class EmbedNode(_IngestionNode):
    """Local embedding-settings normalization (ragent embedding part)."""

    node_type = NODE_EMBED

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        settings = config.settings or {}
        batch_size = settings.get("batch_size")
        if batch_size is not None:
            try:
                batch_size = int(batch_size)
            except (TypeError, ValueError):
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_EMBED_INVALID_SETTINGS",
                        message=f"batch_size 必须是整数: {batch_size!r}",
                        retryable=False,
                    )
                )
            if batch_size <= 0:
                return NodeResult.fail(
                    NodeFailure(
                        node_id=config.node_id,
                        node_type=config.node_type,
                        code="NODE_EMBED_INVALID_SETTINGS",
                        message="batch_size 必须 > 0",
                        retryable=False,
                    )
                )
        context.node_outputs[config.node_id] = {"batch_size": batch_size}
        return NodeResult.ok("向量参数已归一化")


class IndexNode(_IngestionNode):
    """Mandatory terminal node: the single backend /load-files/ call.

    Only reads context and sets context.manifest; it never mutates job/document
    final status. Lifecycle success is decided by process_* from the manifest.
    """

    node_type = NODE_INDEX

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        from frontend.product.services.documents import _load_document_into_backend

        manifest = await _load_document_into_backend(
            document=context.document,
            knowledge_base=context.knowledge_base,
            request_params=context.request_params,
        )
        context.manifest = manifest
        return NodeResult.ok("后端入库完成")


_NODE_INSTANCES: dict[str, _IngestionNode] = {
    NODE_PARSE: ParseNode(),
    NODE_CHUNK: ChunkNode(),
    NODE_EMBED: EmbedNode(),
    NODE_INDEX: IndexNode(),
}


def _node_for(node_type: str) -> _IngestionNode:
    return _NODE_INSTANCES[node_type]
