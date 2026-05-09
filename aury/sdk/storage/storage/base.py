"""存储接口和本地存储实现。"""

from __future__ import annotations

import asyncio
import os
import aiofiles
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from typing import Any

from .models import StorageFile, UploadResult

_DEFAULT_STREAM_CHUNK_SIZE = 8 * 1024 * 1024


async def _run_blocking_io[T](func: Callable[[], T]) -> T:
    """Run blocking filesystem or stream work outside the event loop."""

    return await asyncio.to_thread(func)


def _coerce_payload_chunk(payload: Any) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, str):
        return payload.encode("utf-8")
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return bytes(payload)
    raise TypeError(
        "StorageFile.data.read() returned unsupported type: "
        f"{type(payload).__name__}"
    )


async def read_storage_file_data(file: StorageFile) -> bytes:
    """Read StorageFile.data without blocking the event loop.

    Cloud backends need bytes for their HTTP clients. When callers pass a
    synchronous BinaryIO/BytesIO stream, read it in a worker thread so async
    upload paths do not stall the server event loop.
    """

    data = file.data
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data

    return _coerce_payload_chunk(await _run_blocking_io(data.read))


async def get_storage_file_data_size(file: StorageFile) -> int:
    """Return byte size to upload without consuming stream data.

    For stream inputs, the size is measured from the current position to EOF,
    and the original position is restored. COS PUT Object requires a signed
    ``content-length`` header, so non-seekable streams are rejected instead of
    being buffered into memory.
    """

    data = file.data
    if data is None:
        return 0
    if isinstance(data, bytes):
        return len(data)
    if not hasattr(data, "tell") or not hasattr(data, "seek"):
        raise TypeError("StorageFile.data stream must be seekable to determine content length")
    if hasattr(data, "seekable") and not data.seekable():
        raise TypeError("StorageFile.data stream must be seekable to determine content length")

    def _size() -> int:
        try:
            current = data.tell()
            try:
                data.seek(0, os.SEEK_END)
                end = data.tell()
                return max(0, int(end) - int(current))
            finally:
                data.seek(current, os.SEEK_SET)
        except OSError as exc:
            raise TypeError(
                "StorageFile.data stream must be seekable to determine content length"
            ) from exc

    return await _run_blocking_io(_size)


async def iter_storage_file_data_chunks(
    file: StorageFile,
    *,
    chunk_size: int = _DEFAULT_STREAM_CHUNK_SIZE,
) -> AsyncIterator[bytes]:
    """Yield StorageFile.data in chunks without buffering the full object."""

    data = file.data
    if data is None:
        return
    if isinstance(data, bytes):
        if data:
            yield data
        return

    while True:
        chunk = _coerce_payload_chunk(
            await _run_blocking_io(lambda: data.read(chunk_size))
        )
        if not chunk:
            break
        yield chunk


