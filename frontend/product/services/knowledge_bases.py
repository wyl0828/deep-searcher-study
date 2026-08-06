from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from frontend.product.backend import backend_request_headers
from frontend.product.errors import ProductError
from frontend.product.models import Document, KnowledgeBase
from frontend.product.services import documents

BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")


async def delete_knowledge_base(
    session: Session,
    knowledge_base: KnowledgeBase,
) -> KnowledgeBase | None:
    busy_document = session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == knowledge_base.id,
            Document.status.in_(["queued", "processing"]),
        )
    )
    if busy_document is not None:
        raise ProductError(
            "KNOWLEDGE_BASE_BUSY",
            "知识库中仍有文档正在处理，完成后才能删除。",
            status_code=409,
            retryable=True,
        )

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            trust_env=False,
            headers=backend_request_headers(),
        ) as client:
            response = await client.delete(
                f"{BACKEND_URL}/collections/{knowledge_base.collection_name}",
                headers={
                    "X-Confirm-Collection": knowledge_base.collection_name,
                },
            )
        if not response.is_success:
            raise ProductError(
                "KNOWLEDGE_BASE_VECTOR_DELETE_FAILED",
                "向量集合删除失败，知识库尚未删除，请稍后重试。",
                status_code=502,
                retryable=True,
            )
    except httpx.RequestError as exc:
        raise ProductError(
            "KNOWLEDGE_BASE_VECTOR_SERVICE_UNAVAILABLE",
            "问答服务暂时不可用，知识库尚未删除，请稍后重试。",
            status_code=503,
            retryable=True,
        ) from exc

    upload_root = documents.UPLOAD_DIR.resolve()
    knowledge_base_uploads = (upload_root / knowledge_base.id).resolve()
    if knowledge_base_uploads != upload_root and upload_root not in knowledge_base_uploads.parents:
        raise ProductError(
            "KNOWLEDGE_BASE_STORAGE_INVALID",
            "知识库存储路径异常，未执行删除。",
            status_code=500,
        )
    if knowledge_base_uploads.exists():
        try:
            shutil.rmtree(knowledge_base_uploads)
        except OSError as exc:
            raise ProductError(
                "KNOWLEDGE_BASE_FILE_DELETE_FAILED",
                "知识库文件删除失败，请检查文件是否被占用后重试。",
                status_code=500,
                retryable=True,
            ) from exc

    was_current = knowledge_base.is_current
    session.delete(knowledge_base)
    session.flush()

    next_current: KnowledgeBase | None = None
    if was_current:
        next_current = session.scalar(
            select(KnowledgeBase).order_by(
                KnowledgeBase.updated_at.desc(),
                KnowledgeBase.created_at.desc(),
            )
        )
        if next_current is not None:
            next_current.is_current = True
    else:
        next_current = session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.is_current.is_(True)).limit(1)
        )
    session.commit()
    return next_current


async def reindex_knowledge_base(
    session: Session,
    knowledge_base: KnowledgeBase,
) -> None:
    documents_to_index = session.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base.id)
        .order_by(Document.created_at)
    ).all()
    if not documents_to_index:
        raise ProductError(
            "KNOWLEDGE_BASE_EMPTY",
            "请先上传至少一份 PDF，再重建索引。",
            status_code=409,
        )
    if any(document.status in {"queued", "processing"} for document in documents_to_index):
        raise ProductError(
            "KNOWLEDGE_BASE_BUSY",
            "知识库中仍有文档正在处理，完成后才能重建索引。",
            status_code=409,
            retryable=True,
        )
    paths = [document.storage_path for document in documents_to_index]
    if any(not Path(path).is_file() for path in paths):
        raise ProductError(
            "KNOWLEDGE_BASE_SOURCE_MISSING",
            "部分原始 PDF 已丢失，无法完整重建索引。",
            status_code=409,
        )

    try:
        async with httpx.AsyncClient(
            timeout=600.0,
            trust_env=False,
            headers=backend_request_headers(),
        ) as client:
            response = await client.post(
                f"{BACKEND_URL}/collections/{knowledge_base.collection_name}/rebuild",
                headers={
                    "X-Confirm-Collection": knowledge_base.collection_name,
                },
                json={
                    "paths": paths,
                    "collection_description": knowledge_base.description,
                    "batch_size": documents.EMBEDDING_BATCH_SIZE,
                },
            )
        if not response.is_success:
            raise ProductError(
                "KNOWLEDGE_BASE_REINDEX_FAILED",
                "知识库索引重建失败，旧索引仍然保留，请稍后重试。",
                status_code=502,
                retryable=True,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProductError(
                "KNOWLEDGE_BASE_REINDEX_RESPONSE_INVALID",
                "索引服务返回了无法识别的结果，旧索引仍然保留。",
                status_code=502,
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise ProductError(
                "KNOWLEDGE_BASE_REINDEX_RESPONSE_INVALID",
                "索引服务返回了无法识别的结果，旧索引仍然保留。",
                status_code=502,
                retryable=True,
            )
        collection_result = payload.get("collection")
        manifest = (
            collection_result.get("manifest") if isinstance(collection_result, dict) else None
        )
        if not isinstance(manifest, dict):
            raise ProductError(
                "KNOWLEDGE_BASE_REINDEX_MANIFEST_MISSING",
                "索引重建未返回模型版本信息，旧索引仍然保留。",
                status_code=502,
                retryable=True,
            )
    except httpx.RequestError as exc:
        raise ProductError(
            "KNOWLEDGE_BASE_VECTOR_SERVICE_UNAVAILABLE",
            "问答服务暂时不可用，旧索引仍然保留，请稍后重试。",
            status_code=503,
            retryable=True,
        ) from exc

    knowledge_base.index_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    previous_collection = collection_result.get("previous_collection")
    if isinstance(previous_collection, str) and previous_collection:
        knowledge_base.index_previous_collection = previous_collection
    for document in documents_to_index:
        document.status = "ready"
        document.error_code = None
        document.error_message = None
    session.commit()
