from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

COLLECTION_MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_COLLECTION_MANIFEST_SCHEMA_VERSIONS = frozenset({1, 2})
LEGACY_CHUNK_ALGORITHM = "recursive-character-window-v1"
CHUNK_ALGORITHM = "section-aware-recursive-character-window-v2"
DEFAULT_NORMALIZATION = "provider_default"


def _canonical_json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identity_value(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    normalized = str(value).strip()
    return normalized[:256] if normalized else fallback


def _normalization_value(value: Any) -> str:
    if isinstance(value, bool):
        return "l2" if value else "none"
    return _safe_identity_value(value, DEFAULT_NORMALIZATION)


@dataclass(frozen=True)
class EmbeddingProfile:
    """Secret-free identity for one query/document embedding space."""

    provider: str
    model: str
    version: str
    dimension: int
    normalization: str = DEFAULT_NORMALIZATION

    def __post_init__(self) -> None:
        if int(self.dimension) <= 0:
            raise ValueError("embedding dimension must be positive")
        for field_name in ("provider", "model", "version", "normalization"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"embedding {field_name} must not be empty")

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(self.identity_dict()))

    def identity_dict(self) -> dict:
        return {
            "embedding_provider": self.provider,
            "embedding_model": self.model,
            "embedding_version": self.version,
            "dimension": int(self.dimension),
            "normalization": self.normalization,
        }

    def to_dict(self) -> dict:
        return {
            **self.identity_dict(),
            "embedding_fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: dict) -> EmbeddingProfile:
        required_fields = {
            "embedding_provider",
            "embedding_model",
            "embedding_version",
            "embedding_fingerprint",
            "dimension",
            "normalization",
        }
        if not isinstance(value, dict) or required_fields.difference(value):
            raise ValueError("embedding profile is missing required fields")
        try:
            profile = cls(
                provider=_safe_identity_value(
                    value.get("embedding_provider"),
                    "unknown",
                ),
                model=_safe_identity_value(
                    value.get("embedding_model"),
                    "unknown",
                ),
                version=_safe_identity_value(
                    value.get("embedding_version"),
                    "unknown",
                ),
                dimension=int(value["dimension"]),
                normalization=_normalization_value(value.get("normalization")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid embedding profile") from exc
        expected_fingerprint = value.get("embedding_fingerprint")
        if not isinstance(expected_fingerprint, str) or expected_fingerprint != profile.fingerprint:
            raise ValueError("embedding profile fingerprint does not match its fields")
        return profile

    @classmethod
    def from_embedding(cls, embedding_model) -> EmbeddingProfile:
        provider = getattr(
            embedding_model,
            "_deepsearcher_embedding_provider",
            embedding_model.__class__.__name__,
        )
        model = getattr(embedding_model, "_deepsearcher_embedding_model", None)
        if model is None:
            for attribute in ("model_name", "model", "deployment"):
                candidate = getattr(embedding_model, attribute, None)
                if isinstance(candidate, str) and candidate.strip():
                    model = candidate
                    break
        model = _safe_identity_value(
            model,
            f"{embedding_model.__class__.__module__}.{embedding_model.__class__.__name__}",
        )
        version = getattr(
            embedding_model,
            "_deepsearcher_embedding_version",
            model,
        )
        normalization = getattr(
            embedding_model,
            "_deepsearcher_embedding_normalization",
            None,
        )
        if normalization is None:
            for attribute in (
                "normalize_embeddings",
                "normalize",
                "normalization",
            ):
                if hasattr(embedding_model, attribute):
                    normalization = getattr(embedding_model, attribute)
                    break
        return cls(
            provider=_safe_identity_value(provider, "unknown"),
            model=model,
            version=_safe_identity_value(version, model),
            dimension=int(embedding_model.dimension),
            normalization=_normalization_value(normalization),
        )


def bind_embedding_identity(
    embedding_model,
    *,
    provider: str,
    config: dict | None = None,
    identity: dict | None = None,
):
    """Attach only governance metadata; provider credentials are never retained."""
    config = config or {}
    identity = identity or {}
    configured_model = identity.get("model") or config.get("model") or config.get("model_name")
    actual_model = configured_model
    if actual_model is None:
        candidate = getattr(embedding_model, "model", None)
        actual_model = candidate if isinstance(candidate, str) else None
    model = _safe_identity_value(
        actual_model,
        f"{embedding_model.__class__.__module__}.{embedding_model.__class__.__name__}",
    )
    version = (
        identity.get("version")
        or config.get("embedding_version")
        or config.get("model_revision")
        or config.get("revision")
        or model
    )
    normalization = (
        identity.get("normalization")
        or config.get("normalization")
        or config.get("normalize_embeddings")
        or DEFAULT_NORMALIZATION
    )
    embedding_model._deepsearcher_embedding_provider = _safe_identity_value(
        provider,
        embedding_model.__class__.__name__,
    )
    embedding_model._deepsearcher_embedding_model = model
    embedding_model._deepsearcher_embedding_version = _safe_identity_value(version, model)
    embedding_model._deepsearcher_embedding_normalization = _normalization_value(normalization)
    return embedding_model


def chunk_config_version(
    *,
    chunk_size: int,
    chunk_overlap: int,
    algorithm: str = CHUNK_ALGORITHM,
) -> str:
    value = {
        "algorithm": algorithm,
        "chunk_overlap": int(chunk_overlap),
        "chunk_size": int(chunk_size),
    }
    return f"sha256:{_sha256(_canonical_json(value))}"


def document_version(chunks: Iterable) -> str:
    fingerprints = []
    for chunk in chunks:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        document_id = metadata.get("document_id")
        source_identity = document_id or _sha256(f"{chunk.reference}\n{chunk.text}")
        fingerprints.append(
            _sha256(
                _canonical_json(
                    {
                        "chunk_index": metadata.get("chunk_index"),
                        "document": str(source_identity),
                        "page_number": metadata.get("page_number"),
                        "published_at": metadata.get("published_at"),
                        "effective_at": metadata.get("effective_at"),
                        "superseded_at": metadata.get("superseded_at"),
                        "temporal_metadata_source": metadata.get("temporal_metadata_source"),
                        "version_family": metadata.get("version_family"),
                        "version_family_source": metadata.get("version_family_source"),
                        "text_hash": _sha256(chunk.text),
                    }
                )
            )
        )
    return f"sha256:{_sha256(_canonical_json({'chunks': sorted(fingerprints)}))}"


@dataclass(frozen=True)
class CollectionManifest:
    """Persisted contract between an indexed collection and query embeddings."""

    logical_collection: str
    embedding: EmbeddingProfile
    metric_type: str
    chunk_size: int
    chunk_overlap: int
    chunk_config_version: str
    document_version: str
    data_version: str
    created_at: str
    chunk_algorithm: str = CHUNK_ALGORITHM
    user_description: str = ""
    schema_version: int = COLLECTION_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if int(self.schema_version) not in SUPPORTED_COLLECTION_MANIFEST_SCHEMA_VERSIONS:
            raise ValueError("unsupported collection manifest schema")
        if not isinstance(self.logical_collection, str) or not self.logical_collection.strip():
            raise ValueError("logical collection must not be empty")
        if not isinstance(self.metric_type, str) or not self.metric_type.strip():
            raise ValueError("metric type must not be empty")
        if int(self.chunk_size) <= 0:
            raise ValueError("chunk size must be positive")
        if int(self.chunk_overlap) < 0 or int(self.chunk_overlap) >= int(self.chunk_size):
            raise ValueError("chunk overlap must be non-negative and smaller than chunk size")
        for field_name in (
            "chunk_config_version",
            "chunk_algorithm",
            "document_version",
            "data_version",
            "created_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")

    @classmethod
    def create(
        cls,
        *,
        logical_collection: str,
        embedding: EmbeddingProfile,
        metric_type: str,
        chunk_size: int,
        chunk_overlap: int,
        chunks: Iterable,
        user_description: str | None = None,
    ) -> CollectionManifest:
        chunk_list = list(chunks)
        return cls(
            logical_collection=logical_collection,
            embedding=embedding,
            metric_type=_safe_identity_value(metric_type, "unknown").upper(),
            chunk_size=int(chunk_size),
            chunk_overlap=int(chunk_overlap),
            chunk_config_version=chunk_config_version(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            ),
            chunk_algorithm=CHUNK_ALGORITHM,
            document_version=document_version(chunk_list),
            data_version=f"data_{uuid4().hex}",
            created_at=datetime.now(timezone.utc).isoformat(),
            user_description=(user_description or "")[:2048],
        )

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(self.contract_dict()))

    def contract_dict(self) -> dict:
        contract = {
            "schema_version": int(self.schema_version),
            "logical_collection": self.logical_collection,
            **self.embedding.to_dict(),
            "metric_type": self.metric_type,
            "chunk_size": int(self.chunk_size),
            "chunk_overlap": int(self.chunk_overlap),
            "chunk_config_version": self.chunk_config_version,
            "document_version": self.document_version,
            "data_version": self.data_version,
        }
        if int(self.schema_version) >= 2:
            contract["chunk_algorithm"] = self.chunk_algorithm
        return contract

    def to_dict(self) -> dict:
        serialized = {
            **self.contract_dict(),
            "created_at": self.created_at,
            "user_description": self.user_description,
            "manifest_fingerprint": self.fingerprint,
        }
        if int(self.schema_version) >= 2:
            serialized["chunk_algorithm"] = self.chunk_algorithm
        return serialized

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: dict) -> CollectionManifest:
        required_fields = {
            "schema_version",
            "logical_collection",
            "metric_type",
            "chunk_size",
            "chunk_overlap",
            "chunk_config_version",
            "document_version",
            "data_version",
            "created_at",
            "manifest_fingerprint",
        }
        if not isinstance(value, dict) or required_fields.difference(value):
            raise ValueError("collection manifest is missing required fields")
        try:
            schema_version = int(value.get("schema_version", 0))
            if schema_version not in SUPPORTED_COLLECTION_MANIFEST_SCHEMA_VERSIONS:
                raise ValueError("unsupported collection manifest schema")
            if schema_version >= 2 and "chunk_algorithm" not in value:
                raise ValueError("collection manifest is missing chunk algorithm")
            chunk_algorithm = (
                _safe_identity_value(value.get("chunk_algorithm"), CHUNK_ALGORITHM)
                if schema_version >= 2
                else LEGACY_CHUNK_ALGORITHM
            )
            manifest = cls(
                logical_collection=_safe_identity_value(
                    value.get("logical_collection"),
                    "unknown",
                ),
                embedding=EmbeddingProfile.from_dict(value),
                metric_type=_safe_identity_value(value.get("metric_type"), "unknown").upper(),
                chunk_size=int(value["chunk_size"]),
                chunk_overlap=int(value["chunk_overlap"]),
                chunk_config_version=_safe_identity_value(
                    value.get("chunk_config_version"),
                    "unknown",
                ),
                chunk_algorithm=chunk_algorithm,
                document_version=_safe_identity_value(
                    value.get("document_version"),
                    "unknown",
                ),
                data_version=_safe_identity_value(value.get("data_version"), "unknown"),
                created_at=_safe_identity_value(value.get("created_at"), "unknown"),
                user_description=str(value.get("user_description") or "")[:2048],
                schema_version=schema_version,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid collection manifest") from exc
        expected_fingerprint = value.get("manifest_fingerprint")
        if (
            not isinstance(expected_fingerprint, str)
            or expected_fingerprint != manifest.fingerprint
        ):
            raise ValueError("collection manifest fingerprint does not match its fields")
        expected_chunk_version = chunk_config_version(
            chunk_size=manifest.chunk_size,
            chunk_overlap=manifest.chunk_overlap,
            algorithm=manifest.chunk_algorithm,
        )
        if expected_chunk_version != manifest.chunk_config_version:
            raise ValueError("chunk configuration fingerprint does not match its fields")
        return manifest

    @classmethod
    def from_json(cls, value: str) -> CollectionManifest:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("collection manifest must be a JSON object")
        return cls.from_dict(parsed)

    def embedding_mismatches(self, expected: EmbeddingProfile) -> list[str]:
        mismatches = []
        for field in (
            "provider",
            "model",
            "version",
            "dimension",
            "normalization",
        ):
            if getattr(self.embedding, field) != getattr(expected, field):
                mismatches.append(field)
        return mismatches

    def merge_append(self, incoming: CollectionManifest) -> CollectionManifest:
        if self.embedding_mismatches(incoming.embedding):
            raise ValueError("embedding profile mismatch")
        if self.metric_type != incoming.metric_type:
            raise ValueError("metric type mismatch")
        if self.chunk_config_version != incoming.chunk_config_version:
            raise ValueError("chunk configuration mismatch")
        if self.chunk_algorithm != incoming.chunk_algorithm:
            raise ValueError("chunk algorithm mismatch")
        merged_document_version = f"sha256:{_sha256(_canonical_json({'documents': sorted([self.document_version, incoming.document_version])}))}"
        return replace(
            self,
            document_version=merged_document_version,
            data_version=f"data_{uuid4().hex}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def record_mutation(self, operation: str, identifier: str) -> CollectionManifest:
        next_document_version = f"sha256:{_sha256(_canonical_json({'current': self.document_version, 'identifier': identifier, 'operation': operation}))}"
        return replace(
            self,
            document_version=next_document_version,
            data_version=f"data_{uuid4().hex}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
