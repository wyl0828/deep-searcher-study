"""Secret-free provenance for one Trust Layer decision.

The contract intentionally records stable identities and fingerprints instead of
raw configuration, prompts, questions, credentials, or collection names.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from copy import deepcopy
from datetime import date
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from deepsearcher.collection_manifest import EmbeddingProfile
from deepsearcher.entailment import ENTAILMENT_CONTRACT_VERSION
from deepsearcher.freshness import FRESHNESS_CLASSIFIER_VERSION, FRESHNESS_CONTRACT_VERSION
from deepsearcher.grounding import GROUNDING_PROMPT
from deepsearcher.risk import RISK_CLASSIFIER_VERSION, RISK_CONTRACT_VERSION
from deepsearcher.runtime_registry import configuration_digest
from deepsearcher.temporal import extract_document_temporal_metadata
from deepsearcher.trust import (
    ANSWER_POLICY_VERSION,
    CONSISTENCY_CHECKER_VERSION,
    TRUST_CONTRACT_VERSION,
)
from deepsearcher.versioning import extract_document_version_metadata

PROVENANCE_CONTRACT_VERSION = 2
SUPPORTED_PROVENANCE_CONTRACT_VERSIONS = frozenset({1, 2})
PROVENANCE_DIGEST_ALGORITHM = "sha256"
TRUST_PROVENANCE_BUILDER_VERSION = "2.3.0"
CLAIM_EXTRACTOR_VERSION = "1.0.0"
CITATION_VALIDATOR_VERSION = "1.1.0"
CITATION_SPAN_LOCATOR_VERSION = "1.0.0"
GROUNDING_PROMPT_VERSION = "1.2.0"
ENTAILMENT_PROMPT_VERSION = "1.0.0"

_SAFE_IDENTITY = re.compile(r"[^\x00-\x1f\x7f]{1,256}")
_SAFE_EXECUTION_SCOPES = {"online", "stream", "library", "evaluation"}
_SAFE_INDEX_STATUSES = {"complete", "partial", "dynamic_unbound", "unavailable"}
_SAFE_MANIFEST_STATUSES = {"verified", "legacy_unverified", "unavailable"}
_SAFE_EVIDENCE_STATUSES = {"unbound", "complete", "partial"}
_SAFE_SOURCE_TYPES = {"knowledge_base", "web"}
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_SENSITIVE_IDENTITY = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,})"
)
_TIMEZONE_PATTERN = re.compile(r"[A-Za-z0-9._+/-]{1,64}")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Mapping[str, Any] | str) -> str:
    serialized = value if isinstance(value, str) else _canonical_json(value)
    return (
        f"{PROVENANCE_DIGEST_ALGORITHM}:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    )


def _safe_identity(value: Any, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip()[:256]
    return (
        normalized
        if _SAFE_IDENTITY.fullmatch(normalized) and _SENSITIVE_IDENTITY.search(normalized) is None
        else fallback
    )


def _optional_identity(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()[:256]
    return (
        normalized
        if _SAFE_IDENTITY.fullmatch(normalized) and _SENSITIVE_IDENTITY.search(normalized) is None
        else None
    )


def _safe_digest(value: Any, *, unavailable: bool = False) -> str | None:
    if unavailable and value == "unavailable":
        return "unavailable"
    return str(value) if _DIGEST_PATTERN.fullmatch(str(value or "")) else None


def _safe_int(value: Any, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def _safe_float(value: Any, *, minimum: float, maximum: float) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 4) if minimum <= parsed <= maximum else None


def _temporal_identity(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(context, Mapping) or context.get("version") != 1:
        return None
    source = str(context.get("source") or "")
    timezone_name = str(context.get("timezone") or "")
    reference_date = str(context.get("reference_date") or "")
    try:
        date.fromisoformat(reference_date)
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if source != "request_clock" or _TIMEZONE_PATTERN.fullmatch(timezone_name) is None:
        return None
    identity = {
        "version": 1,
        "source": source,
        "reference_date": reference_date,
        "timezone": timezone_name,
    }
    return {**identity, "fingerprint": _digest(identity)}


def _object_identity(component: Any, *attributes: str) -> str:
    for attribute in attributes:
        candidate = getattr(component, attribute, None)
        if isinstance(candidate, str) and candidate.strip():
            return _safe_identity(candidate)
    if component is None:
        return "unavailable"
    return _safe_identity(f"{component.__class__.__module__}.{component.__class__.__name__}")


def _setting(config: Any, feature: str) -> dict[str, Any]:
    providers = getattr(config, "provide_settings", {})
    value = providers.get(feature, {}) if isinstance(providers, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _llm_identity(runtime: Any) -> dict[str, Any]:
    llm = getattr(runtime, "llm", None)
    setting = _setting(getattr(runtime, "config", None), "llm")
    provider_config = setting.get("config")
    provider_config = provider_config if isinstance(provider_config, Mapping) else {}
    provider = _safe_identity(setting.get("provider"), llm.__class__.__name__ if llm else "unknown")
    model = _safe_identity(
        provider_config.get("model")
        or provider_config.get("model_name")
        or provider_config.get("deployment")
        or _object_identity(llm, "model", "model_name", "deployment")
    )
    version = _safe_identity(
        provider_config.get("model_revision")
        or provider_config.get("revision")
        or provider_config.get("version")
        or model
    )
    identity = {"provider": provider, "model": model, "version": version}
    return {**identity, "fingerprint": _digest(identity)}


def _embedding_identity(runtime: Any) -> dict[str, Any]:
    embedding = getattr(runtime, "embedding_model", None)
    try:
        profile = EmbeddingProfile.from_embedding(embedding)
    except (AttributeError, TypeError, ValueError):
        setting = _setting(getattr(runtime, "config", None), "embedding")
        provider_config = setting.get("config")
        provider_config = provider_config if isinstance(provider_config, Mapping) else {}
        identity = {
            "provider": _safe_identity(setting.get("provider"), "unknown"),
            "model": _safe_identity(
                provider_config.get("model")
                or provider_config.get("model_name")
                or _object_identity(embedding, "model", "model_name")
            ),
            "version": _safe_identity(
                provider_config.get("model_revision")
                or provider_config.get("revision")
                or provider_config.get("version")
                or "unknown"
            ),
            "dimension": None,
            "normalization": "unknown",
        }
        return {**identity, "fingerprint": _digest(identity)}
    return {
        "provider": profile.provider,
        "model": profile.model,
        "version": profile.version,
        "dimension": profile.dimension,
        "normalization": profile.normalization,
        "fingerprint": f"sha256:{profile.fingerprint}",
    }


def _manifest_identity(vector_db: Any, collection: str) -> dict[str, Any]:
    try:
        manifest = vector_db.get_collection_manifest(collection)
    except Exception:  # provider errors must not escape through Trust Trace
        return {"status": "unavailable"}
    if manifest is None:
        return {"status": "legacy_unverified"}
    return {
        "status": "verified",
        "schema_version": int(manifest.schema_version),
        "manifest_fingerprint": f"sha256:{manifest.fingerprint}",
        "data_version": _safe_identity(manifest.data_version),
        "document_version": _safe_identity(manifest.document_version),
        "embedding_fingerprint": f"sha256:{manifest.embedding.fingerprint}",
        "chunk_config_version": _safe_identity(manifest.chunk_config_version),
    }


def _index_identity(
    runtime: Any,
    collection_names: Sequence[str] | None,
    *,
    selection_mode: str | None = None,
) -> dict[str, Any]:
    if collection_names is None:
        return {
            "selection_mode": "dynamic",
            "snapshot_status": "dynamic_unbound",
            "collection_count": 0,
            "manifests": [],
        }
    all_names = list(dict.fromkeys(str(item) for item in collection_names))
    names = all_names[:32]
    vector_db = getattr(runtime, "vector_db", None)
    if vector_db is None:
        manifests = [{"status": "unavailable"} for _ in names]
    else:
        manifests = [_manifest_identity(vector_db, collection) for collection in names]
    statuses = {item["status"] for item in manifests}
    snapshot_status = (
        "complete" if statuses <= {"verified"} and len(all_names) <= len(names) else "partial"
    )
    # Sorting by fingerprint/status makes the identity stable without persisting names.
    manifests.sort(key=_canonical_json)
    return {
        "selection_mode": selection_mode
        if selection_mode in {"explicit", "dynamic"}
        else "explicit",
        "snapshot_status": snapshot_status,
        "collection_count": len(all_names),
        "manifests": manifests,
    }


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


def _safe_locator(metadata: Mapping[str, Any]) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    for key in ("page_number", "chunk_index", "char_start", "char_end"):
        value = _safe_int(metadata.get(key), minimum=0)
        if value is not None:
            locator[key] = value
    for key in ("location_id", "source_locator"):
        value = _optional_identity(metadata.get(key))
        if value is not None:
            locator[key] = value
    return locator


def _evidence_identity(
    evidence_snapshot: Sequence[tuple[Any, str]] | None,
) -> dict[str, Any]:
    if evidence_snapshot is None:
        return {
            "snapshot_status": "unbound",
            "evidence_count": 0,
            "knowledge_base_count": 0,
            "web_count": 0,
            "snapshot_fingerprint": None,
            "items": [],
        }
    all_items = list(evidence_snapshot)
    bounded_items = all_items[:20]
    items: list[dict[str, Any]] = []
    unresolved_source = False
    for position, (result, evidence_text) in enumerate(bounded_items, start=1):
        metadata = getattr(result, "metadata", None)
        metadata = metadata if isinstance(metadata, Mapping) else {}
        source_type = "web" if metadata.get("source_type") == "web" else "knowledge_base"
        source_value: str | None = None
        provider = None
        if source_type == "web":
            from deepsearcher.web_search.tavily import canonical_public_url

            source = canonical_public_url(
                metadata.get("source_url") or getattr(result, "reference", None)
            )
            source_value = source[0] if source is not None else None
            provider = _safe_identity(metadata.get("web_search_provider"), "unknown")
        else:
            raw_source = metadata.get("document_id") or getattr(result, "reference", None)
            source_value = str(raw_source) if raw_source not in (None, "") else None
        if source_value is None:
            unresolved_source = True
        locator = _safe_locator(metadata)
        temporal_identity = extract_document_temporal_metadata(metadata)
        version_identity = extract_document_version_metadata(metadata)
        item = {
            "position": position,
            "source_type": source_type,
            "content_fingerprint": _digest(f"content:{str(evidence_text)}"),
            "source_fingerprint": (
                _digest(f"{source_type}:{source_value}") if source_value is not None else None
            ),
            "locator_fingerprint": _digest(locator) if locator else None,
            "temporal_fingerprint": (_digest(temporal_identity) if temporal_identity else None),
            "publication_anchor_bound": "published_at" in temporal_identity,
            "version_family_fingerprint": (_digest(version_identity) if version_identity else None),
            "version_family_bound": "version_family" in version_identity,
            "trusted": (bool(metadata.get("trusted", False)) if source_type == "web" else True),
        }
        if provider is not None:
            item["provider"] = provider
        items.append(item)
    status = (
        "complete" if len(all_items) == len(bounded_items) and not unresolved_source else "partial"
    )
    return {
        "snapshot_status": status,
        "evidence_count": len(all_items),
        "knowledge_base_count": sum(item["source_type"] == "knowledge_base" for item in items),
        "web_count": sum(item["source_type"] == "web" for item in items),
        "snapshot_fingerprint": _digest({"items": items}),
        "items": items,
    }


def build_trust_provenance(
    runtime: Any,
    *,
    context: Any = None,
    collection_names: Sequence[str] | None = None,
    collection_selection_mode: str | None = None,
    evidence_snapshot: Sequence[tuple[Any, str]] | None = None,
    evidence_identity: Mapping[str, Any] | None = None,
    execution_scope: str = "online",
    temporal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable, secret-free provenance bound to one answer."""

    scope = execution_scope if execution_scope in _SAFE_EXECUTION_SCOPES else "library"
    config = getattr(runtime, "config", None)
    try:
        config_fingerprint = f"sha256:{configuration_digest(config)}"
    except (AttributeError, TypeError, ValueError):
        config_fingerprint = "unavailable"
    tenant = _context_value(context, "tenant_id")
    runtime_identity = {
        "configuration_fingerprint": config_fingerprint,
        "runtime_version": _context_value(context, "runtime_version"),
        "binding_revision": _context_value(context, "binding_revision"),
        "tenant_fingerprint": _digest(f"tenant:{tenant}") if tenant else None,
        "model_policy": _safe_identity(_context_value(context, "model_policy"), "unversioned"),
    }
    entailment_checker = getattr(runtime, "entailment_checker", None)
    checkers = {
        "claim_extractor": {"version": CLAIM_EXTRACTOR_VERSION},
        "citation_validator": {"version": CITATION_VALIDATOR_VERSION},
        "citation_span_locator": {"version": CITATION_SPAN_LOCATOR_VERSION},
        "consistency_checker": {"version": CONSISTENCY_CHECKER_VERSION},
        "freshness_classifier": {
            "contract_version": FRESHNESS_CONTRACT_VERSION,
            "version": FRESHNESS_CLASSIFIER_VERSION,
        },
        "entailment_checker": {
            "contract_version": ENTAILMENT_CONTRACT_VERSION,
            "name": _safe_identity(getattr(entailment_checker, "checker_name", None), "disabled"),
            "version": _safe_identity(
                getattr(entailment_checker, "checker_version", None), "disabled"
            ),
            "minimum_confidence": (
                round(float(entailment_checker.min_confidence), 4)
                if entailment_checker is not None and hasattr(entailment_checker, "min_confidence")
                else None
            ),
        },
        "risk_classifier": {
            "contract_version": RISK_CONTRACT_VERSION,
            "version": RISK_CLASSIFIER_VERSION,
        },
    }
    payload = {
        "version": PROVENANCE_CONTRACT_VERSION,
        "builder_version": TRUST_PROVENANCE_BUILDER_VERSION,
        "execution_scope": scope,
        "runtime": runtime_identity,
        "generation_model": _llm_identity(runtime),
        "embedding": _embedding_identity(runtime),
        "index": _index_identity(
            runtime,
            collection_names,
            selection_mode=collection_selection_mode,
        ),
        "evidence": (
            dict(evidence_identity)
            if isinstance(evidence_identity, Mapping)
            else _evidence_identity(evidence_snapshot)
        ),
        "prompts": {
            "grounding": {
                "version": GROUNDING_PROMPT_VERSION,
                "fingerprint": _digest(GROUNDING_PROMPT),
            },
            "entailment": {"version": ENTAILMENT_PROMPT_VERSION},
        },
        "checkers": checkers,
        "policy": {
            "trust_contract_version": TRUST_CONTRACT_VERSION,
            "answer_policy_version": ANSWER_POLICY_VERSION,
        },
    }
    temporal_identity = _temporal_identity(temporal_context)
    if temporal_context is not None and temporal_identity is None:
        raise ValueError("invalid Trust Provenance temporal context")
    if temporal_identity is not None:
        payload["temporal"] = temporal_identity
    result = {**payload, "digest": _digest(payload)}
    sanitized = sanitize_trust_provenance(result)
    if sanitized is None:
        raise ValueError("generated Trust Provenance did not satisfy its contract")
    return sanitized


