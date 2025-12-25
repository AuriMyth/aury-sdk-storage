"""存储接口和本地存储实现。"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import BinaryIO

from .models import StorageFile, UploadResult


class IStorage(ABC):
    """存储接口。

    所有存储后端必须实现此接口。
    """

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

    def _read_file_data(self, file: StorageFile) -> bytes:
        """读取文件数据。"""
        if file.data is None:
            return b""
        if isinstance(file.data, bytes):
            return file.data
        # BinaryIO
        return file.data.read()

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
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # 写入文件
        data = self._read_file_data(file)
        with open(file_path, "wb") as f:
            f.write(data)

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

        if os.path.exists(file_path):
            os.remove(file_path)

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
        return os.path.exists(file_path)

    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。"""
        bucket = bucket_name or "default"
        file_path = self._get_file_path(bucket, object_name)

        with open(file_path, "rb") as f:
            return f.read()


__all__ = [
    "IStorage",
    "LocalStorage",
]
