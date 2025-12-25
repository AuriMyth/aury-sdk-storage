"""存储相关数据模型（Pydantic）。"""

from __future__ import annotations

from enum import Enum
from io import BytesIO
from typing import Annotated, Any, BinaryIO

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StorageBackend(str, Enum):
    """存储后端类型。"""

    S3 = "s3"
    LOCAL = "local"
    OSS = "oss"  # 阿里云 OSS（S3 兼容）
    COS = "cos"  # 腾讯云 COS（S3 兼容）


class StorageFile(BaseModel):
    """存储文件对象。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    object_name: str = Field(..., description="对象名/路径")
    bucket_name: str | None = Field(default=None, description="桶名（可选，使用默认桶）")
    data: BinaryIO | BytesIO | bytes | None = Field(default=None, description="文件数据")
    content_type: str | None = Field(default=None, description="MIME 类型")
    metadata: dict[str, str] | None = Field(default=None, description="元数据")


class StorageConfig(BaseModel):
    """存储配置。"""

    model_config = ConfigDict(frozen=True)

    backend: StorageBackend = Field(..., description="存储后端类型")

    # 通用配置
    bucket_name: str | None = Field(default=None, description="默认桶名")
    region: str | None = Field(default=None, description="区域")

    # S3 兼容配置
    access_key_id: str | None = Field(default=None, description="访问密钥 ID")
    access_key_secret: str | None = Field(default=None, description="访问密钥")
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