class IStorage(ABC):
    """存储接口。

    所有存储后端必须实现此接口。
    """

    @abstractmethod
    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket_name: str | None = None,
    ) -> list[str]:
        """列出对象名（按 prefix 过滤）。"""
        pass

    @abstractmethod
    async def upload_file(
        self,
        file: StorageFile,
        *,
        bucket_name: str | None = None,
    ) -> UploadResult:
        """上传文件。

        Args:
            file: 文件对象
            bucket_name: 桶名（可选，使用默认桶）

        Returns:
            上传结果
        """
        pass

    @abstractmethod
    async def upload_files(
        self,
        files: list[StorageFile],
        *,
        bucket_name: str | None = None,
    ) -> list[UploadResult]:
        """批量上传文件。

        Args:
            files: 文件列表
            bucket_name: 桶名（可选）

        Returns:
            上传结果列表
        """
        pass

    @abstractmethod
    async def delete_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> None:
        """删除文件。

        Args:
            object_name: 对象名
            bucket_name: 桶名（可选）
        """
        pass

    @abstractmethod
    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。

        Args:
            object_name: 对象名
            bucket_name: 桶名（可选）
            expires_in: 过期时间（秒，用于生成预签名 URL）

        Returns:
            文件 URL
        """
        pass

    @abstractmethod
    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。

        Args:
            object_name: 对象名
            bucket_name: 桶名（可选）

        Returns:
            是否存在
        """
        pass

    @abstractmethod
    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。

        Args:
            object_name: 对象名
            bucket_name: 桶名（可选）

        Returns:
            文件内容
        """
        pass

    @abstractmethod
    async def append_file(
        self,
        object_name: str,
        data: bytes,
        *,
        bucket_name: str | None = None,
        position: int | None = None,
    ) -> int:
        """追加内容到文件。

        如果文件不存在则创建。返回追加后的文件位置（下次追加的 position）。

        Args:
            object_name: 对象名
            data: 追加的数据
            bucket_name: 桶名（可选）
            position: 追加位置（None 表示追加到末尾）

        Returns:
            追加后的文件位置
        """
        pass


class LocalStorage(IStorage):
    """本地文件系统存储实现。"""

    def __init__(self, base_path: str = "./storage") -> None:
        """初始化本地存储。

        Args:
            base_path: 基础路径
        """
        self._base_path = os.path.abspath(base_path)
        os.makedirs(self._base_path, exist_ok=True)

    def _get_file_path(self, bucket: str, object_name: str) -> str:
        """获取文件完整路径。"""
        return os.path.join(self._base_path, bucket, object_name)

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket_name: str | None = None,
    ) -> list[str]:
        """列出对象名（相对于 bucket 根目录）。"""
        bucket = bucket_name or "default"
        bucket_root = os.path.join(self._base_path, bucket)

        def _list() -> list[str]:
            if not os.path.isdir(bucket_root):
                return []

            objects: list[str] = []
            for root, _, files in os.walk(bucket_root):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    rel = os.path.relpath(file_path, bucket_root).replace(os.sep, "/")
                    if not prefix or rel.startswith(prefix):
                        objects.append(rel)
            objects.sort()
            return objects

        return await _run_blocking_io(_list)

    async def upload_file(
        self,
        file: StorageFile,
        *,
        bucket_name: str | None = None,
    ) -> UploadResult:
        """上传文件。"""
        bucket = bucket_name or file.bucket_name or "default"
        file_path = self._get_file_path(bucket, file.object_name)

        # 确保目录存在
        await _run_blocking_io(
            lambda: os.makedirs(os.path.dirname(file_path), exist_ok=True)
        )

        # 写入文件
        data = await read_storage_file_data(file)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(data)

        return UploadResult(
            url=f"file://{file_path}",
            bucket_name=bucket,
            object_name=file.object_name,
        )

    async def upload_files(
        self,
        files: list[StorageFile],
        *,
        bucket_name: str | None = None,
    ) -> list[UploadResult]:
        """批量上传文件。"""
        results = []
        for f in files:
            result = await self.upload_file(f, bucket_name=bucket_name)
            results.append(result)
        return results

    async def delete_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> None:
        """删除文件。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)

        def _delete() -> None:
            if os.path.exists(file_path):
                os.remove(file_path)

        await _run_blocking_io(_delete)

    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)
        return f"file://{file_path}"

    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)
        return await _run_blocking_io(lambda: os.path.exists(file_path))

    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)

        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def append_file(
        self,
        object_name: str,
        data: bytes,
        *,
        bucket_name: str | None = None,
        position: int | None = None,
    ) -> int:
        """追加内容到文件。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)

        # 确保目录存在
        await _run_blocking_io(
            lambda: os.makedirs(os.path.dirname(file_path), exist_ok=True)
        )

        # 追加写入
        async with aiofiles.open(file_path, "ab") as f:
            await f.write(data)
            return await f.tell()


__all__ = [
    "get_storage_file_data_size",
    "IStorage",
    "iter_storage_file_data_chunks",
    "LocalStorage",
    "read_storage_file_data",
]
