from importlib import import_module

from .exceptions import (
    CollectionNotFound,
    VectorDBError,
    VectorDBUnavailable,
    VectorDimensionMismatch,
    VectorInitializationFailed,
    VectorInsertFailed,
    VectorListFailed,
    VectorSearchFailed,
)

_LAZY_IMPORTS = {
    "AzureSearch": (".azure_search", "AzureSearch"),
    "Milvus": (".milvus", "Milvus"),
    "OracleDB": (".oracle", "OracleDB"),
    "Qdrant": (".qdrant", "Qdrant"),
    "RetrievalResult": (".base", "RetrievalResult"),
}


def __getattr__(name):
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = [
    "AzureSearch",
    "CollectionNotFound",
    "Milvus",
    "OracleDB",
    "Qdrant",
    "RetrievalResult",
    "VectorDBError",
    "VectorDBUnavailable",
    "VectorDimensionMismatch",
    "VectorInitializationFailed",
    "VectorInsertFailed",
    "VectorListFailed",
    "VectorSearchFailed",
]
