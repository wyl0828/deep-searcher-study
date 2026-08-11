lint:
	uv run --frozen ruff format --diff .
	uv run --frozen ruff check .

format:
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

test:
	uv run --frozen pytest -q

quality:
	uv run --frozen python scripts/quality_gate.py --mode fast
