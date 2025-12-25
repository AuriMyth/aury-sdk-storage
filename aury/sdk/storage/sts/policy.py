"""策略构建器 - 将业务意图翻译为各厂商 Policy JSON。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from .models import ActionType, STSRequest


class IPolicyBuilder(ABC):
    """策略构建器抽象接口。"""

    @abstractmethod
    def build(self, request: STSRequest, **kwargs: Any) -> str:
        """构建 Policy JSON 字符串。"""
        pass


class TencentPolicyBuilder(IPolicyBuilder):
    """腾讯云 CAM Policy 构建器。

    资源格式: qcs::cos:{region}:uid/{appid}:{bucket}/{path}*
    """

    def __init__(self, appid: str | None = None) -> None:
        self._appid = appid

    def _extract_appid_from_bucket(self, bucket: str) -> str | None:
        """从 bucket 名称中提取 appid。

        腾讯云 bucket 格式: {name}-{appid}，如 my-bucket-1250000000
        """
        if "-" not in bucket:
            return None
        parts = bucket.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[1]
        return None

    def _get_appid(self, bucket: str) -> str:
        """获取 appid。"""
        if self._appid:
            return self._appid
        extracted = self._extract_appid_from_bucket(bucket)
        if extracted:
            return extracted
        raise ValueError(
            f"无法从 bucket '{bucket}' 提取 appid，请显式指定 appid 或使用标准格式 bucket-appid"
        )

    def _get_actions(self, action_type: ActionType) -> list[str]:
        """获取操作列表。"""
        read_actions = [
            "cos:GetObject",
            "cos:HeadObject",
        ]
        write_actions = [
            "cos:PutObject",
            "cos:PostObject",
            "cos:InitiateMultipartUpload",
            "cos:ListMultipartUploads",
            "cos:ListParts",
            "cos:UploadPart",
            "cos:CompleteMultipartUpload",
            "cos:AbortMultipartUpload",
            # 删除权限（单版本与多版本桶）
            "cos:DeleteObject",
            "cos:DeleteObjectVersion",
        ]

        match action_type:
            case ActionType.READ:
                return read_actions
            case ActionType.WRITE:
                return write_actions
            case ActionType.ALL:
                return read_actions + write_actions

    def _build_resource(
        self,
        region: str,
        appid: str,
        bucket: str,
        path: str,
    ) -> str:
        """构建资源 ARN。"""
        # 路径处理：如果为空则允许整个 bucket
        if path:
            resource_path = f"{path}*"
        else:
            resource_path = "*"

        return f"qcs::cos:{region}:uid/{appid}:{bucket}/{resource_path}"

    def build(self, request: STSRequest, **kwargs: Any) -> str:
        """构建腾讯云 CAM Policy。"""
        appid = kwargs.get("appid") or self._appid
        if not appid:
            appid = self._get_appid(request.bucket)

        actions = self._get_actions(request.action_type)
        resource = self._build_resource(
            region=request.region,
            appid=appid,
            bucket=request.bucket,
            path=request.allow_path,
        )

        policy = {
            "version": "2.0",
            "statement": [
                {
                    "effect": "allow",
                    "action": actions,
                    "resource": [resource],
                }
            ],
        }

        return json.dumps(policy, separators=(",", ":"))


# 预留其他厂商的 PolicyBuilder
# class AliyunPolicyBuilder(IPolicyBuilder): ...
# class AWSPolicyBuilder(IPolicyBuilder): ...


__all__ = [
    "IPolicyBuilder",
    "TencentPolicyBuilder",
]