def bind_trust_provenance_temporal(
    provenance: Mapping[str, Any] | None,
    temporal_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Bind the request date/timezone identity to an existing valid v2 snapshot."""

    sanitized = sanitize_trust_provenance(provenance)
    temporal = _temporal_identity(temporal_context)
    if sanitized is None or sanitized.get("version") != 2 or temporal is None:
        return None
    payload = {key: deepcopy(value) for key, value in sanitized.items() if key != "digest"}
    payload["temporal"] = temporal
    rebound = {**payload, "digest": _digest(payload)}
    return sanitize_trust_provenance(rebound)


class TrustProvenanceSession:
    """Request-scoped resolver that binds dynamic routes without persisting names."""

    def __init__(
        self,
        runtime: Any,
        *,
        context: Any = None,
        collection_names: Sequence[str] | None = None,
        execution_scope: str = "online",
    ) -> None:
        self._runtime = runtime
        self._context = context
        self._execution_scope = execution_scope
        self._selection_mode = "dynamic" if collection_names is None else "explicit"
        self._collection_names = {
            item for item in (collection_names or ()) if isinstance(item, str) and item
        }
        self._evidence_identity = _evidence_identity(None)
        self._lock = threading.Lock()
        self._snapshot = self._build_snapshot()

    def _build_snapshot(self) -> dict[str, Any]:
        names = (
            sorted(self._collection_names)
            if self._selection_mode == "explicit" or self._collection_names
            else None
        )
        return build_trust_provenance(
            self._runtime,
            context=self._context,
            collection_names=names,
            collection_selection_mode=self._selection_mode,
            evidence_identity=self._evidence_identity,
            execution_scope=self._execution_scope,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._snapshot)

    def bind_evidence(self, evidence_snapshot: Sequence[tuple[Any, str]]) -> dict[str, Any]:
        """Replace the current evidence identity with the exact final-model snapshot."""

        safe_identity = _evidence_identity(evidence_snapshot)
        with self._lock:
            self._evidence_identity = safe_identity
            self._snapshot = self._build_snapshot()
            return deepcopy(self._snapshot)

    def bind_collections(self, collections: Sequence[str]) -> dict[str, Any]:
        """Merge actual routed collections and return a newly signed snapshot."""

        selected = {item for item in collections if isinstance(item, str) and 0 < len(item) <= 128}
        with self._lock:
            if selected.issubset(self._collection_names):
                return deepcopy(self._snapshot)
            self._collection_names.update(selected)
            self._snapshot = self._build_snapshot()
            return deepcopy(self._snapshot)


def sanitize_trust_provenance(value: Any) -> dict[str, Any] | None:
    """Accept internally valid v1/v2 contracts and discard injected fields."""

    if not isinstance(value, Mapping):
        return None
    version = _safe_int(value.get("version"), minimum=1)
    if version not in SUPPORTED_PROVENANCE_CONTRACT_VERSIONS:
        return None
    runtime = value.get("runtime")
    model = value.get("generation_model")
    embedding = value.get("embedding")
    index = value.get("index")
    prompts = value.get("prompts")
    checkers = value.get("checkers")
    policy = value.get("policy")
    if not all(
        isinstance(item, Mapping)
        for item in (runtime, model, embedding, index, prompts, checkers, policy)
    ):
        return None

    clean_evidence: dict[str, Any] | None = None
    clean_temporal: dict[str, Any] | None = None
    if version >= 2:
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, Mapping):
            return None
        snapshot_status = raw_evidence.get("snapshot_status")
        raw_items = raw_evidence.get("items")
        if snapshot_status not in _SAFE_EVIDENCE_STATUSES or not isinstance(raw_items, list):
            return None
        if len(raw_items) > 20:
            return None
        clean_items = []
        for expected_position, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, Mapping):
                return None
            source_type = raw_item.get("source_type")
            position = _safe_int(raw_item.get("position"), minimum=1)
            content_fingerprint = _safe_digest(raw_item.get("content_fingerprint"))
            source_fingerprint = (
                _safe_digest(raw_item.get("source_fingerprint"))
                if raw_item.get("source_fingerprint") is not None
                else None
            )
            locator_fingerprint = (
                _safe_digest(raw_item.get("locator_fingerprint"))
                if raw_item.get("locator_fingerprint") is not None
                else None
            )
            has_temporal_identity = (
                "temporal_fingerprint" in raw_item or "publication_anchor_bound" in raw_item
            )
            temporal_fingerprint = None
            publication_anchor_bound = None
            if has_temporal_identity:
                if not isinstance(raw_item.get("publication_anchor_bound"), bool):
                    return None
                temporal_fingerprint = (
                    _safe_digest(raw_item.get("temporal_fingerprint"))
                    if raw_item.get("temporal_fingerprint") is not None
                    else None
                )
                publication_anchor_bound = raw_item["publication_anchor_bound"]
            has_version_identity = (
                "version_family_fingerprint" in raw_item or "version_family_bound" in raw_item
            )
            version_family_fingerprint = None
            version_family_bound = None
            if has_version_identity:
                if not isinstance(raw_item.get("version_family_bound"), bool):
                    return None
                version_family_fingerprint = (
                    _safe_digest(raw_item.get("version_family_fingerprint"))
                    if raw_item.get("version_family_fingerprint") is not None
                    else None
                )
                version_family_bound = raw_item["version_family_bound"]
                if version_family_bound != (version_family_fingerprint is not None):
                    return None
            if (
                source_type not in _SAFE_SOURCE_TYPES
                or position != expected_position
                or content_fingerprint is None
                or not isinstance(raw_item.get("trusted"), bool)
            ):
                return None
            clean_item = {
                "position": position,
                "source_type": source_type,
                "content_fingerprint": content_fingerprint,
                "source_fingerprint": source_fingerprint,
                "locator_fingerprint": locator_fingerprint,
                "trusted": raw_item["trusted"],
            }
            if has_temporal_identity:
                clean_item["temporal_fingerprint"] = temporal_fingerprint
                clean_item["publication_anchor_bound"] = publication_anchor_bound
            if has_version_identity:
                clean_item["version_family_fingerprint"] = version_family_fingerprint
                clean_item["version_family_bound"] = version_family_bound
            if source_type == "web":
                provider = _optional_identity(raw_item.get("provider"))
                if provider is None:
                    return None
                clean_item["provider"] = provider
            clean_items.append(clean_item)
        evidence_count = _safe_int(raw_evidence.get("evidence_count"), minimum=0)
        knowledge_base_count = _safe_int(raw_evidence.get("knowledge_base_count"), minimum=0)
        web_count = _safe_int(raw_evidence.get("web_count"), minimum=0)
        snapshot_fingerprint = (
            _safe_digest(raw_evidence.get("snapshot_fingerprint"))
            if raw_evidence.get("snapshot_fingerprint") is not None
            else None
        )
        if (
            evidence_count is None
            or knowledge_base_count is None
            or web_count is None
            or knowledge_base_count
            != sum(item["source_type"] == "knowledge_base" for item in clean_items)
            or web_count != sum(item["source_type"] == "web" for item in clean_items)
            or evidence_count < len(clean_items)
        ):
            return None
        if snapshot_status == "unbound":
            if evidence_count or clean_items or snapshot_fingerprint is not None:
                return None
        else:
            if snapshot_fingerprint != _digest({"items": clean_items}):
                return None
            if snapshot_status == "complete" and (
                evidence_count != len(clean_items)
                or any(item["source_fingerprint"] is None for item in clean_items)
            ):
                return None
        clean_evidence = {
            "snapshot_status": snapshot_status,
            "evidence_count": evidence_count,
            "knowledge_base_count": knowledge_base_count,
            "web_count": web_count,
            "snapshot_fingerprint": snapshot_fingerprint,
            "items": clean_items,
        }
        raw_temporal = value.get("temporal")
        if raw_temporal is not None:
            clean_temporal = _temporal_identity(raw_temporal)
            if (
                clean_temporal is None
                or raw_temporal.get("fingerprint") != clean_temporal["fingerprint"]
            ):
                return None

    clean_model = {
        "provider": _optional_identity(model.get("provider")),
        "model": _optional_identity(model.get("model")),
        "version": _optional_identity(model.get("version")),
        "fingerprint": _safe_digest(model.get("fingerprint")),
    }
    clean_embedding = {
        "provider": _optional_identity(embedding.get("provider")),
        "model": _optional_identity(embedding.get("model")),
        "version": _optional_identity(embedding.get("version")),
        "dimension": (
            _safe_int(embedding.get("dimension"), minimum=1)
            if embedding.get("dimension") is not None
            else None
        ),
        "normalization": _optional_identity(embedding.get("normalization")),
        "fingerprint": _safe_digest(embedding.get("fingerprint")),
    }
    if any(item is None for item in clean_model.values()) or any(
        clean_embedding[key] is None
        for key in ("provider", "model", "version", "normalization", "fingerprint")
    ):
        return None

    raw_manifests = index.get("manifests")
    if not isinstance(raw_manifests, list) or len(raw_manifests) > 32:
        return None
    clean_manifests = []
    for raw in raw_manifests:
        if not isinstance(raw, Mapping) or raw.get("status") not in _SAFE_MANIFEST_STATUSES:
            return None
        clean = {"status": raw["status"]}
        if raw["status"] == "verified":
            clean.update(
                {
                    "schema_version": _safe_int(raw.get("schema_version"), minimum=1),
                    "manifest_fingerprint": _safe_digest(raw.get("manifest_fingerprint")),
                    "data_version": _optional_identity(raw.get("data_version")),
                    "document_version": _optional_identity(raw.get("document_version")),
                    "embedding_fingerprint": _safe_digest(raw.get("embedding_fingerprint")),
                    "chunk_config_version": _optional_identity(raw.get("chunk_config_version")),
                }
            )
            if any(item is None for item in clean.values()):
                return None
        clean_manifests.append(clean)

    grounding_prompt = prompts.get("grounding")
    entailment_prompt = prompts.get("entailment")
    if not isinstance(grounding_prompt, Mapping) or not isinstance(entailment_prompt, Mapping):
        return None
    clean_prompts = {
        "grounding": {
            "version": _optional_identity(grounding_prompt.get("version")),
            "fingerprint": _safe_digest(grounding_prompt.get("fingerprint")),
        },
        "entailment": {"version": _optional_identity(entailment_prompt.get("version"))},
    }
    if any(item is None for section in clean_prompts.values() for item in section.values()):
        return None

    deterministic_checker_names = (
        "claim_extractor",
        "citation_validator",
        "citation_span_locator",
        "consistency_checker",
    )
    checker_names = (*deterministic_checker_names, "entailment_checker", "risk_classifier")
    if any(not isinstance(checkers.get(name), Mapping) for name in checker_names):
        return None
    clean_checkers = {
        name: {"version": _optional_identity(checkers[name].get("version"))}
        for name in deterministic_checker_names
    }
    raw_entailment = checkers["entailment_checker"]
    clean_checkers["entailment_checker"] = {
        "contract_version": _safe_int(raw_entailment.get("contract_version"), minimum=1),
        "name": _optional_identity(raw_entailment.get("name")),
        "version": _optional_identity(raw_entailment.get("version")),
        "minimum_confidence": (
            _safe_float(raw_entailment.get("minimum_confidence"), minimum=0.5, maximum=1.0)
            if raw_entailment.get("minimum_confidence") is not None
            else None
        ),
    }
    raw_risk = checkers["risk_classifier"]
    clean_checkers["risk_classifier"] = {
        "contract_version": _safe_int(raw_risk.get("contract_version"), minimum=1),
        "version": _optional_identity(raw_risk.get("version")),
    }
    raw_freshness = checkers.get("freshness_classifier")
    if raw_freshness is not None:
        if not isinstance(raw_freshness, Mapping):
            return None
        clean_checkers["freshness_classifier"] = {
            "contract_version": _safe_int(raw_freshness.get("contract_version"), minimum=1),
            "version": _optional_identity(raw_freshness.get("version")),
        }
    if any("version" not in clean_checkers[name] for name in checker_names):
        return None
    if any(
        item is None
        for name, section in clean_checkers.items()
        for key, item in section.items()
        if not (name == "entailment_checker" and key == "minimum_confidence")
    ):
        return None

    scope = value.get("execution_scope")
    selection_mode = index.get("selection_mode")
    snapshot_status = index.get("snapshot_status")
    if (
        scope not in _SAFE_EXECUTION_SCOPES
        or selection_mode not in {"explicit", "dynamic"}
        or snapshot_status not in _SAFE_INDEX_STATUSES
    ):
        return None
    payload = {
        "version": version,
        "builder_version": _optional_identity(value.get("builder_version")),
        "execution_scope": scope,
        "runtime": {
            "configuration_fingerprint": _safe_digest(
                runtime.get("configuration_fingerprint"), unavailable=True
            ),
            "runtime_version": (
                _safe_int(runtime.get("runtime_version"), minimum=1)
                if runtime.get("runtime_version") is not None
                else None
            ),
            "binding_revision": (
                _safe_int(runtime.get("binding_revision"), minimum=1)
                if runtime.get("binding_revision") is not None
                else None
            ),
            "tenant_fingerprint": (
                _safe_digest(runtime.get("tenant_fingerprint"))
                if runtime.get("tenant_fingerprint") is not None
                else None
            ),
            "model_policy": _optional_identity(runtime.get("model_policy")),
        },
        "generation_model": clean_model,
        "embedding": clean_embedding,
        "index": {
            "selection_mode": selection_mode,
            "snapshot_status": snapshot_status,
            "collection_count": _safe_int(index.get("collection_count"), minimum=0),
            "manifests": clean_manifests,
        },
        "prompts": clean_prompts,
        "checkers": clean_checkers,
        "policy": {
            "trust_contract_version": _safe_int(policy.get("trust_contract_version"), minimum=1),
            "answer_policy_version": _safe_int(policy.get("answer_policy_version"), minimum=1),
        },
    }
    if version >= 2:
        payload["evidence"] = clean_evidence
        if clean_temporal is not None:
            payload["temporal"] = clean_temporal
    if (
        payload["builder_version"] is None
        or payload["runtime"]["configuration_fingerprint"] is None
        or payload["runtime"]["model_policy"] is None
        or payload["index"]["collection_count"] is None
        or any(item is None for item in payload["policy"].values())
    ):
        return None
    expected = _digest(payload)
    if value.get("digest") != expected:
        return None
    serialized = _canonical_json(payload)
    if len(serialized) > 32_000:
        return None
    return {**payload, "digest": expected}
