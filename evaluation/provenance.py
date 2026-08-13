"""Model-free gate for Trust Trace Provenance invariants."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.provenance import (
    TrustProvenanceSession,
    build_trust_provenance,
    sanitize_trust_provenance,
)
from deepsearcher.trust import build_temporal_context
from deepsearcher.vector_db.base import RetrievalResult

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "trust_provenance_v1.json"
SUPPORTED_KINDS = {
    "stable_explicit_order",
    "secret_rotation_invariant",
    "model_change_detected",
    "consistency_checker_version_bound",
    "freshness_classifier_version_bound",
    "dynamic_scope_disclosed",
    "dynamic_scope_resolved",
    "dynamic_multi_route_merged",
    "evidence_snapshot_bound",
    "web_evidence_redacted",
    "evidence_content_change_detected",
    "evidence_order_change_detected",
    "evidence_tamper_rejected",
    "raw_identity_not_exposed",
    "nested_injection_removed",
    "meaningful_tamper_rejected",
    "temporal_reference_bound",
    "temporal_reference_change_detected",
    "temporal_tamper_rejected",
    "evidence_temporal_identity_bound",
    "evidence_temporal_change_detected",
    "evidence_version_family_identity_bound",
    "evidence_version_family_change_detected",
}


class _Embedding:
    dimension = 8


class _VectorDB:
    def __init__(self, manifests: dict[str, CollectionManifest]):
        self.manifests = manifests

    def get_collection_manifest(self, collection: str) -> CollectionManifest | None:
        return self.manifests.get(collection)


def _manifest(name: str) -> CollectionManifest:
    return CollectionManifest.create(
        logical_collection=name,
        embedding=EmbeddingProfile(
            provider="EvalEmbedding",
            model="eval-embedding",
            version="eval-embedding-v1",
            dimension=8,
            normalization="none",
        ),
        metric_type="L2",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=[],
    )


def _runtime(*, model: str = "eval-model", secret: str = "secret-a") -> Any:
    embedding = _Embedding()
    embedding._deepsearcher_embedding_provider = "EvalEmbedding"
    embedding._deepsearcher_embedding_model = "eval-embedding"
    embedding._deepsearcher_embedding_version = "eval-embedding-v1"
    embedding._deepsearcher_embedding_normalization = "none"
    return SimpleNamespace(
        config=SimpleNamespace(
            provide_settings={
                "llm": {
                    "provider": "EvalLLM",
                    "config": {"model": model, "api_key": secret},
                },
                "embedding": {
                    "provider": "EvalEmbedding",
                    "config": {"model": "eval-embedding"},
                },
            },
            query_settings={},
            load_settings={},
        ),
        llm=SimpleNamespace(model=model),
        embedding_model=embedding,
        vector_db=_VectorDB({"kb_a": _manifest("kb_a"), "kb_b": _manifest("kb_b")}),
        entailment_checker=None,
    )


def _context() -> Any:
    return SimpleNamespace(
        tenant_id="sensitive-tenant-name",
        runtime_version=4,
        binding_revision=2,
        model_policy="EvalLLM:eval-model",
    )


def _mixed_evidence() -> list[tuple[RetrievalResult, str]]:
    return [
        (
            RetrievalResult(
                [],
                "private fact",
                "private.pdf",
                {
                    "document_id": "doc-private",
                    "page_number": 3,
                    "published_at": "2026-08-11",
                    "effective_at": "2026-08-12",
                    "temporal_metadata_source": "user_declared",
                    "version_family": "private-policy",
                    "version_family_source": "admin_verified",
                },
            ),
            "private fact 42",
        ),
        (
            RetrievalResult(
                [],
                "web fact",
                "https://docs.example.com/fact?token=secret",
                {
                    "source_type": "web",
                    "source_url": "https://docs.example.com/fact?token=secret",
                    "web_search_provider": "tavily",
                    "trusted": True,
                },
            ),
            "web fact 2026",
        ),
    ]


def _evaluate(kind: str) -> bool:
    runtime = _runtime()
    context = _context()
    if kind == "stable_explicit_order":
        left = build_trust_provenance(runtime, context=context, collection_names=["kb_a", "kb_b"])
        right = build_trust_provenance(runtime, context=context, collection_names=["kb_b", "kb_a"])
        return left["digest"] == right["digest"]
    if kind == "secret_rotation_invariant":
        left = build_trust_provenance(_runtime(secret="secret-a"))
        right = build_trust_provenance(_runtime(secret="secret-b"))
        return left["digest"] == right["digest"]
    if kind == "model_change_detected":
        left = build_trust_provenance(_runtime(model="eval-model"))
        right = build_trust_provenance(_runtime(model="eval-model-v2"))
        return left["digest"] != right["digest"]
    if kind == "consistency_checker_version_bound":
        provenance = build_trust_provenance(runtime)
        return provenance["checkers"]["consistency_checker"]["version"] == "1.6.0"
    if kind == "freshness_classifier_version_bound":
        provenance = build_trust_provenance(runtime)
        return provenance["checkers"]["freshness_classifier"] == {
            "contract_version": 1,
            "version": "1.1.0",
        }
    if kind in {
        "temporal_reference_bound",
        "temporal_reference_change_detected",
        "temporal_tamper_rejected",
    }:
        first_context = build_temporal_context(
            reference_time="2026-08-11T03:00:00+00:00",
            timezone_name="Asia/Shanghai",
        )
        first = build_trust_provenance(runtime, temporal_context=first_context)
        if kind == "temporal_reference_bound":
            serialized = json.dumps(first["temporal"], ensure_ascii=False)
            return (
                first["temporal"]["reference_date"] == "2026-08-11"
                and first["temporal"]["timezone"] == "Asia/Shanghai"
                and "reference_time" not in serialized
            )
        if kind == "temporal_reference_change_detected":
            second_context = build_temporal_context(
                reference_time="2026-08-12T03:00:00+00:00",
                timezone_name="Asia/Shanghai",
            )
            second = build_trust_provenance(runtime, temporal_context=second_context)
            return first["digest"] != second["digest"]
        tampered = deepcopy(first)
        tampered["temporal"]["reference_date"] = "2026-08-12"
        return sanitize_trust_provenance(tampered) is None
    provenance = build_trust_provenance(runtime, context=context)
    if kind == "dynamic_scope_disclosed":
        return provenance["index"]["snapshot_status"] == "dynamic_unbound"
    if kind in {"dynamic_scope_resolved", "dynamic_multi_route_merged"}:
        session = TrustProvenanceSession(runtime, context=context, collection_names=None)
        first = session.bind_collections(["kb_a"])
        if kind == "dynamic_scope_resolved":
            return (
                first["index"]["selection_mode"] == "dynamic"
                and first["index"]["snapshot_status"] == "complete"
                and first["index"]["collection_count"] == 1
                and "kb_a" not in json.dumps(first)
            )
        second = session.bind_collections(["kb_b", "kb_a"])
        return (
            second["index"]["selection_mode"] == "dynamic"
            and second["index"]["snapshot_status"] == "complete"
            and second["index"]["collection_count"] == 2
            and second["digest"] != first["digest"]
        )
    if kind in {
        "evidence_snapshot_bound",
        "web_evidence_redacted",
        "evidence_content_change_detected",
        "evidence_order_change_detected",
        "evidence_tamper_rejected",
        "evidence_temporal_identity_bound",
        "evidence_temporal_change_detected",
        "evidence_version_family_identity_bound",
        "evidence_version_family_change_detected",
    }:
        evidence = _mixed_evidence()
        bound = build_trust_provenance(runtime, evidence_snapshot=evidence)
        if kind == "evidence_snapshot_bound":
            return (
                bound["version"] == 2
                and bound["evidence"]["snapshot_status"] == "complete"
                and bound["evidence"]["evidence_count"] == 2
                and bound["evidence"]["knowledge_base_count"] == 1
                and bound["evidence"]["web_count"] == 1
            )
        if kind == "web_evidence_redacted":
            serialized = json.dumps(bound, ensure_ascii=False)
            return all(
                value not in serialized
                for value in (
                    "private fact 42",
                    "web fact 2026",
                    "doc-private",
                    "docs.example.com",
                    "token=secret",
                )
            )
        if kind == "evidence_content_change_detected":
            changed = list(evidence)
            changed[1] = (changed[1][0], "web fact changed")
            other = build_trust_provenance(runtime, evidence_snapshot=changed)
            return (
                bound["evidence"]["snapshot_fingerprint"]
                != other["evidence"]["snapshot_fingerprint"]
            )
        if kind == "evidence_order_change_detected":
            other = build_trust_provenance(runtime, evidence_snapshot=list(reversed(evidence)))
            return (
                bound["evidence"]["snapshot_fingerprint"]
                != other["evidence"]["snapshot_fingerprint"]
            )
        if kind == "evidence_temporal_identity_bound":
            serialized = json.dumps(bound, ensure_ascii=False)
            return (
                bound["evidence"]["items"][0]["publication_anchor_bound"] is True
                and bound["evidence"]["items"][0]["temporal_fingerprint"].startswith("sha256:")
                and "2026-08-11" not in serialized
                and "2026-08-12" not in serialized
            )
        if kind == "evidence_temporal_change_detected":
            changed = deepcopy(evidence)
            changed[0][0].metadata["published_at"] = "2026-08-10"
            other = build_trust_provenance(runtime, evidence_snapshot=changed)
            return (
                bound["evidence"]["snapshot_fingerprint"]
                != other["evidence"]["snapshot_fingerprint"]
            )
        if kind == "evidence_version_family_identity_bound":
            serialized = json.dumps(bound, ensure_ascii=False)
            return (
                bound["evidence"]["items"][0]["version_family_bound"] is True
                and bound["evidence"]["items"][0]["version_family_fingerprint"].startswith(
                    "sha256:"
                )
                and "private-policy" not in serialized
            )
        if kind == "evidence_version_family_change_detected":
            changed = deepcopy(evidence)
            changed[0][0].metadata["version_family"] = "another-policy"
            other = build_trust_provenance(runtime, evidence_snapshot=changed)
            return (
                bound["evidence"]["snapshot_fingerprint"]
                != other["evidence"]["snapshot_fingerprint"]
            )
        tampered = deepcopy(bound)
        tampered["evidence"]["items"][1]["trusted"] = False
        return sanitize_trust_provenance(tampered) is None
    if kind == "raw_identity_not_exposed":
        serialized = json.dumps(provenance, ensure_ascii=False)
        return all(
            value not in serialized
            for value in ("secret-a", "sensitive-tenant-name", "kb_a", "kb_b")
        )
    if kind == "nested_injection_removed":
        injected = deepcopy(provenance)
        injected["runtime"]["raw_prompt"] = "do not persist"
        return sanitize_trust_provenance(injected) == provenance
    if kind == "meaningful_tamper_rejected":
        tampered = deepcopy(provenance)
        tampered["generation_model"]["model"] = "tampered-model"
        return sanitize_trust_provenance(tampered) is None
    raise ValueError(f"unsupported provenance case: {kind}")


def evaluate_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("invalid provenance dataset schema")
    seen: set[str] = set()
    details = []
    for item in payload["cases"]:
        if not isinstance(item, dict):
            raise ValueError("invalid provenance case")
        case_id = str(item.get("id") or "")
        kind = str(item.get("kind") or "")
        if not case_id or case_id in seen or kind not in SUPPORTED_KINDS:
            raise ValueError("invalid or duplicate provenance case")
        seen.add(case_id)
        actual = _evaluate(kind)
        expected = item.get("expected") is True
        details.append(
            {
                "id": case_id,
                "kind": kind,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    passed_count = sum(item["passed"] for item in details)
    return {
        "schema_version": 1,
        "dataset_id": payload.get("dataset_id"),
        "case_count": len(details),
        "passed_count": passed_count,
        "passed": passed_count == len(details),
        "details": details,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trust Provenance 契约门禁")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_dataset(args.dataset)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.output)
    print(serialized)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
