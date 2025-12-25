"""腾讯云 STS Provider（不依赖 tencentcloud-sdk-python）。

自实现 TC3-HMAC-SHA256 签名算法。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from aury.sdk.storage.exceptions import STSRequestError, STSSignatureError
from aury.sdk.storage.sts.models import (
    STSCredentials,
    STSRequest,
    TencentSTSConfig,
)
from aury.sdk.storage.sts.policy import TencentPolicyBuilder
from aury.sdk.storage.sts.provider import ISTSProvider


class TencentTC3Signer:
    """腾讯云 TC3-HMAC-SHA256 签名器。

    参考: https://cloud.tencent.com/document/api/1312/48171
    """

    SERVICE = "sts"
    ALGORITHM = "TC3-HMAC-SHA256"
    HOST = "sts.tencentcloudapi.com"
    ENDPOINT = f"https://{HOST}"

    def __init__(self, secret_id: str, secret_key: str) -> None:
        self._secret_id = secret_id
        self._secret_key = secret_key

    @staticmethod
    def _hmac_sha256(key: bytes, msg: str) -> bytes:
        """HMAC-SHA256。"""
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    @staticmethod
    def _sha256_hex(data: str) -> str:
        """SHA256 哈希（十六进制）。"""
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _get_signature_key(self, date: str) -> bytes:
        """派生签名密钥。

        TC3 + SecretKey -> date -> service -> tc3_request
        """
        k_date = self._hmac_sha256(f"TC3{self._secret_key}".encode("utf-8"), date)
        k_service = self._hmac_sha256(k_date, self.SERVICE)
        k_signing = self._hmac_sha256(k_service, "tc3_request")
        return k_signing

    def sign(
        self,
        action: str,
        payload: dict[str, Any],
        timestamp: int | None = None,
        region: str = "",
    ) -> dict[str, str]:
        """生成签名并返回完整的请求头。

        Args:
            action: API 动作名，如 GetFederationToken
            payload: 请求体 JSON
            timestamp: 时间戳（秒），默认当前时间
            region: 区域

        Returns:
            包含 Authorization 等签名头的字典
        """
        if timestamp is None:
            timestamp = int(datetime.now(timezone.utc).timestamp())

        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

        # 1. 构造规范请求
        http_method = "POST"
        canonical_uri = "/"
        canonical_querystring = ""
        content_type = "application/json; charset=utf-8"
        payload_json = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        hashed_payload = self._sha256_hex(payload_json)

        canonical_headers = f"content-type:{content_type}\nhost:{self.HOST}\n"
        signed_headers = "content-type;host"

        canonical_request = "\n".join([
            http_method,
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            hashed_payload,
        ])

        # 2. 构造待签名字符串
        credential_scope = f"{date}/{self.SERVICE}/tc3_request"
        hashed_request = self._sha256_hex(canonical_request)
        string_to_sign = "\n".join([
            self.ALGORITHM,
            str(timestamp),
            credential_scope,
            hashed_request,
        ])

        # 3. 计算签名
        signing_key = self._get_signature_key(date)
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # 4. 组装 Authorization
        authorization = (
            f"{self.ALGORITHM} "
            f"Credential={self._secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        # 返回请求头
        headers = {
            "Host": self.HOST,
            "Content-Type": content_type,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2018-08-13",
            "Authorization": authorization,
        }
        if region:
            headers["X-TC-Region"] = region

        return headers


class TencentSTSProvider(ISTSProvider):
    """腾讯云 STS Provider。

    支持两种模式：
    1. GetFederationToken: 联合身份，不需要预建 Role
    2. AssumeRole: 角色扮演，需要配置 role_arn

    默认使用 GetFederationToken（适合简单的 C 端上传场景）。
    """

    def __init__(self, config: TencentSTSConfig) -> None:
        self._config = config
        self._signer = TencentTC3Signer(config.secret_id, config.secret_key)
        self._policy_builder = TencentPolicyBuilder(appid=config.appid)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _extract_appid(self, bucket: str) -> str | None:
        """从 bucket 名提取 appid。"""
        if self._config.appid:
            return self._config.appid
        if "-" in bucket:
            parts = bucket.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return parts[1]
        return None

    async def _call_api(
        self,
        action: str,
        params: dict[str, Any],
        region: str = "",
    ) -> dict[str, Any]:
        """调用腾讯云 API。"""
        headers = self._signer.sign(action, params, region=region)
        payload = json.dumps(params, separators=(',', ':'), ensure_ascii=False)

        client = self._get_client()
        try:
            resp = await client.post(
                TencentTC3Signer.ENDPOINT,
                content=payload,
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise STSRequestError(
                f"HTTP 请求失败: {e.response.status_code}",
                code="HTTPError",
            ) from e
        except httpx.RequestError as e:
            raise STSRequestError(f"网络请求失败: {e}", code="NetworkError") from e

        data = resp.json()

        # 检查 API 错误
        response = data.get("Response", {})
        if "Error" in response:
            error = response["Error"]
            raise STSRequestError(
                message=error.get("Message", "Unknown error"),
                code=error.get("Code"),
                request_id=response.get("RequestId"),
            )

        return response

    async def get_credentials(self, request: STSRequest) -> STSCredentials:
        """获取 STS 临时凭证。"""
        # 构建 Policy
        appid = self._extract_appid(request.bucket)
        policy = self._policy_builder.build(request, appid=appid)

        # 根据配置选择 API
        if self._config.role_arn:
            return await self._assume_role(request, policy)
        else:
            return await self._get_federation_token(request, policy)

    async def _get_federation_token(
        self,
        request: STSRequest,
        policy: str,
    ) -> STSCredentials:
        """使用 GetFederationToken 获取临时凭证。"""
        params = {
            "Name": "storage-sdk",
            "Policy": policy,
            "DurationSeconds": request.duration_seconds,
        }

        response = await self._call_api(
            action="GetFederationToken",
            params=params,
            region=request.region,
        )

        credentials = response.get("Credentials", {})
        expiration_str = response.get("Expiration")

        # 解析过期时间
        if expiration_str:
            expiration = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        else:
            expired_time = response.get("ExpiredTime", 0)
            expiration = datetime.fromtimestamp(expired_time, tz=timezone.utc)

        return STSCredentials(
            access_key_id=credentials.get("TmpSecretId", ""),
            secret_access_key=credentials.get("TmpSecretKey", ""),
            session_token=credentials.get("Token", ""),
            expiration=expiration,
            region=request.region,
            endpoint=self._config.get_cos_endpoint(request.region),
            bucket=request.bucket,
        )

    async def _assume_role(
        self,
        request: STSRequest,
        policy: str,
    ) -> STSCredentials:
        """使用 AssumeRole 获取临时凭证。"""
        params = {
            "RoleArn": self._config.role_arn,
            "RoleSessionName": "storage-sdk",
            "DurationSeconds": request.duration_seconds,
            "Policy": policy,
        }

        response = await self._call_api(
            action="AssumeRole",
            params=params,
            region=request.region,
        )

        credentials = response.get("Credentials", {})
        expiration_str = response.get("Expiration")

        # 解析过期时间
        if expiration_str:
            expiration = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        else:
            expired_time = response.get("ExpiredTime", 0)
            expiration = datetime.fromtimestamp(expired_time, tz=timezone.utc)

        return STSCredentials(
            access_key_id=credentials.get("TmpSecretId", ""),
            secret_access_key=credentials.get("TmpSecretKey", ""),
            session_token=credentials.get("Token", ""),
            expiration=expiration,
            region=request.region,
            endpoint=self._config.get_cos_endpoint(request.region),
            bucket=request.bucket,
        )


__all__ = [
    "TencentSTSProvider",
    "TencentTC3Signer",
]
