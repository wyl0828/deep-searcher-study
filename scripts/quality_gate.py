"""Run the repository quality gate in fast or live mode."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "tmp" / "quality-gate" / "latest"
MANIFEST_PATH = ROOT / "evaluation" / "quality_gate.json"
DATASET_PATH = ROOT / "evaluation" / "datasets" / "workspace_v2.json"


def _executable(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        raise RuntimeError(f"required executable is missing: {name}")
    return value


class QualityGateRunner:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict] = []

    def run(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path = ROOT,
        environment: dict[str, str] | None = None,
        echo_output: bool = True,
        allow_failure: bool = False,
    ) -> None:
        log_path = self.output_dir / f"{len(self.steps) + 1:02d}-{name}.log"
        started = perf_counter()
        rendered_command = [str(part) for part in command]
        print(f"\n[quality-gate] {name}: {' '.join(rendered_command)}", flush=True)
        merged_environment = os.environ.copy()
        if environment:
            merged_environment.update(environment)
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                rendered_command,
                cwd=cwd,
                env=merged_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                if echo_output:
                    print(line, end="", flush=True)
                log_handle.write(line)
            return_code = process.wait()
        self.steps.append(
            {
                "name": name,
                "command": rendered_command,
                "cwd": str(cwd),
                "return_code": return_code,
                "blocking": not allow_failure,
                "duration_seconds": round(perf_counter() - started, 3),
                "log": str(log_path),
            }
        )
        if return_code and not allow_failure:
            raise RuntimeError(f"quality gate step failed: {name} (exit {return_code})")

    def write_summary(self, *, mode: str, error: str | None = None) -> Path:
        summary_path = self.output_dir / "summary.json"
        summary = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "passed": error is None,
            "error": error,
            "steps": self.steps,
        }
        temporary = summary_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(summary_path)
        return summary_path


def _uv_command(*arguments: str) -> list[str]:
    return [_executable("uv"), "run", "--frozen", *arguments]


def _context_sample_ids() -> str:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return ",".join(
        str(sample["id"])
        for sample in dataset["samples"]
        if isinstance(sample, dict) and sample.get("history")
    )


def _run_fast_gate(
    runner: QualityGateRunner,
    *,
    install_dependencies: bool,
    skip_docs: bool,
) -> None:
    if install_dependencies:
        runner.run("uv-sync", [_executable("uv"), "sync", "--frozen", "--dev"])
        runner.run(
            "npm-ci",
            [
                _executable("npm"),
                "ci",
                "--cache",
                ".npm-cache",
                "--prefer-offline",
            ],
            cwd=ROOT / "frontend",
        )
        runner.run(
            "playwright-install",
            [_executable("npx"), "playwright", "install", "--with-deps", "chromium"],
            cwd=ROOT / "frontend",
        )
    runner.run(
        "ruff-format-check",
        _uv_command("ruff", "format", "--check", "."),
    )
    runner.run("ruff-check", _uv_command("ruff", "check", "."))
    runner.run("pytest", _uv_command("pytest", "-q"))
    runner.run("frontend-test", [_executable("npm"), "test"], cwd=ROOT / "frontend")
    runner.run(
        "frontend-typecheck",
        [_executable("npm"), "run", "typecheck"],
        cwd=ROOT / "frontend",
    )
    runner.run(
        "frontend-build",
        [_executable("npm"), "run", "build"],
        cwd=ROOT / "frontend",
        echo_output=False,
    )
    runner.run(
        "frontend-bundle-check",
        [_executable("npm"), "run", "check:bundle"],
        cwd=ROOT / "frontend",
    )
    runner.run(
        "frontend-e2e",
        [_executable("npm"), "run", "test:e2e"],
        cwd=ROOT / "frontend",
    )

    migration_db = runner.output_dir / "migration.db"
    if migration_db.exists():
        migration_db.unlink()
    migration_environment = {
        "DEEPSEARCHER_DATABASE_URL": f"sqlite:///{migration_db.as_posix()}",
        "DEEPSEARCHER_DATA_DIR": str(runner.output_dir / "product-data"),
    }
    runner.run(
        "alembic-upgrade",
        _uv_command("alembic", "upgrade", "head"),
        environment=migration_environment,
    )
    runner.run(
        "trust-consistency-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.trust_consistency",
            "--output",
            str(runner.output_dir / "trust-consistency.json"),
        ),
    )
    runner.run(
        "citation-span-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.citation_span",
            "--output",
            str(runner.output_dir / "citation-span.json"),
        ),
    )
    runner.run(
        "entailment-dataset-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.entailment",
            "--mode",
            "validate",
            "--output",
            str(runner.output_dir / "entailment-dataset.json"),
        ),
    )
    runner.run(
        "entailment-live-report-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.entailment",
            "--mode",
            "report",
            "--report",
            str(
                ROOT
                / "evaluation"
                / "results"
                / "v0.3-entailment-deepseek-20260812"
                / "report.json"
            ),
            "--output",
            str(runner.output_dir / "entailment-live-report.json"),
        ),
    )
    runner.run(
        "risk-profile-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.risk_profile",
            "--output",
            str(runner.output_dir / "risk-profile.json"),
        ),
    )
    runner.run(
        "trust-provenance-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.provenance",
            "--output",
            str(runner.output_dir / "trust-provenance.json"),
        ),
    )
    runner.run(
        "evaluation-report-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.quality_gate",
            "--manifest",
            str(MANIFEST_PATH),
            "--output",
            str(runner.output_dir / "report-validation.json"),
        ),
        echo_output=False,
    )
    if not skip_docs:
        runner.run(
            "mkdocs-build",
            _uv_command("mkdocs", "build", "--clean"),
            echo_output=False,
        )
    runner.run("git-diff-check", [_executable("git"), "diff", "--check"])


def _run_live_gate(runner: QualityGateRunner) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    live_config = manifest["full"]
    collection = str(live_config["collection"])
    answer_sample_ids = ",".join(str(item) for item in live_config["answer_sample_ids"])
    answer_sample_profile = str(live_config["answer_sample_profile"])
    live_root = runner.output_dir / "live"
    retrieval_dir = live_root / "retrieval"
    context_dir = live_root / "context"
    answer_dir = live_root / "answer"
    entailment_path = live_root / "entailment-calibration.json"

    runner.run(
        "live-entailment-calibration",
        _uv_command(
            "python",
            "-m",
            "evaluation.entailment",
            "--mode",
            "live",
            "--repetitions",
            "3",
            "--batch-size",
            "8",
            "--output",
            str(entailment_path),
        ),
    )

    runner.run(
        "live-retrieval",
        _uv_command(
            "python",
            "-m",
            "evaluation.retrieval_compare",
            "--dataset",
            str(DATASET_PATH),
            "--prepare",
            "--collection",
            collection,
            "--top-k",
            "8",
            "--repetitions",
            "3",
            "--output",
            str(retrieval_dir),
        ),
    )
    runner.run(
        "live-context",
        _uv_command(
            "python",
            "-m",
            "evaluation.retrieval_compare",
            "--dataset",
            str(DATASET_PATH),
            "--collection",
            collection,
            "--top-k",
            "8",
            "--repetitions",
            "3",
            "--sample-ids",
            _context_sample_ids(),
            "--output",
            str(context_dir),
        ),
    )
    runner.run(
        "live-answer",
        _uv_command(
            "python",
            "-m",
            "evaluation.benchmark",
            "--dataset",
            str(DATASET_PATH),
            "--collection",
            collection,
            "--agents",
            "naive,deep_search,chain_of_rag",
            "--mode",
            "answer",
            "--top-k",
            "8",
            "--max-iter",
            "2",
            "--llm-timeout-seconds",
            "120",
            "--external-call-timeout-seconds",
            "180",
            "--request-timeout-seconds",
            "600",
            "--sample-ids",
            answer_sample_ids,
            "--sample-profile",
            answer_sample_profile,
            "--output",
            str(answer_dir),
        ),
    )
    runner.run(
        "live-report-gate",
        _uv_command(
            "python",
            "-m",
            "evaluation.quality_gate",
            "--manifest",
            str(MANIFEST_PATH),
            "--output",
            str(live_root / "report-validation.json"),
            "--report-override",
            f"retrieval={retrieval_dir / 'report.json'}",
            "--report-override",
            f"context={context_dir / 'report.json'}",
            "--report-override",
            f"answer={answer_dir / 'report.json'}",
        ),
        echo_output=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--install-dependencies", action="store_true")
    parser.add_argument("--skip-docs", action="store_true")
    args = parser.parse_args(argv)

    runner = QualityGateRunner(args.output_dir)
    error: str | None = None
    try:
        _run_fast_gate(
            runner,
            install_dependencies=args.install_dependencies,
            skip_docs=args.skip_docs,
        )
        if args.mode == "full":
            _run_live_gate(runner)
    except Exception as exc:
        error = str(exc)
        print(f"\n[quality-gate] FAILED: {error}", file=sys.stderr)
    summary_path = runner.write_summary(mode=args.mode, error=error)
    print(f"\n[quality-gate] summary: {summary_path}")
    return 0 if error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
