from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
import tempfile
from pathlib import Path
from time import perf_counter

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

FRONTEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_ROOT.parent
DIST_DIR = FRONTEND_ROOT / "dist"
CONFIG_PATH = PROJECT_ROOT / "deepsearcher" / "config.yaml"

BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")
MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = int(os.environ.get("MILVUS_PORT", "19530"))
MAX_PDF_BYTES = 20 * 1024 * 1024
COLLECTION_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

app = FastAPI(title="DeepSearcher 学习控制台")


class IngestRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    collection_name: str = Field(default="deepsearcher", min_length=1, max_length=64)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    max_iter: int = Field(default=3, ge=1, le=10)


def map_query_response(payload: dict, latency_ms: int) -> dict:
    response = {
        "result": payload.get("result", ""),
        "consume_token": payload.get("consume_token"),
        "latency_ms": latency_ms,
    }
    if "trace" in payload:
        response["trace"] = payload["trace"]
    return response


def validate_collection_name(value: str) -> str:
    normalized = value.strip()
    if not COLLECTION_PATTERN.fullmatch(normalized):
        raise ValueError("Collection 名称只能包含字母、数字和下划线，且不能以数字开头")
    return normalized


def decode_pdf(filename: str, content_base64: str) -> bytes:
    if not filename.lower().endswith(".pdf"):
        raise ValueError("仅支持 PDF 文件")
    try:
        payload = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PDF 文件内容无法解码") from exc
    if not payload.startswith(b"%PDF"):
        raise ValueError("所选文件不是有效的 PDF")
    if len(payload) > MAX_PDF_BYTES:
        raise ValueError("PDF 文件不能超过 20 MiB")
    return payload


def load_config_summary() -> dict[str, str]:
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        providers = config.get("provide_settings", {})
        llm = providers.get("llm", {})
        embedding = providers.get("embedding", {})
        vector_db = providers.get("vector_db", {})
        return {
            "llm_model": str(llm.get("config", {}).get("model") or "未配置"),
            "embedding_model": str(embedding.get("config", {}).get("model") or "未配置"),
            "collection": str(
                vector_db.get("config", {}).get("default_collection") or "deepsearcher"
            ),
        }
    except (OSError, yaml.YAMLError, AttributeError):
        return {
            "llm_model": "读取失败",
            "embedding_model": "读取失败",
            "collection": "deepsearcher",
        }


async def probe_backend() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(f"{BACKEND_URL}/openapi.json")
            return response.is_success
    except httpx.HTTPError:
        return False


async def probe_milvus() -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(MILVUS_HOST, MILVUS_PORT), timeout=2.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


@app.get("/api/health")
async def health() -> dict:
    config = load_config_summary()
    backend_online, milvus_online = await asyncio.gather(probe_backend(), probe_milvus())
    return {
        "services": {
            "fastapi": {"state": "online" if backend_online else "offline"},
            "milvus": {"state": "online" if milvus_online else "offline"},
            "llm": {"state": "configured" if config["llm_model"] != "未配置" else "offline"},
            "embedding": {
                "state": "configured" if config["embedding_model"] != "未配置" else "offline"
            },
        },
        "config": config,
    }


@app.post("/api/ingest")
async def ingest(request: IngestRequest) -> dict:
    try:
        collection_name = validate_collection_name(request.collection_name)
        payload = decode_pdf(request.filename, request.content_base64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    safe_name = Path(request.filename).name
    try:
        with tempfile.TemporaryDirectory(prefix="deepsearcher-upload-") as temp_dir:
            pdf_path = Path(temp_dir) / safe_name
            pdf_path.write_bytes(payload)
            async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
                response = await client.post(
                    f"{BACKEND_URL}/load-files/",
                    json={"paths": str(pdf_path), "collection_name": collection_name},
                )
            if not response.is_success:
                raise HTTPException(
                    status_code=502, detail="DeepSearcher 入库失败，请查看本地服务日志"
                )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="FastAPI 当前不可用，请先启动后端服务") from exc

    return {"message": "入库请求成功", "collection_name": collection_name}


@app.post("/api/query")
async def query(request: QueryRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题")

    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            response = await client.get(
                f"{BACKEND_URL}/query/",
                params={
                    "original_query": question,
                    "max_iter": request.max_iter,
                    "include_trace": True,
                },
            )
        if not response.is_success:
            raise HTTPException(
                status_code=502, detail="DeepSearcher 查询失败，请查看本地服务日志"
            )
        payload = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="FastAPI 当前不可用，请先启动后端服务") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="FastAPI 返回了无法解析的响应") from exc

    return map_query_response(
        payload,
        latency_ms=round((perf_counter() - started) * 1000),
    )


if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")
else:

    @app.get("/")
    async def frontend_not_built() -> dict[str, str]:
        return {"detail": "前端尚未构建，请先运行 npm run build"}
