# DeepSearcher Original Baseline Verification

## Status

Passed on 2026-07-01. The official source baseline, locked Python environment, Docker Milvus, FastAPI, real PDF ingestion, Agent query, persistence re-query, focused tests, and Git safety checks are verified.

## Versions

- DeepSearcher upstream commit: `d89e37cdfbbef5e44ae6162ce9cc2c627a69b7e1`
- Working branch: `study-baseline`
- uv: `0.7.8`
- Python: `3.10.20`
- PyMilvus: `2.5.8`
- Milvus server: `2.5.8`
- Docker Engine: `29.4.1`
- LLM: Alibaba Cloud Model Studio OpenAI-compatible `qwen-plus`
- Embedding: Alibaba Cloud Model Studio OpenAI-compatible `text-embedding-v4`, 1024 dimensions

The baseline uses uv 0.7.8 because current uv 0.11 rewrites the repository's older lock file. All project commands use `--frozen`; `uv.lock` remains identical to upstream.

## Runtime checks

- Docker Desktop is running with the WSL2 Linux engine.
- `milvus-etcd`, `milvus-minio`, and `milvus-standalone` are healthy.
- TCP `127.0.0.1:19530` is reachable.
- PyMilvus reports Milvus server `2.5.8`.
- A synthetic vector was inserted, searched through DeepSearcher's Milvus wrapper, and deleted successfully.
- FastAPI initialized with the configured LLM, embedding, loader, and Milvus modules.
- `GET http://127.0.0.1:8500/openapi.json` returned HTTP 200 during the bootstrap smoke test.

Windows/Hyper-V reserves TCP ports `7942-8041` on this machine, which includes the upstream default port 8000. A direct socket test reproduced `WinError 10013` on 8000 and succeeded on 8500, so the local baseline uses 8500 without modifying `main.py`.

## Automated checks

The following focused upstream suites passed without real provider calls:

```text
tests/llm/test_siliconflow.py
tests/embedding/test_siliconflow_embedding.py
tests/agent
tests/loader/test_splitter.py
tests/loader/file_loader/test_pdf_loader.py
tests/utils/test_log.py
```

Final result: `78 passed in 1.95s`.

## Test PDF

- Local path: `data/baseline/aurora-facts.pdf`
- SHA-256: `2267DFC442A999B58E9AED4CD7DA970586F5FB5B9DAF6B51D92B2F2D6F13A99A`
- Git status: ignored by `/data/`
- Visual render: one readable A4 page with no clipping or overlap
- Extracted facts:
  - Project Aurora is owned by Lin Qiao.
  - The approved production launch date is September 15, 2026.
  - The approved budget is 2.4 million yuan.
  - The primary deployment region is Shanghai.

## Provider resolution

The first real ingestion attempt parsed the PDF and created collection `deepsearcher`, then failed at the Embedding boundary:

- `POST https://api.siliconflow.cn/v1/embeddings`: HTTP 401
- Independent `GET https://api.siliconflow.cn/v1/models`: HTTP 401
- `.env` diagnostics: exactly one key entry, no BOM, no surrounding quotes, no leading/trailing whitespace, and no placeholder text
- Milvus collection `deepsearcher` at that point: `row_count = 0`

The credential belonged to the user-provided Alibaba Cloud Model Studio Beijing workspace rather than SiliconFlow. After switching to the workspace's OpenAI-compatible interface:

- `GET /models`: HTTP 200, 220 models listed.
- `text-embedding-v4` probe: returned 1024 dimensions.
- `qwen-plus` probe: returned exactly `OK`, consuming 14 tokens.
- `.env` now stores `OPENAI_API_KEY` and `OPENAI_BASE_URL`; both remain ignored and are never printed.
- The Anthropic-compatible endpoint is not used because the OpenAI-compatible endpoint supports both chat and embeddings.

## End-to-end evidence

- FastAPI start: `GET http://127.0.0.1:8500/openapi.json` returned HTTP 200.
- PDF ingestion: `POST /load-files/` returned `Files loaded successfully.`.
- Milvus direct query returned the complete PDF text.
- After flush, collection `deepsearcher` reported `row_count = 1`.
- Query: `Who owns Project Aurora and what is its approved production launch date?`
- Agent route: `ChainOfRAG`, three search iterations, one retrieved chunk.
- Answer: `Lin Qiao owns Project Aurora, and its approved production launch date is September 15, 2026.`
- Token consumption: `1795`.
- Persistence: after stopping and restarting FastAPI, the same `max_iter=3` query returned the same facts without re-ingesting the PDF.

## Git safety

- `.env`, `.venv`, `data/`, `logs/`, and `infra/milvus/volumes/` are ignored.
- The test PDF and Milvus data are not tracked.
- No core DeepSearcher Agent, Loader, Vector DB, offline-loading, or query implementation has been modified.
- No API key has been written to a tracked file.
- Final tracked runtime-risk scan count: `0`.
- Final tracked secret-pattern scan count: `0`.
- `uv.lock` matches `upstream/master`.
- The only changes from upstream are `.gitignore`, `deepsearcher/config.yaml`, the design/plan/verification documents, and `infra/milvus/docker-compose.yml`.
