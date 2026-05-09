"""阿里云 STS Provider（不依赖 aliyun-python-sdk）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiohttp

from aury.sdk.storage.exceptions import STSRequestError
from aury.sdk.storage.sts.models import OSSSTSConfig, STSCredentials, STSRequest
from aury.sdk.storage.sts.policy import AliyunPolicyBuilder
from aury.sdk.storage.sts.provider import ISTSProvider


def _percent_encode(value: Any) -> str:
    return quote(str(value), safe="~")


class AliyunRPCSigner:
    """阿里云 RPC API HMAC-SHA1 签名器。"""

    def __init__(self, access_key_secret: str) -> None:
        self._access_key_secret = access_key_secret

    def sign(self, method: str, params: dict[str, Any]) -> str:
        """生成 RPC API 签名。"""
        canonical_query = "&".join(
            f"{_percent_encode(key)}={_percent_encode(params[key])}"
            for key in sorted(params)
        )
        string_to_sign = f"{method.upper()}&%2F&{_percent_encode(canonical_query)}"
        digest = hmac.new(
            f"{self._access_key_secret}&".encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")


class AliyunSTSProvider(ISTSProvider):
    """阿里云 STS Provider。"""

    def __init__(self, config: OSSSTSConfig) -> None:
        self._config = config
        self._signer = AliyunRPCSigner(config.access_key_secret)
        self._policy_builder = AliyunPolicyBuilder()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP 会话。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _build_common_params(self, action: str) -> dict[str, Any]:
        """构建阿里云 RPC 公共参数。"""
        return {
            "Action": action,
            "Version": "2015-04-01",
            "Format": "JSON",
            "AccessKeyId": self._config.access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": uuid4().hex,
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    async def _call_api(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """调用阿里云 STS RPC API。"""
        payload = self._build_common_params(action)
        payload.update(params)
        payload["Signature"] = self._signer.sign("POST", payload)

        session = await self._get_session()
        try:
            async with session.post(
                self._config.sts_endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    raise STSRequestError(
                        data.get("Message", f"HTTP 请求失败: {resp.status}"),
                        code=data.get("Code", "HTTPError"),
                        request_id=data.get("RequestId"),
                    )
        except aiohttp.ClientError as e:
            raise STSRequestError(f"网络请求失败: {e}", code="NetworkError") from e

        if "Code" in data and "Message" in data:
            raise STSRequestError(
                data.get("Message", "Unknown error"),
                code=data.get("Code"),
                request_id=data.get("RequestId"),
            )
        return data

    async def get_credentials(self, request: STSRequest) -> STSCredentials:
        """获取 STS 临时凭证。"""
        policy = self._policy_builder.build(request)
        params: dict[str, Any] = {
            "RoleArn": self._config.role_arn,
            "RoleSessionName": self._config.role_session_name,
            "Policy": policy,
            "DurationSeconds": max(900, request.duration_seconds),
        }
        if self._config.external_id:
            params["ExternalId"] = self._config.external_id

        response = await self._call_api("AssumeRole", params)
        credentials = response.get("Credentials", {})
        expiration_str = credentials.get("Expiration")
        if expiration_str:
            expiration = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        else:
            expiration = datetime.now(timezone.utc)

        return STSCredentials(
            access_key_id=credentials.get("AccessKeyId", ""),
            secret_access_key=credentials.get("AccessKeySecret", ""),
            session_token=credentials.get("SecurityToken", ""),
            expiration=expiration,
            region=request.region,
            endpoint=self._config.get_endpoint(request.region),
            bucket=request.bucket,
        )

    async def close(self) -> None:
        """关闭 aiohttp 会话。"""
        if self._session:
            await self._session.close()
            self._session = None


__all__ = [
    "AliyunRPCSigner",
    "AliyunSTSProvider",
]
