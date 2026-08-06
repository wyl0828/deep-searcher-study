from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset violates the public schema."""


@dataclass(frozen=True)
class EvidenceTarget:
    document: str
    page: int


@dataclass(frozen=True)
class EvalSample:
    id: str
    question: str
    answerable: bool
    reference_answer: str
    evidence: tuple[EvidenceTarget, ...]
    criteria: tuple[tuple[str, ...], ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class EvalDataset:
    schema_version: int
    dataset_id: str
    version: str
    description: str
    source: dict[str, Any]
    samples: tuple[EvalSample, ...]
    sha256: str
    path: Path


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{field} must be a non-empty string")
    return value.strip()


def load_dataset(path: str | Path) -> EvalDataset:
    dataset_path = Path(path).resolve()
    raw_bytes = dataset_path.read_bytes()
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DatasetValidationError("dataset root must be an object")
    if raw.get("schema_version") != 1:
        raise DatasetValidationError("schema_version must be 1")

    source = raw.get("source")
    if not isinstance(source, dict):
        raise DatasetValidationError("source must be an object")
    source_sha = _require_text(source.get("sha256"), "source.sha256")
    if re.fullmatch(r"[0-9a-f]{64}", source_sha) is None:
        raise DatasetValidationError("source.sha256 must be a lowercase SHA-256")

    raw_samples = raw.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise DatasetValidationError("samples must be a non-empty array")

    samples: list[EvalSample] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_samples):
        prefix = f"samples[{index}]"
        if not isinstance(item, dict):
            raise DatasetValidationError(f"{prefix} must be an object")
        sample_id = _require_text(item.get("id"), f"{prefix}.id")
        if sample_id in seen_ids:
            raise DatasetValidationError(f"duplicate sample id: {sample_id}")
        seen_ids.add(sample_id)
        answerable = item.get("answerable")
        if not isinstance(answerable, bool):
            raise DatasetValidationError(f"{prefix}.answerable must be boolean")

        evidence: list[EvidenceTarget] = []
        for evidence_index, target in enumerate(item.get("evidence", [])):
            if not isinstance(target, dict):
                raise DatasetValidationError(
                    f"{prefix}.evidence[{evidence_index}] must be an object"
                )
            page = target.get("page")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise DatasetValidationError(
                    f"{prefix}.evidence[{evidence_index}].page must be positive"
                )
            evidence.append(
                EvidenceTarget(
                    document=_require_text(
                        target.get("document"),
                        f"{prefix}.evidence[{evidence_index}].document",
                    ),
                    page=page,
                )
            )

        criteria: list[tuple[str, ...]] = []
        raw_criteria = item.get("criteria", [])
        if not isinstance(raw_criteria, list):
            raise DatasetValidationError(f"{prefix}.criteria must be an array")
        for criterion_index, alternatives in enumerate(raw_criteria):
            if not isinstance(alternatives, list) or not alternatives:
                raise DatasetValidationError(
                    f"{prefix}.criteria[{criterion_index}] must be a non-empty array"
                )
            criteria.append(
                tuple(
                    _require_text(
                        alternative,
                        f"{prefix}.criteria[{criterion_index}] alternative",
                    )
                    for alternative in alternatives
                )
            )

        if answerable and (not evidence or not criteria):
            raise DatasetValidationError(
                f"{prefix} answerable samples require evidence and criteria"
            )
        if not answerable and (evidence or criteria):
            raise DatasetValidationError(
                f"{prefix} unanswerable samples cannot declare evidence or criteria"
            )
        tags = item.get("tags", [])
        if not isinstance(tags, list):
            raise DatasetValidationError(f"{prefix}.tags must be an array")
        samples.append(
            EvalSample(
                id=sample_id,
                question=_require_text(item.get("question"), f"{prefix}.question"),
                answerable=answerable,
                reference_answer=_require_text(
                    item.get("reference_answer"), f"{prefix}.reference_answer"
                ),
                evidence=tuple(evidence),
                criteria=tuple(criteria),
                tags=tuple(_require_text(tag, f"{prefix}.tags item") for tag in tags),
            )
        )

    return EvalDataset(
        schema_version=1,
        dataset_id=_require_text(raw.get("dataset_id"), "dataset_id"),
        version=_require_text(raw.get("version"), "version"),
        description=_require_text(raw.get("description"), "description"),
        source=source,
        samples=tuple(samples),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        path=dataset_path,
    )
