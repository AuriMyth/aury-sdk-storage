"""存储相关数据模型（Pydantic）。"""

from __future__ import annotations

from enum import Enum
from io import BytesIO
from typing import Annotated, Any, BinaryIO

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StorageBackend(str, Enum):
    """存储后端类型。"""

    LOCAL = "local"   # 本地文件系统
    COS = "cos"       # 腾讯云 COS
    OSS = "oss"       # 阿里云 OSS
    AWS = "aws"       # AWS S3
    MINIO = "minio"   # MinIO


class StorageFile(BaseModel):
    """存储文件对象。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    object_name: str = Field(..., description="对象名/路径")
    bucket_name: str | None = Field(default=None, description="桶名（可选，使用默认桶）")
    data: BinaryIO | BytesIO | bytes | None = Field(default=None, description="文件数据")
    content_type: str | None = Field(default=None, description="MIME 类型")
    metadata: dict[str, str] | None = Field(default=None, description="元数据")


class StorageConfig(BaseModel):
    """存储配置。

    支持两种密钥命名风格（可互换使用）：
    - AWS 风格: access_key_id / access_key_secret
    - 腾讯云风格: secret_id / secret_key
    """

    model_config = ConfigDict(frozen=True)

    backend: StorageBackend = Field(..., description="存储后端类型")

    # 通用配置
    bucket_name: str | None = Field(default=None, description="默认桶名")
    region: str | None = Field(default=None, description="区域")

    # 密钥配置（支持两种命名风格）
    # AWS 风格
    access_key_id: str | None = Field(default=None, description="访问密钥 ID")
    access_key_secret: str | None = Field(default=None, description="访问密钥")
    # 腾讯云风格（别名）
    secret_id: str | None = Field(default=None, description="腾讯云 SecretId（等同 access_key_id）")
    secret_key: str | None = Field(default=None, description="腾讯云 SecretKey（等同 access_key_secret）")

    session_token: str | None = Field(default=None, description="会话令牌（STS 临时凭证）")
    endpoint: str | None = Field(default=None, description="端点 URL")
    addressing_style: Annotated[
        str, Field(description="S3 寻址风格（virtual/path）")
    ] = "virtual"

    # 本地存储配置
    base_path: str | None = Field(default=None, description="基础路径（本地存储）")

    # STS 配置（用于自动刷新凭证）
    role_arn: str | None = Field(default=None, description="STS AssumeRole 角色 ARN")
    role_session_name: str = Field(default="storage-sdk", description="STS 会话名")
    external_id: str | None = Field(default=None, description="STS ExternalId")
    sts_endpoint: str | None = Field(default=None, description="STS 端点")
    sts_region: str | None = Field(default=None, description="STS 区域")
    sts_duration_seconds: int = Field(default=3600, ge=900, le=43200, description="STS 凭证有效期")

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: dict) -> dict:
        """统一密钥命名：将 secret_id/secret_key 映射到 access_key_id/access_key_secret。"""
        if isinstance(data, dict):
            # secret_id -> access_key_id
            if data.get("secret_id") and not data.get("access_key_id"):
                data["access_key_id"] = data["secret_id"]
            # secret_key -> access_key_secret
            if data.get("secret_key") and not data.get("access_key_secret"):
                data["access_key_secret"] = data["secret_key"]
        return data

    @field_validator("addressing_style")
    @classmethod
    def validate_addressing_style(cls, v: str) -> str:
        if v not in ("virtual", "path"):
            return "virtual"
        return v


class UploadResult(BaseModel):
    """上传结果。"""

    model_config = ConfigDict(frozen=True)

    url: str = Field(..., description="文件 URL")
    bucket_name: str = Field(..., description="桶名")
    object_name: str = Field(..., description="对象名")
    etag: str | None = Field(default=None, description="ETag")


__all__ = [
    "StorageBackend",
    "StorageFile",
    "StorageConfig",
    "UploadResult",
]
