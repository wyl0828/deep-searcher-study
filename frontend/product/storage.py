from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol

from frontend.product.db import DATA_DIR

UPLOAD_DIR = DATA_DIR / "uploads"


class StorageError(RuntimeError):
    pass


class ObjectStorage(Protocol):
    storage_type: str
    bucket: str | None

    def put_staged(self, source: Path, *, knowledge_base_id: str) -> str: ...

    @contextmanager
    def materialize(self, object_key: str) -> Iterator[Path]: ...

    def open(self, object_key: str) -> BinaryIO: ...

    def delete(self, object_key: str) -> None: ...


class LocalObjectStorage:
    storage_type = "local"
    bucket = None

    def __init__(self, root: Path = UPLOAD_DIR):
        self.root = root

    def _safe_path(self, object_key: str) -> Path:
        root = self.root.resolve()
        candidate = Path(object_key).resolve()
        if candidate == root or root not in candidate.parents:
            raise StorageError("local object path escapes the upload root")
        return candidate

    def put_staged(self, source: Path, *, knowledge_base_id: str) -> str:
        destination_dir = self.root / knowledge_base_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        try:
            destination_dir.chmod(0o700)
        except OSError:
            pass
        destination = destination_dir / f"{secrets.token_hex(24)}.pdf"
        os.replace(source, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        return str(destination)

    @contextmanager
    def materialize(self, object_key: str) -> Iterator[Path]:
        path = self._safe_path(object_key)
        if not path.is_file():
            raise FileNotFoundError(object_key)
        yield path

    def open(self, object_key: str) -> BinaryIO:
        return self._safe_path(object_key).open("rb")

    def delete(self, object_key: str) -> None:
        path = self._safe_path(object_key)
        if path.exists():
            path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass


class S3ObjectStorage:
    storage_type = "s3"

    def __init__(self, *, bucket: str | None = None):
        try:
            import boto3
        except ImportError as exc:
            raise StorageError("S3 storage requires boto3") from exc

        self.bucket = (bucket or os.environ.get("DEEPSEARCHER_S3_BUCKET", "")).strip()
        if not self.bucket:
            raise StorageError("DEEPSEARCHER_S3_BUCKET is required for S3 storage")
        endpoint_url = os.environ.get("DEEPSEARCHER_S3_ENDPOINT") or None
        region_name = os.environ.get("DEEPSEARCHER_S3_REGION") or None
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=os.environ.get("DEEPSEARCHER_S3_ACCESS_KEY") or None,
            aws_secret_access_key=os.environ.get("DEEPSEARCHER_S3_SECRET_KEY") or None,
        )

    @staticmethod
    def _key(knowledge_base_id: str) -> str:
        return f"knowledge-bases/{knowledge_base_id}/{secrets.token_hex(24)}.pdf"

    def put_staged(self, source: Path, *, knowledge_base_id: str) -> str:
        key = self._key(knowledge_base_id)
        try:
            self.client.upload_file(str(source), self.bucket, key)
        except Exception as exc:
            raise StorageError(f"failed to upload S3 object: {key}") from exc
        return key

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        error = response.get("Error")
        if not isinstance(error, dict):
            return False
        return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}

    @contextmanager
    def materialize(self, object_key: str) -> Iterator[Path]:
        temp_dir = Path(tempfile.mkdtemp(prefix="deepsearcher-object-"))
        destination = temp_dir / "document.pdf"
        try:
            try:
                self.client.download_file(self.bucket, object_key, str(destination))
            except Exception as exc:
                if self._is_missing(exc):
                    raise FileNotFoundError(object_key) from exc
                raise StorageError(f"failed to download S3 object: {object_key}") from exc
            yield destination
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def open(self, object_key: str) -> BinaryIO:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            if self._is_missing(exc):
                raise FileNotFoundError(object_key) from exc
            raise StorageError(f"failed to open S3 object: {object_key}") from exc
        return response["Body"]

    def delete(self, object_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            raise StorageError(f"failed to delete S3 object: {object_key}") from exc


def configured_storage_type() -> str:
    value = os.environ.get("DEEPSEARCHER_STORAGE_TYPE", "local").strip().lower()
    if value not in {"local", "s3"}:
        raise StorageError("DEEPSEARCHER_STORAGE_TYPE must be local or s3")
    return value


def get_object_storage(
    storage_type: str | None = None,
    *,
    local_root: Path = UPLOAD_DIR,
    bucket: str | None = None,
) -> ObjectStorage:
    selected = (storage_type or configured_storage_type()).strip().lower()
    if selected == "local":
        return LocalObjectStorage(local_root)
    if selected == "s3":
        return S3ObjectStorage(bucket=bucket)
    raise StorageError(f"unsupported storage type: {selected}")
