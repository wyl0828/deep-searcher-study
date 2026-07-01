# DeepSearcher Original Baseline Verification

## Status

Execution is in progress. Source, Python, Milvus, FastAPI startup, core tests, and the test PDF are verified. The real PDF ingestion and answer checks are pending because no local `.env` file with `SILICONFLOW_API_KEY` exists yet.

## Versions

- DeepSearcher upstream commit: `d89e37cdfbbef5e44ae6162ce9cc2c627a69b7e1`
- Working branch: `study-baseline`
- uv: `0.7.8`
- Python: `3.10.20`
- PyMilvus: `2.5.8`
- Milvus server: `2.5.8`
- Docker Engine: `29.4.1`

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

Result: `78 passed in 3.09s`.

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

## Pending end-to-end evidence

The baseline is not complete until all of these checks pass with a real provider credential:

1. Start FastAPI with the ignored `.env` file.
2. Load the test PDF through `POST /load-files/`.
3. Confirm collection `deepsearcher` contains entities.
4. Query the owner and launch date through `GET /query/`.
5. Confirm the answer contains `Lin Qiao` and `September 15, 2026`.
6. Record token consumption.
7. Restart FastAPI and confirm the persisted collection is still queryable.
8. Run the final Git and secret-safety audit.

## Git safety

- `.env`, `.venv`, `data/`, `logs/`, and `infra/milvus/volumes/` are ignored.
- The test PDF and Milvus data are not tracked.
- No core DeepSearcher Agent, Loader, Vector DB, offline-loading, or query implementation has been modified.
- No API key has been written to a tracked file.

