from __future__ import annotations

import io
import os
import threading

import pytest

from aury.sdk.storage.storage.base import (
    LocalStorage,
    get_storage_file_data_size,
    iter_storage_file_data_chunks,
    read_storage_file_data,
)
from aury.sdk.storage.storage.cos import COSStorage
from aury.sdk.storage.storage.models import StorageBackend, StorageConfig, StorageFile


class _ThreadRecordingBytesIO(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_thread_name: str | None = None

    def read(self, *args, **kwargs) -> bytes:
        self.read_thread_name = threading.current_thread().name
        return super().read(*args, **kwargs)


class _NonSeekableBytesIO(_ThreadRecordingBytesIO):
    def seekable(self) -> bool:
        return False

    def seek(self, *args, **kwargs):
        raise io.UnsupportedOperation("not seekable")

    def tell(self):
        raise io.UnsupportedOperation("not seekable")


class _ThreadRecordingBufferedReader(io.BufferedReader):
    def __init__(self, raw) -> None:
        super().__init__(raw)
        self.read_thread_name: str | None = None

    def read(self, *args, **kwargs) -> bytes:
        self.read_thread_name = threading.current_thread().name
        return super().read(*args, **kwargs)


def test_storage_file_normalizes_bytes_like_payloads() -> None:
    storage_file = StorageFile(object_name="x.txt", data=memoryview(b"hello"))

    assert storage_file.data == b"hello"


@pytest.mark.asyncio
async def test_read_storage_file_data_reads_binary_stream_off_event_loop() -> None:
    main_thread = threading.current_thread().name
    stream = _ThreadRecordingBytesIO(b"hello")
    storage_file = StorageFile(object_name="x.txt", data=stream)

    payload = await read_storage_file_data(storage_file)

    assert payload == b"hello"
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread


@pytest.mark.asyncio
async def test_iter_storage_file_data_chunks_preserves_size_and_streams() -> None:
    main_thread = threading.current_thread().name
    stream = _ThreadRecordingBytesIO(b"0123456789")
    stream.seek(2)
    storage_file = StorageFile(object_name="x.txt", data=stream)

    size = await get_storage_file_data_size(storage_file)
    chunks = [
        chunk
        async for chunk in iter_storage_file_data_chunks(storage_file, chunk_size=3)
    ]

    assert size == 8
    assert chunks == [b"234", b"567", b"89"]
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread


@pytest.mark.asyncio
async def test_iter_storage_file_data_chunks_accepts_regular_binary_file(tmp_path) -> None:
    main_thread = threading.current_thread().name
    file_path = tmp_path / "payload.bin"
    file_path.write_bytes(b"abcdef")

    with file_path.open("rb", buffering=0) as raw:
        stream = _ThreadRecordingBufferedReader(raw)
        storage_file = StorageFile(object_name="x.txt", data=stream)

        size = await get_storage_file_data_size(storage_file)
        chunks = [
            chunk
            async for chunk in iter_storage_file_data_chunks(storage_file, chunk_size=2)
        ]

    assert size == 6
    assert chunks == [b"ab", b"cd", b"ef"]
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread


@pytest.mark.asyncio
async def test_get_storage_file_data_size_rejects_non_seekable_stream() -> None:
    stream = _NonSeekableBytesIO(b"payload")

    with pytest.raises(TypeError, match="seekable"):
        await get_storage_file_data_size(StorageFile(object_name="x.txt", data=stream))


@pytest.mark.asyncio
async def test_local_storage_upload_accepts_binary_stream(tmp_path) -> None:
    storage = LocalStorage(base_path=str(tmp_path))
    stream = io.BytesIO(b"payload")

    result = await storage.upload_file(
        StorageFile(object_name="dir/file.txt", data=stream, content_type="text/plain"),
        bucket_name="bucket",
    )

    assert result.object_name == "dir/file.txt"
    assert result.bucket_name == "bucket"
    assert await storage.file_exists("dir/file.txt", bucket_name="bucket") is True
    assert await storage.download_file("dir/file.txt", bucket_name="bucket") == b"payload"
    assert os.path.exists(tmp_path / "bucket" / "dir" / "file.txt")


@pytest.mark.asyncio
async def test_local_storage_list_and_delete(tmp_path) -> None:
    storage = LocalStorage(base_path=str(tmp_path))
    await storage.upload_file(StorageFile(object_name="a/one.txt", data=b"1"), bucket_name="bucket")
    await storage.upload_file(StorageFile(object_name="b/two.txt", data=b"2"), bucket_name="bucket")

    assert await storage.list_objects(prefix="a/", bucket_name="bucket") == ["a/one.txt"]

    await storage.delete_file("a/one.txt", bucket_name="bucket")

    assert await storage.file_exists("a/one.txt", bucket_name="bucket") is False
    assert await storage.list_objects(bucket_name="bucket") == ["b/two.txt"]


@pytest.mark.asyncio
async def test_cos_upload_reads_binary_stream_off_event_loop() -> None:
    main_thread = threading.current_thread().name
    stream = _ThreadRecordingBytesIO(b"cos-payload")
    seen: dict[str, object] = {}
    uploaded_chunks: list[bytes] = []

    class _Response:
        status = 200
        headers = {"etag": '"etag-cos"'}

        def __init__(self, data) -> None:
            self._data = data

        async def __aenter__(self):
            async for chunk in self._data:
                uploaded_chunks.append(chunk)
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def text(self) -> str:
            return ""

    class _Session:
        def put(self, url, *, data, headers):
            seen["url"] = url
            seen["data"] = data
            seen["headers"] = headers
            return _Response(data)

    storage = COSStorage(
        StorageConfig(
            backend=StorageBackend.COS,
            bucket_name="bucket-123",
            region="ap-guangzhou",
            access_key_id="id",
            access_key_secret="secret",
        )
    )

    async def _fake_session() -> _Session:
        return _Session()

    storage._ensure_session = _fake_session

    result = await storage.upload_file(StorageFile(object_name="x.txt", data=stream))

    assert result.etag == "etag-cos"
    assert uploaded_chunks == [b"cos-payload"]
    assert seen["headers"]["content-length"] == str(len(b"cos-payload"))
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread


@pytest.mark.asyncio
async def test_s3_upload_reads_binary_stream_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from aury.sdk.storage.storage import s3 as s3_module

    monkeypatch.setattr(s3_module, "_AIOBOTO3_AVAILABLE", True)
    main_thread = threading.current_thread().name
    stream = _ThreadRecordingBytesIO(b"s3-payload")
    seen: dict[str, object] = {}

    class _Client:
        async def put_object(self, **kwargs):
            seen.update(kwargs)
            return {"ETag": '"etag-s3"'}

    class _ClientContext:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    storage = s3_module.S3Storage(
        StorageConfig(backend=StorageBackend.AWS, bucket_name="bucket")
    )

    async def _fake_client() -> _ClientContext:
        return _ClientContext()

    storage._get_client = _fake_client

    result = await storage.upload_file(StorageFile(object_name="x.txt", data=stream))

    assert result.etag == "etag-s3"
    assert seen["Body"] == b"s3-payload"
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread
