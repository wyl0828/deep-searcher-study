FROM node:22-bookworm-slim AS frontend-build

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim

WORKDIR /app

RUN mkdir -p /tmp/uv-cache /app/data /app/logs

COPY pyproject.toml uv.lock LICENSE README.md ./
LABEL org.opencontainers.image.source="https://github.com/wyl0828/deep-searcher-study"

COPY deepsearcher/ ./deepsearcher/

RUN uv sync --frozen --no-dev

COPY . .
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from urllib.request import urlopen; assert urlopen('http://localhost:8000/health/live', timeout=5).status == 200" || exit 1

CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
