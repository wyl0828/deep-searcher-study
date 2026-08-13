from __future__ import annotations

import io
from pathlib import Path

import pytest

from frontend.product.storage import LocalObjectStorage, S3ObjectStorage, StorageError


class MissingObjectError(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3Client:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_file(self, source: str, bucket: str, key: str) -> None:
        self.objects[(bucket, key)] = Path(source).read_bytes()

    def download_file(self, bucket: str, key: str, destination: str) -> None:
        try:
            payload = self.objects[(bucket, key)]
        except KeyError as exc:
            raise MissingObjectError from exc
        Path(destination).write_bytes(payload)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        try:
            payload = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObjectError from exc
        return {"Body": io.BytesIO(payload)}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def s3_storage(client: FakeS3Client) -> S3ObjectStorage:
    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "documents"
    storage.client = client
    return storage


def test_local_storage_round_trip(tmp_path: Path) -> None:
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-local")
    storage = LocalObjectStorage(tmp_path / "uploads")

    key = storage.put_staged(staged, knowledge_base_id="kb-1")

    assert not staged.exists()
    with storage.materialize(key) as materialized:
        assert materialized.read_bytes() == b"%PDF-local"
    with storage.open(key) as stream:
        assert stream.read() == b"%PDF-local"
    storage.delete(key)
    assert not Path(key).exists()


def test_local_storage_rejects_paths_outside_root(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "uploads")

    with pytest.raises(StorageError, match="escapes"):
        storage.open(str(tmp_path / "other.pdf"))


def test_s3_storage_round_trip_uses_temporary_materialization(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = s3_storage(client)
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-s3")

    key = storage.put_staged(staged, knowledge_base_id="kb-1")

    assert key.startswith("knowledge-bases/kb-1/")
    with storage.materialize(key) as materialized:
        assert materialized.read_bytes() == b"%PDF-s3"
        temporary_path = materialized
    assert not temporary_path.exists()
    assert storage.open(key).read() == b"%PDF-s3"
    storage.delete(key)
    with pytest.raises(FileNotFoundError):
        storage.open(key)


def test_s3_storage_maps_client_failure_to_storage_error(tmp_path: Path) -> None:
    class FailingClient(FakeS3Client):
        def upload_file(self, source: str, bucket: str, key: str) -> None:
            raise RuntimeError("network failure")

    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"%PDF-s3")

    with pytest.raises(StorageError, match="failed to upload"):
        s3_storage(FailingClient()).put_staged(staged, knowledge_base_id="kb-1")
