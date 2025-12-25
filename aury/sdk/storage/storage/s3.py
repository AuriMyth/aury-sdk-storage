"""S3 兼容存储实现。

支持 AWS S3、MinIO、腾讯云 COS、阿里云 OSS 等 S3 兼容存储。
需要安装可选依赖: pip install aurimyth-storage-sdk[aws]
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aury.sdk.storage.exceptions import StorageBackendError

from .base import IStorage
from .models import StorageConfig, StorageFile, UploadResult

# 延迟导入 aioboto3（可选依赖）
try:
    import aioboto3
    from botocore.config import Config as BotoConfig

    _AIOBOTO3_AVAILABLE = True
except ImportError:
    _AIOBOTO3_AVAILABLE = False
    if TYPE_CHECKING:
        import aioboto3
        from botocore.config import Config as BotoConfig
    else:
        aioboto3 = None
        BotoConfig = None


class S3Storage(IStorage):
    """S3 兼容存储实现。"""

    def __init__(self, config: StorageConfig) -> None:
        """初始化 S3 存储。

        Args:
            config: 存储配置
        """
        if not _AIOBOTO3_AVAILABLE:
            raise ImportError(
                "aioboto3 未安装。请安装可选依赖: pip install 'aurimyth-storage-sdk[aws]'"
            )

        self._config = config
        self._session: aioboto3.Session | None = None
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """确保已初始化。"""
        if not self._initialized:
            self._session = aioboto3.Session()
            self._initialized = True

    def _get_bucket(self, bucket_name: str | None) -> str:
        """获取桶名。"""
        bucket = bucket_name or self._config.bucket_name
        if not bucket:
            raise StorageBackendError("桶名未指定")
        return bucket

    def _get_client_kwargs(self) -> dict[str, Any]:
        """获取 S3 客户端参数。"""
        kwargs: dict[str, Any] = {}

        if self._config.access_key_id:
            kwargs["aws_access_key_id"] = self._config.access_key_id
        if self._config.access_key_secret:
            kwargs["aws_secret_access_key"] = self._config.access_key_secret
        if self._config.session_token:
            kwargs["aws_session_token"] = self._config.session_token
        if self._config.endpoint:
            kwargs["endpoint_url"] = self._config.endpoint
        if self._config.region:
            kwargs["region_name"] = self._config.region

        # S3 配置
        style = self._config.addressing_style
        if style not in ("virtual", "path"):
            style = "virtual"

        kwargs["config"] = BotoConfig(
            s3={"addressing_style": style, "signature_version": "s3v4"}
        )

        return kwargs

    async def _get_client(self):
        """获取 S3 客户端上下文管理器。"""
        await self._ensure_initialized()
        return self._session.client("s3", **self._get_client_kwargs())

    def _read_file_data(self, file: StorageFile) -> bytes:
        """读取文件数据。"""
        if file.data is None:
            return b""
        if isinstance(file.data, bytes):
            return file.data
        return file.data.read()

    def _build_url(self, bucket: str, object_name: str) -> str:
        """构建对象 URL。"""
        if self._config.endpoint:
            # 自定义端点（MinIO、COS、OSS 等）
            endpoint = self._config.endpoint.rstrip("/")
            if self._config.addressing_style == "path":
                return f"{endpoint}/{bucket}/{object_name}"
            else:
                # virtual-hosted style
                # 对于 COS: https://{bucket}.cos.{region}.myqcloud.com/{key}
                # 简化处理：使用 path style
                return f"{endpoint}/{bucket}/{object_name}"
        else:
            # AWS S3
            return f"s3://{bucket}/{object_name}"

    async def upload_file(
        self,
        file: StorageFile,
        *,
        bucket_name: str | None = None,
    ) -> UploadResult:
        """上传文件。"""
        bucket = self._get_bucket(bucket_name or file.bucket_name)
        data = self._read_file_data(file)

        extra_args: dict[str, Any] = {}
        if file.content_type:
            extra_args["ContentType"] = file.content_type
        if file.metadata:
            extra_args["Metadata"] = file.metadata

        async with await self._get_client() as client:
            response = await client.put_object(
                Bucket=bucket,
                Key=file.object_name,
                Body=data,
                **extra_args,
            )

            etag = response.get("ETag", "").strip('"')

            return UploadResult(
                url=self._build_url(bucket, file.object_name),
                bucket_name=bucket,
                object_name=file.object_name,
                etag=etag or None,
            )

    async def upload_files(
        self,
        files: list[StorageFile],
        *,
        bucket_name: str | None = None,
    ) -> list[UploadResult]:
        """批量上传文件。"""
        tasks = [self.upload_file(f, bucket_name=bucket_name) for f in files]
        return await asyncio.gather(*tasks)

    async def delete_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> None:
        """删除文件。"""
        bucket = self._get_bucket(bucket_name)

        async with await self._get_client() as client:
            await client.delete_object(Bucket=bucket, Key=object_name)

    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。"""
        bucket = self._get_bucket(bucket_name)

        if expires_in:
            # 生成预签名 URL
            async with await self._get_client() as client:
                url = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": object_name},
                    ExpiresIn=expires_in,
                )
                return url

        return self._build_url(bucket, object_name)

    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。"""
        bucket = self._get_bucket(bucket_name)

        try:
            async with await self._get_client() as client:
                await client.head_object(Bucket=bucket, Key=object_name)
                return True
        except Exception:
            return False

    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。"""
        bucket = self._get_bucket(bucket_name)

        async with await self._get_client() as client:
            response = await client.get_object(Bucket=bucket, Key=object_name)
            async with response["Body"] as stream:
                return await stream.read()


__all__ = [
    "S3Storage",
]
