"""Storage 工厂。"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar

from .base import IStorage, LocalStorage
from .models import StorageBackend, StorageConfig


class StorageType(str, Enum):
    """Storage 类型。"""

    LOCAL = "local"   # 本地文件系统
    COS = "cos"       # 腾讯云 COS
    OSS = "oss"       # 阿里云 OSS
    AWS = "aws"       # AWS S3
    MINIO = "minio"   # MinIO


class StorageFactory:
    """Storage 工厂。

    支持两种密钥命名风格（可互换使用）：
    - AWS 风格: access_key_id / access_key_secret
    - 腾讯云风格: secret_id / secret_key

    使用示例:
        # 方式1: 使用 AWS 风格密钥名
        storage = StorageFactory.create(
            StorageType.COS,
            bucket_name="my-bucket-1250000000",
            region="ap-beijing",
            access_key_id="...",
            access_key_secret="...",
        )

        # 方式2: 使用腾讯云风格密钥名（与 STS 配置一致）
        storage = StorageFactory.create(
            StorageType.COS,
            bucket_name="my-bucket-1250000000",
            region="ap-beijing",
            secret_id="...",
            secret_key="...",
        )

        # 方式3: 从 STS 凭证创建 COS 存储
        credentials = await sts_provider.get_credentials(request)
        storage = StorageFactory.from_sts_credentials(credentials)
    """

    # 存储类型到后端枚举的映射
    _backend_mapping: ClassVar[dict[StorageType, StorageBackend]] = {
        StorageType.LOCAL: StorageBackend.LOCAL,
        StorageType.COS: StorageBackend.COS,
        StorageType.OSS: StorageBackend.OSS,
        StorageType.AWS: StorageBackend.AWS,
        StorageType.MINIO: StorageBackend.MINIO,
    }

    @classmethod
    def create(cls, storage_type: StorageType, **kwargs: Any) -> IStorage:
        """创建 Storage 实例。

        Args:
            storage_type: 存储类型
            **kwargs: 配置参数，将传递给 StorageConfig

        Returns:
            Storage 实例
        """
        # 设置对应的 backend
        backend = cls._backend_mapping.get(storage_type)
        if backend is None:
            available = ", ".join(t.value for t in StorageType)
            raise ValueError(f"Storage 类型 '{storage_type.value}' 不支持。可用: {available}")

        kwargs["backend"] = backend
        config = StorageConfig(**kwargs)
        return cls.from_config(config)

    @classmethod
    def from_config(cls, config: StorageConfig) -> IStorage:
        """从 StorageConfig 创建 Storage。

        Args:
            config: 存储配置

        Returns:
            Storage 实例
        """
        if config.backend == StorageBackend.LOCAL:
            return LocalStorage(base_path=config.base_path or "./storage")

        elif config.backend == StorageBackend.COS:
            # 优先使用 COS 原生 SDK
            try:
                from .cos import COSStorage

                return COSStorage(config)
            except ImportError:
                # 如果 COS SDK 未安装，回退到 S3 兼容模式
                try:
                    from .s3 import S3Storage

                    return S3Storage(config)
                except ImportError:
                    raise ImportError(
                        "COS 存储需要安装依赖。请选择:\n"
                        "  1. pip install 'aury-sdk-storage[cos]'  # 推荐，使用腾讯云官方 SDK\n"
                        "  2. pip install 'aury-sdk-storage[aws]'  # S3 兼容模式"
                    )

        elif config.backend in (StorageBackend.OSS, StorageBackend.AWS, StorageBackend.MINIO):
            # OSS、AWS、MinIO 使用 S3 兼容实现
            try:
                from .s3 import S3Storage

                return S3Storage(config)
            except ImportError:
                raise ImportError(
                    "存储需要安装依赖: pip install 'aury-sdk-storage[aws]'"
                )

        else:
            raise ValueError(f"不支持的存储后端: {config.backend}")

    @classmethod
    def from_sts_credentials(
        cls,
        credentials: Any,  # STSCredentials，避免循环导入
        *,
        storage_type: StorageType = StorageType.COS,
    ) -> IStorage:
        """从 STS 凭证创建 Storage。

        这是一个便捷方法，用于快速从 STS 临时凭证创建存储实例。

        Args:
            credentials: STS 凭证（STSCredentials 实例）
            storage_type: 存储类型，默认 COS

        Returns:
            Storage 实例

        Example:
            credentials = await sts_provider.get_credentials(request)
            storage = StorageFactory.from_sts_credentials(credentials)
            await storage.upload_file(...)
        """
        # 从凭证提取配置
        config_kwargs: dict[str, Any] = {
            "access_key_id": credentials.access_key_id,
            "access_key_secret": credentials.secret_access_key,
            "session_token": credentials.session_token,
        }

        if credentials.bucket:
            config_kwargs["bucket_name"] = credentials.bucket
        if credentials.region:
            config_kwargs["region"] = credentials.region
        if credentials.endpoint:
            config_kwargs["endpoint"] = credentials.endpoint

        return cls.create(storage_type, **config_kwargs)

    @classmethod
    def get_available_storage_types(cls) -> list[StorageType]:
        """获取可用的 Storage 类型列表。"""
        return list(StorageType)


__all__ = [
    "StorageType",
    "StorageFactory",
]
