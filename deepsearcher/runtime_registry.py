from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from deepsearcher.configuration import Configuration, RuntimeComponents, RuntimeInitializationError

DEFAULT_TENANT_ID = "local"
DEFAULT_RUNTIME_DB_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "runtime" / "deepsearcher-runtime.db"
)
TENANT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
COLLECTION_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
ENV_NAME_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
    "wallet_password",
}

RuntimeFactory = Callable[[Configuration], RuntimeComponents]
logger = logging.getLogger(__name__)


class RuntimeControlError(RuntimeError):
    code = "RUNTIME_CONTROL_ERROR"
    safe_message = "The requested runtime operation could not be completed."
    status_code = 409
    retryable = False

    def __init__(self, detail: str | None = None):
        super().__init__(self.safe_message)
        self.detail = detail


class RuntimeTenantNotFound(RuntimeControlError):
    code = "RUNTIME_TENANT_NOT_FOUND"
    safe_message = "The requested tenant runtime does not exist."
    status_code = 403


class RuntimeTenantUnauthorized(RuntimeControlError):
    code = "RUNTIME_TENANT_UNAUTHORIZED"
    safe_message = "The caller is not authorized to select this tenant."
    status_code = 403


class RuntimeCollectionAccessDenied(RuntimeControlError):
    code = "RUNTIME_COLLECTION_ACCESS_DENIED"
    safe_message = "The tenant is not authorized to access the requested collection."
    status_code = 403


class RuntimeVersionConflict(RuntimeControlError):
    code = "RUNTIME_VERSION_CONFLICT"
    safe_message = "The runtime configuration changed before this operation completed."
    status_code = 409
    retryable = True


class RuntimeRollbackUnavailable(RuntimeControlError):
    code = "RUNTIME_ROLLBACK_UNAVAILABLE"
    safe_message = "There is no previous runtime version available for rollback."
    status_code = 409


class RuntimeConfigRejected(RuntimeControlError):
    code = "RUNTIME_CONFIG_REJECTED"
    safe_message = "The runtime configuration contains an unsupported or unsafe value."
    status_code = 400


@dataclass(frozen=True)
class RuntimePatch:
    feature: str
    provider: str
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "provider": self.provider,
            "config": self.config,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimePatch":
        return cls(
            feature=str(payload["feature"]),
            provider=str(payload["provider"]),
            config=dict(payload.get("config") or {}),
        )


@dataclass(frozen=True)
class TenantRuntimeBinding:
    tenant_id: str
    active_version: int
    previous_version: int | None
    allowed_collections: tuple[str, ...]
    model_policy: str
    revision: int


@dataclass(frozen=True)
class RuntimeRequestContext:
    tenant_id: str
    runtime_version: int
    model_policy: str
    allowed_collections: tuple[str, ...]
    binding_revision: int

    def allows_collection(self, collection_name: str) -> bool:
        if COLLECTION_NAME_PATTERN.fullmatch(collection_name) is None:
            return False
        for rule in self.allowed_collections:
            if rule == "*":
                return True
            if rule.endswith("*") and collection_name.startswith(rule[:-1]):
                return True
            if rule == collection_name:
                return True
        return False

    def require_collection(self, collection_name: str) -> str:
        if not self.allows_collection(collection_name):
            raise RuntimeCollectionAccessDenied()
        return collection_name

    def collections_for_query(
        self,
        requested: Sequence[str] | None,
    ) -> list[str] | None:
        if requested is not None:
            collections = list(dict.fromkeys(requested))
            for collection_name in collections:
                self.require_collection(collection_name)
            return collections
        if "*" in self.allowed_collections:
            return None
        if all(not rule.endswith("*") for rule in self.allowed_collections):
            return list(self.allowed_collections)
        raise RuntimeCollectionAccessDenied(
            "An explicit collection is required when the tenant policy uses a prefix rule."
        )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(key: object) -> bool:
    return str(key).strip().lower() in SENSITIVE_CONFIG_KEYS


