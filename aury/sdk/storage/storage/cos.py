"""腾讯云 COS 存储实现（使用官方 cos-python-sdk-v5）。

使用腾讯云官方 SDK，避免 S3 兼容层的一些问题。
需要安装可选依赖: pip install cos-python-sdk-v5
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from typing import TYPE_CHECKING, Any

from aury.sdk.storage.exceptions import StorageBackendError, StorageNotFoundError

from .base import IStorage
from .models import StorageConfig, StorageFile, UploadResult

# 延迟导入 cos sdk（可选依赖）
try:
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosClientError, CosServiceError

    _COS_SDK_AVAILABLE = True
except ImportError:
    _COS_SDK_AVAILABLE = False
    if TYPE_CHECKING:
        from qcloud_cos import CosConfig, CosS3Client
        from qcloud_cos.cos_exception import CosClientError, CosServiceError
    else:
        CosConfig = None
        CosS3Client = None
        CosClientError = Exception
        CosServiceError = Exception


class COSStorage(IStorage):
    """腾讯云 COS 存储实现（使用官方 SDK）。

    相比 S3Storage，使用腾讯云官方 SDK 可以避免一些兼容性问题。
    支持全球加速域名和自定义域名。
    """

    def __init__(self, config: StorageConfig) -> None:
        """初始化 COS 存储。

        Args:
            config: 存储配置
        """
        if not _COS_SDK_AVAILABLE:
            raise ImportError(
                "cos-python-sdk-v5 未安装。请安装可选依赖: pip install 'aury-sdk-storage[cos]'"
            )

        self._config = config
        self._client: CosS3Client | None = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """确保已初始化。"""
        if not self._initialized:
            # 构建 CosConfig
            cos_config_kwargs: dict[str, Any] = {
                "Region": self._config.region,
                "SecretId": self._config.access_key_id,
                "SecretKey": self._config.access_key_secret,
                "Scheme": "https",
            }

            # 如果有 session_token，添加 Token
            if self._config.session_token:
                cos_config_kwargs["Token"] = self._config.session_token

            # 如果指定了 endpoint，使用 Endpoint 初始化（全球加速或自定义域名）
            if self._config.endpoint:
                # 从 endpoint URL 提取域名
                endpoint = self._config.endpoint
                if endpoint.startswith("https://"):
                    endpoint = endpoint[8:]
                elif endpoint.startswith("http://"):
                    endpoint = endpoint[7:]
                # 去除尾部斜杠
                endpoint = endpoint.rstrip("/")

                cos_config_kwargs["Endpoint"] = endpoint
                # 使用 Endpoint 时 Region 可为 None
                cos_config_kwargs["Region"] = self._config.region or None

            cos_cfg = CosConfig(**cos_config_kwargs)
            self._client = CosS3Client(cos_cfg)
            self._initialized = True

    def _get_bucket(self, bucket_name: str | None) -> str:
        """获取桶名。"""
        bucket = bucket_name or self._config.bucket_name
        if not bucket:
            raise StorageBackendError("桶名未指定")
        return bucket

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
            # 自定义端点
            endpoint = self._config.endpoint.rstrip("/")
            return f"{endpoint}/{bucket}/{object_name}"
        elif self._config.region:
            # 标准 COS 域名
            return f"https://{bucket}.cos.{self._config.region}.myqcloud.com/{object_name}"
        else:
            # 回退
            return f"cos://{bucket}/{object_name}"

    async def upload_file(
        self,
        file: StorageFile,
        *,
        bucket_name: str | None = None,
    ) -> UploadResult:
        """上传文件。"""
        self._ensure_initialized()
        bucket = self._get_bucket(bucket_name or file.bucket_name)
        data = self._read_file_data(file)

        # 构建上传参数
        put_kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": file.object_name,
            "Body": data,
        }

        if file.content_type:
            put_kwargs["ContentType"] = file.content_type

        if file.metadata:
            # COS SDK 支持自定义元数据，以 x-cos-meta- 前缀存储
            put_kwargs["Metadata"] = file.metadata

        # COS SDK 是同步的，使用 run_in_executor 包装
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.put_object(**put_kwargs),
            )
        except CosServiceError as e:
            raise StorageBackendError(
                f"COS 上传失败: [{e.get_error_code()}] {e.get_error_msg()}"
            ) from e
        except CosClientError as e:
            raise StorageBackendError(f"COS 客户端错误: {e}") from e

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
        self._ensure_initialized()
        bucket = self._get_bucket(bucket_name)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._client.delete_object(Bucket=bucket, Key=object_name),
            )
        except CosServiceError as e:
            raise StorageBackendError(
                f"COS 删除失败: [{e.get_error_code()}] {e.get_error_msg()}"
            ) from e
        except CosClientError as e:
            raise StorageBackendError(f"COS 客户端错误: {e}") from e

    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。"""
        self._ensure_initialized()
        bucket = self._get_bucket(bucket_name)

        if expires_in:
            # 生成预签名 URL
            loop = asyncio.get_event_loop()
            try:
                url = await loop.run_in_executor(
                    None,
                    lambda: self._client.get_presigned_url(
                        Bucket=bucket,
                        Key=object_name,
                        Method="GET",
                        Expired=expires_in,
                    ),
                )
                return url
            except (CosServiceError, CosClientError) as e:
                raise StorageBackendError(f"生成预签名 URL 失败: {e}") from e

        return self._build_url(bucket, object_name)

    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。"""
        self._ensure_initialized()
        bucket = self._get_bucket(bucket_name)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._client.head_object(Bucket=bucket, Key=object_name),
            )
            return True
        except CosServiceError as e:
            # 404 表示不存在
            if e.get_error_code() == "NoSuchKey" or e.get_status_code() == 404:
                return False
            raise StorageBackendError(
                f"COS 检查文件失败: [{e.get_error_code()}] {e.get_error_msg()}"
            ) from e
        except CosClientError:
            return False

    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。"""
        self._ensure_initialized()
        bucket = self._get_bucket(bucket_name)

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.get_object(Bucket=bucket, Key=object_name),
            )
            # response['Body'] 是一个 StreamBody 对象
            body = response["Body"]
            # 读取全部内容
            content = await loop.run_in_executor(None, body.get_raw_stream().read)
            return content
        except CosServiceError as e:
            if e.get_error_code() == "NoSuchKey" or e.get_status_code() == 404:
                raise StorageNotFoundError(f"文件不存在: {object_name}") from e
            raise StorageBackendError(
                f"COS 下载失败: [{e.get_error_code()}] {e.get_error_msg()}"
            ) from e
        except CosClientError as e:
            raise StorageBackendError(f"COS 客户端错误: {e}") from e


__all__ = [
    "COSStorage",
]
