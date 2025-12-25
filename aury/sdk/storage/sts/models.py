"""STS 数据模型（Pydantic）。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionType(str, Enum):
    """允许的操作类型。"""

    READ = "read"
    WRITE = "write"
    ALL = "all"


class STSRequest(BaseModel):
    """STS 临时凭证请求。

    业务层传入的统一请求格式，SDK 内部翻译为各厂商的 Policy。
    """

    model_config = ConfigDict(frozen=True)

    bucket: str = Field(..., description="桶名")
    region: str = Field(..., description="区域")
    allow_path: Annotated[str, Field(description="允许访问的路径前缀，如 'user/123/' 或 'public/avatar/'")] = ""
    action_type: ActionType = Field(default=ActionType.WRITE, description="允许的操作类型")
    duration_seconds: Annotated[int, Field(ge=60, le=43200, description="凭证有效期（秒）")] = 900

    @field_validator("allow_path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        """规范化路径：去除开头的 /，确保非空路径以 / 结尾。"""
        if not v:
            return ""
        v = v.strip()
        if v.startswith("/"):
            v = v[1:]
        # 如果路径不为空且不以 / 结尾，添加 /
        if v and not v.endswith("/"):
            v = v + "/"
        return v


class STSCredentials(BaseModel):
    """STS 临时凭证（统一输出格式，AWS 标准命名）。

    前端 S3 SDK 可直接使用这些字段。
    """

    model_config = ConfigDict(frozen=True)

    access_key_id: str = Field(..., description="临时 AccessKeyId")
    secret_access_key: str = Field(..., description="临时 SecretAccessKey")
    session_token: str = Field(..., description="临时 SessionToken")
    expiration: datetime = Field(..., description="过期时间（UTC）")

    # 返回给 client 用于配置 S3 SDK
    region: str | None = Field(default=None, description="区域")
    endpoint: str | None = Field(default=None, description="S3 端点（MinIO/私有云场景）")
    bucket: str | None = Field(default=None, description="桶名")


class TencentSTSConfig(BaseModel):
    """腾讯云 STS Provider 配置。"""

    model_config = ConfigDict(frozen=True)

    secret_id: str = Field(..., description="腾讯云 SecretId")
    secret_key: str = Field(..., description="腾讯云 SecretKey")
    region: str = Field(default="ap-guangzhou", description="默认区域")

    # 可选：用于 AssumeRole 模式
    role_arn: str | None = Field(default=None, description="角色 ARN（AssumeRole 模式）")

    # appid 用于构造资源 ARN
    appid: str | None = Field(default=None, description="腾讯云 AppId（从 bucket 名解析或显式指定）")

    # 返回给 client 的 S3 端点
    cos_endpoint_template: str = Field(
        default="https://cos.{region}.myqcloud.com",
        description="COS S3 兼容端点模板",
    )

    def get_cos_endpoint(self, region: str | None = None) -> str:
        """获取 COS S3 兼容端点。"""
        r = region or self.region
        return self.cos_endpoint_template.format(region=r)


__all__ = [
    "ActionType",
    "STSRequest",
    "STSCredentials",
    "TencentSTSConfig",
]