def _redact_secrets(value: Any, *, parent_key: str | None = None) -> Any:
    if parent_key is not None and _is_sensitive_key(parent_key):
        if isinstance(value, dict) and set(value) == {"$env"}:
            return {"$env": str(value["$env"])}
        return {"$configured": value not in (None, "")}
    if isinstance(value, dict):
        return {
            str(key): _redact_secrets(item, parent_key=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def configuration_digest(config: Configuration) -> str:
    payload = {
        "provide_settings": getattr(config, "provide_settings", {}),
        "query_settings": getattr(config, "query_settings", {}),
        "load_settings": getattr(config, "load_settings", {}),
    }
    serialized = json.dumps(
        _redact_secrets(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_env_marker(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"$env"}
        and isinstance(value["$env"], str)
        and ENV_NAME_PATTERN.fullmatch(value["$env"]) is not None
    )


def validate_runtime_patch(patch: RuntimePatch) -> RuntimePatch:
    if not patch.feature or not patch.provider:
        raise RuntimeConfigRejected()

    def walk(value: Any, parent_key: str | None = None) -> None:
        if parent_key is not None and _is_sensitive_key(parent_key):
            if not _validate_env_marker(value):
                raise RuntimeConfigRejected(
                    "Sensitive values must use an environment reference such as "
                    '{"$env": "TENANT_OPENAI_API_KEY"}.'
                )
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, str(key))
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise RuntimeConfigRejected()

    walk(patch.config)
    return patch


def _resolve_environment_references(value: Any) -> Any:
    if _validate_env_marker(value):
        environment_name = value["$env"]
        resolved = os.environ.get(environment_name)
        if resolved is None:
            cause = RuntimeError(f"Required environment variable is missing: {environment_name}")
            error = RuntimeInitializationError("configuration")
            error.__cause__ = cause
            raise error
        return resolved
    if isinstance(value, dict):
        return {key: _resolve_environment_references(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_environment_references(item) for item in value]
    return value


def normalize_allowed_collections(rules: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_rule in rules:
        rule = str(raw_rule).strip()
        valid = rule == "*" or COLLECTION_NAME_PATTERN.fullmatch(rule) is not None
        if rule.endswith("*") and rule != "*":
            prefix = rule[:-1]
            valid = COLLECTION_NAME_PATTERN.fullmatch(prefix) is not None
        if not valid:
            raise RuntimeConfigRejected("Invalid collection permission rule.")
        if rule not in normalized:
            normalized.append(rule)
    return tuple(normalized)


def derive_model_policy(config: Configuration) -> str:
    llm_settings = (getattr(config, "provide_settings", {}) or {}).get("llm", {})
    provider = str(llm_settings.get("provider") or "configured-runtime")
    model = str((llm_settings.get("config") or {}).get("model") or "default")
    return f"{provider}:{model}"[:160]


class RuntimeControlStore:
    """SQLite control plane shared by all API workers on one deployment."""

    def __init__(self, path: str | Path | None = None):
        configured_path = path or os.environ.get("DEEPSEARCHER_RUNTIME_DB")
        self.path = Path(configured_path or DEFAULT_RUNTIME_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT,
                    base_digest TEXT NOT NULL,
                    parent_version INTEGER REFERENCES runtime_versions(version),
                    patch_json TEXT,
                    allowed_collections_json TEXT,
                    model_policy TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_base_version
                ON runtime_versions(base_digest)
                WHERE parent_version IS NULL;

                CREATE TABLE IF NOT EXISTS tenant_runtime_bindings (
                    tenant_id TEXT PRIMARY KEY,
                    active_version INTEGER NOT NULL REFERENCES runtime_versions(version),
                    previous_version INTEGER REFERENCES runtime_versions(version),
                    allowed_collections_json TEXT NOT NULL,
                    model_policy TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(runtime_versions)").fetchall()
            }
            if "allowed_collections_json" not in columns:
                connection.execute(
                    "ALTER TABLE runtime_versions ADD COLUMN allowed_collections_json TEXT"
                )
            if "model_policy" not in columns:
                connection.execute("ALTER TABLE runtime_versions ADD COLUMN model_policy TEXT")

    @staticmethod
    def _binding_from_row(row: sqlite3.Row) -> TenantRuntimeBinding:
        return TenantRuntimeBinding(
            tenant_id=str(row["tenant_id"]),
            active_version=int(row["active_version"]),
            previous_version=(
                int(row["previous_version"]) if row["previous_version"] is not None else None
            ),
            allowed_collections=tuple(json.loads(row["allowed_collections_json"])),
            model_policy=str(row["model_policy"]),
            revision=int(row["revision"]),
        )

    def initialize(
        self,
        *,
        base_digest: str,
        default_tenant: str,
        allowed_collections: Sequence[str],
        model_policy: str,
    ) -> TenantRuntimeBinding:
        allowed = normalize_allowed_collections(allowed_collections)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            base_row = connection.execute(
                """
                SELECT version FROM runtime_versions
                WHERE base_digest = ? AND parent_version IS NULL
                """,
                (base_digest,),
            ).fetchone()
            if base_row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO runtime_versions(
                        base_digest, parent_version, patch_json,
                        allowed_collections_json, model_policy, created_at
                    )
                    VALUES (?, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        base_digest,
                        json.dumps(allowed),
                        model_policy,
                        _utcnow(),
                    ),
                )
                base_version = int(cursor.lastrowid)
            else:
                base_version = int(base_row["version"])
                connection.execute(
                    """
                    UPDATE runtime_versions
                    SET allowed_collections_json = COALESCE(allowed_collections_json, ?),
                        model_policy = COALESCE(model_policy, ?)
                    WHERE version = ?
                    """,
                    (json.dumps(allowed), model_policy, base_version),
                )

            binding_row = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (default_tenant,),
            ).fetchone()
            if binding_row is None:
                connection.execute(
                    """
                    INSERT INTO tenant_runtime_bindings(
                        tenant_id, active_version, previous_version,
                        allowed_collections_json, model_policy, revision, updated_at
                    ) VALUES (?, ?, NULL, ?, ?, 1, ?)
                    """,
                    (
                        default_tenant,
                        base_version,
                        json.dumps(allowed),
                        model_policy,
                        _utcnow(),
                    ),
                )
            else:
                active_base = connection.execute(
                    "SELECT base_digest FROM runtime_versions WHERE version = ?",
                    (binding_row["active_version"],),
                ).fetchone()
                if active_base is None or active_base["base_digest"] != base_digest:
                    connection.execute(
                        """
                        UPDATE tenant_runtime_bindings
                        SET active_version = ?, previous_version = NULL,
                            model_policy = ?, revision = revision + 1, updated_at = ?
                        WHERE tenant_id = ?
                        """,
                        (base_version, model_policy, _utcnow(), default_tenant),
                    )
            row = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (default_tenant,),
            ).fetchone()
            return self._binding_from_row(row)

    def get_binding(self, tenant_id: str) -> TenantRuntimeBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return self._binding_from_row(row) if row is not None else None

    def get_patch_chain(self, version: int, *, base_digest: str) -> list[RuntimePatch]:
        patches: list[RuntimePatch] = []
        current_version: int | None = version
        with self._connect() as connection:
            while current_version is not None:
                row = connection.execute(
                    "SELECT * FROM runtime_versions WHERE version = ?",
                    (current_version,),
                ).fetchone()
                if row is None or row["base_digest"] != base_digest:
                    raise RuntimeVersionConflict()
                if row["patch_json"]:
                    patches.append(RuntimePatch.from_dict(json.loads(row["patch_json"])))
                current_version = (
                    int(row["parent_version"]) if row["parent_version"] is not None else None
                )
        patches.reverse()
        return patches

    def get_base_version(self, base_digest: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT version FROM runtime_versions
                WHERE base_digest = ? AND parent_version IS NULL
                """,
                (base_digest,),
            ).fetchone()
        if row is None:
            raise RuntimeVersionConflict()
        return int(row["version"])

    def publish(
        self,
        *,
        tenant_id: str,
        expected_version: int | None,
        parent_version: int,
        patch: RuntimePatch,
        allowed_collections: Sequence[str],
        model_policy: str,
    ) -> TenantRuntimeBinding:
        allowed = normalize_allowed_collections(allowed_collections)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                "SELECT base_digest FROM runtime_versions WHERE version = ?",
                (parent_version,),
            ).fetchone()
            if parent is None:
                raise RuntimeVersionConflict()
            binding = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if binding is None:
                if expected_version is not None:
                    raise RuntimeVersionConflict()
            elif int(binding["active_version"]) != expected_version:
                raise RuntimeVersionConflict()

            cursor = connection.execute(
                """
                INSERT INTO runtime_versions(
                    base_digest, parent_version, patch_json,
                    allowed_collections_json, model_policy, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    parent["base_digest"],
                    parent_version,
                    json.dumps(patch.to_dict(), ensure_ascii=False, sort_keys=True),
                    json.dumps(allowed),
                    model_policy,
                    _utcnow(),
                ),
            )
            version = int(cursor.lastrowid)
            if binding is None:
                connection.execute(
                    """
                    INSERT INTO tenant_runtime_bindings(
                        tenant_id, active_version, previous_version,
                        allowed_collections_json, model_policy, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        tenant_id,
                        version,
                        None,
                        json.dumps(allowed),
                        model_policy,
                        _utcnow(),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE tenant_runtime_bindings
                    SET active_version = ?, previous_version = ?,
                        allowed_collections_json = ?, model_policy = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE tenant_id = ?
                    """,
                    (
                        version,
                        parent_version,
                        json.dumps(allowed),
                        model_policy,
                        _utcnow(),
                        tenant_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return self._binding_from_row(row)

    def rollback(self, *, tenant_id: str, expected_version: int) -> TenantRuntimeBinding:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if binding is None:
                raise RuntimeTenantNotFound()
            if int(binding["active_version"]) != expected_version:
                raise RuntimeVersionConflict()
            if binding["previous_version"] is None:
                raise RuntimeRollbackUnavailable()
            previous = connection.execute(
                """
                SELECT allowed_collections_json, model_policy
                FROM runtime_versions
                WHERE version = ?
                """,
                (binding["previous_version"],),
            ).fetchone()
            if previous is None:
                raise RuntimeRollbackUnavailable()
            allowed_collections_json = (
                previous["allowed_collections_json"] or binding["allowed_collections_json"]
            )
            model_policy = previous["model_policy"] or binding["model_policy"]
            connection.execute(
                """
                UPDATE tenant_runtime_bindings
                SET active_version = previous_version, previous_version = active_version,
                    allowed_collections_json = ?, model_policy = ?,
                    revision = revision + 1, updated_at = ?
                WHERE tenant_id = ?
                """,
                (
                    allowed_collections_json,
                    model_policy,
                    _utcnow(),
                    tenant_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM tenant_runtime_bindings WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return self._binding_from_row(row)

    def version_is_active(self, version: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM tenant_runtime_bindings
                WHERE active_version = ?
                LIMIT 1
                """,
                (version,),
            ).fetchone()
        return row is not None


@dataclass
class _RuntimeEntry:
    runtime: RuntimeComponents
    references: int = 0


class RuntimeLease:
    def __init__(
        self,
        registry: "RuntimeRegistry",
        runtime: RuntimeComponents,
        context: RuntimeRequestContext,
    ):
        self.runtime = runtime
        self.context = context
        self._registry = registry
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._registry.release(self.context.runtime_version)


async def close_runtime(runtime: RuntimeComponents | None) -> None:
    if runtime is None:
        return
    components = (
        getattr(runtime, "llm", None),
        getattr(runtime, "embedding_model", None),
        getattr(runtime, "file_loader", None),
        getattr(runtime, "vector_db", None),
        getattr(runtime, "web_crawler", None),
        getattr(runtime, "web_search", None),
    )
    seen: set[int] = set()
    for component in components:
        for target in (component, getattr(component, "client", None)):
            if target is None or id(target) in seen:
                continue
            seen.add(id(target))
            close = getattr(target, "close", None)
            if not callable(close):
                close = getattr(target, "aclose", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning(
                    "runtime_component_close_failed component=%s exception_type=%s",
                    type(target).__name__,
                    type(exc).__name__,
                )


class RuntimeRegistry:
    """Per-worker runtime cache backed by a shared version/binding control plane."""

    def __init__(
        self,
        *,
        base_config: Configuration,
        runtime_factory: RuntimeFactory,
        store: RuntimeControlStore,
        default_tenant: str = DEFAULT_TENANT_ID,
        default_allowed_collections: Sequence[str] = ("*",),
    ):
        if TENANT_ID_PATTERN.fullmatch(default_tenant) is None:
            raise ValueError("Invalid default tenant id")
        self.base_config = copy.deepcopy(base_config)
        self.base_digest = configuration_digest(base_config)
        self.runtime_factory = runtime_factory
        self.store = store
        self.default_tenant = default_tenant
        self.default_allowed_collections = normalize_allowed_collections(
            default_allowed_collections
        )
        self.base_version: int | None = None
        self._entries: dict[int, _RuntimeEntry] = {}
        self._entry_lock = asyncio.Lock()
        self._build_lock = asyncio.Lock()

    async def start(self) -> TenantRuntimeBinding:
        model_policy = derive_model_policy(self.base_config)
        binding = await asyncio.to_thread(
            self.store.initialize,
            base_digest=self.base_digest,
            default_tenant=self.default_tenant,
            allowed_collections=self.default_allowed_collections,
            model_policy=model_policy,
        )
        self.base_version = await asyncio.to_thread(
            self.store.get_base_version,
            self.base_digest,
        )
        await self._ensure_entry(binding.active_version)
        return binding

    async def shutdown(self) -> None:
        async with self._entry_lock:
            entries = list(self._entries.values())
            self._entries.clear()
        seen: set[int] = set()
        for entry in entries:
            runtime_id = id(entry.runtime)
            if runtime_id in seen:
                continue
            seen.add(runtime_id)
            await close_runtime(entry.runtime)

    def _configuration_for_version(self, version: int) -> Configuration:
        config = copy.deepcopy(self.base_config)
        patches = self.store.get_patch_chain(version, base_digest=self.base_digest)
        for patch in patches:
            resolved_config = _resolve_environment_references(patch.config)
            try:
                config.set_provider_config(
                    patch.feature,
                    patch.provider,
                    resolved_config,
                )
            except ValueError as exc:
                raise RuntimeConfigRejected() from exc
        return config

    async def _build_runtime_for_version(
        self,
        version: int,
    ) -> RuntimeComponents:
        config = await asyncio.to_thread(self._configuration_for_version, version)
        try:
            return await asyncio.to_thread(self.runtime_factory, config)
        except RuntimeInitializationError:
            raise
        except Exception as exc:
            error = RuntimeInitializationError("runtime")
            error.__cause__ = exc
            raise error from exc

    async def _ensure_entry(self, version: int) -> _RuntimeEntry:
        async with self._entry_lock:
            existing = self._entries.get(version)
        if existing is not None:
            return existing

        async with self._build_lock:
            async with self._entry_lock:
                existing = self._entries.get(version)
            if existing is not None:
                return existing
            runtime = await self._build_runtime_for_version(version)
            entry = _RuntimeEntry(runtime=runtime)
            async with self._entry_lock:
                self._entries[version] = entry
            return entry

    async def acquire(self, tenant_id: str) -> RuntimeLease:
        if TENANT_ID_PATTERN.fullmatch(tenant_id) is None:
            raise RuntimeTenantNotFound()
        binding = await asyncio.to_thread(self.store.get_binding, tenant_id)
        if binding is None:
            raise RuntimeTenantNotFound()
        entry = await self._ensure_entry(binding.active_version)
        async with self._entry_lock:
            entry.references += 1
        context = RuntimeRequestContext(
            tenant_id=binding.tenant_id,
            runtime_version=binding.active_version,
            model_policy=binding.model_policy,
            allowed_collections=binding.allowed_collections,
            binding_revision=binding.revision,
        )
        await self._reap_inactive(exclude={binding.active_version})
        return RuntimeLease(self, entry.runtime, context)

    async def release(self, version: int) -> None:
        async with self._entry_lock:
            entry = self._entries.get(version)
            if entry is None:
                return
            entry.references = max(entry.references - 1, 0)
        await self._reap_inactive()

    async def _reap_inactive(self, *, exclude: set[int] | None = None) -> None:
        excluded = exclude or set()
        async with self._entry_lock:
            candidates = [
                version
                for version, entry in self._entries.items()
                if version not in excluded and entry.references == 0
            ]
        for version in candidates:
            if await asyncio.to_thread(self.store.version_is_active, version):
                continue
            async with self._entry_lock:
                entry = self._entries.get(version)
                if entry is None or entry.references != 0:
                    continue
                self._entries.pop(version, None)
            await close_runtime(entry.runtime)

    async def get_binding(self, tenant_id: str) -> TenantRuntimeBinding:
        binding = await asyncio.to_thread(self.store.get_binding, tenant_id)
        if binding is None:
            raise RuntimeTenantNotFound()
        return binding

    async def publish(
        self,
        *,
        tenant_id: str,
        patch: RuntimePatch,
        expected_version: int | None = None,
        allowed_collections: Sequence[str] | None = None,
        model_policy: str | None = None,
    ) -> TenantRuntimeBinding:
        if TENANT_ID_PATTERN.fullmatch(tenant_id) is None:
            raise RuntimeConfigRejected("Invalid tenant id.")
        validate_runtime_patch(patch)
        current = await asyncio.to_thread(self.store.get_binding, tenant_id)
        if current is None:
            if self.base_version is None:
                raise RuntimeVersionConflict()
            parent_version = self.base_version
            effective_expected = expected_version
            if effective_expected is not None:
                raise RuntimeVersionConflict()
            effective_allowed = normalize_allowed_collections(allowed_collections or ())
        else:
            parent_version = current.active_version
            effective_expected = (
                current.active_version if expected_version is None else expected_version
            )
            effective_allowed = normalize_allowed_collections(
                allowed_collections
                if allowed_collections is not None
                else current.allowed_collections
            )

        candidate_config = await asyncio.to_thread(
            self._configuration_for_version,
            parent_version,
        )
        try:
            candidate_config.set_provider_config(
                patch.feature,
                patch.provider,
                _resolve_environment_references(patch.config),
            )
        except ValueError as exc:
            raise RuntimeConfigRejected() from exc
        try:
            candidate_runtime = await asyncio.to_thread(self.runtime_factory, candidate_config)
        except RuntimeInitializationError:
            raise
        except Exception as exc:
            error = RuntimeInitializationError("runtime")
            error.__cause__ = exc
            raise error from exc

        effective_policy = (model_policy or derive_model_policy(candidate_config)).strip()[:160]
        try:
            binding = await asyncio.to_thread(
                self.store.publish,
                tenant_id=tenant_id,
                expected_version=effective_expected,
                parent_version=parent_version,
                patch=patch,
                allowed_collections=effective_allowed,
                model_policy=effective_policy,
            )
        except Exception:
            await close_runtime(candidate_runtime)
            raise

        entry = _RuntimeEntry(runtime=candidate_runtime)
        runtime_to_close: RuntimeComponents | None = None
        async with self._entry_lock:
            existing = self._entries.get(binding.active_version)
            if existing is None:
                self._entries[binding.active_version] = entry
            else:
                runtime_to_close = candidate_runtime
        if runtime_to_close is not None:
            await close_runtime(runtime_to_close)
        await self._reap_inactive(exclude={binding.active_version})
        return binding

    async def rollback(
        self,
        *,
        tenant_id: str,
        expected_version: int | None = None,
    ) -> TenantRuntimeBinding:
        current = await self.get_binding(tenant_id)
        active_version = current.active_version if expected_version is None else expected_version
        if current.previous_version is None:
            raise RuntimeRollbackUnavailable()
        await self._ensure_entry(current.previous_version)
        try:
            binding = await asyncio.to_thread(
                self.store.rollback,
                tenant_id=tenant_id,
                expected_version=active_version,
            )
        except Exception:
            await self._reap_inactive()
            raise
        await self._reap_inactive(exclude={binding.active_version})
        return binding
