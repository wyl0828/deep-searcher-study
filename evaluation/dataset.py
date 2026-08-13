from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class DatasetValidationError(ValueError):
    """Raised when an evaluation dataset violates the public schema."""


@dataclass(frozen=True)
class EvalSource:
    document: str
    path: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "document": self.document,
            "path": self.path,
            "sha256": self.sha256,
        }


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
    difficulty: str = "unspecified"
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context_dependent: bool = False
    standalone_question: str | None = None


@dataclass(frozen=True)
class EvalDataset:
    schema_version: int
    dataset_id: str
    version: str
    description: str
    source: dict[str, Any]
    sources: tuple[EvalSource, ...]
    samples: tuple[EvalSample, ...]
    sha256: str
    path: Path


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _load_source(value: Any, field: str) -> EvalSource:
    if not isinstance(value, dict):
        raise DatasetValidationError(f"{field} must be an object")
    sha256 = _require_text(value.get("sha256"), f"{field}.sha256")
    if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise DatasetValidationError(f"{field}.sha256 must be a lowercase SHA-256")
    return EvalSource(
        document=_require_text(value.get("document"), f"{field}.document"),
        path=_require_text(value.get("path"), f"{field}.path"),
        sha256=sha256,
    )


def load_dataset(path: str | Path) -> EvalDataset:
    dataset_path = Path(path).resolve()
    raw_bytes = dataset_path.read_bytes()
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise DatasetValidationError("dataset root must be an object")
    schema_version = raw.get("schema_version")
    if schema_version not in {1, 2}:
        raise DatasetValidationError("schema_version must be 1 or 2")

    if schema_version == 1:
        sources = (_load_source(raw.get("source"), "source"),)
    else:
        raw_sources = raw.get("sources")
        if not isinstance(raw_sources, list) or len(raw_sources) < 2:
            raise DatasetValidationError("sources must contain at least two source objects")
        sources = tuple(
            _load_source(item, f"sources[{index}]") for index, item in enumerate(raw_sources)
        )
    source_documents = [source.document for source in sources]
    if len(set(source_documents)) != len(source_documents):
        raise DatasetValidationError("source documents must be unique")
    source_paths = [source.path for source in sources]
    if len(set(source_paths)) != len(source_paths):
        raise DatasetValidationError("source paths must be unique")

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
        if schema_version == 2:
            unknown_documents = sorted(
                {target.document for target in evidence} - set(source_documents)
            )
            if unknown_documents:
                raise DatasetValidationError(
                    f"{prefix}.evidence references unknown documents: {unknown_documents}"
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
        difficulty = str(item.get("difficulty") or "unspecified").strip().lower()
        if difficulty not in {"easy", "medium", "hard", "unspecified"}:
            raise DatasetValidationError(
                f"{prefix}.difficulty must be easy, medium, hard or unspecified"
            )
        history: list[tuple[str, str]] = []
        raw_history = item.get("history", [])
        if not isinstance(raw_history, list):
            raise DatasetValidationError(f"{prefix}.history must be an array")
        for turn_index, turn in enumerate(raw_history):
            if not isinstance(turn, dict) or turn.get("role") not in {"user", "assistant"}:
                raise DatasetValidationError(
                    f"{prefix}.history[{turn_index}] must have user or assistant role"
                )
            history.append(
                (
                    str(turn["role"]),
                    _require_text(turn.get("content"), f"{prefix}.history[{turn_index}].content"),
                )
            )
        context_dependent = item.get("context_dependent", False)
        if not isinstance(context_dependent, bool):
            raise DatasetValidationError(f"{prefix}.context_dependent must be boolean")
        standalone_question = item.get("standalone_question")
        if history:
            if standalone_question is None:
                raise DatasetValidationError(
                    f"{prefix}.standalone_question is required when history is present"
                )
            standalone_question = _require_text(
                standalone_question,
                f"{prefix}.standalone_question",
            )
        elif context_dependent or standalone_question is not None:
            raise DatasetValidationError(
                f"{prefix} contextualization labels require conversation history"
            )
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
                difficulty=difficulty,
                history=tuple(history),
                context_dependent=context_dependent,
                standalone_question=standalone_question,
            )
        )

    return EvalDataset(
        schema_version=schema_version,
        dataset_id=_require_text(raw.get("dataset_id"), "dataset_id"),
        version=_require_text(raw.get("version"), "version"),
        description=_require_text(raw.get("description"), "description"),
        source=sources[0].as_dict(),
        sources=sources,
        samples=tuple(samples),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        path=dataset_path,
    )
