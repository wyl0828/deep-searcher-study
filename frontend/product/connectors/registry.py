"""Connector registry: source_type -> connector factory (v0.6)."""

from __future__ import annotations

from frontend.product.connectors.base import Connector, ConnectorError
from frontend.product.connectors.local_directory import (
    SOURCE_TYPE_LOCAL_DIRECTORY,
    LocalDirectoryConnector,
)


class ConnectorUnsupportedSourceError(ConnectorError):
    pass


def create_connector(source_type: str, config: dict) -> Connector:
    if source_type == SOURCE_TYPE_LOCAL_DIRECTORY:
        return LocalDirectoryConnector(**config)
    raise ConnectorUnsupportedSourceError(f"不支持的连接器类型: {source_type}")
